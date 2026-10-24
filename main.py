import importlib.util
import os
import shutil
import ssl
import sys
import traceback
import urllib.request
from datetime import datetime

import flet as ft

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/DemonSwans/MasterADHD/master"

# main.py przekazuje wersję kodu wbudowanego do app_logic, żeby Ustawienia
# pokazywały prawdziwą wersję APK, także gdy działa nowszy plik z OTA.
BUNDLED_VERSION_ENV = "MASTER_ADHD_BUNDLED_VERSION"


def get_ssl_context():
    """Tworzy kontekst SSL z bezpiecznym fallbackiem dla środowisk mobilnych (Android)."""
    try:
        return ssl._create_unverified_context()
    except Exception:
        try:
            return ssl.create_default_context()
        except Exception:
            return None


def get_ota_dir():
    base = os.environ.get("FLET_APP_STORAGE_DATA") or os.path.expanduser("~")
    ota_dir = os.path.join(base, ".master_adhd_data")
    try:
        os.makedirs(ota_dir, exist_ok=True)
        return ota_dir
    except Exception:
        return "."


OTA_DIR = get_ota_dir()
OTA_LOGIC_FILE = os.path.join(OTA_DIR, "app_logic_ota.py")
OTA_VERSION_FILE = os.path.join(OTA_DIR, "ota_version.txt")
# Rollback OTA: poprzednia działająca wersja + informacja, którą wersję uznano za wadliwą.
OTA_PREV_LOGIC_FILE = os.path.join(OTA_DIR, "app_logic_ota.prev.py")
OTA_PREV_VERSION_FILE = os.path.join(OTA_DIR, "ota_prev_version.txt")
OTA_FAILED_VERSION_FILE = os.path.join(OTA_DIR, "ota_failed_version.txt")
OTA_LOG_FILE = os.path.join(OTA_DIR, "ota.log")


def _log(message):
    """Log OTA do pliku — na Androidzie print() nie trafia do żadnego logu."""
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [OTA] {message}"
    try:
        print(line)
    except Exception:
        pass
    try:
        if os.path.exists(OTA_LOG_FILE) and os.path.getsize(OTA_LOG_FILE) > 512_000:
            os.replace(OTA_LOG_FILE, OTA_LOG_FILE + ".1")
    except Exception:
        pass
    try:
        with open(OTA_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _read_text(path):
    try:
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _write_text_atomic(path, value):
    """Zapis przez plik tymczasowy — przerwany zapis nie uszkodzi działającej wersji."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(value)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp_path, path)


def get_failed_ota_version() -> str:
    """Wersja, która wcześniej wywaliła aplikację (nie pobieramy jej ponownie)."""
    return _read_text(OTA_FAILED_VERSION_FILE)


def mark_failed_ota_version(version: str):
    try:
        if not version:
            return
        _write_text_atomic(OTA_FAILED_VERSION_FILE, version.strip())
        _log(f"Wersja v{version} oznaczona jako wadliwa — nie zostanie pobrana ponownie.")
    except Exception as e:
        _log(f"Nie udało się zapisać informacji o wadliwej wersji: {e}")


def clear_failed_ota_version():
    try:
        if os.path.exists(OTA_FAILED_VERSION_FILE):
            os.remove(OTA_FAILED_VERSION_FILE)
    except Exception:
        pass


def rollback_ota() -> bool:
    """Przywraca poprzednią wersję OTA po błędzie nowego kodu."""
    if not os.path.exists(OTA_PREV_LOGIC_FILE):
        return False
    try:
        shutil.copy2(OTA_PREV_LOGIC_FILE, OTA_LOGIC_FILE)
        prev_ver = _read_text(OTA_PREV_VERSION_FILE)
        if prev_ver:
            for ver_path in (OTA_VERSION_FILE, os.path.join(OTA_DIR, "version.txt")):
                try:
                    _write_text_atomic(ver_path, prev_ver)
                except Exception:
                    pass
        _log(f"Rollback do poprzedniej wersji v{prev_ver or '?'} zakończony.")
        return True
    except Exception as e:
        _log(f"Rollback nie udał się: {e}")
        return False


def is_newer_version(remote_ver: str, local_ver: str) -> bool:
    try:
        r_parts = [int(x) for x in remote_ver.strip().split(".")]
        l_parts = [int(x) for x in local_ver.strip().split(".")]
        return r_parts > l_parts
    except Exception:
        return remote_ver.strip() != local_ver.strip()


def _version_tuple(value: str):
    """Zwraca krotkę liczb dla '1.6.14' albo None, gdy wersja jest niepoprawna."""
    try:
        return tuple(int(part) for part in value.strip().lstrip("vV").split("."))
    except Exception:
        return None


def get_bundled_version() -> str:
    """Wersja kodu wbudowanego w APK (version.txt obok main.py)."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for name in ("version.txt", "ota_version.txt"):
        version = _read_text(os.path.join(base_dir, name))
        if version:
            return version
    try:
        import app_logic as bundled

        return str(getattr(bundled, "FALLBACK_VERSION", "") or "")
    except Exception:
        return ""


_PREPARED_CACHE = {"key": None, "path": None, "version": ""}


def _prepared_cache_key():
    try:
        logic_stat = os.stat(OTA_LOGIC_FILE)
    except Exception:
        return None
    try:
        version_stat = os.stat(OTA_VERSION_FILE)
        version_key = (version_stat.st_mtime_ns, version_stat.st_size)
    except Exception:
        version_key = None
    return (OTA_LOGIC_FILE, logic_stat.st_mtime_ns, logic_stat.st_size,
            version_key, get_failed_ota_version(), get_bundled_version())


def _evaluate_prepared_ota():
    """Sprawdza, czy plik OTA jest nowszy i poprawny. Zwraca (ścieżka, wersja)."""
    if not os.path.exists(OTA_LOGIC_FILE):
        return None, ""
    ota_ver = _read_text(OTA_VERSION_FILE)
    if not ota_ver:
        _log("Cache OTA bez pliku wersji — pomijam")
        return None, ""
    bundled_ver = get_bundled_version()
    # Kluczowa zasada: kod OTA uruchamiamy TYLKO gdy jest nowszy od wbudowanego.
    # Bez tego stary plik z folderu danych przesłaniał świeży kod z nowego APK.
    if bundled_ver and not is_newer_version(ota_ver, bundled_ver):
        _log(f"Cache OTA v{ota_ver} nie jest nowszy od wbudowanego v{bundled_ver} — pomijam")
        return None, ""
    failed_ver = get_failed_ota_version()
    if failed_ver and _version_tuple(failed_ver) == _version_tuple(ota_ver):
        _log(f"Cache OTA v{ota_ver} oznaczony jako wadliwy — pomijam")
        return None, ""
    code = _read_text(OTA_LOGIC_FILE)
    if not code:
        return None, ""
    try:
        compile(code, OTA_LOGIC_FILE, "exec")
    except Exception as e:
        _log(f"Cache OTA v{ota_ver} nie kompiluje się ({e}) — pomijam")
        return None, ""
    return OTA_LOGIC_FILE, ota_ver


def get_prepared_ota():
    """Zwraca (ścieżka, wersja) OTA gotowego do uruchomienia albo (None, "")."""
    key = _prepared_cache_key()
    if key is None:
        return None, ""
    if _PREPARED_CACHE["key"] == key:
        return _PREPARED_CACHE["path"], _PREPARED_CACHE["version"]
    path, version = _evaluate_prepared_ota()
    _PREPARED_CACHE.update({"key": key, "path": path, "version": version})
    return path, version


def get_installed_ota_version() -> str:
    """Wersja kodu, który faktycznie się uruchomi (gotowy OTA albo wbudowany)."""
    _, ota_ver = get_prepared_ota()
    return ota_ver or get_bundled_version() or "0.0.0"


def check_and_download_ota(force=False, status_cb=None) -> tuple[bool, str]:
    """Sprawdza na GitHubie czy jest nowszy kod i pobiera go do folderu danych."""
    ts = int(datetime.now().timestamp())
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "User-Agent": "MasterADHD-OTA/1.0"
    }
    ctx = get_ssl_context()

    def update_status(text, progress=None):
        if status_cb:
            try:
                status_cb(text, progress)
            except Exception:
                pass

    try:
        update_status("Sprawdzanie dostępności aktualizacji...", 0.2)
        url_ver = f"{GITHUB_RAW_BASE}/version.txt?t={ts}"
        req_ver = urllib.request.Request(url_ver, headers=headers)
        with urllib.request.urlopen(req_ver, timeout=5, context=ctx) as resp:
            remote_ver = resp.read().decode("utf-8").strip()

        local_ver = get_installed_ota_version()
        _log(f"Sprawdzanie aktualizacji: zdalna v{remote_ver}, lokalna v{local_ver}")

        if not _version_tuple(remote_ver):
            _log(f"Niepoprawny numer wersji z GitHuba: {remote_ver!r}")
            update_status("Błąd numeru wersji na GitHubie", 1.0)
            return False, local_ver

        # Wersja, która wcześniej wysypała aplikację, nie jest pobierana ponownie.
        failed_ver = get_failed_ota_version()
        if failed_ver and _version_tuple(failed_ver) == _version_tuple(remote_ver):
            _log(f"Pomijam wersję v{remote_ver} — oznaczona jako wadliwa.")
            update_status(f"Wersja v{remote_ver} jest oznaczona jako wadliwa — pominięto", 1.0)
            return False, local_ver

        if not (force or is_newer_version(remote_ver, local_ver)):
            _log(f"Aplikacja jest aktualna (v{local_ver}).")
            update_status(f"Aplikacja jest aktualna (v{local_ver})", 1.0)
            return False, local_ver

        _log(f"Pobieranie kodu v{remote_ver}...")
        update_status(f"Pobieranie nowego kodu v{remote_ver}...", 0.5)
        url_code = f"{GITHUB_RAW_BASE}/app_logic.py?t={ts}"
        req_code = urllib.request.Request(url_code, headers=headers)
        with urllib.request.urlopen(req_code, timeout=15, context=ctx) as resp_code:
            new_code = resp_code.read().decode("utf-8")

        update_status("Weryfikacja i instalacja aktualizacji...", 0.8)
        # Weryfikacja czy pobrany kod to poprawny Python (ochrona przed uciętym plikiem)
        compile(new_code, "app_logic_ota.py", "exec")

        # Zanim nadpiszemy działający plik, odkładamy poprzednią wersję na bok.
        if os.path.exists(OTA_LOGIC_FILE):
            try:
                shutil.copy2(OTA_LOGIC_FILE, OTA_PREV_LOGIC_FILE)
                _write_text_atomic(OTA_PREV_VERSION_FILE, local_ver)
            except Exception as e:
                _log(f"Nie udało się zapisać kopii poprzedniej wersji: {e}")

        _write_text_atomic(OTA_LOGIC_FILE, new_code)
        _write_text_atomic(OTA_VERSION_FILE, remote_ver)
        _PREPARED_CACHE.update({"key": None, "path": None, "version": ""})

        clear_failed_ota_version()
        _log(f"Pomyślnie pobrano i zapisano nową wersję v{remote_ver}.")
        update_status(f"Zaktualizowano pomyślnie do v{remote_ver}!", 1.0)
        return True, remote_ver
    except Exception as e:
        _log(f"Błąd podczas sprawdzania lub pobierania aktualizacji: {e}")
        update_status(f"Błąd sieci OTA: {str(e)[:40]}", 1.0)
    return False, get_installed_ota_version()


def _run_app_logic(page: ft.Page, ota_path=None):
    """Uruchamia interfejs z pobranego pliku OTA albo z kodu wbudowanego."""
    if ota_path:
        _log(f"Uruchamiam kod OTA: {ota_path}")
        spec = importlib.util.spec_from_file_location("app_logic_ota", ota_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Nie można załadować modułu z {ota_path}")
        module = importlib.util.module_from_spec(spec)
        # Rejestrujemy pod obiema nazwami: kod aplikacji i testy odwołują się do "app_logic".
        sys.modules["app_logic_ota"] = module
        sys.modules["app_logic"] = module
        spec.loader.exec_module(module)
    else:
        sys.modules.pop("app_logic_ota", None)
        import app_logic as module

    if not hasattr(module, "main"):
        raise AttributeError("Moduł app_logic nie zawiera funkcji main(page)")
    module.main(page)


def _show_update_snackbar(page: ft.Page, version: str):
    try:
        snack = ft.SnackBar(
            ft.Text(f"🚀 Zaktualizowano do wersji v{version}!"),
            open=True,
            bgcolor=ft.Colors.GREEN_800
        )
        page.overlay.append(snack)
        opener = getattr(page, "open", None)
        if callable(opener):
            try:
                opener(snack)
            except Exception:
                pass
        page.update()
    except Exception as e:
        _log(f"Nie udało się pokazać komunikatu o aktualizacji: {e}")


def load_and_run(page: ft.Page, allow_rollback: bool = True):
    disable_ota = os.environ.get("DISABLE_OTA", "0") == "1"
    updated = False
    new_ver = ""

    bundled_version = get_bundled_version()
    if bundled_version:
        os.environ[BUNDLED_VERSION_ENV] = bundled_version
    _PREPARED_CACHE.update({"key": None, "path": None, "version": ""})

    # Moduły wbudowane — przywracamy je, jeśli pobrany kod OTA wysypie się w trakcie.
    original_app_logic = sys.modules.get("app_logic")
    original_app_logic_ota = sys.modules.get("app_logic_ota")

    # Wizualny ekran ładowania / paska postępu aktualizacji
    if not disable_ota and not getattr(page, "_ota_checked", False):
        page._ota_checked = True
        page.bgcolor = "#0d1117"
        page.theme_mode = ft.ThemeMode.DARK

        progress_bar = ft.ProgressBar(width=260, color="#f59e0b", bgcolor="#21262d", value=0.1)
        status_label = ft.Text("Inicjalizacja aplikacji...", size=12, color="#8b949e")
        ver_label = ft.Text(f"Wersja lokalna: v{get_installed_ota_version()}", size=11, color="#484f58")

        splash = ft.Container(
            expand=True,
            alignment=ft.Alignment(0, 0),
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.BOLT, size=40, color="#f59e0b"),
                    ft.Text("Master ADHD", size=20, weight=ft.FontWeight.BOLD, color="#f0f6fc"),
                    ft.Container(height=14),
                    progress_bar,
                    ft.Container(height=6),
                    status_label,
                    ft.Container(height=4),
                    ver_label,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                tight=True
            )
        )

        page.controls.clear()
        page.add(ft.SafeArea(splash))
        page.update()

        def status_cb(msg, progress):
            status_label.value = msg
            if progress is not None:
                progress_bar.value = progress
            page.update()

        updated, new_ver = check_and_download_ota(status_cb=status_cb)

    if disable_ota:
        _log("DISABLE_OTA=1 — pomijam OTA i uruchamiam tylko kod wbudowany")

    ota_path, ota_ver = (None, "") if disable_ota else get_prepared_ota()
    if ota_path:
        _log(f"Uruchamiam wersję z OTA: v{ota_ver} ({ota_path})")
    else:
        _log(f"Uruchamiam wersję wbudowaną: v{bundled_version or '?'}")

    try:
        page.controls.clear()
        _run_app_logic(page, ota_path)
        if updated:
            _show_update_snackbar(page, new_ver)
    except Exception as e:
        # ZABEZPIECZENIE: Jeśli pobrany plik ma błąd, nie blokujemy aplikacji.
        err_msg = traceback.format_exc()
        _log(f"Błąd podczas uruchamiania modułu: {err_msg}")

        failed_ver = ota_ver or new_ver or _read_text(OTA_VERSION_FILE)
        if ota_path:
            mark_failed_ota_version(failed_ver)

        # Przywróć moduły wbudowane w sys.modules (pobrany kod mógł je nadpisać).
        if original_app_logic is not None:
            sys.modules["app_logic"] = original_app_logic
        else:
            sys.modules.pop("app_logic", None)
        if original_app_logic_ota is not None:
            sys.modules["app_logic_ota"] = original_app_logic_ota
        else:
            sys.modules.pop("app_logic_ota", None)
        _PREPARED_CACHE.update({"key": None, "path": None, "version": ""})

        if allow_rollback and rollback_ota():
            _log("Próba ponownego startu na poprzedniej wersji...")
            page.controls.clear()
            try:
                load_and_run(page, allow_rollback=False)
                return
            except Exception:
                _log("Poprzednia wersja również nie wystartowała.")
                err_msg = traceback.format_exc()
        elif allow_rollback:
            # Brak kopii .prev — odkładamy wadliwy plik OTA, żeby nie blokował startu.
            # Dotyczy wyłącznie pliku w folderze danych (nie ruszamy repo!).
            try:
                if os.path.exists(OTA_LOGIC_FILE):
                    os.replace(OTA_LOGIC_FILE, OTA_LOGIC_FILE + ".bad")
                    _log("Wadliwy plik OTA odłożony jako app_logic_ota.py.bad")
            except Exception:
                pass

        def retry_ota(_):
            page.controls.clear()
            check_and_download_ota(force=True)
            load_and_run(page)

        page.controls.clear()
        page.add(
            ft.SafeArea(
                ft.Container(
                    padding=20,
                    content=ft.Column([
                        ft.Text("⚠️ Błąd w module aplikacji (Przywrócono wersję bazową)", size=18,
                                weight=ft.FontWeight.BOLD, color=ft.Colors.RED_400),
                        ft.Text(err_msg, size=11, selectable=True),
                        ft.Button("🔄 Pobierz poprawkę z GitHuba", bgcolor=ft.Colors.GREEN_700,
                                  color=ft.Colors.WHITE, on_click=retry_ota)
                    ], scroll=ft.ScrollMode.AUTO)
                )
            )
        )
        page.update()


def main(page: ft.Page):
    load_and_run(page)


# Re-eksport symboli z app_logic dla testów i starego kodu, KTÓRY NIE NADPISUJE
# `main`. Wcześniej `from app_logic import *` podmieniał entrypoint na
# app_logic.main, przez co OTA nigdy nie była uruchamiana (ft.run(main) wołał
# kod wbudowany, a pobrany app_logic_ota.py leżał bezużyteczny na dysku).
import app_logic as _app_logic_module  # noqa: E402


def _reexport_app_logic_symbols():
    for _name in dir(_app_logic_module):
        if _name.startswith("_") or _name == "main":
            continue
        globals().setdefault(_name, getattr(_app_logic_module, _name))


_reexport_app_logic_symbols()

if __name__ == "__main__":
    ft.run(main)
