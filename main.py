import json
import os
import shutil
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
import flet as ft

APP_VERSION = "1.1"

LOCAL_DB = "tasks.db"
CONFIG_DB = "app_config.db"


# --- KONFIGURACJA LOKALNA ---
def init_config_db():
    with sqlite3.connect(CONFIG_DB) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")


def get_config(key, default=""):
    with sqlite3.connect(CONFIG_DB) as conn:
        res = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return res[0] if res else default


def set_config(key, value):
    with sqlite3.connect(CONFIG_DB) as conn:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))


# --- SYSTEM AKTUALIZACJI ---
def check_for_updates(page: ft.Page):
    try:
        url = "https://raw.githubusercontent.com/DemonSwans/MasterADHD/main/version.txt"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                remote_version = resp.read().decode("utf-8").strip()
                if remote_version != APP_VERSION:
                    show_update_dialog(page, remote_version)
    except Exception:
        pass


def show_update_dialog(page: ft.Page, new_version: str):
    def download_update(e):
        apk_url = "https://github.com/DemonSwans/MasterADHD/releases/download/latest/MasterADHD.apk"
        page.launch_url(apk_url)
        dialog.open = False
        page.update()

    def cancel_update(e):
        dialog.open = False
        page.update()

    dialog = ft.AlertDialog(
        title=ft.Text("🚀 Dostępna nowa wersja!"),
        content=ft.Text(f"Wykryto aktualizację do wersji {new_version}.\nCzy chcesz ją teraz zainstalować?"),
        actions=[
            ft.TextButton("Później", on_click=cancel_update),
            ft.Button("Aktualizuj", bgcolor=ft.Colors.GREEN_700, color=ft.Colors.WHITE, on_click=download_update)
        ]
    )
    page.overlay.append(dialog)
    dialog.open = True
    page.update()


# --- SYNCHRONIZACJA Z ARCHIWIZACJĄ ---
def is_valid_sqlite(file_data):
    return file_data.startswith(b"SQLite format 3")


def pull_database():
    mode = get_config("sync_mode", "none")
    if mode == "ha_server":
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
    mode = get_config("sync_mode", "none")
    if not os.path.exists(LOCAL_DB): return False, ""

    if mode == "ha_server":
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


# --- BAZA DANYCH I LOGIKA ---
def init_db():
    init_config_db()
    pull_database()
    with sqlite3.connect(LOCAL_DB) as conn:
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
    push_database()


def toggle_routine_db(routine_id, is_done):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute("UPDATE routines SET is_done = ? WHERE id = ?",
                                                         (int(is_done), routine_id))
    push_database()


def delete_routine_db(routine_id):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
    push_database()


def update_subtasks_db(task_id, subtasks_list):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute("UPDATE tasks SET subtasks = ? WHERE id = ?",
                                                         (json.dumps(subtasks_list), task_id))
    push_database()


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
    push_database()


def toggle_done_db(task_id, is_done):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute("UPDATE tasks SET is_done = ? WHERE id = ?",
                                                         (int(is_done), task_id))
    push_database()


def set_daily_db(task_id, is_daily):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute("UPDATE tasks SET is_daily = ? WHERE id = ?",
                                                         (int(is_daily), task_id))
    push_database()


def update_priority_db(task_id, new_priority):
    with sqlite3.connect(LOCAL_DB) as conn: conn.execute("UPDATE tasks SET priority = ? WHERE id = ?",
                                                         (new_priority, task_id))
    push_database()


def end_of_day_db():
    with sqlite3.connect(LOCAL_DB) as conn:
        conn.execute("DELETE FROM tasks WHERE is_done = 1")
        conn.execute("UPDATE tasks SET is_daily = 0 WHERE is_daily = 1 AND is_done = 0")
        conn.execute("UPDATE routines SET is_done = 0")
    push_database()


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


# --- INTERFEJS (Flet) ---
def main(page: ft.Page):
    page.title = "ADHD Master & Daily"
    page.window.width = 950
    page.window.height = 760
    page.padding = 0
    page.theme_mode = ft.ThemeMode.DARK
    init_db()

    check_for_updates(page)

    current_view = "daily"
    max_a_limit = int(get_config("max_a_limit", "3"))
    open_prio_task_id = None
    active_drag_task_id = None
    active_subtask_idx_map = {}

    priority_colors = {"A": ft.Colors.RED_500, "B": ft.Colors.AMBER_700, "C": ft.Colors.BLUE_500}

    tasks_column = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=8)
    routines_row = ft.Row(wrap=True, spacing=10)

    progress_bar = ft.ProgressBar(value=0.0, color=ft.Colors.GREEN_500, bgcolor=ft.Colors.GREY_800, height=8)
    progress_text = ft.Text("Postęp dnia: 0%", size=12, color=ft.Colors.GREY_400)

    status_text = ft.Text("", size=12, color=ft.Colors.GREEN_400)
    warning_text = ft.Text("", size=12, color=ft.Colors.RED_400)
    title_text = ft.Text("Daily", size=24, weight=ft.FontWeight.BOLD)

    ha_url_input = ft.TextField(label="Adres serwera HA", value=get_config("ha_url", ""))
    folder_input = ft.TextField(label="Ścieżka folderu (wymagana przed włączeniem)",
                                value=get_config("sync_folder", ""))
    routine_input = ft.TextField(label="Nowa rutyna (np. Leki, Woda)", expand=True)

    def change_sync_mode(mode_name):
        set_config("sync_mode", mode_name)
        set_config("ha_url", ha_url_input.value.strip())
        set_config("sync_folder", folder_input.value.strip())
        ok, msg = pull_database()

        if not ok:
            set_config("sync_mode", "none")
            warning_text.value = f"⚠️ {msg} (Zresetowano do trybu lokalnego)"
            status_text.value = ""
        else:
            push_database()
            status_text.value = f"✓ {msg}"
            warning_text.value = ""

        refresh_view()

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

    def close_dialogs():
        quick_dialog.open = master_dialog.open = subtask_dialog.open = False
        page.update()

    page.overlay.extend([quick_dialog, master_dialog, subtask_dialog])

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

    # --- MENU BOCZNE ---
    def switch_view(view_name):
        nonlocal current_view, open_prio_task_id
        current_view = view_name
        open_prio_task_id = None
        status_text.value = warning_text.value = ""
        if view_name in ("daily", "master"): pull_database()
        refresh_view()

    def create_nav(icon, label, view):
        return ft.Container(padding=10, border_radius=8, on_click=lambda e: switch_view(view),
                            content=ft.Row([ft.Icon(icon, size=20), ft.Text(label, size=15)], spacing=12))

    daily_nav_box = create_nav(ft.Icons.WB_SUNNY_OUTLINED, "Daily", "daily")
    master_nav_box = create_nav(ft.Icons.LIST_ALT, "Master", "master")
    settings_nav_box = create_nav(ft.Icons.SETTINGS_OUTLINED, "Ustawienia", "settings")

    def highlight_nav(box, is_hover, default_border=None):
        box.border = ft.Border.all(2, ft.Colors.AMBER_400) if is_hover else default_border
        page.update()

    daily_nav_target = ft.DragTarget(group="tasks", content=daily_nav_box,
                                     on_will_accept=lambda e: highlight_nav(daily_nav_box, True),
                                     on_leave=lambda e: highlight_nav(daily_nav_box, False),
                                     on_accept=lambda e: (highlight_nav(daily_nav_box, False),
                                                          move_task_between_lists(resolve_dragged_task_id(e), 1)))
    master_nav_target = ft.DragTarget(group="tasks", content=master_nav_box,
                                      on_will_accept=lambda e: highlight_nav(master_nav_box, True),
                                      on_leave=lambda e: highlight_nav(master_nav_box, False),
                                      on_accept=lambda e: (highlight_nav(master_nav_box, False),
                                                           move_task_between_lists(resolve_dragged_task_id(e), 0)))

    sync_badge = ft.Text("", size=11, color=ft.Colors.GREY_500)

    sidebar = ft.Container(width=200, bgcolor="#16181d", padding=15, content=ft.Column(
        [ft.Text("Nawigacja", size=12, color=ft.Colors.GREY_500), daily_nav_target, master_nav_target,
         ft.Container(expand=True), sync_badge, ft.Divider(height=1, color=ft.Colors.GREY_800), settings_nav_box],
        expand=True, spacing=6))
    content_column = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO)
    content_area = ft.Container(content=content_column, expand=True, padding=25)

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

    def refresh_view():
        daily_nav_box.bgcolor = ft.Colors.GREY_800 if current_view == "daily" else None
        master_nav_box.bgcolor = ft.Colors.GREY_800 if current_view == "master" else None
        settings_nav_box.bgcolor = ft.Colors.GREY_800 if current_view == "settings" else None

        mode = get_config("sync_mode", "none")
        sync_badge.value = "🏠 Sync: Serwer HA" if mode == "ha_server" else "📁 Sync: Dysk" if mode == "folder" else "💾 Tryb lokalny"
        sync_badge.color = ft.Colors.GREEN_400 if mode == "ha_server" else ft.Colors.BLUE_400 if mode == "folder" else ft.Colors.GREY_500

        tasks_column.controls.clear()
        routines_row.controls.clear()

        if current_view == "settings":
            title_text.value = "Ustawienia i Rutyny"
            rout_db = get_routines()
            rout_list = ft.Column([ft.Row([ft.Text(r[1]), ft.IconButton(icon=ft.Icons.DELETE, icon_size=16,
                                                                        on_click=lambda e, rid=r[0]: (
                                                                            delete_routine_db(rid), refresh_view()))])
                                   for r in rout_db])
            content_column.controls = [
                title_text, status_text, warning_text, ft.Divider(height=15),
                ft.Text("Nawyki i Rutyny (Nie obciążają limitu A)", weight=ft.FontWeight.BOLD),
                ft.Row([routine_input, ft.Button("Dodaj rutynę",
                                                 on_click=lambda e: (add_routine_db(routine_input.value.strip()),
                                                                     setattr(routine_input, 'value', ''),
                                                                     refresh_view()))]),
                rout_list, ft.Divider(height=15),
                ft.Text("Maks. zadań 'A' na Daily:"),
                ft.Row([
                    ft.Button("3", bgcolor=ft.Colors.AMBER_700 if max_a_limit == 3 else ft.Colors.GREY_800,
                              color=ft.Colors.WHITE, on_click=lambda e: (set_config("max_a_limit", 3), refresh_view())),
                    ft.Button("4", bgcolor=ft.Colors.AMBER_700 if max_a_limit == 4 else ft.Colors.GREY_800,
                              color=ft.Colors.WHITE, on_click=lambda e: (set_config("max_a_limit", 4), refresh_view())),
                ]), ft.Divider(height=25),
                ft.Text("Synchronizacja", weight=ft.FontWeight.BOLD),
                ha_url_input, ft.Button("Włącz HA", on_click=lambda e: change_sync_mode("ha_server")),
                folder_input, ft.Button("Włącz Dysk", on_click=lambda e: change_sync_mode("folder")),
                ft.Button("Tylko lokalnie", on_click=lambda e: change_sync_mode("none")),
            ]
            page.update()
            return

        is_daily_mode = (current_view == "daily")
        title_text.value = "Daily" if is_daily_mode else "Master Lista (Projekty i Zadania)"
        all_view_tasks = get_tasks(is_daily_view=is_daily_mode)
        all_routines = get_routines()

        if is_daily_mode:
            prog_val = calculate_progress_logic(all_view_tasks, all_routines, active_subtask_idx_map)
            progress_bar.value = prog_val
            progress_text.value = f"Postęp dnia: {int(prog_val * 100)}%"
            for r in all_routines:
                routines_row.controls.append(
                    ft.Container(bgcolor=ft.Colors.GREEN_800 if r[2] else ft.Colors.GREY_800, padding=8,
                                 border_radius=15,
                                 on_click=lambda e, rid=r[0], cdone=r[2]: (toggle_routine_db(rid, not cdone),
                                                                           refresh_view()), content=ft.Row(
                            [ft.Icon(ft.Icons.CHECK_CIRCLE if r[2] else ft.Icons.RADIO_BUTTON_UNCHECKED, size=16),
                             ft.Text(r[1], size=13,
                                     style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH) if r[2] else None)],
                            spacing=6, tight=True)))

        for task_id, title, is_daily, is_done, priority, subtasks in all_view_tasks:
            prio = priority if priority in ("A", "B", "C") else "B"

            prio_items = [ft.Container(
                content=ft.Row([ft.Text(prio, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=14)],
                               alignment=ft.MainAxisAlignment.CENTER), width=36, height=32, border_radius=6,
                bgcolor=priority_colors[prio] if not is_done else ft.Colors.GREY_700,
                on_click=lambda e, tid=task_id: toggle_prio_menu(tid))]
            if open_prio_task_id == task_id:
                for other_p in ("A", "B", "C"):
                    if other_p != prio: prio_items.append(ft.Container(
                        content=ft.Row([ft.Text(other_p, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=13)],
                                       alignment=ft.MainAxisAlignment.CENTER), width=36, height=28,
                        bgcolor=priority_colors[other_p], border_radius=6,
                        on_click=lambda e, tid=task_id, np=other_p, idm=is_daily_mode: change_priority(tid, np, idm)))

            # Gładkie pole w delikatnie innym odcieniu po prawej stronie zadania do przeciągania
            drag_handle = ft.Draggable(
                group="tasks",
                data=str(task_id),
                content=ft.Container(
                    width=28,
                    height=36,
                    bgcolor="#2a2e38",
                    border_radius=6,
                ),
                content_when_dragging=ft.Container(
                    width=28,
                    height=36,
                    bgcolor="#2a2e38",
                    border_radius=6,
                    opacity=0.3,
                ),
                content_feedback=ft.Container(
                    padding=10,
                    bgcolor=ft.Colors.GREY_800,
                    border_radius=8,
                    content=ft.Text(title, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)
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

                    row_controls = [
                        ft.Column(prio_items, spacing=4, tight=True),
                        ft.Checkbox(
                            label=current_st["title"],
                            value=bool(current_st["is_done"]),
                            expand=True,
                            on_change=lambda e, tid=task_id, s_idx=idx, s_list=subtasks: handle_task_check(tid,
                                                                                                           e.control.value,
                                                                                                           s_idx,
                                                                                                           s_list)
                        )
                    ]

                    if current_st["is_done"] == 1 and idx < len(subtasks) - 1:
                        row_controls.append(
                            ft.IconButton(
                                icon=ft.Icons.ARROW_FORWARD,
                                icon_color=ft.Colors.GREEN_400,
                                tooltip="Kolejny krok",
                                on_click=lambda e, tid=task_id: next_subtask(tid)
                            )
                        )

                    row_controls.append(drag_handle)

                    task_content = ft.Container(
                        bgcolor="#1e2128", padding=10, border_radius=8,
                        content=ft.Row(controls=row_controls, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                    )
                else:
                    task_content = ft.Container(
                        bgcolor="#1e2128", padding=10, border_radius=8,
                        content=ft.Row(
                            controls=[
                                ft.Column(prio_items, spacing=4, tight=True),
                                ft.Checkbox(label=title, value=bool(is_done), expand=True,
                                            on_change=lambda e, tid=task_id: handle_task_check(tid, e.control.value,
                                                                                               None, subtasks)),
                                drag_handle
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        )
                    )
            else:
                title_row_controls = [
                    ft.Column(prio_items, spacing=4, tight=True),
                    ft.Checkbox(label=title, value=bool(is_done), expand=True,
                                on_change=lambda e, tid=task_id: handle_task_check(tid, e.control.value, None,
                                                                                   subtasks)),
                ]

                if is_daily:
                    title_row_controls.append(ft.Icon(ft.Icons.WB_SUNNY, size=18, color=ft.Colors.AMBER_400,
                                                      tooltip="Zadanie jest na liście Daily"))

                title_row_controls.append(
                    ft.TextButton("👣 Dodaj krok", icon=ft.Icons.ADD,
                                  on_click=lambda e, tid=task_id, subs=subtasks: open_subtask_dialog(tid, subs))
                )
                title_row_controls.append(drag_handle)

                subtask_controls = []
                for idx, st in enumerate(subtasks):
                    subtask_controls.append(ft.Row(controls=[ft.Container(width=40),
                                                             ft.Checkbox(label=st["title"], value=bool(st["is_done"]),
                                                                         label_style=ft.TextStyle(size=13,
                                                                                                  color=ft.Colors.GREY_300),
                                                                         on_change=lambda e, tid=task_id, subs=subtasks,
                                                                                          i=idx: handle_task_check(tid,
                                                                                                                   e.control.value,
                                                                                                                   i,
                                                                                                                   subs))]))

                task_content = ft.Container(
                    bgcolor="#1e2128", padding=10, border_radius=8,
                    content=ft.Column([
                                          ft.Row(title_row_controls, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                                      ] + subtask_controls, spacing=2)
                )

            tasks_column.controls.append(task_content)

        top_right_button = ft.Button("⚡ Szybka myśl", bgcolor=ft.Colors.GREY_800, color=ft.Colors.WHITE,
                                     on_click=open_quick_modal) if is_daily_mode else ft.Button(
            "➕ Dodaj Projekt / Zadanie", bgcolor=ft.Colors.GREY_800, color=ft.Colors.WHITE, on_click=open_master_modal)

        elements = [ft.Row([title_text, ft.Container(expand=True),
                            ft.IconButton(ft.Icons.SYNC, on_click=lambda e: (pull_database(), refresh_view())),
                            top_right_button])]
        if is_daily_mode:
            elements.extend([progress_bar, progress_text, ft.Divider(height=10)])
            if all_routines: elements.extend(
                [ft.Text("Nawyki i Rutyny:", size=13, weight=ft.FontWeight.BOLD), routines_row, ft.Divider(height=10)])
        elements.extend([status_text, warning_text, tasks_column])
        content_column.controls = elements

        if is_daily_mode: content_column.controls.append(ft.Row(
            [ft.Button("🌙 Koniec dnia (Reset)", bgcolor=ft.Colors.GREY_800, color=ft.Colors.WHITE,
                       on_click=lambda e: (end_of_day_db(), refresh_view()))], alignment=ft.MainAxisAlignment.END))

        page.update()

    page.add(ft.Row([sidebar, ft.VerticalDivider(width=1, color=ft.Colors.GREY_800), content_area], expand=True, spacing=0))
    refresh_view()

if __name__ == "__main__":
    ft.run(main)