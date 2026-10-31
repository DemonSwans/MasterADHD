import base64
import contextlib
import importlib.util
import json
import logging
import logging.handlers
import os
import shutil
import sqlite3
import ssl
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
import flet as ft

FALLBACK_VERSION = "1.6.27"
# Źródło aktualizacji OTA — musi być identyczne z GITHUB_RAW_BASE w main.py.
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/DemonSwans/MasterADHD/master"

# --- PRYWATNY FOLDER WEWNĘTRZNY APLIKACJI ---
def get_app_dir():
    base = os.environ.get("FLET_APP_STORAGE_DATA") or os.path.expanduser("~")
    app_dir = os.path.join(base, ".master_adhd_data")
    try:
        os.makedirs(app_dir, exist_ok=True)
        return app_dir
    except Exception:
        return "."


APP_DIR = get_app_dir()
LOCAL_DB = os.path.join(APP_DIR, "tasks.db")
CONFIG_DB = os.path.join(APP_DIR, "app_config.db")
LOG_FILE = os.path.join(APP_DIR, "master_adhd.log")

DB_BUSY_TIMEOUT_MS = 5000
SNAPSHOT_LIMIT = 10
ARCHIVE_LIMIT = 2000
SYNC_DEBOUNCE_SECONDS = 1.5

# --- SPRINT 2: rytuał dnia (2.1) i Inbox (2.3) ---
# Kategoria systemowa, do której trafiają szybkie zrzuty myśli (bez zmiany schematu).
INBOX_CATEGORY = "📥 Inbox"
# Tryb przełomu dnia: pytaj użytkownika (domyślnie) albo decyduj automatycznie.
DAY_ROLLOVER_ASK = "ask"
DAY_ROLLOVER_AUTO_KEEP = "auto_keep"
DAY_ROLLOVER_AUTO_MASTER = "auto_master"
DAY_ROLLOVER_MODES = (DAY_ROLLOVER_ASK, DAY_ROLLOVER_AUTO_KEEP, DAY_ROLLOVER_AUTO_MASTER)

# --- SPRINT 3.2: znaczniki czasu i „zadania duchy” ---
# Po ilu dniach bez zmian Master pokazuje zadanie jako „dawno nietknięte”.
STALE_TASK_DAYS = 14


def now_iso():
    """Znacznik czasu ISO (sekundy) dla `tasks.created_at` / `tasks.updated_at`."""
    return datetime.now().isoformat(timespec="seconds")


# Jeden zamek na całą bazę. SQLite i tak serializuje zapisy, a dzięki temu
# wątek synchronizacji nie walczy z UI o plik (koniec "database is locked").
DB_WRITE_LOCK = threading.RLock()


# --- LOGOWANIE DO PLIKU (na telefonie print() przepada) ---
def _setup_logging():
    logger = logging.getLogger("master_adhd")
    if getattr(logger, "_master_configured", False):
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    except Exception:
        pass
    logger._master_configured = True
    return logger


LOG = _setup_logging()


def export_logs():
    """Kopiuje aktualny log do pliku z datą (do wysłania/obejrzenia)."""
    try:
        if not os.path.exists(LOG_FILE):
            return False, "Brak pliku logów — aplikacja nie zapisała jeszcze żadnego wpisu."
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = os.path.join(APP_DIR, f"master_adhd_log_{ts}.txt")
        shutil.copy2(LOG_FILE, target)
        return True, target
    except Exception as e:
        return False, f"Błąd eksportu logów: {e}"


def is_newer_version(remote_ver: str, local_ver: str) -> bool:
    try:
        r_parts = [int(x) for x in remote_ver.strip().split(".")]
        l_parts = [int(x) for x in local_ver.strip().split(".")]
        return r_parts > l_parts
    except Exception:
        return remote_ver.strip() != local_ver.strip()


# --- ODCZYT WERSJI ---
def _read_version_file(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


def get_bundled_version():
    """Wersja kodu wbudowanego w APK (niezmieniana przez OTA).

    main.py ustawia zmienną MASTER_ADHD_BUNDLED_VERSION przed uruchomieniem
    kodu z OTA, dzięki czemu Ustawienia pokazują prawdziwą wersję wbudowaną,
    nawet gdy działa nowszy plik z folderu danych.
    """
    env_version = (os.environ.get("MASTER_ADHD_BUNDLED_VERSION") or "").strip()
    if env_version:
        return env_version
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for name in ("version.txt", "ota_version.txt"):
        val = _read_version_file(os.path.join(base_dir, name))
        if val:
            return val
    return FALLBACK_VERSION


def get_local_version():
    """Wersja kodu, który aktualnie działa (a nie tego, co leży na dysku).

    Czytamy tylko pliki obok modułu: dla kodu wbudowanego to folder APK, dla
    pobranego OTA — folder danych. Wcześniej braliśmy maksimum także z folderu
    danych, przez co pobrany (lecz nieuruchomiony) plik zmieniał numer wersji
    w UI, mimo że wykonywał się stary kod.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    best = ""
    for name in ("version.txt", "ota_version.txt"):
        val = _read_version_file(os.path.join(base_dir, name))
        if val and (not best or is_newer_version(val, best)):
            best = val
    return best or FALLBACK_VERSION


APP_VERSION = get_local_version()


def get_ssl_context():
    """Kontekst SSL z fallbackiem dla środowisk mobilnych (brak CA w Androidzie)."""
    try:
        return ssl._create_unverified_context()
    except Exception:
        try:
            return ssl.create_default_context()
        except Exception:
            return None


# --- POŁĄCZENIA Z BAZĄ ---
@contextlib.contextmanager
def db_connection(db_path=None, write=False):
    """Połączenie SQLite z busy_timeout, poprawnym commit/rollback i zamykaniem.

    `with sqlite3.connect(...)` NIE zamyka połączenia — tylko commituje, przez co
    na Windows pliki zostawały zablokowane, a na telefonie narastały otwarte
    handle'e. Tutaj połączenie jest zawsze zamykane.
    """
    path = db_path or LOCAL_DB
    with DB_WRITE_LOCK:
        conn = None
        try:
            conn = sqlite3.connect(path, timeout=DB_BUSY_TIMEOUT_MS / 1000.0)
            conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
            yield conn
            conn.commit()
            if write and os.path.abspath(path) == os.path.abspath(LOCAL_DB):
                _touch_db_change()
        except Exception:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            if conn is not None:
                conn.close()


def _touch_db_change():
    """Znacznik ostatniej zmiany danych — używany do wykrywania starszej chmury."""
    try:
        conn = sqlite3.connect(CONFIG_DB, timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            row = conn.execute("SELECT value FROM config WHERE key = 'db_change_seq'").fetchone()
            seq = int(row[0]) + 1 if row and row[0] else 1
            conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", ("db_change_seq", str(seq)))
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                ("db_last_change", datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


# --- KONFIGURACJA LOKALNA ---
def init_config_db():
    with db_connection(CONFIG_DB) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")


def get_config(key, default=""):
    try:
        with db_connection(CONFIG_DB) as conn:
            res = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
            return res[0] if res else default
    except Exception:
        return default


def set_config(key, value):
    with db_connection(CONFIG_DB) as conn:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))


# --- INTEGRALNOŚĆ I KOPIE BAZY ---
def check_db_integrity(db_path=None):
    """Sprawdza, czy plik jest poprawną bazą SQLite (nie tylko nagłówek)."""
    path = db_path or LOCAL_DB
    if not os.path.exists(path):
        return False, "Brak pliku bazy"
    conn = None
    try:
        conn = sqlite3.connect(path, timeout=DB_BUSY_TIMEOUT_MS / 1000.0)
        conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
        row = conn.execute("PRAGMA quick_check").fetchone()
        status = (row[0] if row else "").strip()
        if status.lower() == "ok":
            return True, "ok"
        return False, status or "nieznany błąd integralności"
    except Exception as e:
        return False, str(e)
    finally:
        if conn is not None:
            conn.close()


def _validate_sqlite_bytes(data):
    """Waliduje pobrane bajty bazy przez zapis do pliku tymczasowego."""
    if not data or not data.startswith(b"SQLite format 3"):
        return False, "to nie jest plik SQLite"
    tmp_dir = os.path.dirname(os.path.abspath(LOCAL_DB)) or APP_DIR
    tmp_path = os.path.join(tmp_dir, ".validate_download.db")
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
        return check_db_integrity(tmp_path)
    except Exception as e:
        return False, str(e)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def backup_database(dest_path, source_path=None):
    """Tworzy spójną kopię bazy przez API SQLite (bezpieczne dla WAL).

    `shutil.copy2` kopiuje tylko plik główny i gubi transakcje z pliku -wal,
    przez co do chmury/snapshotu mogły trafiać niepełne dane.
    """
    src_path = source_path or LOCAL_DB
    with DB_WRITE_LOCK:
        if not os.path.exists(src_path):
            return False, "Brak źródłowej bazy"
        tmp_path = dest_path + ".tmp"
        try:
            src = sqlite3.connect(src_path, timeout=DB_BUSY_TIMEOUT_MS / 1000.0)
            try:
                src.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
                try:
                    src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass
                dest = sqlite3.connect(tmp_path)
                try:
                    src.backup(dest)
                finally:
                    dest.close()
            finally:
                src.close()
            os.replace(tmp_path, dest_path)
            return True, dest_path
        except Exception as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return False, str(e)


def get_snapshot_dir():
    base = os.path.dirname(os.path.abspath(LOCAL_DB)) or APP_DIR
    path = os.path.join(base, "snapshots")
    os.makedirs(path, exist_ok=True)
    return path


def list_snapshots():
    """Lista snapshotów, najnowsze najpierw (po czasie modyfikacji, nie po nazwie)."""
    try:
        folder = get_snapshot_dir()
        files = [f for f in os.listdir(folder) if f.startswith("tasks_") and f.endswith(".db")]
        # Nazwa zawiera tag (pre_pull, pre_end_of_day...), więc sortowanie po nazwie
        # mieszało czasy. Liczy się data modyfikacji pliku.
        files.sort(
            key=lambda f: (os.path.getmtime(os.path.join(folder, f)), f),
            reverse=True,
        )
        return [os.path.join(folder, f) for f in files]
    except Exception:
        return []


def prune_snapshots(keep=SNAPSHOT_LIMIT):
    try:
        snaps = list_snapshots()
        for old in snaps[keep:]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception:
        pass


def make_local_snapshot(tag="pre_sync"):
    """Snapshot z rotacją (nie jest już kasowany przy zamykaniu aplikacji)."""
    if not os.path.exists(LOCAL_DB):
        return False, "Brak bazy lokalnej"
    try:
        folder = get_snapshot_dir()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        target = os.path.join(folder, f"tasks_{tag}_{ts}.db")
        ok, msg = backup_database(target)
        if ok:
            prune_snapshots()
            return True, target
        return False, msg
    except Exception as e:
        return False, str(e)


def restore_pre_sync_backup(snapshot_path=None):
    """Przywraca najnowszy (lub wskazany) snapshot bezpieczeństwa."""
    snaps = list_snapshots()
    target = snapshot_path or (snaps[0] if snaps else None)
    if not target or not os.path.exists(target):
        return False, "Brak zapisanego snapshotu bezpieczeństwa"
    if os.path.exists(LOCAL_DB):
        make_local_snapshot("pre_restore")
    ok, msg = backup_database(LOCAL_DB, source_path=target)
    if ok:
        # Snapshot może pochodzić sprzed migracji schematu.
        ensure_schema_upgrade()
        LOG.info("Przywrócono snapshot: %s", target)
        push_database_async()
        return True, f"Przywrócono kopię: {os.path.basename(target)}"
    return False, f"Błąd przywracania: {msg}"


def restore_snapshot_file(path):
    return restore_pre_sync_backup(path)


def ensure_db_health():
    """Sprawdza bazę na starcie; przy uszkodzeniu odkłada plik i przywraca snapshot."""
    if not os.path.exists(LOCAL_DB):
        return True, "Nowa baza"
    ok, status = check_db_integrity(LOCAL_DB)
    if ok:
        return True, "ok"
    LOG.error("Uszkodzona baza: %s", status)
    try:
        corrupt_path = LOCAL_DB + ".corrupt_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        os.replace(LOCAL_DB, corrupt_path)
        for suffix in ("-wal", "-shm"):
            p = LOCAL_DB + suffix
            if os.path.exists(p):
                os.remove(p)
        snaps = list_snapshots()
        if snaps:
            restored, msg = backup_database(LOCAL_DB, source_path=snaps[0])
            if restored:
                return True, f"Baza była uszkodzona — przywrócono {os.path.basename(snaps[0])}"
        return False, f"Baza była uszkodzona ({status}), brak snapshotu do przywrócenia"
    except Exception as e:
        return False, f"Baza była uszkodzona, nie udało się jej odłożyć: {e}"


# --- SYNCHRONIZACJA (Chmura Gist / HA / Folder) ---
def is_valid_sqlite(file_data):
    return bool(file_data) and file_data.startswith(b"SQLite format 3")


def _read_remote_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _restore_db_from_bytes(data, label="pobraną bazę"):
    """Podmienia lokalną bazę na pobrane bajty (przez API SQLite, bez WAL-śmieci)."""
    ok, status = _validate_sqlite_bytes(data)
    if not ok:
        return False, f"Odrzucono: {label} jest uszkodzona ({status})"
    tmp_dir = os.path.dirname(os.path.abspath(LOCAL_DB)) or APP_DIR
    tmp_path = os.path.join(tmp_dir, ".download_restore.db")
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
        backup_database(LOCAL_DB, source_path=tmp_path)
        # Pobrana baza może mieć schemat starszy od działającego kodu.
        ensure_schema_upgrade()
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
    return True, "ok"


def pull_database(force=False):
    """Pobiera bazę z chmury/dysku.

    `force=False` (start aplikacji) NIE nadpisuje nowszych danych lokalnych.
    `force=True` (ręczna synchronizacja) pobiera zawsze.
    """
    mode = get_config("sync_mode", "none")

    if mode == "gist":
        token = get_config("gist_token", "").strip()
        gist_id = get_config("gist_id", "").strip()
        if not token:
            return False, "Brak Tokenu GitHub!"
        if not gist_id:
            return True, "Gotowe do utworzenia nowej chmury"
        try:
            req = urllib.request.Request(
                f"https://api.github.com/gists/{gist_id}",
                method="GET",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github.v3+json",
                    "Cache-Control": "no-cache"
                }
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    files = data.get("files", {})
                    if "tasks.db.b64" in files:
                        remote_ts = ""
                        backup_file = files.get("backup_tasks.json") or {}
                        content = backup_file.get("content") or ""
                        if content:
                            try:
                                remote_ts = json.loads(content).get("exported_at", "") or ""
                            except Exception:
                                remote_ts = ""
                        if not force:
                            local_ts = get_config("db_last_change", "")
                            if remote_ts and local_ts and remote_ts < local_ts:
                                return True, "Chmura jest starsza niż dane lokalne — pominięto pobieranie"

                        b64_content = files["tasks.db.b64"].get("content", "")
                        raw_db = base64.b64decode(b64_content.encode("utf-8"))
                        if not is_valid_sqlite(raw_db):
                            return False, "Odrzucono: Plik w chmurze jest uszkodzony"
                        make_local_snapshot("pre_pull")
                        ok, msg = _restore_db_from_bytes(raw_db, "baza w chmurze")
                        if not ok:
                            return False, msg
                        # Po pobraniu lokalny znacznik czasu = czas chmury, więc
                        # kolejny start nie pobiera w kółko tych samych danych.
                        if remote_ts:
                            set_config("db_last_change", remote_ts)
                        return True, "Pobrano bazę z Chmury"
                    return True, "Chmura gotowa"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False, "Nie znaleziono Chmury o podanym ID!"
            if e.code == 401:
                return False, "Nieprawidłowy Token GitHub!"
            return False, f"Błąd chmury: {e.code}"
        except Exception as e:
            LOG.warning("Pull gist: %s", e)
            return False, f"Brak połączenia z chmurą: {e}"

    elif mode == "ha_server":
        url = get_config("ha_url", "").strip().rstrip("/")
        if not url:
            LOG.warning("Brak adresu serwera HA")
            return False, "Brak adresu serwera HA"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    data = resp.read()
                    if not is_valid_sqlite(data):
                        return False, "Odrzucono: Pobrany plik to nie baza"
                    # Serwer HA nie ma metadanych w treści, ale zwykle wysyła
                    # Last-Modified — nie pozwalamy starszym danym nadpisać lokalnych.
                    last_modified = ""
                    remote_dt = None
                    try:
                        last_modified = resp.headers.get("Last-Modified", "") or ""
                    except Exception:
                        last_modified = ""
                    if last_modified:
                        try:
                            remote_dt = parsedate_to_datetime(last_modified)
                            if remote_dt.tzinfo is not None:
                                remote_dt = remote_dt.astimezone().replace(tzinfo=None)
                        except Exception:
                            remote_dt = None
                    if not force and remote_dt is not None:
                        local_ts = get_config("db_last_change", "")
                        if local_ts:
                            try:
                                if remote_dt < datetime.fromisoformat(local_ts):
                                    return True, "Serwer HA ma starsze dane — pominięto pobieranie"
                            except Exception:
                                pass
                    make_local_snapshot("pre_pull")
                    ok, msg = _restore_db_from_bytes(data, "baza z serwera HA")
                    if not ok:
                        return False, msg
                    # Znacznik czasu = czas serwera, żeby nie pobierać w kółko tego samego.
                    set_config(
                        "db_last_change",
                        (remote_dt or datetime.now()).isoformat(timespec="seconds"),
                    )
                    return True, "Pobrano bazę z serwera HA"
        except urllib.error.HTTPError as e:
            LOG.warning("HTTP %s z HA: %s", e.code, e.reason)
            return False, f"Błąd serwera (kod {e.code}): Nie udało się połączyć z serwerem."
        except Exception as e:
            LOG.warning("Błąd połączenia z HA: %s", e)
            return False, f"Brak połączenia z HA: {e}"

    elif mode == "folder":
        folder = get_config("sync_folder", "").strip()
        if not folder:
            LOG.warning("Nie podano ścieżki do folderu")
            return False, "Nie podano ścieżki do folderu!"
        if not os.path.exists(folder):
            LOG.warning("Folder %s nie istnieje", folder)
            return False, "Podany folder nie istnieje na dysku!"

        remote_file = os.path.join(folder, "tasks.db")
        if os.path.exists(remote_file):
            try:
                if not force:
                    last_push = get_config("folder_push_mtime", "")
                    remote_mtime = os.path.getmtime(remote_file)
                    if last_push:
                        try:
                            if abs(remote_mtime - float(last_push)) < 1.0:
                                return True, "Baza już zsynchronizowana z folderem"
                        except ValueError:
                            pass
                    if os.path.exists(LOCAL_DB) and remote_mtime <= os.path.getmtime(LOCAL_DB):
                        return True, "Baza lokalna jest nowsza — pominięto pobieranie"

                data = _read_remote_bytes(remote_file)
                if not is_valid_sqlite(data):
                    return False, "Odrzucono: Plik jest uszkodzony (to nie baza SQLite)"
                ok, status = _validate_sqlite_bytes(data)
                if not ok:
                    return False, f"Odrzucono: Plik jest uszkodzony ({status})"
                make_local_snapshot("pre_pull")
                backup_database(LOCAL_DB, source_path=remote_file)
                # Baza z folderu może pochodzić ze starszej wersji (brak nowych kolumn).
                ensure_schema_upgrade()
                set_config("folder_push_mtime", str(os.path.getmtime(remote_file)))
                return True, "Pobrano bazę z folderu"
            except Exception as e:
                LOG.warning("Błąd odczytu z folderu: %s", e)
                return False, f"Błąd odczytu z dysku: {e}"
        return True, "Folder gotowy"
    return True, "Tryb lokalny"


def push_database():
    """Synchroniczny zapis (używany w testach oraz przy pierwszym łączeniu)."""
    mode = get_config("sync_mode", "none")
    if mode == "none" or not os.path.exists(LOCAL_DB):
        return True, ""

    if mode == "gist":
        token = get_config("gist_token", "").strip()
        gist_id = get_config("gist_id", "").strip()
        if not token:
            return False, "Brak Tokenu GitHub"
        upload_path = os.path.join(os.path.dirname(os.path.abspath(LOCAL_DB)), ".sync_upload.db")
        try:
            ok, msg = backup_database(upload_path)
            if not ok:
                return False, f"Nie udało się przygotować kopii: {msg}"
            with open(upload_path, "rb") as f:
                b64_str = base64.b64encode(f.read()).decode("utf-8")

            # Podwójny backup: baza SQLite oraz czytelny dla człowieka plik JSON w tym samym Giście!
            json_backup_str = json.dumps(export_database_to_json_dict(), ensure_ascii=False, indent=2)

            payload = json.dumps({
                "description": "MasterADHD Cloud Database Sync & Human-Readable JSON Backup",
                "public": False,
                "files": {
                    "tasks.db.b64": {"content": b64_str},
                    "backup_tasks.json": {"content": json_backup_str}
                }
            }).encode("utf-8")

            url = f"https://api.github.com/gists/{gist_id}" if gist_id else "https://api.github.com/gists"
            method = "PATCH" if gist_id else "POST"

            req = urllib.request.Request(
                url,
                data=payload,
                method=method,
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github.v3+json",
                    "Content-Type": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status in (200, 201):
                    res_data = json.loads(resp.read().decode("utf-8"))
                    if not gist_id and "id" in res_data:
                        set_config("gist_id", res_data["id"])
                    set_config("last_push_at", datetime.now().isoformat(timespec="seconds"))
                    return True, "Zapisano w Chmurze"
        except Exception as e:
            LOG.warning("Push gist: %s", e)
            return False, f"Błąd zapisu w chmurze: {e}"
        finally:
            try:
                if os.path.exists(upload_path):
                    os.remove(upload_path)
            except Exception:
                pass

    elif mode == "ha_server":
        url = get_config("ha_url", "").strip().rstrip("/")
        if not url:
            return False, "Brak adresu"
        upload_path = os.path.join(os.path.dirname(os.path.abspath(LOCAL_DB)), ".sync_upload.db")
        try:
            ok, msg = backup_database(upload_path)
            if not ok:
                return False, f"Nie udało się przygotować kopii: {msg}"
            with open(upload_path, "rb") as f:
                data = f.read()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/octet-stream"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    set_config("last_push_at", datetime.now().isoformat(timespec="seconds"))
                    return True, "Zapisano na serwerze HA"
        except Exception as e:
            LOG.warning("Push HA: %s", e)
            return False, f"Błąd wysyłania: {e}"
        finally:
            try:
                if os.path.exists(upload_path):
                    os.remove(upload_path)
            except Exception:
                pass

    elif mode == "folder":
        folder = get_config("sync_folder", "").strip()
        if not folder or not os.path.exists(folder):
            return False, "Folder nie istnieje"
        try:
            archive_dir = os.path.join(folder, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            remote_file = os.path.join(folder, "tasks.db")
            if os.path.exists(remote_file):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_database(os.path.join(archive_dir, f"tasks_{ts}.db"), source_path=remote_file)
                backups = sorted([os.path.join(archive_dir, f) for f in os.listdir(archive_dir) if f.endswith(".db")])
                while len(backups) > 25:
                    try:
                        os.remove(backups.pop(0))
                    except Exception:
                        break
            ok, msg = backup_database(remote_file)
            if not ok:
                return False, f"Błąd zapisu: {msg}"
            try:
                set_config("folder_push_mtime", str(os.path.getmtime(remote_file)))
            except Exception:
                pass
            set_config("last_push_at", datetime.now().isoformat(timespec="seconds"))
            return True, "Zaktualizowano na Dysku"
        except Exception as e:
            LOG.warning("Push folder: %s", e)
            return False, f"Błąd zapisu: {e}"
    return True, ""


# --- KOLEJKA ZAPISU (debounce + jeden wątek) ---
SYNC_STATE = {"status": "idle", "message": "", "updated_at": None}
_SYNC_EVENT = threading.Event()
_SYNC_IDLE = threading.Event()
_SYNC_IDLE.set()
_SYNC_PENDING = threading.Event()
# Startowy pull trwa — nie pozwalamy w tym czasie wysłać (pustej) bazy do chmury.
_STARTUP_PULL_ACTIVE = threading.Event()
_PUSH_DEFERRED = threading.Event()
_SYNC_THREAD = None
_SYNC_THREAD_LOCK = threading.Lock()
_SYNC_STATUS_CALLBACK = None


def register_sync_status_callback(cb):
    global _SYNC_STATUS_CALLBACK
    _SYNC_STATUS_CALLBACK = cb


def get_sync_state():
    return dict(SYNC_STATE)


def set_sync_status(status, message=""):
    SYNC_STATE["status"] = status
    SYNC_STATE["message"] = message
    SYNC_STATE["updated_at"] = datetime.now()
    cb = _SYNC_STATUS_CALLBACK
    if cb:
        try:
            cb(get_sync_state())
        except Exception:
            pass


def _ensure_sync_worker():
    global _SYNC_THREAD
    with _SYNC_THREAD_LOCK:
        if _SYNC_THREAD is not None and _SYNC_THREAD.is_alive():
            return
        _SYNC_THREAD = threading.Thread(target=_sync_worker, name="master-adhd-sync", daemon=True)
        _SYNC_THREAD.start()


def _sync_worker():
    while True:
        _SYNC_EVENT.wait()
        # Debounce: czekamy aż użytkownik przestanie klikać (mniej zapisów, mniej limitów API).
        while True:
            _SYNC_EVENT.clear()
            time.sleep(SYNC_DEBOUNCE_SECONDS)
            if not _SYNC_EVENT.is_set():
                break
        _SYNC_IDLE.clear()
        try:
            if get_config("sync_mode", "none") != "none":
                set_sync_status("syncing", "Zapisywanie...")
                ok, msg = push_database()
                if ok:
                    set_sync_status("ok", msg or "Zapisano")
                else:
                    set_sync_status("error", msg)
                    LOG.error("Synchronizacja nieudana: %s", msg)
        except Exception as e:
            set_sync_status("error", str(e))
            LOG.exception("Błąd wątku synchronizacji")
        finally:
            # Jeśli w trakcie zapisu przyszło nowe żądanie, zostawiamy pending.
            if not _SYNC_EVENT.is_set():
                _SYNC_PENDING.clear()
            _SYNC_IDLE.set()


def flush_pending_sync(timeout=3.0):
    """Dopina zaległy zapis (wywoływane przy zamykaniu aplikacji).

    Czekamy także na zapis, który jest jeszcze w fazie debounce — samo
    `_SYNC_IDLE` było ustawione i zamykanie gubiło ostatnią zmianę.
    """
    try:
        if _SYNC_THREAD is None or not _SYNC_THREAD.is_alive():
            return
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            if not _SYNC_PENDING.is_set() and _SYNC_IDLE.is_set():
                return
            time.sleep(0.1)
    except Exception:
        pass


def push_database_async():
    """Nieblokujący zapis: folder = szybki zapis lokalny, sieć = kolejka z debounce."""
    mode = get_config("sync_mode", "none")
    if mode == "none":
        return
    # Najpierw musimy wiedzieć, co jest w chmurze — inaczej świeżo utworzona
    # pusta baza nadpisałaby dane, które właśnie się pobierają.
    if _STARTUP_PULL_ACTIVE.is_set():
        _PUSH_DEFERRED.set()
        return
    try:
        if mode == "folder":
            ok, msg = push_database()
            set_sync_status("ok" if ok else "error", msg or "Zapisano na dysku")
            if not ok:
                LOG.error("Zapis na dysk nieudany: %s", msg)
            return
        _ensure_sync_worker()
        _SYNC_PENDING.set()
        _SYNC_EVENT.set()
    except Exception as e:
        LOG.warning("Błąd w trybie %s: %s", mode, e)


def reset_sync_on_exit():
    """Dopina zaległy zapis. Snapshoty zostają (rotacja), nie są już kasowane."""
    flush_pending_sync(timeout=4.0)
    LOG.info("Zamknięcie aplikacji — snapshoty zachowane")


# --- INSTALACJA I AKTYWACJA OTA ---
def _version_files():
    """Pliki z numerem wersji, które czyta main.py (muszą zostać w zgodzie)."""
    return (
        os.path.join(APP_DIR, "version.txt"),
        os.path.join(APP_DIR, "ota_version.txt"),
    )


def install_ota_code(new_code, remote_ver):
    """Zapisuje pobrany kod OTA w folderze danych (atomowo, z kopią .prev).

    Zapisywanie przez plik tymczasowy jest istotne na telefonie: przerwany
    zapis zostawiał ucięty app_logic_ota.py, który potem wysypywał start.
    """
    if not new_code or not remote_ver:
        return False, "Brak kodu lub numeru wersji w odpowiedzi GitHuba"
    try:
        compile(new_code, "app_logic_ota.py", "exec")
    except SyntaxError as e:
        return False, f"Błąd składni w pobranym kodzie (odrzucono): {e}"

    target = os.path.join(APP_DIR, "app_logic_ota.py")
    tmp_path = target + ".tmp"
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        previous_version = get_local_version()
        if os.path.exists(target):
            try:
                shutil.copy2(target, os.path.join(APP_DIR, "app_logic_ota.prev.py"))
                with open(os.path.join(APP_DIR, "ota_prev_version.txt"), "w", encoding="utf-8") as f:
                    f.write(previous_version)
            except Exception as e:
                LOG.warning("OTA: nie udało się zapisać kopii poprzedniej wersji: %s", e)

        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(new_code)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp_path, target)

        for path in _version_files():
            with open(path, "w", encoding="utf-8") as f:
                f.write(remote_ver)

        failed_path = os.path.join(APP_DIR, "ota_failed_version.txt")
        if os.path.exists(failed_path):
            try:
                os.remove(failed_path)
            except Exception:
                pass
        LOG.info("OTA: zapisano v%s w %s", remote_ver, target)
        return True, target
    except Exception as e:
        LOG.exception("OTA: błąd zapisu nowej wersji")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False, f"Nie udało się zapisać aktualizacji: {e}"


def _restore_version_files(version):
    if not version:
        return
    for path in _version_files():
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(version)
        except Exception:
            pass


def activate_downloaded_ota(page, expected_version=""):
    """Uruchamia pobrany kod OTA w bieżącym procesie (bez restartu aplikacji).

    Flet na Androidzie/iOS nie potrafi zrestartować własnego procesu, więc
    podmieniamy cały interfejs: ładujemy plik z folderu danych i wołamy jego
    `main(page)`. Dzięki temu aktualizacja działa od razu po pobraniu.
    """
    target = os.path.join(APP_DIR, "app_logic_ota.py")
    if not os.path.exists(target):
        return False, "Brak pobranego pliku app_logic_ota.py"
    try:
        with open(target, "r", encoding="utf-8") as f:
            source = f.read()
        compile(source, target, "exec")
    except Exception as e:
        return False, f"Pobrany kod jest niepoprawny: {e}"

    original_app_logic = sys.modules.get("app_logic")
    original_app_logic_ota = sys.modules.get("app_logic_ota")
    try:
        # Nowy moduł musi widzieć wersję wbudowaną (do porównań i podpisu w UI).
        os.environ.setdefault("MASTER_ADHD_BUNDLED_VERSION", get_bundled_version())
        spec = importlib.util.spec_from_file_location("app_logic_ota", target)
        if spec is None or spec.loader is None:
            return False, "Nie można załadować pobranego modułu"
        module = importlib.util.module_from_spec(spec)
        sys.modules["app_logic_ota"] = module
        sys.modules["app_logic"] = module
        spec.loader.exec_module(module)
        if not hasattr(module, "main"):
            raise AttributeError("Pobrany kod nie zawiera funkcji main(page)")

        # Stary interfejs (i jego okna dialogowe) znika razem z referencjami.
        page.overlay.clear()
        page.controls.clear()
        module.main(page)
        LOG.info("OTA: aktywna wersja v%s", expected_version or "?")
        return True, expected_version
    except Exception as e:
        LOG.exception("OTA: nie udało się uruchomić pobranego kodu")
        if original_app_logic is not None:
            sys.modules["app_logic"] = original_app_logic
        if original_app_logic_ota is not None:
            sys.modules["app_logic_ota"] = original_app_logic_ota
        else:
            sys.modules.pop("app_logic_ota", None)
        return False, str(e)


# --- BAZA DANYCH I LOGIKA ---
def _table_columns(conn, table):
    """Nazwy kolumn istniejącej tabeli (pusty zbiór = tabeli jeszcze nie ma)."""
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _ensure_column(conn, table, column, ddl):
    """Dodaje brakującą kolumnę. Bezpieczne przy każdym wywołaniu (no-op gdy jest)."""
    cols = _table_columns(conn, table)
    if not cols or column in cols:
        return False
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        LOG.info("Migracja schematu: dodano kolumnę %s.%s", table, column)
        return True
    except sqlite3.OperationalError as e:
        # „duplicate column” to zwykły wyścig i można go zignorować. Każdy inny
        # błąd (np. zablokowany plik) musi trafić do logu — wcześniej był połykany
        # i brak kolumny objawiał się dopiero jako „no such column” w widoku.
        if "duplicate column" in str(e).lower() or column in _table_columns(conn, table):
            return False
        LOG.warning("Migracja schematu: nie udało się dodać %s.%s: %s", table, column, e)
        return False


def ensure_schema(conn):
    """Domyka schemat (migracje) na istniejącym połączeniu. Idempotentne."""
    _ensure_column(conn, "tasks", "subtasks", "TEXT DEFAULT '[]'")
    _ensure_column(conn, "tasks", "category", "TEXT DEFAULT 'Ogólne'")
    _ensure_column(conn, "tasks", "deadline", "TEXT")
    _ensure_column(conn, "tasks", "created_at", "TEXT")
    _ensure_column(conn, "tasks", "updated_at", "TEXT")
    _ensure_column(conn, "routines", "routine_days", "TEXT DEFAULT '[]'")
    _backfill_task_timestamps(conn)


def _backfill_task_timestamps(conn):
    """Wypełnia znaczniki czasu w wierszach sprzed migracji (3.2).

    Stare zadania nie mają `created_at`/`updated_at`. Datę migracji ustawiamy na
    „teraz” — nie znamy prawdziwej, a puste pole wyglądałoby jak zadanie-duch od
    zawsze. Idempotentne: kolejne wywołania nie ruszają już wypełnionych wierszy.
    """
    try:
        now = now_iso()
        conn.execute("UPDATE tasks SET created_at = ? WHERE created_at IS NULL", (now,))
        conn.execute("UPDATE tasks SET updated_at = ? WHERE updated_at IS NULL", (now,))
    except sqlite3.Error as e:
        LOG.warning("Migracja znaczników czasu zadań nie powiodła się: %s", e)


def ensure_schema_upgrade(db_path=None):
    """Migruje schemat bazy po jej podmianie (pull z chmury, snapshot, import).

    Każde miejsce, które wstawia do LOCAL_DB plik innej bazy (Gist/HA/folder,
    snapshot, przywracanie po uszkodzeniu), musi potem to wywołać: starsza kopia
    nie ma nowych kolumn i bez migracji `SELECT ... routine_days` kończy się
    `sqlite3.OperationalError: no such column`. write=False — sama migracja
    schematu nie może przesuwać db_last_change (inaczej pull z chmury staje).
    """
    try:
        with db_connection(db_path) as conn:
            ensure_schema(conn)
        return True
    except Exception as e:
        LOG.warning("Migracja schematu po podmianie bazy nie powiodła się: %s", e)
        return False


def init_db():
    init_config_db()
    ok, msg = ensure_db_health()
    if not ok:
        LOG.error("Zdrowie bazy: %s", msg)
    # write=False: samo tworzenie schematu nie jest zmianą danych i nie może
    # przesuwać db_last_change (inaczej start aplikacji blokowałby pull z chmury).
    with db_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
                     CREATE TABLE IF NOT EXISTS tasks
                     (
                         id
                         INTEGER
                         PRIMARY
                         KEY
                         AUTOINCREMENT,
                         title
                         TEXT
                         NOT
                         NULL,
                         is_daily
                         INTEGER
                         DEFAULT
                         0,
                         is_done
                         INTEGER
                         DEFAULT
                         0,
                         priority
                         TEXT
                         DEFAULT
                         'B',
                         subtasks
                         TEXT
                         DEFAULT
                         '[]',
                         category
                         TEXT
                         DEFAULT
                         'Ogólne',
                         deadline
                         TEXT,
                         created_at
                         TEXT,
                         updated_at
                         TEXT
                     )
                     """)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS routines (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, is_done INTEGER DEFAULT 0, routine_days TEXT DEFAULT '[]')")
        conn.execute("""
                     CREATE TABLE IF NOT EXISTS completed_archive
                     (
                         id INTEGER PRIMARY KEY AUTOINCREMENT,
                         title TEXT NOT NULL,
                         priority TEXT DEFAULT 'B',
                         completed_at TEXT NOT NULL
                     )
                     """)
        # Migracje kolumn dla baz utworzonych przez starsze wersje (także tych
        # pobranych z chmury/snapshotu). Wcześniej każda była w try/except i przy
        # zablokowanym pliku migracja przechodziła niezauważona, a SELECT na
        # nowej kolumnie wywalał widok: „no such column: routine_days”.
        ensure_schema(conn)


def export_database_to_json_dict():
    """Eksportuje całą zawartość bazy (zadania, rutyny, archiwum) do słownika JSON."""
    with db_connection() as conn:
        ensure_schema(conn)
        tasks_rows = conn.execute(
            "SELECT id, title, is_daily, is_done, priority, subtasks, category, deadline, "
            "created_at, updated_at FROM tasks").fetchall()
        tasks = []
        for r in tasks_rows:
            try:
                subs = json.loads(r[5] if r[5] else "[]")
            except Exception:
                subs = []
            tasks.append({
                "id": r[0],
                "title": r[1],
                "is_daily": r[2],
                "is_done": r[3],
                "priority": r[4],
                "subtasks": subs,
                "category": r[6] if len(r) > 6 and r[6] else "Ogólne",
                "deadline": (r[7] or "") if len(r) > 7 else "",
                "created_at": (r[8] or "") if len(r) > 8 else "",
                "updated_at": (r[9] or "") if len(r) > 9 else ""
            })
        routines_rows = conn.execute("SELECT id, title, is_done, routine_days FROM routines").fetchall()
        routines = [{"id": r[0], "title": r[1], "is_done": r[2], "days": _normalize_routine_days(r[3])} for r in routines_rows]
        archive_rows = conn.execute("SELECT id, title, priority, completed_at FROM completed_archive").fetchall()
        archive = [{"id": r[0], "title": r[1], "priority": r[2], "completed_at": r[3]} for r in archive_rows]
        return {
            "version": get_local_version(),
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "tasks": tasks,
            "routines": routines,
            "completed_archive": archive
        }


def export_database_to_json_file():
    """Zapisuje czytelny plik JSON z pełną kopią bazy w folderze danych."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_path = os.path.join(APP_DIR, f"master_adhd_backup_{ts}.json")
    try:
        data = export_database_to_json_dict()
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True, target_path
    except Exception as e:
        return False, str(e)


def latest_json_backup():
    try:
        files = [f for f in os.listdir(APP_DIR) if f.startswith("master_adhd_backup_") and f.endswith(".json")]
        if not files:
            return ""
        files.sort(reverse=True)
        return os.path.join(APP_DIR, files[0])
    except Exception:
        return ""


def import_database_from_json_dict(data, replace_existing=False):
    """Importuje zadania, rutyny i archiwum ze słownika JSON do SQLite."""
    make_local_snapshot("pre_import")
    with db_connection(write=True) as conn:
        # Import do bazy ze starszym schematem nie może się wywalić na nowej kolumnie.
        ensure_schema(conn)
        if replace_existing:
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM routines")
            conn.execute("DELETE FROM completed_archive")

        import_now = now_iso()
        for t in data.get("tasks", []):
            created = (t.get("created_at") or "").strip() or import_now
            updated = (t.get("updated_at") or "").strip() or created
            conn.execute(
                "INSERT INTO tasks (title, is_daily, is_done, priority, subtasks, category, deadline, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (t.get("title", ""), int(t.get("is_daily", 0)), int(t.get("is_done", 0)),
                 t.get("priority", "B"), json.dumps(t.get("subtasks", []), ensure_ascii=False),
                 t.get("category", "Ogólne"), normalize_deadline(t.get("deadline")), created, updated)
            )
        for r in data.get("routines", []):
            conn.execute(
                "INSERT INTO routines (title, is_done, routine_days) VALUES (?, ?, ?)",
                (r.get("title", ""), int(r.get("is_done", 0)),
                 json.dumps(_normalize_routine_days(r.get("days", [])), ensure_ascii=False))
            )
        for a in data.get("completed_archive", []):
            conn.execute(
                "INSERT INTO completed_archive (title, priority, completed_at) VALUES (?, ?, ?)",
                (a.get("title", ""), a.get("priority", "B"), a.get("completed_at", ""))
            )
    push_database_async()
    return True, "Pomyślnie zaimportowano dane z JSON"


def import_database_from_json_file(path, replace_existing=False):
    if not path or not os.path.exists(path):
        return False, "Nie znaleziono pliku JSON"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return False, f"Nie udało się odczytać JSON: {e}"
    return import_database_from_json_dict(data, replace_existing=replace_existing)


def get_all_categories(base=None):
    """Strefy bazowe + kategorie już istniejące w bazie (nic nie ginie przy edycji)."""
    cats = list(base or [])
    if "Ogólne" not in cats:
        cats.insert(0, "Ogólne")
    try:
        with db_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM tasks WHERE category IS NOT NULL AND TRIM(category) != ''"
            ).fetchall()
        for (cat,) in rows:
            if cat and cat not in cats:
                cats.append(cat)
    except Exception:
        pass
    return cats


def play_sound_feedback(sound_type="task_done"):
    """Odtwarza dźwięk w osobnym wątku (systemowy tylko na Windows)."""
    sound_enabled = get_config("sound_enabled", "1") == "1"
    if not sound_enabled:
        return

    def _play():
        try:
            if sys.platform == "win32":
                import winsound
                if sound_type == "timer_done":
                    winsound.Beep(523, 120)
                    winsound.Beep(659, 120)
                    winsound.Beep(784, 250)
                else:
                    winsound.Beep(659, 80)
                    winsound.Beep(880, 120)
            else:
                # Android/iOS: brak modułu dźwiękowego w Flet — nie udajemy, że gra.
                LOG.debug("Dźwięk %s pominięty na platformie %s", sound_type, sys.platform)
        except Exception as e:
            LOG.debug("Dźwięk %s pominięty (błąd: %s)", sound_type, e)

    threading.Thread(target=_play, daemon=True).start()


# --- WIBRACJA (tylko platformy mobilne, bez powiadomień systemowych) ---
_HAPTIC_METHODS = {
    "timer_done": "vibrate",
    "task_done": "light_impact",
    "undo": "selection_click",
    "error": "heavy_impact",
}


def init_haptic_service(page):
    """Dodaje `ft.HapticFeedback` do `page.services` na platformach mobilnych.

    Flet 1.0.x w CI może nie mieć tej klasy — wtedy zwracamy None i aplikacja
    działa jak dotąd. Brak wsparcia urządzenia nigdy nie może wywalić UI.
    """
    cls = getattr(ft, "HapticFeedback", None)
    if cls is None or page is None:
        return None
    services = getattr(page, "services", None)
    if not isinstance(services, list):
        return None
    platform = str(getattr(page, "platform", "") or "").lower()
    # Pusty platform = stary Flet/DummyPage w testach; wtedy próbujemy mimo wszystko.
    if platform and not any(k in platform for k in ("android", "ios", "mobile")):
        return None
    try:
        service = cls()
        services.append(service)
        return service
    except Exception as e:
        LOG.debug("HapticFeedback niedostępny: %s", e)
        return None


def trigger_haptic_feedback(page, service, kind="task_done"):
    """Krótka wibracja przez `page.run_task`. Cicha, gdy brak wsparcia."""
    if page is None or service is None:
        return False
    if get_config("sound_enabled", "1") != "1":
        return False
    method_name = _HAPTIC_METHODS.get(kind, "light_impact")
    method = getattr(service, method_name, None)
    run_task = getattr(page, "run_task", None)
    if method is None or not callable(run_task):
        return False

    async def _run():
        try:
            result = method()
            if hasattr(result, "__await__"):
                await result
        except Exception as e:
            LOG.debug("Wibracja %s pominięta: %s", kind, e)

    try:
        run_task(_run)
        return True
    except Exception as e:
        LOG.debug("Nie udało się uruchomić wibracji %s: %s", kind, e)
        return False


def format_mmss(seconds):
    """Formatuje sekundy do „mm:ss” (bez wartości ujemnych)."""
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        total = 0
    return f"{total // 60:02d}:{total % 60:02d}"


def _select_tasks(conn, is_daily_view):
    columns = "id, title, is_daily, is_done, priority, subtasks, category, deadline"
    if is_daily_view:
        rows = conn.execute(
            f"SELECT {columns} FROM tasks WHERE is_daily = 1 ORDER BY is_done ASC, priority ASC, id DESC").fetchall()
    else:
        rows = conn.execute(
            f"SELECT {columns} FROM tasks ORDER BY is_done ASC, priority ASC, id DESC").fetchall()
    result = []
    for r in rows:
        try:
            subs = json.loads(r[5] if r[5] else "[]")
        except Exception:
            subs = []
        # deadline jest ostatnim elementem, żeby dotychczasowe indeksy 0..6
        # (używane przez UI i testy) pozostały bez zmian.
        result.append((r[0], r[1], r[2], r[3], r[4], subs,
                       r[6] if len(r) > 6 and r[6] else "Ogólne",
                       (r[7] or None) if len(r) > 7 else None))
    return result


def get_tasks(is_daily_view=True):
    try:
        with db_connection() as conn:
            return _select_tasks(conn, is_daily_view)
    except sqlite3.OperationalError as e:
        # Baza pobrana z chmury/snapshotu może nie mieć jeszcze kolumny deadline.
        LOG.warning("get_tasks: %s — domykam schemat bazy", e)
        ensure_schema_upgrade()
        with db_connection() as conn:
            return _select_tasks(conn, is_daily_view)


# --- TERMINY ZADAŃ (deadline) ---
# Wartość-sentinel: pozwala odróżnić „nie zmieniaj terminu” od „wyczyść termin”.
DEADLINE_UNSET = object()


def parse_deadline_input(value, today=None):
    """Zamienia datę na ISO `YYYY-MM-DD`.

    Przyjmuje `date`/`datetime`, ISO, `DD.MM.RRRR`, `DD.MM.RR`, `DD.MM`
    (bez roku = najbliższe wystąpienie) oraz warianty z `/` i `-`.
    Puste `None`/`""` zwraca jako `None`. Niepoprawną datę zgłasza jako ValueError.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None

    # 1) ISO (np. 2026-11-24) — najbezpieczniejszy format.
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass

    # 2) DD.MM(.RRRR|.RR) oraz warianty z / i -.
    normalized = text.replace("/", ".").replace("-", ".")
    parts = [p for p in normalized.split(".") if p.strip()]
    if len(parts) not in (2, 3):
        raise ValueError(f"Niepoprawny termin: {text}")
    try:
        day, month = int(parts[0]), int(parts[1])
        if len(parts) == 3:
            year = int(parts[2])
            if year < 100:
                year += 2000
        else:
            base = today or date.today()
            year = base.year
        parsed = date(year, month, day)
        if len(parts) == 2 and parsed < (today or date.today()):
            # „24.11” wpisane w grudniu oznacza kolejny rok.
            parsed = date(year + 1, month, day)
        return parsed.isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"Niepoprawny termin: {text}")


def normalize_deadline(value):
    """Łagodna wersja parsera — dla importu JSON i starych danych."""
    try:
        return parse_deadline_input(value)
    except ValueError:
        return None


def format_deadline_pl(deadline, short=False):
    """Formatuje termin do „24.11.2026” albo krótkiego „24.11”."""
    if not deadline:
        return ""
    try:
        d = date.fromisoformat(str(deadline))
    except ValueError:
        return ""
    return f"{d.day:02d}.{d.month:02d}" if short else f"{d.day:02d}.{d.month:02d}.{d.year}"


def days_to_deadline(deadline, today=None):
    """Liczba dni do terminu (ujemna = po terminie); None gdy brak/niepoprawny."""
    if not deadline:
        return None
    try:
        d = date.fromisoformat(str(deadline))
    except ValueError:
        return None
    return (d - (today or date.today())).days


def deadline_urgency(deadline, today=None):
    """none / overdue / today / soon (≤3 dni) / later — do kolorów i tekstów."""
    days = days_to_deadline(deadline, today)
    if days is None:
        return "none"
    if days < 0:
        return "overdue"
    if days == 0:
        return "today"
    if days <= 3:
        return "soon"
    return "later"


def deadline_chip_text(deadline, today=None):
    """Krótki, spokojny opis terminu na karcie zadania.

    Dalekie terminy pokazują samą datę (bez „za 26 dni”), żeby nie budować
    napięcia; względne „jutro / za N dni” dopiero blisko terminu.
    """
    days = days_to_deadline(deadline, today)
    short = format_deadline_pl(deadline, short=True)
    if days is None:
        return ""
    if days < 0:
        return f"po terminie · {short}"
    if days == 0:
        return "termin dziś"
    if days == 1:
        return f"termin jutro · {short}"
    if days <= 3:
        return f"termin za {days} dni · {short}"
    return f"termin {short}"


def sort_tasks_by_deadline(tasks):
    """Zaległe → najbliższe → bez terminu; wykonane zawsze na końcu.

    Sort jest stabilny, więc w obrębie tych samych terminów zachowuje
    dotychczasową kolejność (priorytet + id z get_tasks).
    """
    def key(task):
        done = 1 if task[3] else 0
        deadline = task[7] if len(task) > 7 else None
        if not deadline:
            return (done, 1, "9999-12-31")
        return (done, 0, deadline)
    return sorted(tasks, key=key)


def get_deadline_tasks(within_days=7, include_overdue=True, limit=None, today=None):
    """Aktywne zadania z terminu w oknie `within_days` (z całej Master listy)."""
    base = today or date.today()
    result = []
    for task in get_tasks(is_daily_view=False):
        if task[3]:  # wykonane nie przypominają
            continue
        deadline = task[7] if len(task) > 7 else None
        days = days_to_deadline(deadline, base)
        if days is None:
            continue
        if days > within_days:
            continue
        if days < 0 and not include_overdue:
            continue
        result.append(task)
    result = sort_tasks_by_deadline(result)
    return result[:limit] if limit else result


def filter_tasks_by_deadline(tasks, mode="ALL", today=None):
    """Filtr Master listy: ALL / WEEK (najbliższe 7 dni) / OVERDUE."""
    if mode not in ("WEEK", "OVERDUE"):
        return list(tasks)
    base = today or date.today()
    result = []
    for task in tasks:
        deadline = task[7] if len(task) > 7 else None
        days = days_to_deadline(deadline, base)
        if days is None:
            continue
        if mode == "WEEK" and 0 <= days <= 7:
            result.append(task)
        elif mode == "OVERDUE" and days < 0:
            result.append(task)
    return result


# --- SPRINT 3.2: ZNACZNIKI CZASU I „ZADANIA DUCHY” ---
def get_task_timestamps(task_id):
    """Zwraca `(created_at, updated_at)` zadania albo None, gdy go nie ma.

    Świadomie osobny getter: `get_tasks()` musi zachować dotychczasową kolejność
    kolumn (indeksy 0..7 są kontraktem UI i testów).
    """
    try:
        with db_connection() as conn:
            row = conn.execute(
                "SELECT created_at, updated_at FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return (row[0], row[1]) if row else None
    except sqlite3.OperationalError:
        ensure_schema_upgrade()
        with db_connection() as conn:
            row = conn.execute(
                "SELECT created_at, updated_at FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return (row[0], row[1]) if row else None


def is_stale_task(updated_at, deadline=None, is_done=0, days=STALE_TASK_DAYS, now=None):
    """Czy zadanie jest „duchem”: dawno nietknięte i bez przyszłego terminu.

    Świeże zadania i te z terminem w przyszłości nigdy nie są duchami — inaczej
    filtr straszyłby rzeczami, które dopiero co powstały albo mają datę.
    """
    if is_done:
        return False
    reference = now or datetime.now()
    if deadline:
        try:
            if date.fromisoformat(str(deadline)) > reference.date():
                return False
        except ValueError:
            pass
    if not updated_at:
        return False
    try:
        updated = datetime.fromisoformat(str(updated_at))
    except ValueError:
        return False
    return (reference - updated) >= timedelta(days=days)


def get_stale_task_ids(days=STALE_TASK_DAYS, now=None):
    """Id zadań, które leżą bez zmian co najmniej `days` dni (filtr Master)."""
    reference = now or datetime.now()
    try:
        with db_connection() as conn:
            rows = conn.execute("SELECT id, is_done, deadline, updated_at FROM tasks").fetchall()
    except sqlite3.OperationalError as e:
        LOG.warning("get_stale_task_ids: %s — domykam schemat bazy", e)
        ensure_schema_upgrade()
        with db_connection() as conn:
            rows = conn.execute("SELECT id, is_done, deadline, updated_at FROM tasks").fetchall()
    return {
        tid for tid, is_done, deadline, updated_at in rows
        if is_stale_task(updated_at, deadline=deadline, is_done=is_done, days=days, now=reference)
    }


def deadline_card_hidden_today(today=None):
    return get_config("deadline_card_hidden_date", "") == (today or date.today()).isoformat()


def hide_deadline_card_today(today=None):
    set_config("deadline_card_hidden_date", (today or date.today()).isoformat())


def deadline_reminder_days():
    try:
        return max(1, int(get_config("deadline_reminder_days", "7") or 7))
    except (TypeError, ValueError):
        return 7


def should_show_deadline_card(today=None, within_days=None):
    """Czy pokazać spokojną kartę „Nadchodzące terminy” na Daily."""
    if get_config("deadline_reminders_enabled", "1") != "1":
        return False
    if deadline_card_hidden_today(today):
        return False
    days = deadline_reminder_days() if within_days is None else within_days
    return bool(get_deadline_tasks(within_days=days, include_overdue=True, limit=1, today=today))


def _select_routines(conn):
    rows = conn.execute("SELECT id, title, is_done, routine_days FROM routines ORDER BY id").fetchall()
    return [(r[0], r[1], r[2], _normalize_routine_days(r[3])) for r in rows]


def get_routines():
    try:
        with db_connection() as conn:
            return _select_routines(conn)
    except sqlite3.OperationalError as e:
        # Baza pobrana z chmury/snapshotu może mieć schemat sprzed kolumny
        # routine_days („no such column”). Domykamy schemat i próbujemy raz
        # jeszcze, a gdy to nie pomoże — czytamy rutyny bez dni tygodnia, żeby
        # widok nigdy nie padł na starszym pliku bazy.
        LOG.warning("get_routines: %s — domykam schemat bazy", e)
        ensure_schema_upgrade()
        try:
            with db_connection() as conn:
                return _select_routines(conn)
        except sqlite3.OperationalError as e2:
            LOG.error("get_routines: %s — czytam rutyny bez dni tygodnia", e2)
            with db_connection() as conn:
                rows = conn.execute("SELECT id, title, is_done FROM routines ORDER BY id").fetchall()
            return [(r[0], r[1], r[2], []) for r in rows]


def add_routine_db(title, days=None):
    """Dodaje rutynę. `days` = dni tygodnia (0=poniedziałek ... 6=niedziela); pusta lista = codziennie."""
    title = (title or "").strip()
    if not title:
        return
    days = _normalize_routine_days(days)
    with db_connection(write=True) as conn:
        ensure_schema(conn)
        conn.execute("INSERT INTO routines (title, is_done, routine_days) VALUES (?, 0, ?)",
                     (title, json.dumps(days)))
    push_database_async()


def update_routine_db(routine_id, title, days=None):
    """Edytuje tytuł i dni tygodnia istniejącej rutyny."""
    title = (title or "").strip()
    if not title:
        return
    days = _normalize_routine_days(days)
    with db_connection(write=True) as conn:
        ensure_schema(conn)
        conn.execute("UPDATE routines SET title = ?, routine_days = ? WHERE id = ?",
                     (title, json.dumps(days), routine_id))
    push_database_async()


def toggle_routine_db(routine_id, is_done):
    with db_connection(write=True) as conn:
        conn.execute("UPDATE routines SET is_done = ? WHERE id = ?", (int(is_done), routine_id))
    push_database_async()


def delete_routine_db(routine_id):
    with db_connection(write=True) as conn:
        conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
    push_database_async()


def update_subtasks_db(task_id, subtasks_list):
    with db_connection(write=True) as conn:
        conn.execute("UPDATE tasks SET subtasks = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(subtasks_list, ensure_ascii=False), now_iso(), task_id))
    push_database_async()


def get_task_priority(task_id):
    with db_connection() as conn:
        res = conn.execute("SELECT priority FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return res[0] if res else "B"


def count_daily_priority_a():
    with db_connection() as conn:
        res = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE is_daily = 1 AND priority = 'A' AND is_done = 0").fetchone()
        return res[0] if res else 0


def add_task_db(title, is_daily, priority="B", category="Ogólne", deadline=None):
    """Dodaje zadanie. `deadline` to opcjonalna data ISO (YYYY-MM-DD)."""
    now = now_iso()
    with db_connection(write=True) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO tasks (title, is_daily, priority, subtasks, category, deadline, "
            "created_at, updated_at) VALUES (?, ?, ?, '[]', ?, ?, ?, ?)",
            (title.strip(), int(is_daily), priority, category, normalize_deadline(deadline), now, now))
    push_database_async()


def get_task_deadline(task_id):
    """Zwraca termin zadania jako ISO (albo None) — do wypełnienia dialogu edycji."""
    try:
        with db_connection() as conn:
            row = conn.execute("SELECT deadline FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return (row[0] or None) if row else None
    except sqlite3.OperationalError:
        ensure_schema_upgrade()
        with db_connection() as conn:
            row = conn.execute("SELECT deadline FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return (row[0] or None) if row else None


def set_task_deadline_db(task_id, deadline):
    """Ustawia lub czyści termin zadania (`None`/`""` = usuń termin)."""
    value = normalize_deadline(deadline)
    with db_connection(write=True) as conn:
        ensure_schema(conn)
        conn.execute("UPDATE tasks SET deadline = ?, updated_at = ? WHERE id = ?",
                     (value, now_iso(), task_id))
    push_database_async()


def update_task_details_db(task_id, title, category=None, deadline=DEADLINE_UNSET):
    """Zapisuje tytuł, kategorię i termin jednym UPDATE-em (jedno pushowanie).

    `deadline=DEADLINE_UNSET` zostawia termin bez zmian; `None` go czyści.
    """
    title = (title or "").strip()
    new_deadline = None if deadline is DEADLINE_UNSET else normalize_deadline(deadline)
    now = now_iso()
    with db_connection(write=True) as conn:
        ensure_schema(conn)
        if category:
            conn.execute("UPDATE tasks SET title = ?, category = ?, updated_at = ? WHERE id = ?",
                         (title, category, now, task_id))
        else:
            conn.execute("UPDATE tasks SET title = ?, updated_at = ? WHERE id = ?",
                         (title, now, task_id))
        if deadline is not DEADLINE_UNSET:
            conn.execute("UPDATE tasks SET deadline = ?, updated_at = ? WHERE id = ?",
                         (new_deadline, now, task_id))
    push_database_async()


def update_task_category_db(task_id, category):
    with db_connection(write=True) as conn:
        conn.execute("UPDATE tasks SET category = ?, updated_at = ? WHERE id = ?",
                     (category, now_iso(), task_id))
    push_database_async()


def toggle_done_db(task_id, is_done):
    with db_connection(write=True) as conn:
        conn.execute("UPDATE tasks SET is_done = ?, updated_at = ? WHERE id = ?",
                     (int(is_done), now_iso(), task_id))
    push_database_async()


def set_daily_db(task_id, is_daily):
    with db_connection(write=True) as conn:
        conn.execute("UPDATE tasks SET is_daily = ?, updated_at = ? WHERE id = ?",
                     (int(is_daily), now_iso(), task_id))
    push_database_async()


def update_priority_db(task_id, new_priority):
    with db_connection(write=True) as conn:
        conn.execute("UPDATE tasks SET priority = ?, updated_at = ? WHERE id = ?",
                     (new_priority, now_iso(), task_id))
    push_database_async()


def delete_task_db(task_id):
    with db_connection(write=True) as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    push_database_async()


TASK_ROW_COLUMNS = "id, title, is_daily, is_done, priority, subtasks, category, deadline"


def get_task_row(task_id):
    """Pełny surowy wiersz zadania (subtasks jako JSON-tekst) — do cofnięcia usunięcia.

    Zwraca krotkę zgodną z `TASK_ROW_COLUMNS` albo None, gdy zadania nie ma.
    """
    with db_connection() as conn:
        row = conn.execute(
            f"SELECT {TASK_ROW_COLUMNS} FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return row


def delete_task_with_undo_db(task_id):
    """Usuwa zadanie i zwraca jego pełny wiersz, żeby można je było przywrócić."""
    row = get_task_row(task_id)
    if row is None:
        return None
    with db_connection(write=True) as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    push_database_async()
    return row


def restore_task_row_db(row):
    """Przywraca usunięte zadanie z tym samym `id`.

    `INSERT OR IGNORE` sprawia, że powtórne cofnięcie (np. drugie kliknięcie)
    nie duplikuje ani nie nadpisuje istniejącego rekordu. Zwraca True, gdy
    wiersz faktycznie wrócił do bazy.
    """
    if not row or len(row) < 8:
        return False
    now = now_iso()
    with db_connection(write=True) as conn:
        ensure_schema(conn)
        cursor = conn.execute(
            f"INSERT OR IGNORE INTO tasks ({TASK_ROW_COLUMNS}, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(row[:8]) + (now, now),
        )
        inserted = cursor.rowcount > 0
    push_database_async()
    return inserted


def edit_task_title_db(task_id, new_title, new_category=None):
    with db_connection(write=True) as conn:
        if new_category:
            conn.execute("UPDATE tasks SET title = ?, category = ?, updated_at = ? WHERE id = ?",
                         (new_title.strip(), new_category, now_iso(), task_id))
        else:
            conn.execute("UPDATE tasks SET title = ?, updated_at = ? WHERE id = ?",
                         (new_title.strip(), now_iso(), task_id))
    push_database_async()


def delete_subtask_db(task_id, subtask_idx):
    """Usuwa podkrok i zwraca go (do „Cofnij”). None, gdy nic nie usunięto."""
    removed = None
    with db_connection(write=True) as conn:
        row = conn.execute("SELECT subtasks FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row and row[0]:
            try:
                subtasks = json.loads(row[0])
            except Exception as e:
                LOG.warning("Nie udało się odczytać podkroków zadania %s: %s", task_id, e)
                subtasks = []
            if 0 <= subtask_idx < len(subtasks):
                removed = subtasks.pop(subtask_idx)
                conn.execute("UPDATE tasks SET subtasks = ?, updated_at = ? WHERE id = ?",
                             (json.dumps(subtasks, ensure_ascii=False), now_iso(), task_id))
    push_database_async()
    return removed


def restore_subtask_db(task_id, subtask_idx, subtask):
    """Wstawia usunięty podkrok z powrotem na tę samą pozycję (idempotentnie).

    Jeśli na danej pozycji jest już identyczny podkrok (np. drugie kliknięcie
    „Cofnij”), nic nie zmieniamy — bez duplikatów.
    """
    if not isinstance(subtask, dict) or not subtask.get("title"):
        return False
    with db_connection(write=True) as conn:
        row = conn.execute("SELECT subtasks FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return False
        try:
            subtasks = json.loads(row[0] or "[]")
        except Exception:
            subtasks = []
        idx = max(0, min(int(subtask_idx), len(subtasks)))
        if idx < len(subtasks):
            existing = subtasks[idx]
            if (isinstance(existing, dict)
                    and existing.get("title") == subtask.get("title")
                    and int(existing.get("is_done", 0)) == int(subtask.get("is_done", 0))):
                return False
        subtasks.insert(idx, subtask)
        conn.execute("UPDATE tasks SET subtasks = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(subtasks, ensure_ascii=False), now_iso(), task_id))
    push_database_async()
    return True


def edit_subtask_title_db(task_id, subtask_idx, new_title):
    with db_connection(write=True) as conn:
        row = conn.execute("SELECT subtasks FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row and row[0]:
            subtasks = json.loads(row[0])
            if 0 <= subtask_idx < len(subtasks):
                subtasks[subtask_idx]["title"] = new_title.strip()
                conn.execute("UPDATE tasks SET subtasks = ?, updated_at = ? WHERE id = ?",
                             (json.dumps(subtasks, ensure_ascii=False), now_iso(), task_id))
    push_database_async()


def move_subtask_db(task_id, subtask_idx, delta):
    """Przesuwa podkrok o `delta` pozycji (-1 w górę, +1 w dół).

    Zwraca nowy indeks po przesunięciu albo None, gdy ruch jest niemożliwy
    (indeks poza listą, próba wyjścia poza zakres).
    """
    with db_connection(write=True) as conn:
        row = conn.execute("SELECT subtasks FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row or not row[0]:
            return None
        subtasks = json.loads(row[0])
        target_idx = subtask_idx + delta
        if not (0 <= subtask_idx < len(subtasks)) or not (0 <= target_idx < len(subtasks)):
            return None
        subtasks[subtask_idx], subtasks[target_idx] = subtasks[target_idx], subtasks[subtask_idx]
        conn.execute("UPDATE tasks SET subtasks = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(subtasks, ensure_ascii=False), now_iso(), task_id))
    push_database_async()
    return target_idx


def get_completed_archive(limit=100):
    with db_connection() as conn:
        return conn.execute(
            "SELECT id, title, priority, completed_at FROM completed_archive ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()


def prune_archive(keep=ARCHIVE_LIMIT):
    with db_connection(write=True) as conn:
        conn.execute(
            "DELETE FROM completed_archive WHERE id NOT IN "
            "(SELECT id FROM completed_archive ORDER BY id DESC LIMIT ?)",
            (keep,)
        )


def clear_completed_archive_db():
    make_local_snapshot("pre_clear_archive")
    with db_connection(write=True) as conn:
        conn.execute("DELETE FROM completed_archive")
    push_database_async()


def end_of_day_db(keep_archive=ARCHIVE_LIMIT):
    make_local_snapshot("pre_end_of_day")
    with db_connection(write=True) as conn:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        done_tasks = conn.execute("SELECT title, priority FROM tasks WHERE is_done = 1").fetchall()
        for dt in done_tasks:
            conn.execute(
                "INSERT INTO completed_archive (title, priority, completed_at) VALUES (?, ?, ?)",
                (dt[0], dt[1], now_str)
            )
        conn.execute("DELETE FROM tasks WHERE is_done = 1")
        conn.execute("UPDATE tasks SET is_daily = 0, updated_at = ? WHERE is_daily = 1 AND is_done = 0",
                     (now_iso(),))
        conn.execute("UPDATE routines SET is_done = 0")
        conn.execute(
            "DELETE FROM completed_archive WHERE id NOT IN "
            "(SELECT id FROM completed_archive ORDER BY id DESC LIMIT ?)",
            (keep_archive,)
        )
    set_config("last_reset_date", datetime.now().strftime("%Y-%m-%d"))
    push_database_async()


# --- SPRINT 2.1: ŚWIADOMY PRZEŁÓW DNIA (bez gubienia zadań) ---
def get_day_rollover_mode():
    """Tryb przełomu dnia: `ask` (pytaj), `auto_keep`, `auto_master`."""
    mode = (get_config("day_rollover_mode", DAY_ROLLOVER_ASK) or DAY_ROLLOVER_ASK).strip()
    return mode if mode in DAY_ROLLOVER_MODES else DAY_ROLLOVER_ASK


def get_pending_day_rollover(today=None):
    """Zwraca stan „nowego dnia” do rozstrzygnięcia albo None.

    None oznacza, że dzień jest już zamknięty (`last_reset_date` == dziś) albo
    aplikacja startuje pierwszy raz (brak znacznika). Funkcja tylko czyta bazę —
    niczego nie zmienia, więc „Później”/anulowanie zostawia wszystko jak jest.
    """
    today_str = (today or date.today()).isoformat()
    try:
        last_reset = get_config("last_reset_date", "")
    except Exception:
        last_reset = ""
    if not last_reset or last_reset == today_str:
        return None
    all_tasks = get_tasks(is_daily_view=False)
    done_tasks = [t for t in all_tasks if t[3]]
    # „Niezrobione z Daily” to dokładnie te, które stary `end_of_day_db()` cicho
    # zdejmował z listy (is_daily=1, is_done=0). Teraz to użytkownik decyduje.
    undone_daily = [t for t in all_tasks if t[2] and not t[3]]
    return {
        "last_reset": last_reset,
        "today": today_str,
        "done_count": len(done_tasks),
        "undone_daily": undone_daily,
    }


def apply_day_rollover(decisions=None, mode=None, today=None):
    """Wykonuje świadomy przełom dnia i zwraca podsumowanie (albo None).

    `decisions` to mapa {task_id: "keep"|"master"|"remove"}. Zadania bez decyzji
    zostają na Daily — żadne zadanie nie ginie po cichu. `mode`
    ("auto_keep"/"auto_master") pozwala pominąć decyzje użytkownika.
    Zrobione zadania trafiają do archiwum dokładnie raz: po archiwizacji są
    usuwane, a `last_reset_date` ustawiany na dziś, więc kolejne wywołanie nie ma
    już nic do zrobienia (żadnych duplikatów w Dzienniku Sukcesów).
    """
    today_str = (today or date.today()).isoformat()
    if not get_pending_day_rollover(today):
        return None
    decisions = decisions or {}
    mode = mode or get_day_rollover_mode()
    if mode not in DAY_ROLLOVER_MODES:
        mode = DAY_ROLLOVER_ASK

    make_local_snapshot("pre_day_rollover")
    with db_connection(write=True) as conn:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        done_rows = conn.execute("SELECT title, priority FROM tasks WHERE is_done = 1").fetchall()
        for row in done_rows:
            conn.execute(
                "INSERT INTO completed_archive (title, priority, completed_at) VALUES (?, ?, ?)",
                (row[0], row[1], now_str)
            )
        archived = len(done_rows)
        conn.execute("DELETE FROM tasks WHERE is_done = 1")
        undone_rows = conn.execute(
            "SELECT id FROM tasks WHERE is_daily = 1 AND is_done = 0").fetchall()
        kept = 0
        to_master = 0
        for (task_id,) in undone_rows:
            choice = decisions.get(task_id, "keep")
            if mode == DAY_ROLLOVER_AUTO_MASTER:
                choice = "master"
            if choice in ("master", "remove"):
                conn.execute("UPDATE tasks SET is_daily = 0, updated_at = ? WHERE id = ?",
                             (now_iso(), task_id))
                to_master += 1
            else:
                kept += 1
        conn.execute("UPDATE routines SET is_done = 0")
        conn.execute(
            "DELETE FROM completed_archive WHERE id NOT IN "
            "(SELECT id FROM completed_archive ORDER BY id DESC LIMIT ?)",
            (ARCHIVE_LIMIT,)
        )
    set_config("last_reset_date", today_str)
    push_database_async()
    return {"archived": archived, "kept": kept, "to_master": to_master, "today": today_str}


# --- SPRINT 2.3: INBOX I 2-MINUTOWY TRIAGE ---
def get_inbox_tasks():
    """Zadania czekające w Inbox (szybkie zrzuty myśli; bez zmian w schemacie)."""
    try:
        return [t for t in get_tasks(is_daily_view=False) if (t[6] or "") == INBOX_CATEGORY]
    except Exception:
        LOG.exception("Nie udało się odczytać Inbox")
        return []


def triage_inbox_task_db(task_id, category="Ogólne", priority="B", is_daily=False, deadline=None):
    """Przenosi zadanie z Inbox do wybranej kategorii/priorytetu (2-minutowy triage)."""
    category = (category or "").strip() or "Ogólne"
    if category == INBOX_CATEGORY:
        category = "Ogólne"
    priority = priority if priority in ("A", "B", "C") else "B"
    with db_connection(write=True) as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE tasks SET category = ?, priority = ?, is_daily = ?, deadline = ?, updated_at = ? "
            "WHERE id = ?",
            (category, priority, int(bool(is_daily)), normalize_deadline(deadline), now_iso(), task_id)
        )
    push_database_async()


def calculate_progress_logic(tasks_list, routines_list, active_subtask_idx_map=None):
    if active_subtask_idx_map is None:
        active_subtask_idx_map = {}
    total_points = len(tasks_list) + len(routines_list)
    if total_points == 0:
        return 0.0
    done_points = 0

    for t in tasks_list:
        task_id, is_done, subtasks = t[0], t[3], t[5]
        if subtasks:
            first_undone = next((i for i, st in enumerate(subtasks) if st["is_done"] == 0), len(subtasks) - 1)
            idx = active_subtask_idx_map.get(task_id, first_undone)
            if first_undone < idx:
                idx = first_undone
            if idx < len(subtasks) and subtasks[idx]["is_done"] == 1:
                done_points += 1
        else:
            if is_done == 1:
                done_points += 1

    for r in routines_list:
        if r[2] == 1:
            done_points += 1

    return done_points / total_points


# --- PALETA KOLORYSTYCZNA (Dark Slate UI) ---
COLOR_BG_MAIN = "#0d1117"
COLOR_BG_CARD = "#161b22"
COLOR_BG_SURFACE = "#21262d"
COLOR_BG_BORDER = "#30363d"
COLOR_PRIO_A = "#ef4444"
COLOR_PRIO_B = "#f59e0b"
COLOR_PRIO_C = "#3b82f6"
COLOR_SUCCESS = "#10b981"
COLOR_TEXT_PRIMARY = "#f0f6fc"
COLOR_TEXT_SECONDARY = "#8b949e"
# Kolory słońca dla daily
COLOR_SUN_ACTIVE = "#facc15"   # żółte (aktywne na daily)
COLOR_SUN_INACTIVE = "#6b7280" # szare (nieaktywne)

# Minimalny obszar klikalny palca (mobile). Na PC zachowujemy gęstość.
TOUCH_TARGET_MIN = 44


def _hex_to_rgb(color):
    """Zamienia „#rrggbb”/„#rgb” na krotkę 0..1. Rzuca ValueError dla złego wejścia."""
    value = str(color or "").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise ValueError(f"Niepoprawny kolor: {color!r}")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def relative_luminance(color):
    """Względna luminancja wg WCAG 2.1 (0 = czarny, 1 = biały)."""
    def _channel(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = _hex_to_rgb(color)
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(fg, bg):
    """Kontrast WCAG dwóch kolorów (1..21). 4.5 = minimum dla zwykłego tekstu."""
    l1 = relative_luminance(fg)
    l2 = relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def readable_text_color(bg, preferred=COLOR_TEXT_SECONDARY, fallback=COLOR_TEXT_PRIMARY, minimum=4.5):
    """Zwraca `preferred`, jeśli ma na tle `bg` kontrast co najmniej `minimum` (WCAG).

    Pozwala trzymać metadane kart czytelne (≥ 4.5:1) i łagodnie degradować się,
    gdy kolor tła się zmieni: wtedy wracamy do jaśniejszego `fallback`. Złe
    wartości kolorów (np. rgba) są ignorowane — nigdy nie wywalamy UI.
    """
    try:
        if contrast_ratio(preferred, bg) >= minimum:
            return preferred
    except (ValueError, TypeError):
        pass
    return fallback


# --- DNI TYGODNIA DLA RUTYN (kotwica zgodna z Python weekday(): 0=poniedziałek ... 6=niedziela) ---
WEEKDAY_LABELS = ["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"]
WEEKDAY_FULL = ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota", "Niedziela"]


def _normalize_routine_days(days):
    """Normalizuje dni tygodnia rutyny do posortowanej listy intów 0..6.

    Pusta lista oznacza „codziennie”. Przyjmuje listę, inta albo JSON-string
    (starsze kopie / surowe wartości z bazy), więc nie psuje się przy obcych danych.
    """
    if days is None:
        return []
    if isinstance(days, str):
        try:
            days = json.loads(days) if days.strip() else []
        except Exception:
            return []
    try:
        out = []
        for d in days:
            i = int(d)
            if 0 <= i <= 6 and i not in out:
                out.append(i)
        out.sort()
        return out
    except Exception:
        return []


def routine_days_label(days):
    """Krótka etykieta dni, np. 'Wt, Śr' ('' dla rutyny codziennej)."""
    days = _normalize_routine_days(days)
    return ", ".join(WEEKDAY_LABELS[d] for d in days)


def get_daily_routines():
    """Rutyny widoczne dziś: bez przypisanych dni (codziennie) albo z dzisiejszym dniem tygodnia."""
    today = datetime.now().weekday()
    return [r for r in get_routines() if not r[3] or today in r[3]]


def build_routine_day_chips(selected, on_toggle):
    """Kafelki wyboru dnia tygodnia (0..6) dla formularzy rutyn.

    `selected` — zbiór wybranych dni; `on_toggle(day)` — callback po kliknięciu.
    Żaden zaznaczony dzień = rutyna codzienna.
    """
    chips = []
    for d, name in enumerate(WEEKDAY_LABELS):
        is_sel = d in selected
        chips.append(ft.Container(
            content=ft.Text(name, size=12,
                            weight=ft.FontWeight.BOLD if is_sel else ft.FontWeight.W_500,
                            color=COLOR_TEXT_PRIMARY if is_sel else COLOR_TEXT_SECONDARY),
            bgcolor=COLOR_PRIO_C if is_sel else COLOR_BG_SURFACE,
            border=ft.Border.all(1, COLOR_PRIO_C if is_sel else COLOR_BG_BORDER),
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=9, vertical=5),
            tooltip=WEEKDAY_FULL[d],
            on_click=lambda e, dd=d: on_toggle(dd)
        ))
    return chips

# --- INTERFEJS (Flet) ---
def main(page: ft.Page):
    current_ver = get_local_version()
    page.title = f"ADHD Master & Daily (v{current_ver})"
    page.window.width = 950
    page.window.height = 760
    page.padding = 0
    page.bgcolor = COLOR_BG_MAIN
    page.theme_mode = ft.ThemeMode.DARK
    
    # Obsługa zamknięcia aplikacji przez klawisz ESC lub wyjście z trybu pełnoekranowego.
    # Nie wołamy sys.exit(0) — na Androidzie/iOS ubijanie procesu z handlera kończyło
    # się crashem. Wystarczy dopiąć zaległy zapis i pozwolić Fletowi zamknąć okno.
    def handle_closing(e=None):
        try:
            reset_sync_on_exit()
        except Exception:
            LOG.exception("Błąd przy zamykaniu aplikacji")

    page.on_closing = handle_closing
    
    init_db()

    # Wibracja na telefonie (bez powiadomień systemowych). Brak wsparcia = None.
    haptic_service = init_haptic_service(page)

    current_view = "daily"
    max_a_limit = int(get_config("max_a_limit", "3"))
    # Tryb dodawania do Daily: "drag" (przeciągnij) lub "tap" (kliknij słońce)
    daily_add_mode = get_config("daily_add_mode", "drag")
    open_prio_task_id = None
    active_drag_task_id = None
    active_subtask_idx_map = {}

    def is_mobile():
        w = getattr(page, "width", None) or getattr(page.window, "width", 950) or 950
        return w < 650

    priority_colors = {"A": COLOR_PRIO_A, "B": COLOR_PRIO_B, "C": COLOR_PRIO_C}

    tasks_column = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=8)
    routines_row = ft.Row(wrap=True, spacing=8)

    progress_bar = ft.ProgressBar(value=0.0, color=ft.Colors.GREEN_500, bgcolor=ft.Colors.GREY_800, height=8)
    progress_text = ft.Text("Postęp dnia: 0%", size=12, color=ft.Colors.GREY_400)

    status_text = ft.Text("", size=12, color=ft.Colors.GREEN_400)
    warning_text = ft.Text("", size=12, color=ft.Colors.RED_400)
    # Trwała kontrolka z instrukcją po aktualizacji OTA (nie znika przy refresh_view)
    ota_restart_hint = ft.Text("", size=12, color=COLOR_PRIO_B, selectable=True)
    title_text = ft.Text("Daily", size=22, weight=ft.FontWeight.BOLD)

    # --- MINUTNIK SKUPIENIA (Zasada 5 minut / Pomodoro) ---
    # Stan jest zapisany w configu i liczony od znacznika czasu (time.time()).
    # Dzięki temu nie dryfuje (stary sleep(1) gubił sekundy) i przeżywa
    # zminimalizowanie aplikacji na telefonie oraz restart procesu.
    def _timer_total_from_config():
        try:
            return max(60, int(float(get_config("timer_total", "300") or 300)))
        except Exception:
            return 300

    timer_total_seconds = _timer_total_from_config()
    timer_seconds_left = timer_total_seconds
    timer_end_ts = 0.0
    timer_running = False
    timer_message = ""
    timer_lock = threading.Lock()

    if get_config("timer_running", "0") == "1":
        try:
            timer_end_ts = float(get_config("timer_end_ts", "0") or 0)
        except Exception:
            timer_end_ts = 0.0
        remaining = max(0, int(round(timer_end_ts - time.time()))) if timer_end_ts else 0
        if remaining > 0:
            timer_running = True
            timer_seconds_left = remaining
        else:
            timer_end_ts = 0.0
            timer_seconds_left = 0
            timer_message = "⏱️ Minutnik zakończył się, gdy aplikacja była zamknięta."
    else:
        # Pauza też jest trwała — wracamy do zapamiętanej liczby sekund.
        try:
            saved_left = int(float(get_config("timer_left", "0") or 0))
        except Exception:
            saved_left = 0
        if 0 < saved_left <= timer_total_seconds:
            timer_seconds_left = saved_left

    def _persist_timer():
        try:
            set_config("timer_total", timer_total_seconds)
            set_config("timer_running", "1" if timer_running else "0")
            set_config("timer_end_ts", f"{timer_end_ts:.3f}")
            set_config("timer_left", timer_seconds_left)
        except Exception:
            pass

    timer_text = ft.Text("05:00", size=22, weight=ft.FontWeight.BOLD, color=COLOR_TEXT_PRIMARY)
    timer_progress = ft.ProgressBar(value=1.0, color=COLOR_PRIO_B, bgcolor="#21262d", height=4)
    timer_msg_text = ft.Text("⚡ Zasada 5 minut: przełam paraliż startu!", size=11, color=COLOR_TEXT_SECONDARY, weight=ft.FontWeight.W_500)
    timer_btn_play = ft.IconButton(
        icon=ft.Icons.PLAY_ARROW_ROUNDED,
        icon_size=24,
        icon_color=COLOR_SUCCESS,
        tooltip="Uruchom minutnik",
        on_click=lambda e: toggle_timer()
    )

    # --- GLOBALNY MINI-MINUTNIK (wskaźnik widoczny w każdym widoku) ---
    # Dwa lekkie wystąpienia (PC w sidebarze, mobile nad dolną nawigacją).
    # Stan timera pozostaje jeden — to tylko wskaźnik klikalny do Daily.
    def _mini_timer_go_daily(e=None):
        try:
            switch_view("daily")
        except Exception as e:
            LOG.debug("Nie udało się wrócić do Daily z mini-minutnika: %s", e)

    def _build_mini_timer(key):
        icon = ft.Icon(ft.Icons.TIMER_OUTLINED, size=15, color=COLOR_PRIO_B)
        text = ft.Text("00:00", size=13, weight=ft.FontWeight.W_600, color=COLOR_TEXT_PRIMARY)
        # Pauza/wznowienie i reset są dostępne z każdego widoku — bez wracania do Daily.
        play_btn = ft.IconButton(
            icon=ft.Icons.PLAY_ARROW_ROUNDED, icon_size=16, icon_color=COLOR_SUCCESS,
            tooltip="Pauza / wznów minutnik", on_click=lambda e: toggle_timer(),
        )
        reset_btn = ft.IconButton(
            icon=ft.Icons.REPLAY_ROUNDED, icon_size=16, icon_color=COLOR_TEXT_SECONDARY,
            tooltip="Reset minutnika", on_click=lambda e: reset_timer(),
        )
        view = ft.Container(
            key=key,
            visible=False,
            ink=True,
            border_radius=8,
            bgcolor=COLOR_BG_SURFACE,
            padding=ft.Padding.symmetric(horizontal=10, vertical=4),
            tooltip="Minutnik skupienia — kliknij, aby wrócić do Daily",
            on_click=_mini_timer_go_daily,
            content=ft.Row([icon, text, play_btn, reset_btn], spacing=4, tight=True,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )
        return icon, text, play_btn, reset_btn, view

    (mini_timer_pc_icon, mini_timer_pc_text,
     mini_timer_pc_play, mini_timer_pc_reset, mini_timer_pc) = _build_mini_timer("mini_timer_pc")
    (mini_timer_mob_icon, mini_timer_mob_text,
     mini_timer_mob_play, mini_timer_mob_reset, mini_timer_mob) = _build_mini_timer("mini_timer_mobile")
    mini_timer_icons = (mini_timer_pc_icon, mini_timer_mob_icon)
    mini_timer_texts = (mini_timer_pc_text, mini_timer_mob_text)
    mini_timer_views = (mini_timer_pc, mini_timer_mob)
    mini_timer_play_buttons = (mini_timer_pc_play, mini_timer_mob_play)
    mini_timer_reset_buttons = (mini_timer_pc_reset, mini_timer_mob_reset)

    # Pasek mobile (Divider + licznik) trzymany jako jeden kontroler, żeby nie
    # przesuwać indeksów `root_layout.controls` (testy: [0] content_area, [2] bottom_nav).
    # Chowamy całość razem z licznikiem, żeby samotny Divider nie wisiał na ekranie.
    mini_timer_mob_wrapper = ft.Column(
        [ft.Divider(height=1, color=ft.Colors.GREY_800), mini_timer_mob],
        spacing=0, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER
    )

    def _mini_timer_should_show():
        """Widoczny w trakcie odliczania, w pauzie z czasem i po zakończeniu."""
        return bool(timer_running or timer_message or (0 < timer_seconds_left < timer_total_seconds))

    def update_mini_timer():
        value = format_mmss(timer_seconds_left)
        color = COLOR_SUCCESS if timer_message else (
            COLOR_PRIO_B if timer_running else COLOR_TEXT_PRIMARY)
        for text_ctrl in mini_timer_texts:
            text_ctrl.value = value
            text_ctrl.color = color
        for icon_ctrl in mini_timer_icons:
            icon_ctrl.color = color
        for btn in mini_timer_play_buttons:
            btn.icon = ft.Icons.PAUSE_ROUNDED if timer_running else ft.Icons.PLAY_ARROW_ROUNDED
            btn.icon_color = COLOR_PRIO_B if timer_running else COLOR_SUCCESS
        # Na telefonie cały chip (i jego przyciski) ma obszar klikalny palca.
        mob = is_mobile()
        side = TOUCH_TARGET_MIN if mob else 30
        for btn in mini_timer_play_buttons + mini_timer_reset_buttons:
            btn.width = side
            btn.height = side
        for view_ctrl in mini_timer_views:
            view_ctrl.height = TOUCH_TARGET_MIN if mob else None
            view_ctrl.padding = (ft.Padding.symmetric(horizontal=12, vertical=8) if mob
                                 else ft.Padding.symmetric(horizontal=10, vertical=4))
        show = _mini_timer_should_show()
        for view_ctrl in mini_timer_views:
            view_ctrl.visible = show
        mini_timer_mob_wrapper.visible = show

    def update_timer_ui():
        mins = timer_seconds_left // 60
        secs = timer_seconds_left % 60
        timer_text.value = f"{mins:02d}:{secs:02d}"
        ratio = (timer_seconds_left / timer_total_seconds) if timer_total_seconds > 0 else 0.0
        timer_progress.value = max(0.0, min(1.0, ratio))
        timer_btn_play.icon = ft.Icons.PAUSE_ROUNDED if timer_running else ft.Icons.PLAY_ARROW_ROUNDED
        timer_btn_play.icon_color = COLOR_PRIO_B if timer_running else COLOR_SUCCESS
        timer_btn_play.tooltip = "Pauza" if timer_running else "Start"
        if timer_message:
            timer_msg_text.value = timer_message
            timer_msg_text.color = COLOR_SUCCESS
        else:
            if timer_total_seconds == 300:
                timer_msg_text.value = "⚡ Zasada 5 minut: zrób tylko 5 min!"
            elif timer_total_seconds == 900:
                timer_msg_text.value = "🎯 15 minut: krótki, intensywny sprint"
            else:
                timer_msg_text.value = "🔥 25 minut: głębokie skupienie (Pomodoro)"
            timer_msg_text.color = COLOR_TEXT_SECONDARY
        update_mini_timer()
        try:
            page.update()
        except Exception:
            pass

    def timer_worker():
        nonlocal timer_running, timer_seconds_left, timer_message, timer_end_ts
        while True:
            with timer_lock:
                if not timer_running:
                    break
                timer_seconds_left = max(0, int(round(timer_end_ts - time.time())))
                done = timer_seconds_left <= 0
            if done:
                with timer_lock:
                    timer_running = False
                    timer_end_ts = 0.0
                    timer_seconds_left = 0
                    timer_message = "🎉 Brawo! Czas minął – paraliż pokonany!"
                _persist_timer()
                play_sound_feedback("timer_done")
                trigger_haptic_feedback(page, haptic_service, "timer_done")
                update_timer_ui()
                break
            update_timer_ui()
            time.sleep(0.5)

    def toggle_timer():
        nonlocal timer_running, timer_seconds_left, timer_end_ts, timer_message
        with timer_lock:
            timer_message = ""
            if timer_running:
                # Pauza: zapamiętujemy ile zostało, licznik czasu przestaje biec.
                timer_seconds_left = max(0, int(round(timer_end_ts - time.time())))
                timer_running = False
                timer_end_ts = 0.0
                start_thread = False
            else:
                if timer_seconds_left <= 0:
                    timer_seconds_left = timer_total_seconds
                timer_end_ts = time.time() + timer_seconds_left
                timer_running = True
                start_thread = True
        _persist_timer()
        update_timer_ui()
        if start_thread:
            threading.Thread(target=timer_worker, daemon=True).start()

    def reset_timer():
        nonlocal timer_running, timer_seconds_left, timer_end_ts, timer_message
        with timer_lock:
            timer_running = False
            timer_end_ts = 0.0
            timer_seconds_left = timer_total_seconds
            timer_message = ""
        _persist_timer()
        update_timer_ui()

    def set_timer_preset(seconds):
        nonlocal timer_running, timer_total_seconds, timer_seconds_left, timer_end_ts, timer_message
        with timer_lock:
            timer_running = False
            timer_end_ts = 0.0
            timer_total_seconds = seconds
            timer_seconds_left = seconds
            timer_message = ""
        _persist_timer()
        update_timer_ui()
        refresh_view()

    # --- STREFY PROJEKTÓW I FILTRY MASTER LISTY ---
    ZONES = ["Ogólne", "💼 Praca", "🏠 Dom", "🎯 Osobiste"]
    # Strefy bazowe + kategorie już istniejące w bazie. Bez tego edycja zadania
    # z własną kategorią (np. po imporcie JSON) po cichu zmieniała ją na "Ogólne".
    KNOWN_CATEGORIES = get_all_categories(ZONES)
    master_search_query = ""
    master_filter_priority = "ALL"
    master_filter_status = "ALL"
    master_filter_category = "ALL"
    master_sort_mode = get_config("master_sort_mode", "DEFAULT") or "DEFAULT"
    master_deadline_filter = get_config("master_deadline_filter", "ALL") or "ALL"
    # Sprint 3.2: filtr „Dawno nietknięte” (zadania-duchy) — zapamiętywany w configu.
    master_stale_only = get_config("master_stale_only", "0") == "1"

    def set_search_query(q):
        nonlocal master_search_query
        master_search_query = q
        refresh_view()

    def set_sort_mode(mode):
        nonlocal master_sort_mode
        master_sort_mode = mode
        set_config("master_sort_mode", mode)
        refresh_view()

    def set_deadline_filter(mode):
        nonlocal master_deadline_filter
        master_deadline_filter = mode
        set_config("master_deadline_filter", mode)
        refresh_view()

    def set_stale_only(value):
        nonlocal master_stale_only
        master_stale_only = bool(value)
        set_config("master_stale_only", "1" if master_stale_only else "0")
        refresh_view()

    def set_filter_category(cat):
        nonlocal master_filter_category
        master_filter_category = cat
        refresh_view()

    def set_filter_priority(prio):
        nonlocal master_filter_priority
        master_filter_priority = prio
        refresh_view()

    def set_filter_status(stat):
        nonlocal master_filter_status
        master_filter_status = stat
        refresh_view()

    gist_token_input = ft.TextField(label="Token GitHub (uprawnienie 'gist')", value=get_config("gist_token", ""),
                                    password=True, can_reveal_password=True)
    gist_id_input = ft.TextField(label="ID Chmury (Zostaw puste, aby utworzyć nową)", value=get_config("gist_id", ""))
    ha_url_input = ft.TextField(label="Adres serwera HA", value=get_config("ha_url", ""))
    folder_input = ft.TextField(label="Ścieżka folderu lokalnego (PC)", value=get_config("sync_folder", ""))
    routine_input = ft.TextField(label="Nowa rutyna (np. Leki, Woda)", expand=True)

    # --- Dni tygodnia dla rutyn: stan formularza dodawania ---
    routine_add_days = set()

    def toggle_routine_add_day(day):
        """Kliknięcie kafelka dnia w formularzu dodawania (odświeża zaznaczenia)."""
        if day in routine_add_days:
            routine_add_days.discard(day)
        else:
            routine_add_days.add(day)
        refresh_view()

    def save_routine_add(e=None):
        """Zapisuje nową rutynę z zaznaczonymi dniami (puste zaznaczenie = codziennie)."""
        title = (routine_input.value or "").strip()
        if title:
            add_routine_db(title, sorted(routine_add_days))
            status_text.value = "✓ Dodano rutynę"
        else:
            status_text.value = ""
        routine_input.value = ""
        routine_add_days.clear()
        refresh_view()

    def change_sync_mode(mode_name):
        nonlocal max_a_limit
        set_config("sync_mode", mode_name)
        set_config("gist_token", gist_token_input.value.strip())
        set_config("gist_id", gist_id_input.value.strip())
        set_config("ha_url", ha_url_input.value.strip())
        set_config("sync_folder", folder_input.value.strip())
        # Ręczne włączenie trybu = świadoma decyzja użytkownika, więc pobieramy zawsze.
        ok, msg = pull_database(force=True)

        if not ok:
            set_config("sync_mode", "none")
            warning_text.value = f"⚠️ {msg} (Zresetowano do trybu lokalnego)"
            status_text.value = ""
        else:
            push_ok, push_msg = push_database()
            gist_id_input.value = get_config("gist_id", "")
            if push_ok:
                status_text.value = f"✓ {push_msg or msg}"
                warning_text.value = ""
            else:
                status_text.value = f"✓ {msg}"
                warning_text.value = f"⚠️ Zapis nieudany: {push_msg}"

        refresh_view()

    def manual_sync(e=None):
        ok, msg = pull_database(force=True)
        if ok:
            push_ok, push_msg = push_database()
            status_text.value = f"✓ {push_msg or msg}"
            warning_text.value = "" if push_ok else f"⚠️ Zapis nieudany: {push_msg}"
        else:
            status_text.value = ""
            warning_text.value = f"⚠️ {msg}"
        refresh_view()

    def _sync_only_local():
        """Change to local-only sync mode with confirmation message."""
        change_sync_mode("none")
        status_text.value = "✓ Zapisano tylko lokalnie"
        refresh_view()

    def trigger_manual_ota(e=None):
        try:
            status_text.value = "⏳ Sprawdzanie i pobieranie najnowszej wersji z GitHuba..."
            warning_text.value = ""
            ota_restart_hint.value = ""
            page.update()

            ts = int(datetime.now().timestamp())
            headers = {
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "User-Agent": "MasterADHD-OTA/1.0"
            }

            # Pobierz numer wersji
            url_ver = f"{GITHUB_RAW_BASE}/version.txt?t={ts}"
            req_ver = urllib.request.Request(url_ver, headers=headers)
            try:
                with urllib.request.urlopen(req_ver, timeout=6, context=get_ssl_context()) as resp:
                    remote_ver = resp.read().decode("utf-8").strip()
            except Exception as ve:
                warning_text.value = f"⚠️ Nie udało się pobrać wersji: {ve}"
                status_text.value = ""
                refresh_view()
                return

            if not remote_ver:
                warning_text.value = "⚠️ GitHub zwrócił pusty numer wersji."
                status_text.value = ""
                refresh_view()
                return

            # Pobierz kod źródłowy
            url_code = f"{GITHUB_RAW_BASE}/app_logic.py?t={ts}"
            req_code = urllib.request.Request(url_code, headers=headers)
            try:
                with urllib.request.urlopen(req_code, timeout=15, context=get_ssl_context()) as resp_code:
                    new_code = resp_code.read().decode("utf-8")
            except Exception as ve:
                warning_text.value = f"⚠️ Nie udało się pobrać kodu: {ve}"
                status_text.value = ""
                refresh_view()
                return

            # Zapisujemy wyłącznie w prywatnym folderze danych aplikacji
            # (na Androidzie folder APK jest tylko do odczytu).
            installed_ver = get_local_version()
            ok_save, save_msg = install_ota_code(new_code, remote_ver)
            if not ok_save:
                warning_text.value = f"⚠️ {save_msg}"
                status_text.value = ""
                refresh_view()
                return

            status_text.value = f"✓ Pobrano v{remote_ver}. Uruchamiam nową wersję..."
            page.update()

            # Dopinamy zaległy zapis starej wersji, zanim podmienimy interfejs.
            try:
                flush_pending_sync(timeout=2.0)
            except Exception:
                pass

            ok_activate, activate_msg = activate_downloaded_ota(page, remote_ver)
            if ok_activate:
                # Nowy interfejs jest już na ekranie — nie dotykamy starych kontrolek.
                return

            # Nie udało się uruchomić nowego kodu: przywracamy numer wersji i
            # pokazujemy bezpieczny ekran zamiast połowicznie zbudowanego UI.
            _restore_version_files(installed_ver)
            LOG.error("OTA: aktywacja nieudana: %s", activate_msg)
            page.controls.clear()
            page.add(
                ft.SafeArea(
                    ft.Container(
                        padding=20,
                        content=ft.Column([
                            ft.Text("⚠️ Nie udało się uruchomić pobranej wersji", size=16,
                                    weight=ft.FontWeight.BOLD, color=COLOR_PRIO_A),
                            ft.Text(f"Pobrano v{remote_ver}, ale nowy kod zgłosił błąd:\n{activate_msg}",
                                    size=12, selectable=True),
                            ft.Text("Zamknij i otwórz aplikację — uruchomi się poprzednia, "
                                    "działająca wersja (pobrany plik zostanie odrzucony).",
                                    size=12, color=COLOR_TEXT_SECONDARY),
                            ft.Button("🔄 Spróbuj ponownie", bgcolor=COLOR_BG_SURFACE,
                                      color=COLOR_TEXT_PRIMARY, on_click=trigger_manual_ota),
                        ], spacing=10, scroll=ft.ScrollMode.AUTO)
                    )
                )
            )
            page.update()
        except Exception as ex:
            LOG.exception("OTA: błąd aktualizacji")
            warning_text.value = f"⚠️ Błąd aktualizacji OTA: {ex}"
            status_text.value = ""
            refresh_view()

    # --- SZYBKIE WPROWADZANIE (Inline Quick Input) ---
    inline_input_field = ft.TextField(
        hint_text="⚡ Błyskawiczny zrzut myśli (Enter aby zapisać)...",
        expand=True,
        dense=True,
        bgcolor=COLOR_BG_CARD,
        border_color=COLOR_BG_BORDER,
        focused_border_color=COLOR_PRIO_B,
        text_size=13,
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=8)
    )

    def save_inline_input(e=None):
        val = inline_input_field.value.strip()
        if not val:
            return
        if current_view == "daily":
            # Sprint 2.3: zrzut myśli ląduje w Inbox (priorytet C), nie zaśmieca Mastera.
            add_task_db(val, is_daily=False, priority="C", category=INBOX_CATEGORY)
            status_text.value = "✓ Zapisano w Inbox"
        else:
            add_task_db(val, is_daily=False, priority="B")
            status_text.value = "✓ Dodano zadanie"
        inline_input_field.value = ""
        refresh_view()

    inline_input_field.on_submit = save_inline_input

    # --- OKIENKA DIALOGOWE ---
    quick_dump_field = ft.TextField(hint_text="Myśl do Inbox...", autofocus=True,
                                    on_submit=lambda e: save_quick_dump())

    def save_quick_dump(e=None):
        if quick_dump_field.value.strip():
            add_task_db(quick_dump_field.value.strip(), is_daily=False, priority="C",
                        category=INBOX_CATEGORY)
            quick_dump_field.value = ""
            quick_dialog.open = False
            status_text.value = "✓ Zapisano w Inbox"
            refresh_view()

    quick_dialog = ft.AlertDialog(title=ft.Text("⚡ Szybki zrzut"), content=quick_dump_field,
                                  actions=[ft.TextButton("Anuluj", on_click=lambda e: close_dialogs()),
                                           ft.Button("Zapisz", on_click=save_quick_dump)])

    master_title_field = ft.TextField(hint_text="Treść zadania (Projektu)...", autofocus=True,
                                      on_submit=lambda e: save_master_task())
    selected_new_prio = "B"
    prio_select_row = ft.Row(spacing=10)

    def select_modal_prio(p):
        nonlocal selected_new_prio;
        selected_new_prio = p
        build_modal_prio_buttons();
        page.update()

    def build_modal_prio_buttons():
        prio_select_row.controls.clear()
        for p in ("A", "B", "C"):
            is_sel = (p == selected_new_prio)
            prio_select_row.controls.append(ft.Container(
                content=ft.Row([ft.Text(p, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)],
                               alignment=ft.MainAxisAlignment.CENTER), width=45, height=36, border_radius=8,
                bgcolor=priority_colors[p] if is_sel else ft.Colors.GREY_800,
                border=ft.Border.all(2, ft.Colors.WHITE) if is_sel else None,
                on_click=lambda e, pr=p: select_modal_prio(pr)))

    master_category_dropdown = ft.Dropdown(
        label="Strefa / Kategoria",
        options=[ft.dropdown.Option(z) for z in KNOWN_CATEGORIES],
        value="Ogólne",
        bgcolor=COLOR_BG_SURFACE,
        border_color=COLOR_BG_BORDER
    )
    new_category_field = ft.TextField(
        label="Nowa strefa (opcjonalnie — nadpisuje wybór powyżej)",
        bgcolor=COLOR_BG_SURFACE,
        border_color=COLOR_BG_BORDER,
        dense=True,
    )

    def make_deadline_editor(state):
        """Sekcja „Termin (opcjonalnie)” ze skrótami i kalendarzem.

        `state` to słownik z kluczem „value” (ISO albo None). Zwraca parę
        (kontrolka, odśwież etykietę).
        """
        label = ft.Text("", size=12, color=COLOR_TEXT_SECONDARY)

        def refresh_label():
            value = state.get("value")
            if not value:
                label.value = "Bez terminu — zadanie nie będzie przypominać."
                label.color = COLOR_TEXT_SECONDARY
            else:
                days = days_to_deadline(value)
                label.value = f"Termin: {format_deadline_pl(value)} ({deadline_chip_text(value)})"
                label.color = COLOR_PRIO_A if days is not None and days < 0 else (
                    COLOR_PRIO_B if days is not None and days <= 3 else COLOR_TEXT_SECONDARY)

        def set_value(value):
            state["value"] = value
            refresh_label()

        def preset(days_ahead):
            set_value((date.today() + timedelta(days=days_ahead)).isoformat())

        def open_picker(e=None):
            picker = state.get("picker")
            if picker is None:
                try:
                    start = date.fromisoformat(state["value"]) if state.get("value") else date.today()
                except ValueError:
                    start = date.today()
                picker = ft.DatePicker(
                    value=start,
                    first_date=date.today() - timedelta(days=3650),
                    last_date=date.today() + timedelta(days=3650),
                    help_text="Wybierz termin zadania",
                    cancel_text="Anuluj",
                    confirm_text="Wybierz",
                    on_change=lambda ev: set_value(picker.value),
                )
                state["picker"] = picker
                page.overlay.append(picker)
            try:
                picker.value = date.fromisoformat(state["value"]) if state.get("value") else date.today()
            except ValueError:
                picker.value = date.today()
            if hasattr(page, "show_dialog"):
                page.show_dialog(picker)
            else:
                picker.open = True
                page.update()

        refresh_label()
        column = ft.Column([
            ft.Text("Termin (opcjonalnie)", size=13, color=COLOR_TEXT_PRIMARY),
            ft.Row([
                ft.TextButton("Dziś", on_click=lambda e: preset(0)),
                ft.TextButton("Jutro", on_click=lambda e: preset(1)),
                ft.TextButton("Za tydzień", on_click=lambda e: preset(7)),
                ft.TextButton("📅 Wybierz datę", on_click=open_picker),
                ft.TextButton("Usuń termin", on_click=lambda e: set_value(None)),
            ], wrap=True, spacing=4),
            label,
        ], tight=True, spacing=4)
        return column, refresh_label

    new_deadline_state = {"value": None}
    new_deadline_editor, refresh_new_deadline = make_deadline_editor(new_deadline_state)

    def save_master_task(e=None):
        if master_title_field.value.strip():
            custom_category = (new_category_field.value or "").strip()
            add_task_db(
                master_title_field.value.strip(),
                is_daily=False,
                priority=selected_new_prio,
                category=custom_category or master_category_dropdown.value or "Ogólne",
                deadline=new_deadline_state.get("value")
            )
            master_title_field.value = ""
            new_category_field.value = ""
            new_deadline_state["value"] = None
            refresh_new_deadline()
            master_dialog.open = False
            status_text.value = "✓ Dodano"
            refresh_view()

    master_dialog = ft.AlertDialog(title=ft.Text("➕ Nowy projekt / zadanie"), content=ft.Column(
        [master_title_field, master_category_dropdown, new_category_field,
         new_deadline_editor,
         ft.Text("Priorytet:", size=13), prio_select_row], tight=True, spacing=10),
                                   actions=[ft.TextButton("Anuluj", on_click=lambda e: close_dialogs()),
                                            ft.Button("Dodaj", on_click=save_master_task)])

    subtask_field = ft.TextField(hint_text="Nazwa konkretnego kroku...", autofocus=True,
                                 on_submit=lambda e: save_subtask())
    active_subtask_id, active_subtask_list = None, []

    def open_subtask_dialog(task_id, current_subtasks):
        nonlocal active_subtask_id, active_subtask_list
        active_subtask_id, active_subtask_list = task_id, current_subtasks
        subtask_field.value = ""
        subtask_dialog.open = True
        page.update()

    def save_subtask(e=None):
        if subtask_field.value.strip():
            active_subtask_list.append({"title": subtask_field.value.strip(), "is_done": 0})
            update_subtasks_db(active_subtask_id, active_subtask_list)
            subtask_field.value = ""
            subtask_dialog.open = False
            refresh_view()

    subtask_dialog = ft.AlertDialog(title=ft.Text("👣 Dopisz krok do planu"), content=subtask_field,
                                    actions=[ft.TextButton("Anuluj", on_click=lambda e: close_dialogs()),
                                             ft.Button("Dodaj", on_click=save_subtask)])

    edit_task_field = ft.TextField(
        hint_text="Treść zadania...",
        autofocus=True,
        bgcolor=COLOR_BG_SURFACE,
        border_color=COLOR_BG_BORDER,
        focused_border_color=COLOR_PRIO_B,
        on_submit=lambda e: save_task_edit()
    )
    edit_category_dropdown = ft.Dropdown(
        label="Strefa / Kategoria",
        options=[ft.dropdown.Option(z) for z in KNOWN_CATEGORIES],
        value="Ogólne",
        bgcolor=COLOR_BG_SURFACE,
        border_color=COLOR_BG_BORDER
    )
    editing_task_id = None
    edit_deadline_state = {"value": None}
    edit_deadline_editor, refresh_edit_deadline = make_deadline_editor(edit_deadline_state)

    def open_edit_task_dialog(task_id, current_title, current_category="Ogólne"):
        nonlocal editing_task_id
        editing_task_id = task_id
        edit_task_field.value = current_title
        cat = current_category or "Ogólne"
        # Dopisz kategorię, której nie ma jeszcze na liście (import JSON / własna strefa).
        existing = [opt.key for opt in edit_category_dropdown.options]
        if cat not in existing:
            edit_category_dropdown.options.append(ft.dropdown.Option(cat))
        edit_category_dropdown.value = cat
        edit_deadline_state["value"] = get_task_deadline(task_id)
        refresh_edit_deadline()
        edit_task_dialog.open = True
        page.update()

    def save_task_edit(e=None):
        nonlocal editing_task_id
        val = edit_task_field.value.strip()
        cat = edit_category_dropdown.value or "Ogólne"
        if editing_task_id and val:
            update_task_details_db(editing_task_id, val, category=cat,
                                   deadline=edit_deadline_state.get("value"))
            edit_task_dialog.open = False
            status_text.value = "✓ Zaktualizowano zadanie"
            warning_text.value = ""
            refresh_view()

    edit_task_dialog = ft.AlertDialog(
        title=ft.Text("✏️ Edytuj zadanie"),
        content=ft.Column([edit_task_field, edit_category_dropdown, edit_deadline_editor],
                          tight=True, spacing=10),
        actions=[
            ft.TextButton("Anuluj", on_click=lambda e: close_dialogs()),
            ft.Button("Zapisz", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY, on_click=save_task_edit)
        ]
    )

    deleting_task_id = None
    delete_task_text = ft.Text("", size=13, color=COLOR_TEXT_PRIMARY)

    def open_delete_task_dialog(task_id, current_title):
        nonlocal deleting_task_id
        deleting_task_id = task_id
        delete_task_text.value = f"Czy na pewno chcesz usunąć to zadanie?\n\n„{current_title}”"
        delete_task_dialog.open = True
        page.update()

    def confirm_delete_task(e=None):
        nonlocal deleting_task_id
        if deleting_task_id:
            delete_task_db(deleting_task_id)
            if deleting_task_id in active_subtask_idx_map:
                del active_subtask_idx_map[deleting_task_id]
            delete_task_dialog.open = False
            status_text.value = "✓ Usunięto zadanie"
            warning_text.value = ""
            refresh_view()

    delete_task_dialog = ft.AlertDialog(
        title=ft.Text("🗑️ Usunąć zadanie?"),
        content=delete_task_text,
        actions=[
            ft.TextButton("Anuluj", on_click=lambda e: close_dialogs()),
            ft.Button("Usuń", bgcolor="#ef444422", color=COLOR_PRIO_A, on_click=confirm_delete_task)
        ]
    )

    # --- „COFNIJ” ZAMIAST OKNA POTWIERDZENIA (tylko ostatnia akcja) ---
    # Dialog usuwania zostaje w overlay[4] (kontrakt testów i operacje bezpośrednie),
    # ale karty zadań usuwają natychmiast i pokazują SnackBar z „Cofnij”.
    undo_state = {"token": 0, "callback": None, "snack": None}

    def _short_title(title, limit=42):
        text = (title or "").strip()
        return text if len(text) <= limit else text[:limit - 1] + "…"

    def _dismiss_undo_snackbar():
        snack = undo_state.get("snack")
        undo_state["snack"] = None
        if snack is not None:
            try:
                snack.open = False
            except Exception as e:
                LOG.debug("Nie udało się zamknąć SnackBar: %s", e)
        try:
            page.update()
        except Exception:
            pass

    def _show_undo_snackbar(message, callback):
        """Pokazuje SnackBar „Cofnij” (~6 s). Token unieważnia stare akcje."""
        undo_state["token"] = int(undo_state.get("token") or 0) + 1
        token = undo_state["token"]
        undo_state["callback"] = callback

        def _on_dismiss(e=None):
            # SnackBar zniknął sam — akcja staje się ostateczna.
            if undo_state.get("token") == token:
                undo_state["token"] = token + 1
                undo_state["callback"] = None
                undo_state["snack"] = None

        def _undo(e=None):
            if undo_state.get("token") != token or undo_state.get("callback") is None:
                return
            action = undo_state["callback"]
            undo_state["callback"] = None
            undo_state["token"] = token + 1
            try:
                action()
            except Exception as ex:
                LOG.exception("Nie udało się cofnąć akcji: %s", ex)
                warning_text.value = "⚠️ Nie udało się cofnąć akcji"
                try:
                    refresh_view()
                except Exception:
                    pass
            _dismiss_undo_snackbar()
            trigger_haptic_feedback(page, haptic_service, "undo")

        snack = ft.SnackBar(
            content=ft.Text(message, size=13, color=COLOR_TEXT_PRIMARY),
            action=ft.SnackBarAction(label="Cofnij", on_click=_undo),
            duration=6000,
            behavior=ft.SnackBarBehavior.FLOATING,
            bgcolor=COLOR_BG_SURFACE,
            margin=ft.Margin.only(left=12, right=12, bottom=112 if is_mobile() else 20),
            on_dismiss=_on_dismiss,
        )
        undo_state["snack"] = snack
        try:
            shower = getattr(page, "show_dialog", None)
            if callable(shower):
                shower(snack)
            else:
                page.overlay.append(snack)
                snack.open = True
            page.update()
        except Exception as e:
            LOG.debug("Nie udało się pokazać SnackBar „Cofnij”: %s", e)

    def delete_task_now(task_id, title):
        """Natychmiast usuwa zadanie i daje jedno kliknięcie „Cofnij”."""
        row = delete_task_with_undo_db(task_id)
        active_subtask_idx_map.pop(task_id, None)
        if row is None:
            status_text.value = "⚠️ Nie znaleziono zadania"
            warning_text.value = ""
            refresh_view()
            return
        status_text.value = "✓ Usunięto zadanie"
        warning_text.value = ""
        refresh_view()

        def undo():
            restored = restore_task_row_db(row)
            status_text.value = "✓ Przywrócono zadanie" if restored else "Zadanie już istniało"
            warning_text.value = ""
            refresh_view()

        _show_undo_snackbar(f"Usunięto „{_short_title(title)}”", undo)

    edit_subtask_field = ft.TextField(
        hint_text="Treść kroku...",
        autofocus=True,
        bgcolor=COLOR_BG_SURFACE,
        border_color=COLOR_BG_BORDER,
        focused_border_color=COLOR_PRIO_C,
        on_submit=lambda e: save_subtask_edit()
    )
    editing_subtask_task_id = None
    editing_subtask_idx = None
    editing_subtask_list = []

    def open_edit_subtask_dialog(task_id, subtask_idx, current_subtasks):
        nonlocal editing_subtask_task_id, editing_subtask_idx, editing_subtask_list
        editing_subtask_task_id = task_id
        editing_subtask_idx = subtask_idx
        editing_subtask_list = list(current_subtasks)
        edit_subtask_field.value = current_subtasks[subtask_idx]["title"]
        edit_subtask_dialog.open = True
        page.update()

    def save_subtask_edit(e=None):
        nonlocal editing_subtask_task_id, editing_subtask_idx, editing_subtask_list
        val = edit_subtask_field.value.strip()
        if editing_subtask_task_id and editing_subtask_idx is not None and val:
            editing_subtask_list[editing_subtask_idx]["title"] = val
            update_subtasks_db(editing_subtask_task_id, editing_subtask_list)
            edit_subtask_dialog.open = False
            status_text.value = "✓ Zaktualizowano krok"
            warning_text.value = ""
            refresh_view()

    edit_subtask_dialog = ft.AlertDialog(
        title=ft.Text("✏️ Edytuj krok"),
        content=edit_subtask_field,
        actions=[
            ft.TextButton("Anuluj", on_click=lambda e: close_dialogs()),
            ft.Button("Zapisz", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY, on_click=save_subtask_edit)
        ]
    )

    def delete_subtask_now(task_id, subtask_idx):
        """Natychmiast usuwa krok i pozwala go cofnąć jednym kliknięciem."""
        removed = delete_subtask_db(task_id, subtask_idx)
        if removed is None:
            status_text.value = "⚠️ Nie znaleziono kroku"
            warning_text.value = ""
            refresh_view()
            return
        row = get_task_row(task_id)
        try:
            remaining = len(json.loads(row[5] or "[]")) if row else 0
        except Exception:
            remaining = 0
        if task_id in active_subtask_idx_map and active_subtask_idx_map[task_id] >= remaining:
            active_subtask_idx_map[task_id] = max(0, remaining - 1)
        status_text.value = "✓ Usunięto krok"
        warning_text.value = ""
        refresh_view()

        def undo():
            restored = restore_subtask_db(task_id, subtask_idx, removed)
            status_text.value = "✓ Przywrócono krok" if restored else "Krok już był na liście"
            warning_text.value = ""
            refresh_view()

        _show_undo_snackbar(f"Usunięto krok „{_short_title(removed.get('title', ''))}”", undo)

    def move_subtask_action(task_id, subtask_idx, delta):
        """Przesuwa krok w górę/dół i pilnuje, żeby Daily wskazywał ten sam krok."""
        new_idx = move_subtask_db(task_id, subtask_idx, delta)
        if new_idx is None:
            return
        if task_id in active_subtask_idx_map:
            active = active_subtask_idx_map[task_id]
            if active == subtask_idx:
                active_subtask_idx_map[task_id] = new_idx
            elif active == new_idx:
                active_subtask_idx_map[task_id] = subtask_idx
        status_text.value = "✓ Zmieniono kolejność kroków"
        warning_text.value = ""
        refresh_view()

    clear_archive_dialog = ft.AlertDialog(
        title=ft.Text("🗑️ Wyczyścić Dziennik Sukcesów?"),
        content=ft.Text("Czy na pewno chcesz bezpowrotnie usunąć całą historię ukończonych zadań?"),
        actions=[
            ft.TextButton("Anuluj", on_click=lambda e: close_dialogs()),
            ft.Button("Wyczyść", bgcolor="#ef444422", color=COLOR_PRIO_A, on_click=lambda e: confirm_clear_archive())
        ]
    )

    def open_clear_archive_dialog(e=None):
        clear_archive_dialog.open = True
        page.update()

    def confirm_clear_archive():
        clear_completed_archive_db()
        clear_archive_dialog.open = False
        status_text.value = "✓ Wyczyszczono historię sukcesów (snapshot zapisany)"
        warning_text.value = ""
        refresh_view()

    # --- POTWIERDZENIE "KOŃCA DNIA" (operacja destrukcyjna) ---
    end_of_day_dialog = ft.AlertDialog(
        title=ft.Text("🌙 Zakończyć dzień?"),
        content=ft.Text(
            "Zrobione zadania trafią do Dziennika Sukcesów i znikną z listy Daily.\n"
            "Niezrobione zadania wrócą do Master listy, a rutyny zostaną odznaczone.\n\n"
            "Przed resetem zapisujemy snapshot — będzie można go przywrócić w Ustawieniach."
        ),
        actions=[
            ft.TextButton("Anuluj", on_click=lambda e: close_dialogs()),
            ft.Button("Zakończ dzień", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY,
                      on_click=lambda e: confirm_end_of_day())
        ]
    )

    def open_end_of_day_dialog(e=None):
        end_of_day_dialog.open = True
        page.update()

    def confirm_end_of_day(e=None):
        end_of_day_db()
        end_of_day_dialog.open = False
        status_text.value = "✓ Zakończono dzień. Snapshot zapisany — można go przywrócić w Ustawieniach."
        warning_text.value = ""
        refresh_view()

    # --- IMPORT Z PLIKU JSON ---
    import_path_input = ft.TextField(
        label="Ścieżka do pliku JSON",
        value=latest_json_backup(),
        bgcolor=COLOR_BG_SURFACE,
        border_color=COLOR_BG_BORDER,
        dense=True,
    )
    import_confirm_dialog = ft.AlertDialog(
        title=ft.Text("📤 Import danych z JSON"),
        content=ft.Text(
            "Jak zaimportować dane?\n\n"
            "• „Dopisz” — dodaje zadania/rutyny z pliku do obecnych danych.\n"
            "• „Zastąp” — usuwa obecne dane i wstawia zawartość pliku.\n\n"
            "Przed importem automatycznie powstaje snapshot."
        ),
        actions=[
            ft.TextButton("Anuluj", on_click=lambda e: close_dialogs()),
            ft.TextButton("Dopisz", on_click=lambda e: run_json_import(False)),
            ft.Button("Zastąp", bgcolor="#ef444422", color=COLOR_PRIO_A,
                      on_click=lambda e: run_json_import(True)),
        ]
    )

    def open_import_dialog(e=None):
        if not (import_path_input.value or "").strip():
            import_path_input.value = latest_json_backup()
        import_confirm_dialog.open = True
        page.update()

    def run_json_import(replace_existing):
        path = (import_path_input.value or "").strip()
        ok, msg = import_database_from_json_file(path, replace_existing=replace_existing)
        import_confirm_dialog.open = False
        if ok:
            status_text.value = f"✓ {msg}"
            warning_text.value = ""
        else:
            status_text.value = ""
            warning_text.value = f"⚠️ {msg}"
        refresh_view()

    # --- EDYCJA RUTYNY (tytuł + dni tygodnia) ---
    edit_routine_title_field = ft.TextField(
        label="Nazwa rutyny",
        bgcolor=COLOR_BG_SURFACE,
        border_color=COLOR_BG_BORDER,
        focused_border_color=COLOR_PRIO_C,
        dense=True,
    )
    edit_routine_chips = ft.Column([], tight=True, wrap=True)
    routine_edit_days = set()
    editing_routine_id = None

    def open_edit_routine_dialog(rid, current_title, current_days):
        nonlocal editing_routine_id, routine_edit_days
        editing_routine_id = rid
        routine_edit_days = set(_normalize_routine_days(current_days))
        edit_routine_title_field.value = current_title
        edit_routine_chips.controls = build_routine_day_chips(routine_edit_days, toggle_edit_routine_day)
        edit_routine_dialog.open = True
        page.update()

    def toggle_edit_routine_day(day):
        """Kliknięcie kafelka dnia w oknie edycji — odświeża tylko kafelki (okno zostaje otwarte)."""
        if day in routine_edit_days:
            routine_edit_days.discard(day)
        else:
            routine_edit_days.add(day)
        edit_routine_chips.controls = build_routine_day_chips(routine_edit_days, toggle_edit_routine_day)
        page.update()

    def save_routine_edit(e=None):
        nonlocal editing_routine_id
        title = (edit_routine_title_field.value or "").strip()
        if editing_routine_id and title:
            update_routine_db(editing_routine_id, title, sorted(routine_edit_days))
            editing_routine_id = None
            edit_routine_dialog.open = False
            status_text.value = "✓ Zaktualizowano rutynę"
            warning_text.value = ""
        refresh_view()

    edit_routine_dialog = ft.AlertDialog(
        title=ft.Text("✏️ Edytuj rutynę"),
        content=ft.Column([
            edit_routine_title_field,
            ft.Text("Dni tygodnia (odznacz wszystkie = codziennie):", size=12, color=COLOR_TEXT_SECONDARY),
            edit_routine_chips
        ], tight=True, spacing=10),
        actions=[
            ft.TextButton("Anuluj", on_click=lambda e: close_dialogs()),
            ft.Button("Zapisz", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY, on_click=save_routine_edit)
        ]
    )

    # --- SPRINT 2.1: PRZEŁÓW DNIA (spokojny wybór zamiast cichego gubienia zadań) ---
    rollover_choice = {}
    rollover_state = {"info": None, "tasks": []}

    def _rollover_pill(label, is_active, on_click):
        return ft.Container(
            content=ft.Text(label, size=11, weight=ft.FontWeight.BOLD,
                            color=COLOR_TEXT_PRIMARY if is_active else COLOR_TEXT_SECONDARY),
            bgcolor="#3b82f633" if is_active else "#21262d",
            border=ft.Border.all(1, COLOR_PRIO_B if is_active else COLOR_BG_BORDER),
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            on_click=on_click
        )

    def _set_rollover_choice(task_id, choice):
        rollover_choice[task_id] = choice
        _render_rollover()
        page.update()

    def _rollover_bulk(choice):
        for t in rollover_state["tasks"]:
            rollover_choice[t[0]] = choice
        _render_rollover()
        page.update()

    def _rollover_choice_row(task):
        tid, title = task[0], task[1]
        current = rollover_choice.get(tid, "keep")
        return ft.Container(
            bgcolor=COLOR_BG_SURFACE,
            border=ft.Border.all(1, COLOR_BG_BORDER),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            content=ft.Column([
                ft.Text(title, size=13, color=COLOR_TEXT_PRIMARY, weight=ft.FontWeight.W_500,
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Row([
                    _rollover_pill("Zostaw na dziś", current == "keep",
                                   lambda e, t=tid: _set_rollover_choice(t, "keep")),
                    _rollover_pill("Wróć do Mastera", current == "master",
                                   lambda e, t=tid: _set_rollover_choice(t, "master")),
                    _rollover_pill("Usuń z Daily", current == "remove",
                                   lambda e, t=tid: _set_rollover_choice(t, "remove")),
                ], spacing=4, wrap=True)
            ], spacing=6, tight=True)
        )

    def _render_rollover():
        info = rollover_state.get("info") or {}
        tasks = rollover_state.get("tasks") or []
        rows = [_rollover_choice_row(t) for t in tasks]
        body = [
            ft.Text(
                f"Zrobione wczoraj: {info.get('done_count', 0)} → Dziennik Sukcesów.",
                size=12, color=COLOR_TEXT_SECONDARY
            ),
            ft.Text(
                "Niezrobione zadania z Daily — zdecyduj, co z nimi. Nic nie zniknie po cichu.",
                size=12, color=COLOR_TEXT_PRIMARY
            ),
        ]
        if rows:
            body.extend(rows)
            body.append(
                ft.Row([
                    ft.TextButton("Wszystkie zostaw", on_click=lambda e: _rollover_bulk("keep")),
                    ft.TextButton("Wszystkie do Mastera", on_click=lambda e: _rollover_bulk("master")),
                ], spacing=6)
            )
        else:
            body.append(ft.Text("Brak niezrobionych zadań z Daily.", size=12,
                                color=COLOR_TEXT_SECONDARY))
        day_rollover_dialog.content = ft.Column(body, tight=True, spacing=8,
                                                scroll=ft.ScrollMode.AUTO, height=380)

    def open_day_rollover(e=None):
        info = get_pending_day_rollover()
        if not info:
            return
        rollover_state["info"] = info
        rollover_state["tasks"] = list(info["undone_daily"])
        rollover_choice.clear()
        for t in rollover_state["tasks"]:
            rollover_choice[t[0]] = "keep"
        _render_rollover()
        day_rollover_dialog.open = True
        page.update()

    def close_day_rollover(e=None):
        # „Później” niczego nie zmienia — dzień zostaje otwarty.
        day_rollover_dialog.open = False
        rollover_choice.clear()
        page.update()

    def confirm_day_rollover(e=None):
        result = apply_day_rollover(decisions=dict(rollover_choice))
        day_rollover_dialog.open = False
        rollover_choice.clear()
        if result:
            status_text.value = (
                f"✓ Nowy dzień. Do Dziennika: {result['archived']}, "
                f"zostało na dziś: {result['kept']}, do Mastera: {result['to_master']}."
            )
        warning_text.value = ""
        refresh_view()

    def _maybe_start_day_rollover():
        """Rytuał dnia przy starcie/powrocie: pytaj albo decyduj automatycznie."""
        try:
            last_reset = get_config("last_reset_date", "")
        except Exception:
            last_reset = ""
        if not last_reset:
            # Pierwsze uruchomienie: cicho oznacz dzień jako rozpoczęty.
            set_config("last_reset_date", date.today().isoformat())
            return
        if not get_pending_day_rollover():
            return
        mode = get_day_rollover_mode()
        if mode == DAY_ROLLOVER_ASK:
            open_day_rollover()
        else:
            apply_day_rollover(mode=mode)
            status_text.value = "✓ Nowy dzień"
            refresh_view()

    day_rollover_dialog = ft.AlertDialog(
        title=ft.Text("🌅 Nowy dzień"),
        content=ft.Column([], tight=True),
        actions=[
            ft.TextButton("Później", on_click=close_day_rollover),
            ft.Button("Zamknij dzień", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY,
                      on_click=confirm_day_rollover)
        ]
    )

    # --- SPRINT 2.3: INBOX I 2-MINUTOWY TRIAGE ---
    inbox_choice = {"task_id": None, "priority": "C", "daily": False,
                    "deadline": None, "category": "Ogólne"}

    def _inbox_reset_triage(task_id):
        if inbox_choice["task_id"] != task_id:
            inbox_choice.update({"task_id": task_id, "priority": "C", "daily": False,
                                 "deadline": None, "category": "Ogólne"})

    def set_inbox_priority(p):
        inbox_choice["priority"] = p
        refresh_view()

    def set_inbox_daily(value):
        inbox_choice["daily"] = bool(value)
        refresh_view()

    def set_inbox_category(cat):
        inbox_choice["category"] = cat
        refresh_view()

    def set_inbox_deadline(value):
        inbox_choice["deadline"] = value
        refresh_view()

    def apply_inbox_triage(task_id):
        _inbox_reset_triage(task_id)
        triage_inbox_task_db(
            task_id,
            category=inbox_choice["category"],
            priority=inbox_choice["priority"],
            is_daily=inbox_choice["daily"],
            deadline=inbox_choice["deadline"],
        )
        inbox_choice["task_id"] = None
        status_text.value = "✓ Posortowano zadanie z Inbox"
        warning_text.value = ""
        refresh_view()

    def _inbox_pill(label, is_active, on_click):
        return ft.Container(
            content=ft.Text(label, size=11, weight=ft.FontWeight.BOLD,
                            color=COLOR_TEXT_PRIMARY if is_active else COLOR_TEXT_SECONDARY),
            bgcolor="#10b98133" if is_active else "#21262d",
            border=ft.Border.all(1, COLOR_SUCCESS if is_active else COLOR_BG_BORDER),
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            on_click=on_click
        )

    def _build_inbox_section(inbox_tasks):
        """Jedna karta naraz: priorytet, kategoria, termin, cel — bez zaśmiecania Mastera."""
        task = inbox_tasks[0]
        task_id, title = task[0], task[1]
        _inbox_reset_triage(task_id)
        touch = TOUCH_TARGET_MIN if is_mobile() else 30

        prio_row = ft.Row([
            _inbox_pill(p, inbox_choice["priority"] == p, lambda e, v=p: set_inbox_priority(v))
            for p in ("A", "B", "C")
        ], spacing=4)

        cat_row = ft.Row([
            _inbox_pill(z, inbox_choice["category"] == z, lambda e, v=z: set_inbox_category(v))
            for z in ZONES
        ], spacing=4, wrap=True)

        today = date.today()
        deadline_opts = [
            ("Dziś", today.isoformat()),
            ("Jutro", (today + timedelta(days=1)).isoformat()),
            ("Za tydzień", (today + timedelta(days=7)).isoformat()),
            ("Bez terminu", None),
        ]
        deadline_row = ft.Row([
            _inbox_pill(lbl, inbox_choice["deadline"] == val,
                        lambda e, v=val: set_inbox_deadline(v))
            for lbl, val in deadline_opts
        ], spacing=4, wrap=True)

        daily_row = ft.Row([
            _inbox_pill("Na dziś", inbox_choice["daily"] is True, lambda e: set_inbox_daily(True)),
            _inbox_pill("Do Mastera", inbox_choice["daily"] is False, lambda e: set_inbox_daily(False)),
        ], spacing=4)

        return ft.Container(
            bgcolor="#3b82f614",
            border=ft.Border.all(1, COLOR_PRIO_C),
            border_radius=12,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.ALL_INBOX_ROUNDED, size=18, color=COLOR_PRIO_C),
                    ft.Text(f"Inbox ({len(inbox_tasks)})", size=13, weight=ft.FontWeight.BOLD,
                            color=COLOR_TEXT_PRIMARY),
                    ft.Container(expand=True),
                    ft.Text("2-minutowy triage", size=11, color=COLOR_TEXT_SECONDARY),
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=6),
                ft.Text(title, size=14, weight=ft.FontWeight.W_500, color=COLOR_TEXT_PRIMARY,
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text("Priorytet:", size=11, color=COLOR_TEXT_SECONDARY),
                prio_row,
                ft.Text("Kategoria:", size=11, color=COLOR_TEXT_SECONDARY),
                cat_row,
                ft.Text("Termin:", size=11, color=COLOR_TEXT_SECONDARY),
                deadline_row,
                ft.Text("Gdzie trafi:", size=11, color=COLOR_TEXT_SECONDARY),
                daily_row,
                ft.Row([
                    ft.Button("✓ Zatwierdź", icon=ft.Icons.CHECK,
                              bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY, height=touch,
                              on_click=lambda e, t=task_id: apply_inbox_triage(t))
                ], alignment=ft.MainAxisAlignment.END)
            ], spacing=6)
        )

    def close_dialogs():
        quick_dialog.open = master_dialog.open = subtask_dialog.open = False
        edit_task_dialog.open = delete_task_dialog.open = edit_subtask_dialog.open = clear_archive_dialog.open = False
        end_of_day_dialog.open = import_confirm_dialog.open = edit_routine_dialog.open = False
        day_rollover_dialog.open = False
        page.update()

    # Kolejność overlay jest używana przez testy (indeksy 3,4,5) — nowe okna tylko na końcu.
    page.overlay.extend([quick_dialog, master_dialog, subtask_dialog, edit_task_dialog, delete_task_dialog,
                         edit_subtask_dialog, clear_archive_dialog, end_of_day_dialog, import_confirm_dialog,
                         edit_routine_dialog, day_rollover_dialog])

    def open_quick_modal(e):
        quick_dump_field.value = ""
        quick_dialog.open = True
        page.update()

    def open_master_modal(e):
        nonlocal selected_new_prio
        master_title_field.value = ""
        selected_new_prio = "B"
        build_modal_prio_buttons()
        master_dialog.open = True
        page.update()

    # --- PRZECIĄGANIE ---
    def set_active_drag(task_id):
        nonlocal active_drag_task_id; active_drag_task_id = task_id

    def resolve_dragged_task_id(e):
        if active_drag_task_id is not None: return active_drag_task_id
        try:
            ctrl = page.get_control(e.src_id)
            if ctrl and getattr(ctrl, "data", None): return int(ctrl.data)
        except:
            pass
        return None

    def move_task_between_lists(task_id, target_daily):
        nonlocal active_drag_task_id
        active_drag_task_id = None
        if task_id is None: return
        prio = get_task_priority(task_id)
        if target_daily == 1 and prio == "A" and count_daily_priority_a() >= max_a_limit:
            warning_text.value = f"⚠️ Blokada: Masz już {max_a_limit} zadania 'A' na Daily!"
            page.update()
            return

        set_daily_db(task_id, target_daily)
        status_text.value = "✓ Przeniesiono"
        warning_text.value = ""
        refresh_view()

    # --- NAWIGACJA (Błyskawiczne przełączanie bez blokowania sieci) ---
    def switch_view(view_name):
        nonlocal current_view, open_prio_task_id
        current_view = view_name
        open_prio_task_id = None
        status_text.value = warning_text.value = ""
        ota_restart_hint.value = ""
        refresh_view()

    def highlight_nav(box, is_hover, default_border=None):
        box.border = ft.Border.all(2, COLOR_PRIO_B) if is_hover else default_border
        page.update()

    daily_count_text = ft.Text("0", size=11, color=COLOR_TEXT_PRIMARY, weight=ft.FontWeight.BOLD)
    master_count_text = ft.Text("0", size=11, color=COLOR_TEXT_PRIMARY, weight=ft.FontWeight.BOLD)
    daily_count_badge = ft.Container(content=daily_count_text, bgcolor=COLOR_BG_SURFACE, padding=ft.Padding.symmetric(horizontal=8, vertical=2), border_radius=10)
    master_count_badge = ft.Container(content=master_count_text, bgcolor=COLOR_BG_SURFACE, padding=ft.Padding.symmetric(horizontal=8, vertical=2), border_radius=10)

    daily_mob_count_text = ft.Text("", size=10, color=COLOR_PRIO_B, weight=ft.FontWeight.BOLD)
    master_mob_count_text = ft.Text("", size=10, color=COLOR_PRIO_C, weight=ft.FontWeight.BOLD)

    def create_pc_nav(icon, label, view, badge=None):
        row_items = [ft.Icon(icon, size=18, color=COLOR_TEXT_PRIMARY), ft.Text(label, size=14, weight=ft.FontWeight.W_500, color=COLOR_TEXT_PRIMARY)]
        if badge:
            row_items.extend([ft.Container(expand=True), badge])
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            border_radius=8,
            on_click=lambda e, v=view: switch_view(v),
            content=ft.Row(row_items, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10)
        )

    daily_pc_box = create_pc_nav(ft.Icons.WB_SUNNY_OUTLINED, "Daily", "daily", daily_count_badge)
    master_pc_box = create_pc_nav(ft.Icons.LIST_ALT, "Master", "master", master_count_badge)
    settings_pc_box = create_pc_nav(ft.Icons.SETTINGS_OUTLINED, "Ustawienia", "settings")
    archive_pc_box = create_pc_nav(ft.Icons.EMOJI_EVENTS_OUTLINED, "Sukcesy", "archive")

    daily_pc_target = ft.DragTarget(group="tasks", content=daily_pc_box,
                                    on_will_accept=lambda e: highlight_nav(daily_pc_box, True),
                                    on_leave=lambda e: highlight_nav(daily_pc_box, False),
                                    on_accept=lambda e: (highlight_nav(daily_pc_box, False),
                                                         move_task_between_lists(resolve_dragged_task_id(e), 1)))
    master_pc_target = ft.DragTarget(group="tasks", content=master_pc_box,
                                     on_will_accept=lambda e: highlight_nav(master_pc_box, True),
                                     on_leave=lambda e: highlight_nav(master_pc_box, False),
                                     on_accept=lambda e: (highlight_nav(master_pc_box, False),
                                                          move_task_between_lists(resolve_dragged_task_id(e), 0)))

    def create_mob_nav(icon, label, view, count_text_ctrl=None):
        label_row = [ft.Text(label, size=11, weight=ft.FontWeight.W_500)]
        if count_text_ctrl:
            label_row.append(count_text_ctrl)
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=6, horizontal=10),
            border_radius=10,
            on_click=lambda e, v=view: switch_view(v),
            content=ft.Column([
                ft.Icon(icon, size=20),
                ft.Row(label_row, spacing=3, alignment=ft.MainAxisAlignment.CENTER, tight=True)
            ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2, tight=True)
        )

    daily_mob_box = create_mob_nav(ft.Icons.WB_SUNNY_OUTLINED, "Daily", "daily", daily_mob_count_text)
    master_mob_box = create_mob_nav(ft.Icons.LIST_ALT, "Master", "master", master_mob_count_text)
    settings_mob_box = create_mob_nav(ft.Icons.SETTINGS_OUTLINED, "Opcje", "settings")
    archive_mob_box = create_mob_nav(ft.Icons.EMOJI_EVENTS_OUTLINED, "Sukcesy", "archive")

    daily_mob_target = ft.DragTarget(group="tasks", content=daily_mob_box,
                                     on_will_accept=lambda e: highlight_nav(daily_mob_box, True),
                                     on_leave=lambda e: highlight_nav(daily_mob_box, False),
                                     on_accept=lambda e: (highlight_nav(daily_mob_box, False),
                                                          move_task_between_lists(resolve_dragged_task_id(e), 1)))
    master_mob_target = ft.DragTarget(group="tasks", content=master_mob_box,
                                      on_will_accept=lambda e: highlight_nav(master_mob_box, True),
                                      on_leave=lambda e: highlight_nav(master_mob_box, False),
                                      on_accept=lambda e: (highlight_nav(master_mob_box, False),
                                                           move_task_between_lists(resolve_dragged_task_id(e), 0)))

    version_badge = ft.Container(
        content=ft.Row([
            ft.Icon(ft.Icons.BOLT, size=13, color=COLOR_SUCCESS),
            ft.Text(f"OTA v{current_ver}", size=11, color=COLOR_SUCCESS, weight=ft.FontWeight.BOLD)
        ], spacing=4, tight=True),
        bgcolor="#10b9811c", padding=ft.Padding.symmetric(horizontal=8, vertical=4), border_radius=6
    )
    sync_badge_text = ft.Text("", size=11, color=COLOR_TEXT_SECONDARY)
    sync_badge_icon = ft.Icon(ft.Icons.STORAGE, size=13, color=COLOR_TEXT_SECONDARY)
    sync_badge = ft.Container(
        content=ft.Row([sync_badge_icon, sync_badge_text], spacing=4, tight=True),
        bgcolor=COLOR_BG_SURFACE, padding=ft.Padding.symmetric(horizontal=8, vertical=4), border_radius=6
    )

    def on_sync_state(state):
        """Bieżący stan zapisu widoczny od razu w pasku bocznym (nie tylko w Ustawieniach)."""
        try:
            status = state.get("status")
            if status == "error":
                sync_badge_text.value = "Błąd zapisu"
                sync_badge_text.color = COLOR_PRIO_A
                sync_badge_icon.name = ft.Icons.CLOUD_OFF
                sync_badge_icon.color = COLOR_PRIO_A
                warning_text.value = f"⚠️ Zapis nieudany: {state.get('message', '')}"
            elif status == "ok":
                sync_badge_text.value = f"Zapisano {datetime.now().strftime('%H:%M')}"
                sync_badge_text.color = COLOR_SUCCESS
                sync_badge_icon.name = ft.Icons.CLOUD_DONE
                sync_badge_icon.color = COLOR_SUCCESS
            elif status == "syncing":
                sync_badge_text.value = "Zapisywanie..."
                sync_badge_text.color = COLOR_PRIO_B
                sync_badge_icon.name = ft.Icons.CLOUD_UPLOAD
                sync_badge_icon.color = COLOR_PRIO_B
            page.update()
        except Exception:
            pass

    register_sync_status_callback(on_sync_state)

    sidebar = ft.Container(
        width=210, bgcolor=COLOR_BG_CARD, padding=12,
        content=ft.Column([
            ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.TASK_ALT, size=20, color=COLOR_PRIO_B),
                    ft.Text("Master ADHD", size=14, weight=ft.FontWeight.BOLD, color=COLOR_TEXT_PRIMARY)
                ], spacing=8),
                padding=ft.Padding.only(bottom=8, left=4)
            ),
            daily_pc_target,
            master_pc_target,
            archive_pc_box,
            # Elastyczna przestrzeń: mini-minutnik tuż nad numerem wersji.
            ft.Container(expand=True, content=mini_timer_pc, alignment=ft.Alignment(0, 1)),
            version_badge,
            sync_badge,
            ft.Divider(height=1, color=COLOR_BG_BORDER),
            settings_pc_box
        ], expand=True, spacing=4)
    )

    bottom_nav = ft.Container(
        bgcolor=COLOR_BG_CARD,
        padding=ft.Padding.symmetric(vertical=4, horizontal=6),
        border=ft.Border(top=ft.BorderSide(1, COLOR_BG_BORDER)),
        content=ft.Row([daily_mob_target, master_mob_target, archive_mob_box, settings_mob_box],
                       alignment=ft.MainAxisAlignment.SPACE_AROUND)
    )

    # `mini_timer_mob_wrapper` (Divider + licznik mobile) jest tworzony razem
    # z kontrolkami mini-minutnika wyżej — tutaj tylko korzystamy z niego w layoucie.

    content_column = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO)
    content_area = ft.Container(content=content_column, expand=True, padding=15)
    root_layout = ft.Container(expand=True)

    def toggle_prio_menu(task_id):
        nonlocal open_prio_task_id;
        open_prio_task_id = None if open_prio_task_id == task_id else task_id
        refresh_view()

    def change_priority(task_id, new_prio, is_daily_task):
        nonlocal open_prio_task_id;
        open_prio_task_id = None
        if is_daily_task and new_prio == "A" and get_task_priority(
                task_id) != "A" and count_daily_priority_a() >= max_a_limit:
            warning_text.value = f"⚠️ Limit osiągnięty! Na Daily nie może być więcej niż {max_a_limit} zadań 'A'."
            refresh_view()
            return
        update_priority_db(task_id, new_prio)
        refresh_view()

    def handle_task_check(task_id, is_checked, subtask_idx, subtasks):
        if is_checked:
            play_sound_feedback("task_done")
            trigger_haptic_feedback(page, haptic_service, "task_done")
        if subtask_idx is not None:
            subtasks[subtask_idx]["is_done"] = 1 if is_checked else 0
            update_subtasks_db(task_id, subtasks)
            if is_checked and all(st["is_done"] == 1 for st in subtasks):
                toggle_done_db(task_id, 1)
            elif not is_checked:
                toggle_done_db(task_id, 0)
        else:
            toggle_done_db(task_id, 1 if is_checked else 0)
        refresh_view()

    def handle_routine_toggle(routine_id, is_done):
        """Zaznaczenie rutyny = krótka wibracja/dźwięk, odznaczenie = cicho."""
        toggle_routine_db(routine_id, is_done)
        if is_done:
            play_sound_feedback("task_done")
            trigger_haptic_feedback(page, haptic_service, "task_done")
        refresh_view()

    def next_subtask(task_id):
        if task_id in active_subtask_idx_map:
            active_subtask_idx_map[task_id] += 1
        refresh_view()

    def update_max_a(new_limit):
        nonlocal max_a_limit
        max_a_limit = new_limit
        set_config("max_a_limit", new_limit)
        refresh_view()

    def refresh_view():
        mob = is_mobile()
        content_area.padding = 14 if mob else 25
        update_mini_timer()

        def _touch(default):
            """Obszar klikalny akcji: na telefonie min. 44 px, na PC zachowuje gęstość."""
            return TOUCH_TARGET_MIN if mob else default

        daily_all = get_tasks(is_daily_view=True)
        master_all = get_tasks(is_daily_view=False)
        daily_count_text.value = str(len(daily_all))
        master_count_text.value = str(len(master_all))
        daily_mob_count_text.value = f"({len(daily_all)})" if daily_all else ""
        master_mob_count_text.value = f"({len(master_all)})" if master_all else ""

        for box, v in ((daily_pc_box, "daily"), (master_pc_box, "master"), (settings_pc_box, "settings"), (archive_pc_box, "archive")):
            is_active = (current_view == v)
            box.bgcolor = COLOR_BG_SURFACE if is_active else None
            box.border = ft.Border(left=ft.BorderSide(3, COLOR_PRIO_B if v in ("daily", "archive") else COLOR_PRIO_C if v == "master" else COLOR_TEXT_PRIMARY)) if is_active else None

        for box, v in ((daily_mob_box, "daily"), (master_mob_box, "master"), (settings_mob_box, "settings"), (archive_mob_box, "archive")):
            is_active = (current_view == v)
            box.bgcolor = COLOR_BG_SURFACE if is_active else None

        mode = get_config("sync_mode", "none")
        if mode == "gist":
            sync_badge_text.value = "Chmura Gist"
            sync_badge_text.color = ft.Colors.CYAN_400
            sync_badge_icon.name = ft.Icons.CLOUD_DONE
            sync_badge_icon.color = ft.Colors.CYAN_400
        elif mode == "ha_server":
            sync_badge_text.value = "Serwer HA"
            sync_badge_text.color = COLOR_SUCCESS
            sync_badge_icon.name = ft.Icons.HOME_ROUNDED
            sync_badge_icon.color = COLOR_SUCCESS
        elif mode == "folder":
            sync_badge_text.value = "Dysk lokalny"
            sync_badge_text.color = COLOR_PRIO_C
            sync_badge_icon.name = ft.Icons.FOLDER_SPECIAL
            sync_badge_icon.color = COLOR_PRIO_C
        else:
            sync_badge_text.value = "Tryb lokalny"
            sync_badge_text.color = COLOR_TEXT_SECONDARY
            sync_badge_icon.name = ft.Icons.STORAGE
            sync_badge_icon.color = COLOR_TEXT_SECONDARY

        # Błąd ostatniego zapisu jest ważniejszy niż nazwa trybu — nie chowamy go.
        if get_sync_state().get("status") == "error":
            sync_badge_text.value = "Błąd zapisu"
            sync_badge_text.color = COLOR_PRIO_A
            sync_badge_icon.name = ft.Icons.CLOUD_OFF
            sync_badge_icon.color = COLOR_PRIO_A

        if mob:
            root_layout.content = ft.Column([content_area, mini_timer_mob_wrapper, bottom_nav],
                                            expand=True, spacing=0)
        else:
            root_layout.content = ft.Row([sidebar, ft.VerticalDivider(width=1, color=ft.Colors.GREY_800), content_area],
                                         expand=True, spacing=0)

        tasks_column.controls.clear()
        routines_row.controls.clear()

        if current_view == "settings":
            title_text.value = f"Ustawienia (v{current_ver})"
            rout_db = get_routines()
            rout_list = ft.Column([
                ft.Container(
                    bgcolor=COLOR_BG_SURFACE, padding=ft.Padding.symmetric(horizontal=12, vertical=8), border_radius=8,
                    content=ft.Row([
                        ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, size=16, color=COLOR_SUCCESS),
                        ft.Text(r[1], expand=True, size=13, color=COLOR_TEXT_PRIMARY),
                        ft.Text(routine_days_label(r[3]), size=11, color=COLOR_PRIO_C, weight=ft.FontWeight.W_500,
                                tooltip=", ".join(WEEKDAY_FULL[d] for d in r[3])) if r[3] else ft.Text("Codziennie", size=11, color=COLOR_TEXT_SECONDARY),
                        ft.IconButton(icon=ft.Icons.EDIT_OUTLINED, icon_size=17, icon_color=COLOR_TEXT_SECONDARY,
                                      tooltip="Edytuj rutynę",
                                      on_click=lambda e, rid=r[0], rtitle=r[1], rdays=r[3]: open_edit_routine_dialog(rid, rtitle, rdays)),
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_size=18, icon_color=COLOR_PRIO_A, tooltip="Usuń rutynę",
                                      on_click=lambda e, rid=r[0]: (delete_routine_db(rid), refresh_view()))
                    ])
                ) for r in rout_db
            ], spacing=6)

            card_routines = ft.Container(
                bgcolor=COLOR_BG_CARD, border=ft.Border.all(1, COLOR_BG_BORDER), border_radius=12, padding=14,
                content=ft.Column([
                    ft.Text("Nawyki i Rutyny (Nie obciążają limitu A)", weight=ft.FontWeight.BOLD, size=14, color=COLOR_TEXT_PRIMARY),
                    ft.Text("Zaznacz dni tygodnia, w które rutyna ma się pokazywać na Daily (odznaczone = codziennie).",
                            size=12, color=COLOR_TEXT_SECONDARY),
                    ft.Row([
                        routine_input,
                        ft.Button("Dodaj", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY, on_click=save_routine_add)
                    ]),
                    ft.Row(build_routine_day_chips(routine_add_days, toggle_routine_add_day), spacing=6),
                    rout_list if rout_db else ft.Text("Brak zdefiniowanych rutyn", size=12, color=COLOR_TEXT_SECONDARY)
                ], spacing=10)
            )

            card_limits = ft.Container(
                bgcolor=COLOR_BG_CARD, border=ft.Border.all(1, COLOR_BG_BORDER), border_radius=12, padding=14,
                content=ft.Column([
                    ft.Text("Limit kluczowych zadań 'A' na Daily:", weight=ft.FontWeight.BOLD, size=14, color=COLOR_TEXT_PRIMARY),
                    ft.Text("Ograniczenie liczby zadań najwyższego priorytetu pomaga utrzymać koncentrację (ADHD Focus).", size=12, color=COLOR_TEXT_SECONDARY),
                    ft.Row([
                        ft.Button("3 zadania (Zalecane)", bgcolor=COLOR_PRIO_B if max_a_limit == 3 else COLOR_BG_SURFACE,
                                  color=COLOR_TEXT_PRIMARY, on_click=lambda e: update_max_a(3)),
                        ft.Button("4 zadania", bgcolor=COLOR_PRIO_B if max_a_limit == 4 else COLOR_BG_SURFACE,
                                  color=COLOR_TEXT_PRIMARY, on_click=lambda e: update_max_a(4)),
                    ], spacing=10)
                ], spacing=8)
            )

            card_gist = ft.Container(
                bgcolor=COLOR_BG_CARD, border=ft.Border.all(1, COLOR_BG_BORDER), border_radius=12, padding=14,
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.CLOUD, size=18, color=ft.Colors.CYAN_400),
                        ft.Text("Chmura (GitHub Gist - bezpłatna synchronizacja)", weight=ft.FontWeight.BOLD, size=14, color=ft.Colors.CYAN_400)
                    ], spacing=8),
                    gist_token_input,
                    gist_id_input,
                    ft.Button("Włącz Chmurę Gist", bgcolor=ft.Colors.CYAN_800, color=COLOR_TEXT_PRIMARY,
                              on_click=lambda e: change_sync_mode("gist"))
                ], spacing=10)
            )

            card_other_sync = ft.Container(
                bgcolor=COLOR_BG_CARD, border=ft.Border.all(1, COLOR_BG_BORDER), border_radius=12, padding=14,
                content=ft.Column([
                    ft.Text("Inne opcje synchronizacji:", weight=ft.FontWeight.BOLD, size=14, color=COLOR_TEXT_PRIMARY),
                    ha_url_input,
                    ft.Button("Włącz Home Assistant (HA)", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY, on_click=lambda e: change_sync_mode("ha_server")),
                    folder_input,
                    ft.Button("Włącz synchronizację z folderem (Dysk PC)", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY, on_click=lambda e: change_sync_mode("folder")),
                    ft.Button("Tylko lokalnie (Brak synchronizacji)", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_SECONDARY, on_click=lambda e: change_sync_mode("none")),
                    ft.Divider(height=1, color=ft.Colors.GREY_800),
                    ft.Text("🔧 Reset i Synchronizacja", weight=ft.FontWeight.BOLD, size=13, color=COLOR_TEXT_PRIMARY),
                    ft.Row([
                        ft.Button("⚡ Ręczny reset synchronizacji", bgcolor=COLOR_BG_SURFACE, color=COLOR_PRIO_B,
                                  on_click=lambda e: (reset_sync_on_exit(), refresh_view())),
                        ft.Button("💾 Zapisz tylko lokalnie", bgcolor="#ef444422", color=COLOR_PRIO_A,
                                  on_click=lambda e: _sync_only_local())
                    ], spacing=8)
                ], spacing=8)
            )

            card_ota = ft.Container(
                bgcolor=COLOR_BG_CARD, border=ft.Border.all(2, "#30363d"), border_radius=12, padding=14,
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, size=18, color=COLOR_PRIO_B),
                        ft.Text("Aktualizacje OTA (GitHub)", weight=ft.FontWeight.BOLD, size=14, color=COLOR_TEXT_PRIMARY)
                    ], spacing=8),
                    ft.Text(f"Wersja wbudowana: v{get_bundled_version()} | Aktywna (OTA): v{current_ver}", size=12, color=COLOR_TEXT_SECONDARY),
                    ft.Row([
                        ft.Button("🔄 Sprawdź i wymuś aktualizację z GitHuba", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY,
                                  on_click=trigger_manual_ota),
                        # Spacer (nie expand - nie tworzymy pustego obszaru na PC)
                        ft.Container(width=15),
                        ft.Button("🛑 Zamknij aplikację (Wymuś restart)", bgcolor="#ef444422", color=COLOR_PRIO_A,
                                  on_click=lambda e: os._exit(0))
                    ], wrap=True, spacing=10)
                ], spacing=10)
            )

            def toggle_sound(e):
                set_config("sound_enabled", "1" if e.control.value else "0")
                refresh_view()

            card_sound = ft.Container(
                bgcolor=COLOR_BG_CARD, border=ft.Border.all(1, COLOR_BG_BORDER), border_radius=12, padding=14,
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.VOLUME_UP_ROUNDED, size=18, color=COLOR_PRIO_B),
                        ft.Text("Dźwięk i wibracja", weight=ft.FontWeight.BOLD, size=14, color=COLOR_TEXT_PRIMARY)
                    ], spacing=8),
                    ft.Switch(
                        label="Dźwięk i wibracja przy sukcesie i końcu minutnika",
                        value=get_config("sound_enabled", "1") == "1",
                        active_color=COLOR_SUCCESS,
                        on_change=toggle_sound
                    ),
                    ft.Text(
                        "Na Windows sygnałem jest krótki dźwięk systemowy. Na telefonie aplikacja "
                        "wibruje (Flet nie ma tam modułu audio) — minutnik działa tak samo. "
                        "Wyłączenie tej opcji wycisza dźwięk i wibrację.",
                        size=11, color=COLOR_TEXT_SECONDARY
                    )
                ], spacing=8)
            )

            # --- NOWA KARTA: Tryb dodawania do Daily (Drag vs Tap) ---
            def toggle_daily_add_mode(e):
                new_mode = "tap" if e.control.value else "drag"
                set_config("daily_add_mode", new_mode)
                refresh_view()

            card_daily_mode = ft.Container(
                bgcolor=COLOR_BG_CARD, border=ft.Border.all(1, COLOR_BG_BORDER), border_radius=12, padding=14,
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.WB_SUNNY_OUTLINED, size=18, color=COLOR_PRIO_B),
                        ft.Text("Dodawanie do Daily (Słońce)", weight=ft.FontWeight.BOLD, size=14, color=COLOR_TEXT_PRIMARY)
                    ], spacing=8),
                    ft.Text(
                        "Wybierz, jak dodawać zadania z Master listy na Daily:",
                        size=12, color=COLOR_TEXT_SECONDARY
                    ),
                    ft.Row([
                        ft.Text("Przeciągnij (Drag & Drop)", size=13, color=COLOR_TEXT_PRIMARY, expand=True),
                        ft.Switch(
                            value=get_config("daily_add_mode", "drag") == "tap",
                            active_color=COLOR_PRIO_B,
                            on_change=toggle_daily_add_mode,
                            tooltip="Włączony = Kliknij słońce | Wyłączony = Przeciągnij słońce"
                        ),
                        ft.Text("Kliknij słońce (Tap)", size=13, color=COLOR_TEXT_PRIMARY),
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
                    ft.Text(
                        "💡 Słońce przy zadaniu: żółte = na Daily, szare = nie na Daily. Zadanie zostaje na obu listach.",
                        size=11, color=COLOR_TEXT_SECONDARY
                    ),
                ], spacing=8)
            )

            # --- KARTA: Przełom dnia (Sprint 2.1) ---
            def set_day_rollover_mode(mode):
                set_config("day_rollover_mode", mode)
                refresh_view()

            rollover_pills = []
            for mode_val, mode_lbl in (
                (DAY_ROLLOVER_ASK, "Pytaj mnie"),
                (DAY_ROLLOVER_AUTO_KEEP, "Auto: zostaw"),
                (DAY_ROLLOVER_AUTO_MASTER, "Auto: do Mastera"),
            ):
                is_active = (get_day_rollover_mode() == mode_val)
                rollover_pills.append(ft.Container(
                    content=ft.Text(mode_lbl, size=11, weight=ft.FontWeight.BOLD,
                                    color=COLOR_TEXT_PRIMARY if is_active else COLOR_TEXT_SECONDARY),
                    bgcolor="#3b82f633" if is_active else "#21262d",
                    border=ft.Border.all(1, COLOR_PRIO_B if is_active else COLOR_BG_BORDER),
                    border_radius=8,
                    padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                    on_click=lambda e, mv=mode_val: set_day_rollover_mode(mv)
                ))

            card_day_rollover = ft.Container(
                bgcolor=COLOR_BG_CARD, border=ft.Border.all(1, COLOR_BG_BORDER), border_radius=12, padding=14,
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.NIGHTLIGHT_ROUND, size=18, color=COLOR_PRIO_B),
                        ft.Text("Przełom dnia", weight=ft.FontWeight.BOLD, size=14, color=COLOR_TEXT_PRIMARY)
                    ], spacing=8),
                    ft.Text("Co zrobić z niezrobionymi zadaniami, gdy zaczyna się nowy dzień:",
                            size=12, color=COLOR_TEXT_SECONDARY),
                    ft.Row(rollover_pills, spacing=6, wrap=True),
                    ft.Text(
                        "„Pytaj mnie” pokazuje spokojny ekran wyboru (zostaw / do Mastera / usuń z Daily). "
                        "Nic nie zniknie po cichu, a zrobione zadania trafiają do Dziennika Sukcesów.",
                        size=11, color=COLOR_TEXT_SECONDARY
                    )
                ], spacing=8)
            )

            # --- KARTA: Terminy i przypomnienia ---
            def toggle_deadline_reminders(e):
                set_config("deadline_reminders_enabled", "1" if e.control.value else "0")
                refresh_view()

            def set_deadline_lead_days(days):
                set_config("deadline_reminder_days", str(days))
                # Nowe ustawienie ma zadziałać od razu, bez czekania do jutra.
                set_config("deadline_card_hidden_date", "")
                refresh_view()

            def toggle_deadline_repeat(e):
                set_config("deadline_reminder_repeat_day_before", "1" if e.control.value else "0")
                refresh_view()

            lead_days = deadline_reminder_days()
            lead_pills = []
            for d_val in (14, 7, 3):
                is_active = (lead_days == d_val)
                lead_pills.append(
                    ft.Container(
                        content=ft.Text(f"{d_val} dni", size=11, weight=ft.FontWeight.BOLD,
                                        color=COLOR_TEXT_PRIMARY if is_active else COLOR_TEXT_SECONDARY),
                        bgcolor="#f59e0b33" if is_active else "#21262d",
                        border=ft.Border.all(1, COLOR_PRIO_B if is_active else COLOR_BG_BORDER),
                        border_radius=8,
                        padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                        on_click=lambda e, dv=d_val: set_deadline_lead_days(dv)
                    )
                )

            card_deadlines = ft.Container(
                bgcolor=COLOR_BG_CARD, border=ft.Border.all(1, COLOR_BG_BORDER), border_radius=12, padding=14,
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.HOURGLASS_BOTTOM_ROUNDED, size=18, color=COLOR_PRIO_B),
                        ft.Text("Terminy i przypomnienia", weight=ft.FontWeight.BOLD, size=14, color=COLOR_TEXT_PRIMARY)
                    ], spacing=8),
                    ft.Switch(
                        label="Spokojna karta „Nadchodzące terminy” na Daily",
                        value=get_config("deadline_reminders_enabled", "1") == "1",
                        active_color=COLOR_SUCCESS,
                        on_change=toggle_deadline_reminders
                    ),
                    ft.Text("Ile dni wcześniej pokazywać terminy:", size=12, color=COLOR_TEXT_SECONDARY),
                    ft.Row(lead_pills, spacing=6),
                    ft.Switch(
                        label="Przypomnij też dzień przed terminem",
                        value=get_config("deadline_reminder_repeat_day_before", "1") == "1",
                        active_color=COLOR_PRIO_B,
                        on_change=toggle_deadline_repeat
                    ),
                    ft.Text(
                        "To przypomnienie w aplikacji: jedna cicha karta nad zadaniami, raz na dobę, "
                        "z opcją „Ukryj na dziś”. Bez wyskakujących okien i bez zmiany priorytetów. "
                        "Powiadomienia systemowe na telefon pojawią się w kolejnym etapie.",
                        size=11, color=COLOR_TEXT_SECONDARY
                    )
                ], spacing=8)
            )

            def handle_export_json(e):
                ok, path = export_database_to_json_file()
                if ok:
                    status_text.value = "✓ Zapisano czytelny plik kopii zapasowej JSON!"
                    warning_text.value = f"Lokalizacja: {path}"
                else:
                    warning_text.value = f"Błąd eksportu: {path}"
                refresh_view()

            def handle_restore_snapshot(e):
                ok, msg = restore_pre_sync_backup()
                if ok:
                    status_text.value = f"✓ {msg}"
                    warning_text.value = ""
                else:
                    warning_text.value = f"⚠️ {msg}"
                refresh_view()

            def handle_export_logs(e):
                ok, path = export_logs()
                if ok:
                    status_text.value = "✓ Zapisano plik logów"
                    warning_text.value = f"Logi: {path}"
                else:
                    warning_text.value = f"⚠️ {path}"
                refresh_view()

            snapshot_count = len(list_snapshots())
            card_backup = ft.Container(
                bgcolor=COLOR_BG_CARD, border=ft.Border.all(1, COLOR_BG_BORDER), border_radius=12, padding=14,
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.SECURITY_ROUNDED, size=18, color=COLOR_PRIO_B),
                        ft.Text("Kopia Zapasowa i Bezpieczeństwo", weight=ft.FontWeight.BOLD, size=14, color=COLOR_TEXT_PRIMARY)
                    ], spacing=8),
                    ft.Text(
                        "Każda synchronizacja i każda operacja destrukcyjna (koniec dnia, czyszczenie "
                        f"archiwum, import) tworzy snapshot bazy. Dostępnych snapshotów: {snapshot_count}.",
                        size=12, color=COLOR_TEXT_SECONDARY
                    ),
                    ft.Row([
                        ft.Button("📥 Eksportuj bazę do pliku JSON", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY, on_click=handle_export_json),
                        ft.Button("🛡️ Przywróć ostatni snapshot", bgcolor=COLOR_BG_SURFACE, color=COLOR_PRIO_B, on_click=handle_restore_snapshot),
                        ft.Button("📄 Eksportuj logi", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_SECONDARY, on_click=handle_export_logs)
                    ], wrap=True, spacing=10),
                    ft.Divider(height=1, color=ft.Colors.GREY_800),
                    ft.Text("Import z pliku JSON", weight=ft.FontWeight.BOLD, size=13, color=COLOR_TEXT_PRIMARY),
                    import_path_input,
                    ft.Row([
                        ft.Button("📤 Importuj z JSON", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY, on_click=open_import_dialog),
                        ft.Button("📂 Użyj najnowszego backupu", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_SECONDARY,
                                  on_click=lambda e: (setattr(import_path_input, "value", latest_json_backup()), refresh_view()))
                    ], wrap=True, spacing=10)
                ], spacing=10)
            )

            # Czyszczenie kontenera przed dodaniem kart ustawień (unikamy gromadzenia starej zawartości)
            content_column.controls.clear()
            
            content_column.controls = [
                ft.Row([title_text, ft.Container(expand=True), sync_badge if mob else ft.Container()]),
                status_text, warning_text,
                ota_restart_hint,
                card_ota,
                card_sound,
                card_deadlines,
                card_daily_mode,
                card_day_rollover,
                card_backup,
                card_routines,
                card_limits,
                card_gist,
                card_other_sync
            ]
            page.update()
            return

        if current_view == "archive":
            title_text.value = "Dziennik Sukcesów"
            archive_items = get_completed_archive(limit=100)
            archive_cards = []

            for item in archive_items:
                aid, atitle, aprio, adate = item[0], item[1], item[2], item[3]
                prio_bg = COLOR_PRIO_A if aprio == "A" else COLOR_PRIO_B if aprio == "B" else COLOR_PRIO_C
                archive_cards.append(
                    ft.Container(
                        bgcolor=COLOR_BG_CARD,
                        border=ft.Border.all(1, COLOR_BG_BORDER),
                        border_radius=10,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                        content=ft.Row([
                            ft.Container(
                                content=ft.Text(aprio, size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                                bgcolor=prio_bg,
                                width=24,
                                height=24,
                                border_radius=6,
                                alignment=ft.Alignment(0, 0)
                            ),
                            ft.Icon(ft.Icons.CHECK_CIRCLE, size=18, color=COLOR_SUCCESS),
                            ft.Text(
                                atitle,
                                size=13,
                                weight=ft.FontWeight.W_500,
                                color=COLOR_TEXT_PRIMARY,
                                expand=True
                            ),
                            ft.Text(adate, size=11, color=COLOR_TEXT_SECONDARY)
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10)
                    )
                )

            empty_placeholder = ft.Container(
                padding=ft.Padding.all(30),
                alignment=ft.Alignment(0, 0),
                content=ft.Column([
                    ft.Icon(ft.Icons.EMOJI_EVENTS_OUTLINED, size=50, color=COLOR_TEXT_SECONDARY),
                    ft.Text("Brak zarchiwizowanych sukcesów", size=16, weight=ft.FontWeight.BOLD, color=COLOR_TEXT_PRIMARY),
                    ft.Text(
                        "Gdy wykonasz zadania i zresetujesz dzień (Koniec dnia), Twoje osiągnięcia zostaną zapisane tutaj zamiast bezpowrotnie znikać!",
                        size=12, color=COLOR_TEXT_SECONDARY, text_align=ft.TextAlign.CENTER
                    )
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10)
            )

            clear_btn = ft.TextButton(
                "Wyczyść archiwum",
                icon=ft.Icons.DELETE_SWEEP,
                style=ft.ButtonStyle(color=COLOR_PRIO_A),
                on_click=open_clear_archive_dialog
            ) if archive_items else ft.Container()

            content_column.controls = [
                ft.Row([
                    ft.Row([
                        ft.Icon(ft.Icons.EMOJI_EVENTS_ROUNDED, size=24, color=COLOR_PRIO_B),
                        ft.Text("Dziennik Sukcesów", size=20, weight=ft.FontWeight.BOLD, color=COLOR_TEXT_PRIMARY)
                    ], spacing=8),
                    ft.Container(expand=True),
                    clear_btn
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                status_text, warning_text,
                ft.Container(
                    content=ft.Text(f"🏆 Łącznie ukończonych zadań w archiwum: {len(archive_items)}", size=13, weight=ft.FontWeight.W_500, color=COLOR_PRIO_B),
                    bgcolor="#f59e0b16",
                    border=ft.Border.all(1, "#f59e0b33"),
                    border_radius=8,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=8)
                ) if archive_items else ft.Container(),
                ft.Column(archive_cards if archive_items else [empty_placeholder], spacing=6)
            ]
            page.update()
            return

        is_daily_mode = (current_view == "daily")
        title_text.value = "Daily" if is_daily_mode else "Master Lista"
        all_view_tasks = get_tasks(is_daily_view=is_daily_mode)
        # Sprint 2.3: zadania z Inbox nie zaśmiecają listy Master — trafiają do
        # osobnej sekcji triage'u (ukrytej, gdy Inbox jest pusty).
        inbox_tasks = [
            t for t in all_view_tasks if (t[6] or "") == INBOX_CATEGORY
        ] if not is_daily_mode else []
        # Rutyny przypisane do konkretnych dni pokazują się na Daily tylko tego dnia
        # (rutyna bez dni = codzienna). Na Daily liczymy też postęp tylko z nich.
        all_routines = get_daily_routines() if is_daily_mode else get_routines()

        if is_daily_mode:
            prog_val = calculate_progress_logic(all_view_tasks, all_routines, active_subtask_idx_map)
            progress_bar.value = prog_val
            progress_bar.color = COLOR_SUCCESS
            progress_bar.bgcolor = COLOR_BG_SURFACE
            progress_text.value = f"{int(prog_val * 100)}% ukończone"
            progress_text.color = COLOR_SUCCESS if prog_val >= 1.0 else COLOR_TEXT_PRIMARY
            progress_text.weight = ft.FontWeight.BOLD

            for r in all_routines:
                rid, rname, cdone, rdays = r[0], r[1], r[2], r[3]
                is_r_done = bool(cdone)
                routines_row.controls.append(
                    ft.Container(
                        bgcolor="#10b98122" if is_r_done else COLOR_BG_SURFACE,
                        border=ft.Border.all(1, COLOR_SUCCESS if is_r_done else COLOR_BG_BORDER),
                        padding=ft.Padding.symmetric(horizontal=10 if mob else 10,
                                                     vertical=10 if mob else 6),
                        border_radius=14,
                        height=TOUCH_TARGET_MIN if mob else None,
                        on_click=lambda e, rid=rid, cdone=cdone: handle_routine_toggle(rid, not cdone),
                        content=ft.Row([
                            ft.Icon(ft.Icons.CHECK_CIRCLE if is_r_done else ft.Icons.RADIO_BUTTON_UNCHECKED,
                                    size=18 if mob else 15,
                                    color=COLOR_SUCCESS if is_r_done else COLOR_TEXT_SECONDARY),
                            ft.Text(rname, size=14 if mob else 12,
                                    color=COLOR_TEXT_PRIMARY if is_r_done else COLOR_TEXT_SECONDARY,
                                    weight=ft.FontWeight.W_500,
                                    style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH if is_r_done else None)),
                            ft.Text(routine_days_label(rdays), size=12 if mob else 10,
                                    color=COLOR_PRIO_C, weight=ft.FontWeight.W_500,
                                    style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH if is_r_done else None)) if rdays else ft.Container()
                        ], spacing=6, tight=True,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER)
                    )
                )

        if is_daily_mode:
            inline_input_field.hint_text = "⚡ Zrzut myśli do Inbox (Enter)..."
            quick_bar = ft.Container(
                bgcolor=COLOR_BG_CARD,
                border=ft.Border.all(1, COLOR_BG_BORDER),
                border_radius=12,
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                content=ft.Row([
                    inline_input_field,
                    ft.IconButton(
                        icon=ft.Icons.ADD_CIRCLE,
                        icon_color=COLOR_PRIO_B,
                        tooltip="Zapisz do Inbox",
                        width=_touch(40),
                        height=_touch(40),
                        on_click=save_inline_input
                    )
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER)
            )
        if not is_daily_mode:
            inline_input_field.hint_text = "➕ Nowe zadanie do Master listy (Enter)..."
            quick_bar = ft.Container(
                bgcolor=COLOR_BG_CARD,
                border=ft.Border.all(1, COLOR_BG_BORDER),
                border_radius=12,
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                content=ft.Row([
                    inline_input_field,
                    ft.IconButton(
                        icon=ft.Icons.ADD_CIRCLE,
                        icon_color=COLOR_PRIO_C,
                        tooltip="Dodaj zadanie do Master listy",
                        width=_touch(40),
                        height=_touch(40),
                        on_click=save_inline_input
                    )
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER)
            )

            filtered_tasks = []
            for t in all_view_tasks:
                tid, tit, dly, dn, prio, subs, *extra = t
                cat = extra[0] if extra else "Ogólne"

                if cat == INBOX_CATEGORY:
                    continue  # Inbox ma własną sekcję triage'u

                if master_search_query:
                    q = master_search_query.lower()
                    m_title = q in tit.lower()
                    m_sub = any(q in st.get("title", "").lower() for st in subs)
                    if not (m_title or m_sub):
                        continue

                if master_filter_priority != "ALL" and prio != master_filter_priority:
                    continue

                if master_filter_status == "ACTIVE" and dn == 1:
                    continue
                if master_filter_status == "DONE" and dn == 0:
                    continue

                if master_filter_category != "ALL" and cat != master_filter_category:
                    continue

                filtered_tasks.append(t)
            if master_stale_only:
                stale_ids = get_stale_task_ids()
                filtered_tasks = [t for t in filtered_tasks if t[0] in stale_ids]
            filtered_tasks = filter_tasks_by_deadline(filtered_tasks, master_deadline_filter)
            if master_sort_mode == "DEADLINE":
                filtered_tasks = sort_tasks_by_deadline(filtered_tasks)
            all_view_tasks = filtered_tasks

        for t in all_view_tasks:
            task_id, title, is_daily, is_done, priority, subtasks, *extra = t
            category = extra[0] if extra else "Ogólne"
            task_deadline = extra[1] if len(extra) > 1 else None
            prio = priority if priority in ("A", "B", "C") else "B"
            card_border_color = COLOR_BG_BORDER if not is_done else "#21262d"

            # Dyskretny chip terminu — tylko informacja, nigdy alarm całej karty.
            deadline_chip = None
            if task_deadline:
                urgency = deadline_urgency(task_deadline)
                if urgency == "overdue":
                    chip_color, chip_border = COLOR_PRIO_A, "#ef444455"
                elif urgency in ("today", "soon"):
                    chip_color, chip_border = COLOR_PRIO_B, "#f59e0b55"
                else:
                    chip_color, chip_border = COLOR_TEXT_SECONDARY, COLOR_BG_BORDER
                deadline_chip = ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.Icons.EVENT_OUTLINED, size=14 if mob else 13, color=chip_color),
                        ft.Text(deadline_chip_text(task_deadline), size=12 if mob else 10, color=chip_color,
                                weight=ft.FontWeight.W_500),
                    ], spacing=4, tight=True),
                    bgcolor="#ef444418" if urgency == "overdue" else "#f59e0b14" if urgency in ("today", "soon") else "#21262d66",
                    border=ft.Border.all(1, chip_border),
                    border_radius=6,
                    padding=ft.Padding.symmetric(horizontal=6, vertical=4 if mob else 2),
                    tooltip=f"Termin: {format_deadline_pl(task_deadline)} (kliknij, aby zmienić)",
                    on_click=lambda e, tid=task_id, tit=title, cat=category: open_edit_task_dialog(
                        tid, tit, current_category=cat)
                )

            prio_items = [ft.Container(
                content=ft.Row([ft.Text(prio, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=13)],
                               alignment=ft.MainAxisAlignment.CENTER),
                width=32, height=30, border_radius=8,
                bgcolor=priority_colors[prio] if not is_done else COLOR_BG_BORDER,
                tooltip=f"Priorytet {prio} (Kliknij, aby zmienić)",
                on_click=lambda e, tid=task_id: toggle_prio_menu(tid)
            )]
            if open_prio_task_id == task_id:
                for other_p in ("A", "B", "C"):
                    if other_p != prio:
                        prio_items.append(ft.Container(
                            content=ft.Row([ft.Text(other_p, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=12)],
                                           alignment=ft.MainAxisAlignment.CENTER),
                            width=32, height=26, border_radius=6,
                            bgcolor=priority_colors[other_p],
                            tooltip=f"Zmień na {other_p}",
                            on_click=lambda e, tid=task_id, np=other_p, idm=is_daily_mode: change_priority(tid, np, idm)
                        ))

            # Ergonomiczny uchwyt przeciągania / słońce Daily
            handle_w = TOUCH_TARGET_MIN if mob else 30
            handle_h = TOUCH_TARGET_MIN if mob else 34
            sun_color = COLOR_SUN_ACTIVE if is_daily else COLOR_SUN_INACTIVE
            sun_icon = ft.Icon(ft.Icons.WB_SUNNY, size=20 if mob else 18, color=sun_color)
            
            if daily_add_mode == "tap":
                # Tryb TAP: kliknięcie słońca przełącza is_daily
                def toggle_daily_on_tap(e, tid=task_id, current_daily=is_daily):
                    new_daily = 0 if current_daily else 1
                    # Sprawdź limit A na Daily
                    if new_daily == 1:
                        prio = get_task_priority(tid)
                        if prio == "A" and count_daily_priority_a() >= max_a_limit:
                            warning_text.value = f"⚠️ Blokada: Masz już {max_a_limit} zadania 'A' na Daily!"
                            page.update()
                            return
                    set_daily_db(tid, new_daily)
                    status_text.value = "✓ Dodano do Daily" if new_daily else "✓ Usunięto z Daily"
                    warning_text.value = ""
                    refresh_view()
                
                drag_handle = ft.Container(
                    content=sun_icon,
                    width=handle_w, height=handle_h, bgcolor=COLOR_BG_SURFACE, border_radius=8,
                    alignment=ft.Alignment.CENTER,
                    tooltip="Kliknij, aby dodać/usunąć z Daily" if not is_daily else "Kliknij, aby usunąć z Daily",
                    on_click=toggle_daily_on_tap,
                    ink=True
                )
            else:
                # Tryb DRAG: przeciągnij słońce na nawigację Daily/Master
                drag_handle = ft.Draggable(
                    group="tasks",
                    data=str(task_id),
                    content=ft.Container(
                        content=sun_icon,
                        width=handle_w, height=handle_h, bgcolor=COLOR_BG_SURFACE, border_radius=8,
                        alignment=ft.Alignment.CENTER,
                        tooltip="Przeciągnij na Daily/Master"
                    ),
                    content_when_dragging=ft.Container(
                        width=handle_w, height=handle_h, bgcolor=COLOR_BG_SURFACE, border_radius=8, opacity=0.3
                    ),
                    content_feedback=ft.Container(
                        padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                        bgcolor=COLOR_BG_CARD,
                        border=ft.Border.all(1, COLOR_PRIO_B),
                        border_radius=10,
                        shadow=ft.BoxShadow(blur_radius=10, color=ft.Colors.BLACK_54),
                        content=ft.Text(title, color=COLOR_TEXT_PRIMARY, weight=ft.FontWeight.BOLD, size=14)
                    ),
                    on_drag_start=lambda e, tid=task_id: set_active_drag(tid)
                )

            if is_daily_mode:
                if subtasks:
                    first_undone = next((i for i, st in enumerate(subtasks) if st["is_done"] == 0), len(subtasks) - 1)

                    if task_id not in active_subtask_idx_map:
                        active_subtask_idx_map[task_id] = first_undone
                    else:
                        if first_undone < active_subtask_idx_map[task_id]:
                            active_subtask_idx_map[task_id] = first_undone
                        elif active_subtask_idx_map[task_id] >= len(subtasks):
                            active_subtask_idx_map[task_id] = len(subtasks) - 1

                    idx = active_subtask_idx_map[task_id]
                    current_st = subtasks[idx]
                    step_done = bool(current_st["is_done"])

                    step_badge = ft.Container(
                        content=ft.Text(f"{idx + 1}/{len(subtasks)}", size=10, color=COLOR_PRIO_C, weight=ft.FontWeight.BOLD),
                        bgcolor="#3b82f622", padding=ft.Padding.symmetric(horizontal=6, vertical=2), border_radius=6
                    )

                    edit_btn = ft.IconButton(
                        icon=ft.Icons.EDIT_OUTLINED,
                        icon_size=16,
                        icon_color=COLOR_TEXT_SECONDARY,
                        tooltip="Edytuj zadanie",
                        width=_touch(28),
                        height=_touch(28),
                        on_click=lambda e, tid=task_id, tit=title, cat=category: open_edit_task_dialog(tid, tit, current_category=cat)
                    )
                    del_btn = ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_size=16,
                        icon_color=COLOR_PRIO_A,
                        tooltip="Usuń zadanie",
                        width=_touch(28),
                        height=_touch(28),
                        on_click=lambda e, tid=task_id, tit=title: delete_task_now(tid, tit)
                    )

                    step_checkbox = ft.Checkbox(
                        value=step_done,
                        active_color=COLOR_SUCCESS,
                        on_change=lambda e, tid=task_id, s_idx=idx, s_list=subtasks: handle_task_check(
                            tid, e.control.value, s_idx, s_list
                        )
                    )
                    step_title_ctrl = ft.Container(
                        content=ft.Text(
                            current_st["title"],
                            size=14,
                            weight=ft.FontWeight.W_500,
                            color=COLOR_TEXT_SECONDARY if step_done else COLOR_TEXT_PRIMARY,
                            style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH if step_done else None),
                            no_wrap=False
                        ),
                        expand=True,
                        on_click=lambda e, tid=task_id, s_idx=idx, s_list=subtasks, sd=step_done: handle_task_check(
                            tid, not sd, s_idx, s_list
                        )
                    )
                    edit_step_btn = ft.IconButton(
                        icon=ft.Icons.EDIT_NOTE,
                        icon_size=16,
                        icon_color=COLOR_TEXT_SECONDARY,
                        tooltip="Edytuj ten krok",
                        width=_touch(26),
                        height=_touch(26),
                        on_click=lambda e, tid=task_id, i=idx, subs=subtasks: open_edit_subtask_dialog(tid, i, subs)
                    )

                    if mob:
                        top_row = ft.Row([
                            ft.Column(prio_items, spacing=3, tight=True),
                            ft.Column([
                                ft.Row([
                                    ft.Text(title, size=12, color=readable_text_color(COLOR_BG_CARD), weight=ft.FontWeight.W_500),
                                    step_badge
                                ], spacing=6, tight=True),
                                ft.Row([
                                    step_checkbox,
                                    step_title_ctrl,
                                    edit_step_btn
                                ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=6)
                            ], spacing=1, expand=True),
                            drag_handle
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8)

                        action_items = []
                        if current_st["is_done"] == 1 and idx < len(subtasks) - 1:
                            action_items.append(
                                ft.TextButton(
                                    "Następny krok",
                                    icon=ft.Icons.ARROW_FORWARD,
                                    style=ft.ButtonStyle(color=COLOR_SUCCESS),
                                    on_click=lambda e, tid=task_id: next_subtask(tid)
                                )
                            )
                        action_items.append(ft.Container(expand=True))
                        action_items.extend([edit_btn, del_btn])
                        action_row = ft.Row(action_items, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=6)

                        task_content = ft.Container(
                            bgcolor=COLOR_BG_CARD,
                            border=ft.Border.all(1, card_border_color),
                            border_radius=12,
                            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                            content=ft.Column([top_row, action_row], spacing=4)
                        )
                    else:
                        row_controls = [
                            ft.Column(prio_items, spacing=3, tight=True),
                            ft.Column([
                                ft.Row([
                                    ft.Text(title, size=12, color=readable_text_color(COLOR_BG_CARD), weight=ft.FontWeight.W_500),
                                    step_badge
                                ], spacing=6, tight=True),
                                ft.Row([
                                    step_checkbox,
                                    step_title_ctrl,
                                    edit_step_btn
                                ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=6)
                            ], spacing=1, expand=True)
                        ]
                        if current_st["is_done"] == 1 and idx < len(subtasks) - 1:
                            row_controls.append(
                                ft.IconButton(
                                    icon=ft.Icons.ARROW_FORWARD,
                                    icon_color=COLOR_SUCCESS,
                                    tooltip="Kolejny krok",
                                    on_click=lambda e, tid=task_id: next_subtask(tid)
                                )
                            )
                        row_controls.append(edit_btn)
                        row_controls.append(del_btn)
                        row_controls.append(drag_handle)

                        task_content = ft.Container(
                            bgcolor=COLOR_BG_CARD,
                            border=ft.Border.all(1, card_border_color),
                            border_radius=12,
                            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                            content=ft.Row(controls=row_controls, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10)
                        )
                else:
                    task_done = bool(is_done)
                    edit_btn = ft.IconButton(
                        icon=ft.Icons.EDIT_OUTLINED,
                        icon_size=16,
                        icon_color=COLOR_TEXT_SECONDARY,
                        tooltip="Edytuj zadanie",
                        width=_touch(28),
                        height=_touch(28),
                        on_click=lambda e, tid=task_id, tit=title, cat=category: open_edit_task_dialog(tid, tit, current_category=cat)
                    )
                    del_btn = ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_size=16,
                        icon_color=COLOR_PRIO_A,
                        tooltip="Usuń zadanie",
                        width=_touch(28),
                        height=_touch(28),
                        on_click=lambda e, tid=task_id, tit=title: delete_task_now(tid, tit)
                    )
                    chk = ft.Checkbox(
                        value=task_done,
                        active_color=COLOR_SUCCESS,
                        on_change=lambda e, tid=task_id: handle_task_check(tid, e.control.value, None, subtasks)
                    )
                    title_text_ctrl = ft.Container(
                        content=ft.Text(
                            title,
                            size=14,
                            weight=ft.FontWeight.W_500,
                            color=COLOR_TEXT_SECONDARY if task_done else COLOR_TEXT_PRIMARY,
                            style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH if task_done else None),
                            no_wrap=False
                        ),
                        expand=True,
                        on_click=lambda e, tid=task_id, td=task_done: handle_task_check(tid, not td, None, subtasks)
                    )

                    if mob:
                        top_row = ft.Row([
                            ft.Column(prio_items, spacing=3, tight=True),
                            chk,
                            title_text_ctrl,
                            drag_handle
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8)

                        action_row = ft.Row([
                            ft.Container(expand=True),
                            edit_btn,
                            del_btn
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=6)

                        task_content = ft.Container(
                            bgcolor=COLOR_BG_CARD,
                            border=ft.Border.all(1, card_border_color),
                            border_radius=12,
                            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                            content=ft.Column([top_row, action_row], spacing=2)
                        )
                    else:
                        task_content = ft.Container(
                            bgcolor=COLOR_BG_CARD,
                            border=ft.Border.all(1, card_border_color),
                            border_radius=12,
                            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                            content=ft.Row(
                                controls=[
                                    ft.Column(prio_items, spacing=3, tight=True),
                                    chk,
                                    title_text_ctrl,
                                    edit_btn,
                                    del_btn,
                                    drag_handle
                                ],
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                spacing=10
                            )
                        )
            else:
                task_done = bool(is_done)
                edit_btn = ft.IconButton(
                    icon=ft.Icons.EDIT_OUTLINED,
                    icon_size=16,
                    icon_color=COLOR_TEXT_SECONDARY,
                    tooltip="Edytuj zadanie",
                    width=_touch(28),
                    height=_touch(28),
                    on_click=lambda e, tid=task_id, tit=title, cat=category: open_edit_task_dialog(tid, tit, current_category=cat)
                )
                del_btn = ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_size=16,
                    icon_color=COLOR_PRIO_A,
                    tooltip="Usuń zadanie",
                    width=_touch(28),
                    height=_touch(28),
                    on_click=lambda e, tid=task_id, tit=title: delete_task_now(tid, tit)
                )
                chk = ft.Checkbox(
                    value=task_done,
                    active_color=COLOR_SUCCESS,
                    on_change=lambda e, tid=task_id: handle_task_check(tid, e.control.value, None, subtasks)
                )
                title_text_ctrl = ft.Container(
                    content=ft.Text(
                        title,
                        size=14,
                        weight=ft.FontWeight.W_500,
                        color=COLOR_TEXT_SECONDARY if task_done else COLOR_TEXT_PRIMARY,
                        style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH if task_done else None),
                        no_wrap=False
                    ),
                    expand=True,
                    on_click=lambda e, tid=task_id, td=task_done: handle_task_check(tid, not td, None, subtasks)
                )

                category_badge = ft.Container(
                    content=ft.Text(category, size=10, color=COLOR_PRIO_C, weight=ft.FontWeight.BOLD),
                    bgcolor="#3b82f622",
                    border=ft.Border.all(1, "#3b82f644"),
                    padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                    border_radius=6,
                    tooltip=f"Strefa: {category}"
                ) if category != "Ogólne" else None

                add_krok_btn = ft.IconButton(
                    icon=ft.Icons.ADD_TASK,
                    icon_size=18,
                    icon_color=COLOR_PRIO_C,
                    tooltip="Dodaj krok do planu",
                    width=_touch(28),
                    height=_touch(28),
                    on_click=lambda e, tid=task_id, subs=subtasks: open_subtask_dialog(tid, subs)
                ) if mob else ft.TextButton(
                    "👣 Krok",
                    icon=ft.Icons.ADD,
                    style=ft.ButtonStyle(color=COLOR_PRIO_C),
                    on_click=lambda e, tid=task_id, subs=subtasks: open_subtask_dialog(tid, subs)
                )

                subtask_controls = []
                for idx, st in enumerate(subtasks):
                    st_done = bool(st["is_done"])
                    subtask_controls.append(
                        ft.Container(
                            padding=ft.Padding.only(left=28 if mob else 36, top=2, bottom=2, right=4),
                            content=ft.Row(
                                controls=[
                                    ft.Checkbox(
                                        value=st_done,
                                        active_color=COLOR_SUCCESS,
                                        on_change=lambda e, tid=task_id, subs=subtasks, i=idx: handle_task_check(
                                            tid, e.control.value, i, subs
                                        )
                                    ),
                                    ft.Container(
                                        content=ft.Text(
                                            st["title"],
                                            size=13,
                                            color=COLOR_TEXT_SECONDARY if st_done else COLOR_TEXT_PRIMARY,
                                            style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH if st_done else None),
                                            no_wrap=False
                                        ),
                                        expand=True,
                                        on_click=lambda e, tid=task_id, subs=subtasks, i=idx, sd=st_done: handle_task_check(
                                            tid, not sd, i, subs
                                        )
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.ARROW_UPWARD,
                                        icon_size=14,
                                        icon_color=COLOR_TEXT_SECONDARY if idx > 0 else COLOR_BG_BORDER,
                                        tooltip="Przesuń krok wyżej",
                                        width=_touch(26),
                                        height=_touch(26),
                                        disabled=idx == 0,
                                        on_click=lambda e, tid=task_id, i=idx: move_subtask_action(tid, i, -1)
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.ARROW_DOWNWARD,
                                        icon_size=14,
                                        icon_color=COLOR_TEXT_SECONDARY if idx < len(subtasks) - 1 else COLOR_BG_BORDER,
                                        tooltip="Przesuń krok niżej",
                                        width=_touch(26),
                                        height=_touch(26),
                                        disabled=idx >= len(subtasks) - 1,
                                        on_click=lambda e, tid=task_id, i=idx: move_subtask_action(tid, i, 1)
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.EDIT_OUTLINED,
                                        icon_size=14,
                                        icon_color=COLOR_TEXT_SECONDARY,
                                        tooltip="Edytuj krok",
                                        width=_touch(26),
                                        height=_touch(26),
                                        on_click=lambda e, tid=task_id, i=idx, subs=subtasks: open_edit_subtask_dialog(tid, i, subs)
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.CLOSE,
                                        icon_size=14,
                                        icon_color=COLOR_PRIO_A,
                                        tooltip="Usuń krok",
                                        width=_touch(26),
                                        height=_touch(26),
                                        on_click=lambda e, tid=task_id, i=idx: delete_subtask_now(tid, i)
                                    )
                                ],
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                spacing=6
                            )
                        )
                    )

                if mob:
                    top_row = ft.Row([
                        ft.Column(prio_items, spacing=3, tight=True),
                        chk,
                        title_text_ctrl,
                        drag_handle
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8)

                    action_items = []
                    if category_badge:
                        action_items.append(category_badge)
                    action_items.append(ft.Container(expand=True))
                    action_items.extend([add_krok_btn, edit_btn, del_btn])
                    action_row = ft.Row(action_items, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=6)

                    card_rows = [top_row, action_row] + subtask_controls
                    task_content = ft.Container(
                        bgcolor=COLOR_BG_CARD,
                        border=ft.Border.all(1, card_border_color),
                        border_radius=12,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                        content=ft.Column(card_rows, spacing=4)
                    )
                else:
                    title_row_controls = [
                        ft.Column(prio_items, spacing=3, tight=True),
                        chk,
                        title_text_ctrl
                    ]
                    if category_badge:
                        title_row_controls.append(category_badge)
                    title_row_controls.append(add_krok_btn)
                    title_row_controls.append(edit_btn)
                    title_row_controls.append(del_btn)
                    title_row_controls.append(drag_handle)

                    task_content = ft.Container(
                        bgcolor=COLOR_BG_CARD,
                        border=ft.Border.all(1, card_border_color),
                        border_radius=12,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                        content=ft.Column(
                            [ft.Row(title_row_controls, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10)] + subtask_controls,
                            spacing=2
                        )
                    )

            if deadline_chip is not None:
                # Chip dokładany na końcu istniejącego rzędu (lub jako osobny rząd
                # w Column), więc indeksy przycisków edycji/usuwania się nie zmieniają.
                if isinstance(task_content.content, ft.Row):
                    task_content.content.controls.append(deadline_chip)
                else:
                    task_content.content.controls.append(
                        ft.Row([deadline_chip], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                    )

            tasks_column.controls.append(task_content)

        elements = [
            ft.Row([
                ft.Row([
                    ft.Icon(ft.Icons.WB_SUNNY if is_daily_mode else ft.Icons.ALL_INBOX_ROUNDED, size=22,
                            color=COLOR_PRIO_B if is_daily_mode else COLOR_PRIO_C),
                    ft.Text("Daily" if is_daily_mode else "Master Lista", size=20, weight=ft.FontWeight.BOLD,
                            color=COLOR_TEXT_PRIMARY)
                ], spacing=8),
                ft.Container(expand=True),
                ft.IconButton(ft.Icons.EMOJI_EVENTS_ROUNDED, icon_color=COLOR_PRIO_B, tooltip="Dziennik Sukcesów",
                              width=_touch(40), height=_touch(40), on_click=lambda e: switch_view("archive")),
                ft.IconButton(ft.Icons.SYNC, icon_color=COLOR_TEXT_SECONDARY, tooltip="Synchronizuj bazę",
                              width=_touch(40), height=_touch(40), on_click=manual_sync),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            quick_bar
        ]

        if not is_daily_mode:
            search_input = ft.TextField(
                hint_text="🔍 Szukaj w zadaniach i krokach...",
                value=master_search_query,
                expand=True,
                dense=True,
                content_padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                bgcolor=COLOR_BG_CARD,
                border_color=COLOR_BG_BORDER,
                on_change=lambda e: set_search_query(e.control.value)
            )

            zone_pills = []
            for z_val, z_lbl in [("ALL", "Wszystkie")] + [(z, z) for z in ZONES]:
                is_active = (master_filter_category == z_val)
                zone_pills.append(
                    ft.Container(
                        content=ft.Text(z_lbl, size=11, weight=ft.FontWeight.BOLD,
                                        color=COLOR_TEXT_PRIMARY if is_active else COLOR_TEXT_SECONDARY),
                        bgcolor="#3b82f633" if is_active else "#21262d",
                        border=ft.Border.all(1, COLOR_PRIO_C if is_active else COLOR_BG_BORDER),
                        border_radius=8,
                        padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                        on_click=lambda e, zv=z_val: set_filter_category(zv)
                    )
                )

            prio_pills = []
            for p_val, p_lbl in [("ALL", "Wsz"), ("A", "A"), ("B", "B"), ("C", "C")]:
                is_active = (master_filter_priority == p_val)
                prio_bg = priority_colors.get(p_val, "#21262d") if is_active else "#21262d"
                prio_pills.append(
                    ft.Container(
                        content=ft.Text(p_lbl, size=11, weight=ft.FontWeight.BOLD,
                                        color=COLOR_TEXT_PRIMARY if is_active else COLOR_TEXT_SECONDARY),
                        bgcolor=prio_bg if p_val != "ALL" else ("#f59e0b33" if is_active else "#21262d"),
                        border=ft.Border.all(1, COLOR_PRIO_B if is_active else COLOR_BG_BORDER),
                        border_radius=8,
                        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                        on_click=lambda e, pv=p_val: set_filter_priority(pv)
                    )
                )

            status_pills = []
            for s_val, s_lbl in [("ALL", "Wsz"), ("ACTIVE", "Do zrobienia"), ("DONE", "Zrobione")]:
                is_active = (master_filter_status == s_val)
                status_pills.append(
                    ft.Container(
                        content=ft.Text(s_lbl, size=11, weight=ft.FontWeight.BOLD,
                                        color=COLOR_TEXT_PRIMARY if is_active else COLOR_TEXT_SECONDARY),
                        bgcolor="#10b98133" if is_active else "#21262d",
                        border=ft.Border.all(1, COLOR_SUCCESS if is_active else COLOR_BG_BORDER),
                        border_radius=8,
                        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                        on_click=lambda e, sv=s_val: set_filter_status(sv)
                    )
                )

            deadline_pills = []
            for d_val, d_lbl in [("ALL", "Wszystkie"), ("WEEK", "Ten tydzień"), ("OVERDUE", "Zaległe")]:
                is_active = (master_deadline_filter == d_val)
                deadline_pills.append(
                    ft.Container(
                        content=ft.Text(d_lbl, size=11, weight=ft.FontWeight.BOLD,
                                        color=COLOR_TEXT_PRIMARY if is_active else COLOR_TEXT_SECONDARY),
                        bgcolor="#ef444433" if is_active and d_val == "OVERDUE" else (
                            "#f59e0b33" if is_active else "#21262d"),
                        border=ft.Border.all(1, COLOR_PRIO_A if is_active and d_val == "OVERDUE" else (
                            COLOR_PRIO_B if is_active else COLOR_BG_BORDER)),
                        border_radius=8,
                        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                        on_click=lambda e, dv=d_val: set_deadline_filter(dv)
                    )
                )

            sort_pills = []
            for s_val, s_lbl in [("DEFAULT", "Domyślnie"), ("DEADLINE", "📅 Termin")]:
                is_active = (master_sort_mode == s_val)
                sort_pills.append(
                    ft.Container(
                        content=ft.Text(s_lbl, size=11, weight=ft.FontWeight.BOLD,
                                        color=COLOR_TEXT_PRIMARY if is_active else COLOR_TEXT_SECONDARY),
                        bgcolor="#3b82f633" if is_active else "#21262d",
                        border=ft.Border.all(1, COLOR_PRIO_C if is_active else COLOR_BG_BORDER),
                        border_radius=8,
                        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                        on_click=lambda e, sv=s_val: set_sort_mode(sv)
                    )
                )

            stale_pill = ft.Container(
                content=ft.Text("🕰 Dawno nietknięte", size=11, weight=ft.FontWeight.BOLD,
                                color=COLOR_TEXT_PRIMARY if master_stale_only else COLOR_TEXT_SECONDARY),
                bgcolor="#8b5cf633" if master_stale_only else "#21262d",
                border=ft.Border.all(1, COLOR_PRIO_C if master_stale_only else COLOR_BG_BORDER),
                border_radius=8,
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                tooltip=f"Zadania bez zmian od {STALE_TASK_DAYS}+ dni, bez przyszłego terminu",
                on_click=lambda e: set_stale_only(not master_stale_only)
            )

            filter_bar = ft.Container(
                bgcolor=COLOR_BG_CARD,
                border=ft.Border.all(1, COLOR_BG_BORDER),
                border_radius=12,
                padding=ft.Padding.all(10),
                content=ft.Column([
                    ft.Row([search_input], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Row(zone_pills, scroll=ft.ScrollMode.AUTO, spacing=6),
                    ft.Row([
                        ft.Text("Priorytet:", size=11, color=COLOR_TEXT_SECONDARY),
                        ft.Row(prio_pills, spacing=4),
                        ft.Container(width=8),
                        ft.Text("Status:", size=11, color=COLOR_TEXT_SECONDARY),
                        ft.Row(status_pills, spacing=4)
                    ], scroll=ft.ScrollMode.AUTO, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=6),
                    ft.Row([
                        ft.Text("Termin:", size=11, color=COLOR_TEXT_SECONDARY),
                        ft.Row(deadline_pills, spacing=4),
                        ft.Container(width=8),
                        ft.Text("Sortuj:", size=11, color=COLOR_TEXT_SECONDARY),
                        ft.Row(sort_pills, spacing=4),
                        ft.Container(width=8),
                        stale_pill
                    ], scroll=ft.ScrollMode.AUTO, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=6),
                    ft.Text(
                        f"Pokazuję zadania bez zmian od co najmniej {STALE_TASK_DAYS} dni (bez przyszłego terminu).",
                        size=11, color=COLOR_TEXT_SECONDARY, visible=master_stale_only
                    )
                ], spacing=8)
            )
            elements.append(filter_bar)
            if inbox_tasks:
                elements.append(_build_inbox_section(inbox_tasks))
        if is_daily_mode:
            preset_5 = ft.Container(
                content=ft.Text("5 min", size=11, weight=ft.FontWeight.BOLD,
                                color=COLOR_TEXT_PRIMARY if timer_total_seconds == 300 else COLOR_TEXT_SECONDARY),
                bgcolor="#f59e0b33" if timer_total_seconds == 300 else "#21262d",
                border_radius=6,
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                on_click=lambda e: set_timer_preset(300)
            )
            preset_15 = ft.Container(
                content=ft.Text("15 min", size=11, weight=ft.FontWeight.BOLD,
                                color=COLOR_TEXT_PRIMARY if timer_total_seconds == 900 else COLOR_TEXT_SECONDARY),
                bgcolor="#f59e0b33" if timer_total_seconds == 900 else "#21262d",
                border_radius=6,
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                on_click=lambda e: set_timer_preset(900)
            )
            preset_25 = ft.Container(
                content=ft.Text("25 min", size=11, weight=ft.FontWeight.BOLD,
                                color=COLOR_TEXT_PRIMARY if timer_total_seconds == 1500 else COLOR_TEXT_SECONDARY),
                bgcolor="#f59e0b33" if timer_total_seconds == 1500 else "#21262d",
                border_radius=6,
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                on_click=lambda e: set_timer_preset(1500)
            )

            timer_btn_play.width = _touch(40)
            timer_btn_play.height = _touch(40)
            timer_card = ft.Container(
                bgcolor=COLOR_BG_CARD,
                border=ft.Border.all(1, COLOR_BG_BORDER),
                border_radius=12,
                padding=12,
                content=ft.Column([
                    ft.Row([
                        ft.Row([
                            ft.Icon(ft.Icons.TIMER_OUTLINED, size=16, color=COLOR_PRIO_B),
                            ft.Text("Minutnik Skupienia", size=13, weight=ft.FontWeight.BOLD, color=COLOR_TEXT_PRIMARY),
                        ], spacing=6),
                        ft.Container(expand=True),
                        preset_5,
                        preset_15,
                        preset_25
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=6),
                    ft.Row([
                        timer_text,
                        timer_msg_text,
                        ft.Container(expand=True),
                        timer_btn_play,
                        ft.IconButton(
                            icon=ft.Icons.REPLAY_ROUNDED,
                            icon_size=20,
                            icon_color=COLOR_TEXT_SECONDARY,
                            tooltip="Reset minutnika",
                            width=_touch(40),
                            height=_touch(40),
                            on_click=lambda e: reset_timer()
                        )
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
                    timer_progress
                ], spacing=6)
            )
            elements.append(timer_card)
            progress_card = ft.Container(
                bgcolor=COLOR_BG_CARD,
                border=ft.Border.all(1, COLOR_BG_BORDER),
                border_radius=12,
                padding=12,
                content=ft.Column([
                    ft.Row([
                        ft.Row([
                            ft.Icon(ft.Icons.TRENDING_UP_ROUNDED, size=16, color=COLOR_SUCCESS),
                            ft.Text("Postęp dnia", size=13, weight=ft.FontWeight.BOLD, color=COLOR_TEXT_PRIMARY),
                        ], spacing=6),
                        ft.Container(expand=True),
                        ft.Container(
                            content=progress_text,
                            bgcolor="#10b98122",
                            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                            border_radius=8
                        )
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    progress_bar,
                ], spacing=8)
            )
            elements.append(progress_card)
            if all_routines:
                elements.append(
                    ft.Container(
                        bgcolor=COLOR_BG_CARD,
                        border=ft.Border.all(1, COLOR_BG_BORDER),
                        border_radius=12,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                        content=ft.Column([
                            ft.Text("Nawyki i Rutyny:", size=12, weight=ft.FontWeight.BOLD, color=COLOR_TEXT_SECONDARY),
                            routines_row
                        ], spacing=8)
                    )
                )

        if is_daily_mode and should_show_deadline_card():
            due_tasks = get_deadline_tasks(within_days=deadline_reminder_days(), include_overdue=True)
            if due_tasks:
                reminder_rows = []
                for dt in due_tasks[:3]:
                    d_id = dt[0]
                    d_title = dt[1]
                    d_deadline = dt[7] if len(dt) > 7 else None
                    d_cat = dt[6] if len(dt) > 6 else "Ogólne"
                    d_urgency = deadline_urgency(d_deadline)
                    d_color = COLOR_PRIO_A if d_urgency == "overdue" else (
                        COLOR_PRIO_B if d_urgency in ("today", "soon") else COLOR_TEXT_SECONDARY)
                    reminder_rows.append(
                        ft.Container(
                            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
                            border_radius=8,
                            bgcolor="#0d111744",
                            on_click=lambda e, tid=d_id, tit=d_title, cat=d_cat: open_edit_task_dialog(
                                tid, tit, current_category=cat),
                            content=ft.Row([
                                ft.Icon(ft.Icons.EVENT_OUTLINED, size=14, color=d_color),
                                ft.Text(d_title, size=12, color=COLOR_TEXT_PRIMARY, expand=True,
                                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(deadline_chip_text(d_deadline), size=10, color=d_color,
                                        weight=ft.FontWeight.W_500),
                            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                        )
                    )
                extra_count = len(due_tasks) - len(reminder_rows)
                card_children = [
                    ft.Row([
                        ft.Row([
                            ft.Icon(ft.Icons.HOURGLASS_BOTTOM_ROUNDED, size=16, color=COLOR_PRIO_B),
                            ft.Text("Nadchodzące terminy", size=13, weight=ft.FontWeight.BOLD,
                                    color=COLOR_TEXT_PRIMARY),
                        ], spacing=6),
                        ft.Container(expand=True),
                        ft.TextButton("Ukryj na dziś", on_click=lambda e: (
                            hide_deadline_card_today(), refresh_view())),
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ] + reminder_rows
                if extra_count > 0:
                    card_children.append(
                        ft.TextButton(
                            f"+{extra_count} więcej w Master liście",
                            on_click=lambda e: (set_sort_mode("DEADLINE"), switch_view("master"))
                        )
                    )
                elements.append(
                    ft.Container(
                        bgcolor="#f59e0b0d",
                        border=ft.Border.all(1, "#f59e0b33"),
                        border_radius=12,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                        content=ft.Column(card_children, spacing=2)
                    )
                )

        elements.extend([status_text, warning_text, tasks_column])
        content_column.controls = elements

        if is_daily_mode:
            # Spokojne przypomnienie o przełomie dnia — bez czerwieni, bez wyrzutów.
            if get_pending_day_rollover():
                content_column.controls.append(
                    ft.Container(
                        bgcolor="#3b82f622",
                        border=ft.Border.all(1, COLOR_PRIO_B),
                        border_radius=10,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                        content=ft.Row([
                            ft.Icon(ft.Icons.WB_SUNNY_OUTLINED, size=18, color=COLOR_PRIO_B),
                            ft.Text(
                                "Nowy dzień. Zamknij poprzedni dzień, aby zdecydować, "
                                "co zrobić z niezrobionymi zadaniami i przenieść sukcesy do Dziennika.",
                                size=12, color=COLOR_TEXT_PRIMARY, expand=True
                            ),
                            ft.TextButton("Rozpocznij dzień", on_click=open_day_rollover)
                        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                    )
                )
            content_column.controls.append(
                ft.Row([
                    ft.Button(
                        "🌅 Nowy dzień / Koniec dnia",
                        icon=ft.Icons.NIGHTLIGHT_ROUND,
                        bgcolor=COLOR_BG_SURFACE,
                        color=COLOR_TEXT_PRIMARY,
                        on_click=open_day_rollover
                    )
                ], alignment=ft.MainAxisAlignment.END)
            )

        page.update()

    page.on_resize = lambda e: refresh_view()
    page.add(ft.SafeArea(content=root_layout, expand=True))
    refresh_view()

    # Sprint 2.1: rytuał przełomu dnia przy starcie (i po powrocie z tła).
    _maybe_start_day_rollover()
    try:
        page.on_resume = lambda e: _maybe_start_day_rollover()
    except Exception:
        LOG.debug("Platforma nie wspiera on_resume — brak sprawdzania po powrocie z tła")

    # Minutnik odtworzony z configu (np. po restarcie w trakcie odliczania)
    # musi sam wznowić wątek — inaczej stałby w miejscu.
    if timer_running:
        threading.Thread(target=timer_worker, daemon=True).start()

    # Startowy pull w tle: nie blokuje pierwszego rysowania UI i NIE nadpisuje
    # nowszych danych lokalnych (force=False).
    def startup_pull():
        if get_config("sync_mode", "none") == "none":
            return
        _STARTUP_PULL_ACTIVE.set()
        try:
            ok, msg = pull_database(force=False)
            LOG.info("Startowy pull: %s (ok=%s)", msg, ok)
            if ok and "Pobrano" in msg:
                try:
                    refresh_view()
                except Exception:
                    pass
        except Exception:
            LOG.exception("Błąd startowego pobierania bazy")
        finally:
            _STARTUP_PULL_ACTIVE.clear()
            # Jeśli użytkownik coś zmienił w trakcie pobierania, teraz wysyłamy.
            if _PUSH_DEFERRED.is_set():
                _PUSH_DEFERRED.clear()
                push_database_async()

    threading.Thread(target=startup_pull, name="master-adhd-startup-pull", daemon=True).start()