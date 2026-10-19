import base64
import importlib.util
import json
import os
import shutil
import sqlite3
import ssl
import sys
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
import flet as ft

FALLBACK_VERSION = "1.6.8"


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


# --- ODCZYT WERSJI ---
def get_local_version():
    try:
        ota_ver_file = os.path.join(APP_DIR, "ota_version.txt")
        if os.path.exists(ota_ver_file):
            with open(ota_ver_file, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
        base_dir = os.path.dirname(os.path.abspath(__file__))
        ver_file = os.path.join(base_dir, "version.txt")
        if os.path.exists(ver_file):
            with open(ver_file, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
    except Exception:
        pass
    return FALLBACK_VERSION


APP_VERSION = get_local_version()


def is_newer_version(remote_ver: str, local_ver: str) -> bool:
    try:
        r_parts = [int(x) for x in remote_ver.strip().split(".")]
        l_parts = [int(x) for x in local_ver.strip().split(".")]
        return r_parts > l_parts
    except Exception:
        return remote_ver.strip() != local_ver.strip()


# --- KONFIGURACJA LOKALNA ---
def init_config_db():
    with sqlite3.connect(CONFIG_DB) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")


def get_config(key, default=""):
    with sqlite3.connect(CONFIG_DB) as conn:
        res = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return res[0] if res else default


def set_config(key, value):
    with sqlite3.connect(CONFIG_DB) as conn:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))


# --- SYNCHRONIZACJA (Chmura Gist / HA / Folder) ---
def is_valid_sqlite(file_data):
    return file_data.startswith(b"SQLite format 3")


def pull_database():
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
                        b64_content = files["tasks.db.b64"].get("content", "")
                        raw_db = base64.b64decode(b64_content.encode("utf-8"))
                        if is_valid_sqlite(raw_db):
                            with open(LOCAL_DB, "wb") as f:
                                f.write(raw_db)
                            return True, "Pobrano bazę z Chmury"
                        return False, "Odrzucono: Plik w chmurze jest uszkodzony"
                    return True, "Chmura gotowa"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False, "Nie znaleziono Chmury o podanym ID!"
            if e.code == 401:
                return False, "Nieprawidłowy Token GitHub!"
            return False, f"Błąd chmury: {e.code}"
        except Exception as e:
            return False, f"Brak połączenia z chmurą: {e}"

    elif mode == "ha_server":
        url = get_config("ha_url", "").strip().rstrip("/")
        if not url: return False, "Brak adresu serwera HA"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    data = resp.read()
                    if is_valid_sqlite(data):
                        with open(LOCAL_DB, "wb") as f: f.write(data)
                        return True, "Pobrano bazę z serwera HA"
                    return False, "Odrzucono: Pobrany plik to nie baza"
        except urllib.error.HTTPError as e:
            if e.code == 404: return True, "Serwer HA gotowy"
            return False, f"Błąd serwera: {e.code}"
        except Exception as e:
            return False, f"Brak połączenia z HA: {e}"

    elif mode == "folder":
        folder = get_config("sync_folder", "").strip()
        if not folder: return False, "Nie podano ścieżki do folderu!"
        if not os.path.exists(folder): return False, "Podany folder nie istnieje na dysku!"

        remote_file = os.path.join(folder, "tasks.db")
        if os.path.exists(remote_file):
            try:
                with open(remote_file, "rb") as f:
                    data = f.read()
                if is_valid_sqlite(data):
                    shutil.copy2(remote_file, LOCAL_DB)
                    return True, "Pobrano bazę z folderu"
                return False, "Odrzucono: Plik uszkodzony"
            except Exception as e:
                return False, f"Błąd odczytu z dysku: {e}"
        return True, "Folder gotowy"
    return True, "Tryb lokalny"


def push_database():
    """Synchroniczny zapis (używany w testach oraz przy pierwszym łączeniu w ustawieniach)."""
    mode = get_config("sync_mode", "none")
    if mode == "none" or not os.path.exists(LOCAL_DB):
        return True, ""

    if mode == "gist":
        token = get_config("gist_token", "").strip()
        gist_id = get_config("gist_id", "").strip()
        if not token:
            return False, "Brak Tokenu GitHub"
        try:
            with open(LOCAL_DB, "rb") as f:
                b64_str = base64.b64encode(f.read()).decode("utf-8")
            payload = json.dumps({
                "description": "MasterADHD Cloud Database Sync",
                "public": False,
                "files": {"tasks.db.b64": {"content": b64_str}}
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
                    return True, "Zapisano w Chmurze"
        except Exception as e:
            return False, f"Błąd zapisu w chmurze: {e}"

    elif mode == "ha_server":
        url = get_config("ha_url", "").strip().rstrip("/")
        if not url: return False, "Brak adresu"
        try:
            with open(LOCAL_DB, "rb") as f:
                data = f.read()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/octet-stream"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200: return True, "Zapisano na serwerze HA"
        except Exception as e:
            return False, f"Błąd wysyłania: {e}"

    elif mode == "folder":
        folder = get_config("sync_folder", "").strip()
        if not folder or not os.path.exists(folder): return False, "Folder nie istnieje"
        try:
            archive_dir = os.path.join(folder, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            remote_file = os.path.join(folder, "tasks.db")
            if os.path.exists(remote_file):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                shutil.copy2(remote_file, os.path.join(archive_dir, f"tasks_{ts}.db"))
                backups = sorted([os.path.join(archive_dir, f) for f in os.listdir(archive_dir) if f.endswith(".db")])
                while len(backups) > 25: os.remove(backups.pop(0))
            shutil.copy2(LOCAL_DB, remote_file)
            return True, "Zaktualizowano na Dysku"
        except Exception as e:
            return False, f"Błąd zapisu: {e}"
    return True, ""


def push_database_async():
    """Szybki, nieblokujący zapis w tle dla płynnego działania interfejsu."""
    mode = get_config("sync_mode", "none")
    if mode == "none":
        return
    if mode == "folder":
        push_database()
    else:
        threading.Thread(target=push_database, daemon=True).start()


# --- BAZA DANYCH I LOGIKA ---
def init_db():
    init_config_db()
    pull_database()
    with sqlite3.connect(LOCAL_DB) as conn:
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
                         '[]'
                     )
                     """)
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN subtasks TEXT DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "CREATE TABLE IF NOT EXISTS routines (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, is_done INTEGER DEFAULT 0)")
        conn.execute("""
                     CREATE TABLE IF NOT EXISTS completed_archive
                     (
                         id INTEGER PRIMARY KEY AUTOINCREMENT,
                         title TEXT NOT NULL,
                         priority TEXT DEFAULT 'B',
                         completed_at TEXT NOT NULL
                     )
                     """)


def get_tasks(is_daily_view=True):
    with sqlite3.connect(LOCAL_DB) as conn:
        if is_daily_view:
            rows = conn.execute(
                "SELECT id, title, is_daily, is_done, priority, subtasks FROM tasks WHERE is_daily = 1 ORDER BY is_done ASC, priority ASC, id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, is_daily, is_done, priority, subtasks FROM tasks ORDER BY is_done ASC, priority ASC, id DESC").fetchall()
        return [(r[0], r[1], r[2], r[3], r[4], json.loads(r[5] if r[5] else '[]')) for r in rows]


def get_routines():
    with sqlite3.connect(LOCAL_DB) as conn: return conn.execute(
        "SELECT id, title, is_done FROM routines ORDER BY id").fetchall()


def add_routine_db(title):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute("INSERT INTO routines (title) VALUES (?)", (title,))
    push_database_async()


def toggle_routine_db(routine_id, is_done):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute("UPDATE routines SET is_done = ? WHERE id = ?",
                                                         (int(is_done), routine_id))
    push_database_async()


def delete_routine_db(routine_id):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
    push_database_async()


def update_subtasks_db(task_id, subtasks_list):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute("UPDATE tasks SET subtasks = ? WHERE id = ?",
                                                         (json.dumps(subtasks_list), task_id))
    push_database_async()


def get_task_priority(task_id):
    with sqlite3.connect(LOCAL_DB) as conn:
        res = conn.execute("SELECT priority FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return res[0] if res else "B"


def count_daily_priority_a():
    with sqlite3.connect(LOCAL_DB) as conn:
        res = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE is_daily = 1 AND priority = 'A' AND is_done = 0").fetchone()
        return res[0] if res else 0


def add_task_db(title, is_daily, priority="B"):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute(
        "INSERT INTO tasks (title, is_daily, priority) VALUES (?, ?, ?)", (title, int(is_daily), priority))
    push_database_async()


def toggle_done_db(task_id, is_done):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute("UPDATE tasks SET is_done = ? WHERE id = ?",
                                                         (int(is_done), task_id))
    push_database_async()


def set_daily_db(task_id, is_daily):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute("UPDATE tasks SET is_daily = ? WHERE id = ?",
                                                         (int(is_daily), task_id))
    push_database_async()


def update_priority_db(task_id, new_priority):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute("UPDATE tasks SET priority = ? WHERE id = ?",
                                                         (new_priority, task_id))
    push_database_async()


def delete_task_db(task_id):
    with sqlite3.connect(LOCAL_DB) as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    push_database_async()


def edit_task_title_db(task_id, new_title):
    with sqlite3.connect(LOCAL_DB) as conn:
        conn.execute("UPDATE tasks SET title = ? WHERE id = ?", (new_title.strip(), task_id))
    push_database_async()


def delete_subtask_db(task_id, subtask_idx):
    with sqlite3.connect(LOCAL_DB) as conn:
        row = conn.execute("SELECT subtasks FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row and row[0]:
            subtasks = json.loads(row[0])
            if 0 <= subtask_idx < len(subtasks):
                subtasks.pop(subtask_idx)
                conn.execute("UPDATE tasks SET subtasks = ? WHERE id = ?", (json.dumps(subtasks), task_id))
    push_database_async()


def edit_subtask_title_db(task_id, subtask_idx, new_title):
    with sqlite3.connect(LOCAL_DB) as conn:
        row = conn.execute("SELECT subtasks FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row and row[0]:
            subtasks = json.loads(row[0])
            if 0 <= subtask_idx < len(subtasks):
                subtasks[subtask_idx]["title"] = new_title.strip()
                conn.execute("UPDATE tasks SET subtasks = ? WHERE id = ?", (json.dumps(subtasks), task_id))
    push_database_async()


def get_completed_archive(limit=100):
    with sqlite3.connect(LOCAL_DB) as conn:
        return conn.execute(
            "SELECT id, title, priority, completed_at FROM completed_archive ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()


def clear_completed_archive_db():
    with sqlite3.connect(LOCAL_DB) as conn:
        conn.execute("DELETE FROM completed_archive")
    push_database_async()


def end_of_day_db():
    with sqlite3.connect(LOCAL_DB) as conn:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        done_tasks = conn.execute("SELECT title, priority FROM tasks WHERE is_done = 1").fetchall()
        for dt in done_tasks:
            conn.execute(
                "INSERT INTO completed_archive (title, priority, completed_at) VALUES (?, ?, ?)",
                (dt[0], dt[1], now_str)
            )
        conn.execute("DELETE FROM tasks WHERE is_done = 1")
        conn.execute("UPDATE tasks SET is_daily = 0 WHERE is_daily = 1 AND is_done = 0")
        conn.execute("UPDATE routines SET is_done = 0")
    push_database_async()


def calculate_progress_logic(tasks_list, routines_list, active_subtask_idx_map=None):
    if active_subtask_idx_map is None:
        active_subtask_idx_map = {}
    total_points = len(tasks_list) + len(routines_list)
    if total_points == 0: return 0.0
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
        if r[2] == 1: done_points += 1

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

# --- INTERFEJS (Flet) ---
def main(page: ft.Page):
    current_ver = get_local_version()
    page.title = f"ADHD Master & Daily (v{current_ver})"
    page.window.width = 950
    page.window.height = 760
    page.padding = 0
    page.bgcolor = COLOR_BG_MAIN
    page.theme_mode = ft.ThemeMode.DARK
    init_db()

    current_view = "daily"
    max_a_limit = int(get_config("max_a_limit", "3"))
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
    title_text = ft.Text("Daily", size=22, weight=ft.FontWeight.BOLD)

    # --- MINUTNIK SKUPIENIA (Zasada 5 minut / Pomodoro) ---
    timer_total_seconds = 300
    timer_seconds_left = 300
    timer_running = False
    timer_message = ""
    timer_lock = threading.Lock()

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

    def update_timer_ui():
        mins = timer_seconds_left // 60
        secs = timer_seconds_left % 60
        timer_text.value = f"{mins:02d}:{secs:02d}"
        timer_progress.value = (timer_seconds_left / timer_total_seconds) if timer_total_seconds > 0 else 0.0
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
        try:
            page.update()
        except Exception:
            pass

    def timer_worker():
        nonlocal timer_running, timer_seconds_left, timer_message
        while True:
            with timer_lock:
                if not timer_running or timer_seconds_left <= 0:
                    break
                timer_seconds_left -= 1
                done = (timer_seconds_left == 0)

            update_timer_ui()

            if done:
                with timer_lock:
                    timer_running = False
                    timer_message = "🎉 Brawo! Czas minął – paraliż pokonany!"
                update_timer_ui()
                break

            time.sleep(1)

    def toggle_timer():
        nonlocal timer_running, timer_message
        with timer_lock:
            timer_running = not timer_running
            timer_message = ""
            start_thread = timer_running
        update_timer_ui()
        if start_thread:
            threading.Thread(target=timer_worker, daemon=True).start()

    def reset_timer():
        nonlocal timer_running, timer_seconds_left, timer_message
        with timer_lock:
            timer_running = False
            timer_seconds_left = timer_total_seconds
            timer_message = ""
        update_timer_ui()

    def set_timer_preset(seconds):
        nonlocal timer_running, timer_total_seconds, timer_seconds_left, timer_message
        with timer_lock:
            timer_running = False
            timer_total_seconds = seconds
            timer_seconds_left = seconds
            timer_message = ""
        update_timer_ui()
        refresh_view()

    gist_token_input = ft.TextField(label="Token GitHub (uprawnienie 'gist')", value=get_config("gist_token", ""),
                                    password=True, can_reveal_password=True)
    gist_id_input = ft.TextField(label="ID Chmury (Zostaw puste, aby utworzyć nową)", value=get_config("gist_id", ""))
    ha_url_input = ft.TextField(label="Adres serwera HA", value=get_config("ha_url", ""))
    folder_input = ft.TextField(label="Ścieżka folderu lokalnego (PC)", value=get_config("sync_folder", ""))
    routine_input = ft.TextField(label="Nowa rutyna (np. Leki, Woda)", expand=True)

    def change_sync_mode(mode_name):
        nonlocal max_a_limit
        set_config("sync_mode", mode_name)
        set_config("gist_token", gist_token_input.value.strip())
        set_config("gist_id", gist_id_input.value.strip())
        set_config("ha_url", ha_url_input.value.strip())
        set_config("sync_folder", folder_input.value.strip())
        ok, msg = pull_database()

        if not ok:
            set_config("sync_mode", "none")
            warning_text.value = f"⚠️ {msg} (Zresetowano do trybu lokalnego)"
            status_text.value = ""
        else:
            push_ok, push_msg = push_database()
            gist_id_input.value = get_config("gist_id", "")
            status_text.value = f"✓ {push_msg or msg}"
            warning_text.value = ""

        refresh_view()

    def manual_sync(e=None):
        ok, msg = pull_database()
        status_text.value = f"✓ {msg}" if ok else ""
        warning_text.value = "" if ok else f"⚠️ {msg}"
        refresh_view()

    def trigger_manual_ota(e=None):
        try:
            status_text.value = "⏳ Sprawdzanie i pobieranie najnowszej wersji z GitHuba..."
            warning_text.value = ""
            page.update()

            github_base = "https://raw.githubusercontent.com/DemonSwans/MasterADHD/master"
            ts = int(datetime.now().timestamp())
            headers = {
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "User-Agent": "MasterADHD-OTA/1.0"
            }
            try:
                ctx = ssl._create_unverified_context()
            except Exception:
                ctx = None

            url_ver = f"{github_base}/version.txt?t={ts}"
            req_ver = urllib.request.Request(url_ver, headers=headers)
            with urllib.request.urlopen(req_ver, timeout=6, context=ctx) as resp:
                remote_ver = resp.read().decode("utf-8").strip()

            url_code = f"{github_base}/app_logic.py?t={ts}"
            req_code = urllib.request.Request(url_code, headers=headers)
            with urllib.request.urlopen(req_code, timeout=12, context=ctx) as resp_code:
                new_code = resp_code.read().decode("utf-8")

            # Weryfikacja składni przed zapisem
            compile(new_code, "app_logic_ota.py", "exec")

            # Zapisujemy w obu potencjalnych lokalizacjach (dla kompatybilności ze wszystkimi wersjami main.py)
            targets = [APP_DIR]
            base_env = os.environ.get("FLET_APP_STORAGE_DATA")
            if base_env:
                targets.append(os.path.join(base_env, ".master_adhd_data"))
                targets.append(os.path.join(base_env, "data"))
            else:
                targets.append(os.path.join(os.path.expanduser("~"), ".master_adhd_data"))

            for target_dir in set(targets):
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    with open(os.path.join(target_dir, "app_logic_ota.py"), "w", encoding="utf-8") as f:
                        f.write(new_code)
                    with open(os.path.join(target_dir, "ota_version.txt"), "w", encoding="utf-8") as f:
                        f.write(remote_ver)
                except Exception:
                    pass

            status_text.value = f"✓ Pomyślnie pobrano v{remote_ver}! Przeładowywanie..."
            page.update()

            ota_file = os.path.join(APP_DIR, "app_logic_ota.py")

            # Dynamiczne przeładowanie modułu w pamięci i natychmiastowe odświeżenie okna
            spec = importlib.util.spec_from_file_location("app_logic_ota", ota_file)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["app_logic"] = mod
            spec.loader.exec_module(mod)
            page.controls.clear()
            mod.main(page)
            return
        except Exception as ex:
            warning_text.value = f"Błąd aktualizacji OTA: {ex}"
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
            add_task_db(val, is_daily=False, priority="B")
            status_text.value = "✓ Zapisano myśl w Master liście"
        else:
            add_task_db(val, is_daily=False, priority="B")
            status_text.value = "✓ Dodano zadanie"
        inline_input_field.value = ""
        refresh_view()

    inline_input_field.on_submit = save_inline_input

    # --- OKIENKA DIALOGOWE ---
    quick_dump_field = ft.TextField(hint_text="Myśl do Master listy...", autofocus=True,
                                    on_submit=lambda e: save_quick_dump())

    def save_quick_dump(e=None):
        if quick_dump_field.value.strip():
            add_task_db(quick_dump_field.value.strip(), is_daily=False, priority="B")
            quick_dump_field.value = ""
            quick_dialog.open = False
            status_text.value = "✓ Zapisano w Masterzie"
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

    def save_master_task(e=None):
        if master_title_field.value.strip():
            add_task_db(master_title_field.value.strip(), is_daily=False, priority=selected_new_prio)
            master_title_field.value = ""
            master_dialog.open = False
            status_text.value = "✓ Dodano"
            refresh_view()

    master_dialog = ft.AlertDialog(title=ft.Text("➕ Nowy projekt / zadanie"), content=ft.Column(
        [master_title_field, ft.Text("Priorytet:", size=13), prio_select_row], tight=True),
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
    editing_task_id = None

    def open_edit_task_dialog(task_id, current_title):
        nonlocal editing_task_id
        editing_task_id = task_id
        edit_task_field.value = current_title
        edit_task_dialog.open = True
        page.update()

    def save_task_edit(e=None):
        nonlocal editing_task_id
        val = edit_task_field.value.strip()
        if editing_task_id and val:
            edit_task_title_db(editing_task_id, val)
            edit_task_dialog.open = False
            status_text.value = "✓ Zaktualizowano zadanie"
            warning_text.value = ""
            refresh_view()

    edit_task_dialog = ft.AlertDialog(
        title=ft.Text("✏️ Edytuj zadanie"),
        content=edit_task_field,
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

    def delete_subtask_action(task_id, subtask_idx, current_subtasks):
        if 0 <= subtask_idx < len(current_subtasks):
            current_subtasks.pop(subtask_idx)
            update_subtasks_db(task_id, current_subtasks)
            if task_id in active_subtask_idx_map:
                if active_subtask_idx_map[task_id] >= len(current_subtasks):
                    active_subtask_idx_map[task_id] = max(0, len(current_subtasks) - 1)
            status_text.value = "✓ Usunięto krok"
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
        status_text.value = "✓ Wyczyszczono historię sukcesów"
        warning_text.value = ""
        refresh_view()

    def close_dialogs():
        quick_dialog.open = master_dialog.open = subtask_dialog.open = False
        edit_task_dialog.open = delete_task_dialog.open = edit_subtask_dialog.open = clear_archive_dialog.open = False
        page.update()

    page.overlay.extend([quick_dialog, master_dialog, subtask_dialog, edit_task_dialog, delete_task_dialog, edit_subtask_dialog, clear_archive_dialog])

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
            ft.Container(expand=True),
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
        content=ft.Row([daily_mob_target, master_mob_target, settings_mob_box, archive_mob_box],
                       alignment=ft.MainAxisAlignment.SPACE_AROUND)
    )

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

        if mob:
            root_layout.content = ft.Column([content_area, ft.Divider(height=1, color=ft.Colors.GREY_800), bottom_nav],
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
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_size=18, icon_color=COLOR_PRIO_A, tooltip="Usuń rutynę",
                                      on_click=lambda e, rid=r[0]: (delete_routine_db(rid), refresh_view()))
                    ])
                ) for r in rout_db
            ], spacing=6)

            card_routines = ft.Container(
                bgcolor=COLOR_BG_CARD, border=ft.Border.all(1, COLOR_BG_BORDER), border_radius=12, padding=14,
                content=ft.Column([
                    ft.Text("Nawyki i Rutyny (Nie obciążają limitu A)", weight=ft.FontWeight.BOLD, size=14, color=COLOR_TEXT_PRIMARY),
                    ft.Row([
                        routine_input,
                        ft.Button("Dodaj", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY,
                                  on_click=lambda e: (add_routine_db(routine_input.value.strip()),
                                                      setattr(routine_input, 'value', ''),
                                                      refresh_view()))
                    ]),
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
                    ft.Button("Tylko lokalnie (Brak synchronizacji)", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_SECONDARY, on_click=lambda e: change_sync_mode("none"))
                ], spacing=8)
            )

            card_ota = ft.Container(
                bgcolor=COLOR_BG_CARD, border=ft.Border.all(1, COLOR_BG_BORDER), border_radius=12, padding=14,
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, size=18, color=COLOR_PRIO_B),
                        ft.Text("Aktualizacje OTA (GitHub)", weight=ft.FontWeight.BOLD, size=14, color=COLOR_TEXT_PRIMARY)
                    ], spacing=8),
                    ft.Text(f"Wersja wbudowana: v{FALLBACK_VERSION} | Aktywna: v{current_ver}", size=12, color=COLOR_TEXT_SECONDARY),
                    ft.Row([
                        ft.Button("🔄 Sprawdź i wymuś aktualizację z GitHuba", bgcolor=COLOR_BG_SURFACE, color=COLOR_TEXT_PRIMARY,
                                  on_click=trigger_manual_ota),
                        ft.Button("🛑 Zamknij aplikację (Wymuś restart)", bgcolor="#ef444422", color=COLOR_PRIO_A,
                                  on_click=lambda e: os._exit(0))
                    ], wrap=True, spacing=10)
                ], spacing=10)
            )

            content_column.controls = [
                ft.Row([title_text, ft.Container(expand=True), sync_badge if mob else ft.Container()]),
                status_text, warning_text,
                card_ota,
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
        all_routines = get_routines()

        if is_daily_mode:
            prog_val = calculate_progress_logic(all_view_tasks, all_routines, active_subtask_idx_map)
            progress_bar.value = prog_val
            progress_bar.color = COLOR_SUCCESS
            progress_bar.bgcolor = COLOR_BG_SURFACE
            progress_text.value = f"{int(prog_val * 100)}% ukończone"
            progress_text.color = COLOR_SUCCESS if prog_val >= 1.0 else COLOR_TEXT_PRIMARY
            progress_text.weight = ft.FontWeight.BOLD

            for r in all_routines:
                is_r_done = bool(r[2])
                routines_row.controls.append(
                    ft.Container(
                        bgcolor="#10b98122" if is_r_done else COLOR_BG_SURFACE,
                        border=ft.Border.all(1, COLOR_SUCCESS if is_r_done else COLOR_BG_BORDER),
                        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                        border_radius=14,
                        on_click=lambda e, rid=r[0], cdone=r[2]: (toggle_routine_db(rid, not cdone), refresh_view()),
                        content=ft.Row([
                            ft.Icon(ft.Icons.CHECK_CIRCLE if is_r_done else ft.Icons.RADIO_BUTTON_UNCHECKED, size=15,
                                    color=COLOR_SUCCESS if is_r_done else COLOR_TEXT_SECONDARY),
                            ft.Text(r[1], size=12, color=COLOR_TEXT_PRIMARY if is_r_done else COLOR_TEXT_SECONDARY,
                                    weight=ft.FontWeight.W_500,
                                    style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH if is_r_done else None))
                        ], spacing=6, tight=True)
                    )
                )

        if is_daily_mode:
            inline_input_field.hint_text = "⚡ Zapisz myśl do Master listy (Enter)..."
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
                        tooltip="Zapisz do Master listy",
                        on_click=save_inline_input
                    )
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER)
            )
        else:
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
                        on_click=save_inline_input
                    )
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER)
            )

        for task_id, title, is_daily, is_done, priority, subtasks in all_view_tasks:
            prio = priority if priority in ("A", "B", "C") else "B"
            card_border_color = COLOR_BG_BORDER if not is_done else "#21262d"

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

            # Ergonomiczny uchwyt przeciągania
            handle_w = 34 if mob else 30
            drag_handle = ft.Draggable(
                group="tasks",
                data=str(task_id),
                content=ft.Container(
                    content=ft.Icon(ft.Icons.DRAG_INDICATOR, size=18, color=COLOR_TEXT_SECONDARY),
                    width=handle_w, height=34, bgcolor=COLOR_BG_SURFACE, border_radius=8,
                    alignment=ft.Alignment.CENTER,
                    tooltip="Przeciągnij zadanie"
                ),
                content_when_dragging=ft.Container(
                    width=handle_w, height=34, bgcolor=COLOR_BG_SURFACE, border_radius=8, opacity=0.3
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
                        width=28,
                        height=28,
                        on_click=lambda e, tid=task_id, tit=title: open_edit_task_dialog(tid, tit)
                    )
                    del_btn = ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_size=16,
                        icon_color=COLOR_PRIO_A,
                        tooltip="Usuń zadanie",
                        width=28,
                        height=28,
                        on_click=lambda e, tid=task_id, tit=title: open_delete_task_dialog(tid, tit)
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
                        width=26,
                        height=26,
                        on_click=lambda e, tid=task_id, i=idx, subs=subtasks: open_edit_subtask_dialog(tid, i, subs)
                    )

                    if mob:
                        top_row = ft.Row([
                            ft.Column(prio_items, spacing=3, tight=True),
                            ft.Column([
                                ft.Row([
                                    ft.Text(title, size=12, color=COLOR_TEXT_SECONDARY, weight=ft.FontWeight.W_500),
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
                                    ft.Text(title, size=12, color=COLOR_TEXT_SECONDARY, weight=ft.FontWeight.W_500),
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
                        width=28,
                        height=28,
                        on_click=lambda e, tid=task_id, tit=title: open_edit_task_dialog(tid, tit)
                    )
                    del_btn = ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_size=16,
                        icon_color=COLOR_PRIO_A,
                        tooltip="Usuń zadanie",
                        width=28,
                        height=28,
                        on_click=lambda e, tid=task_id, tit=title: open_delete_task_dialog(tid, tit)
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
                    width=28,
                    height=28,
                    on_click=lambda e, tid=task_id, tit=title: open_edit_task_dialog(tid, tit)
                )
                del_btn = ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_size=16,
                    icon_color=COLOR_PRIO_A,
                    tooltip="Usuń zadanie",
                    width=28,
                    height=28,
                    on_click=lambda e, tid=task_id, tit=title: open_delete_task_dialog(tid, tit)
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

                daily_badge = ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.Icons.WB_SUNNY, size=12, color=COLOR_PRIO_B),
                        ft.Text("Daily", size=10, color=COLOR_PRIO_B, weight=ft.FontWeight.BOLD)
                    ], spacing=4, tight=True),
                    bgcolor="#f59e0b22",
                    padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                    border_radius=6,
                    tooltip="Zadanie jest na liście Daily"
                )

                add_krok_btn = ft.IconButton(
                    icon=ft.Icons.ADD_TASK,
                    icon_size=18,
                    icon_color=COLOR_PRIO_C,
                    tooltip="Dodaj krok do planu",
                    width=28,
                    height=28,
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
                                        icon=ft.Icons.EDIT_OUTLINED,
                                        icon_size=14,
                                        icon_color=COLOR_TEXT_SECONDARY,
                                        tooltip="Edytuj krok",
                                        width=26,
                                        height=26,
                                        on_click=lambda e, tid=task_id, i=idx, subs=subtasks: open_edit_subtask_dialog(tid, i, subs)
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.CLOSE,
                                        icon_size=14,
                                        icon_color=COLOR_PRIO_A,
                                        tooltip="Usuń krok",
                                        width=26,
                                        height=26,
                                        on_click=lambda e, tid=task_id, i=idx, subs=subtasks: delete_subtask_action(tid, i, subs)
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
                    if is_daily:
                        action_items.append(daily_badge)
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
                    if is_daily:
                        title_row_controls.append(daily_badge)
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
                ft.IconButton(ft.Icons.EMOJI_EVENTS_ROUNDED, icon_color=COLOR_PRIO_B, tooltip="Dziennik Sukcesów", on_click=lambda e: switch_view("archive")),
                ft.IconButton(ft.Icons.SYNC, icon_color=COLOR_TEXT_SECONDARY, tooltip="Synchronizuj bazę", on_click=manual_sync),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            quick_bar
        ]
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

        elements.extend([status_text, warning_text, tasks_column])
        content_column.controls = elements

        if is_daily_mode:
            content_column.controls.append(
                ft.Row([
                    ft.Button(
                        "🌙 Koniec dnia (Reset)",
                        icon=ft.Icons.NIGHTLIGHT_ROUND,
                        bgcolor=COLOR_BG_SURFACE,
                        color=COLOR_TEXT_PRIMARY,
                        on_click=lambda e: (end_of_day_db(), refresh_view())
                    )
                ], alignment=ft.MainAxisAlignment.END)
            )

        page.update()

    page.on_resize = lambda e: refresh_view()
    page.add(ft.SafeArea(content=root_layout, expand=True))
    refresh_view()