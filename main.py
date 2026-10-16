import json
import os
import shutil
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
import flet as ft

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


# --- MECHANIZM SYNCHRONIZACJI Z ARCHIWIZACJĄ ---
def pull_database():
    mode = get_config("sync_mode", "none")

    if mode == "ha_server":
        url = get_config("ha_url", "").strip().rstrip("/")
        if not url:
            return False, "Brak adresu serwera HA"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    with open(LOCAL_DB, "wb") as f:
                        f.write(resp.read())
                    return True, "Pobrano bazę z serwera HA"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return True, "Serwer HA gotowy (brak pliku – zostanie utworzony)"
            return False, f"Błąd serwera: {e.code}"
        except Exception as e:
            return False, f"Brak połączenia z HA: {e}"

    elif mode == "folder":
        folder = get_config("sync_folder", "").strip()
        remote_file = os.path.join(folder, "tasks.db")
        if os.path.exists(remote_file):
            try:
                shutil.copy2(remote_file, LOCAL_DB)
                return True, "Pobrano bazę z folderu Dysku"
            except Exception as e:
                return False, f"Błąd odczytu z dysku: {e}"
        return True, "Folder gotowy (plik zostanie utworzony)"

    return True, "Tryb lokalny"


def push_database():
    mode = get_config("sync_mode", "none")
    if not os.path.exists(LOCAL_DB):
        return False, ""

    if mode == "ha_server":
        url = get_config("ha_url", "").strip().rstrip("/")
        if not url:
            return False, "Brak adresu serwera HA"
        try:
            with open(LOCAL_DB, "rb") as f:
                data = f.read()
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/octet-stream"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    return True, "Zapisano na serwerze HA (stara kopia w archiwum)"
        except Exception as e:
            return False, f"Błąd wysyłania na serwer HA: {e}"

    elif mode == "folder":
        folder = get_config("sync_folder", "").strip()
        if not folder or not os.path.exists(folder):
            return False, "Wskazany folder nie istnieje!"
        try:
            archive_dir = os.path.join(folder, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            remote_file = os.path.join(folder, "tasks.db")

            if os.path.exists(remote_file):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                shutil.copy2(remote_file, os.path.join(archive_dir, f"tasks_{ts}.db"))

                backups = sorted(
                    [os.path.join(archive_dir, f) for f in os.listdir(archive_dir) if f.endswith(".db")]
                )
                while len(backups) > 25:
                    os.remove(backups.pop(0))

            shutil.copy2(LOCAL_DB, remote_file)
            return True, "Zaktualizowano na Dysku (stara wersja w archive/)"
        except Exception as e:
            return False, f"Błąd zapisu na dysku: {e}"

    return True, ""


# --- BAZA DANYCH ZADAŃ ---
def init_db():
    init_config_db()
    pull_database()
    with sqlite3.connect(LOCAL_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                is_daily INTEGER DEFAULT 0,
                is_done INTEGER DEFAULT 0,
                priority TEXT DEFAULT 'B'
            )
        """)


def get_tasks(is_daily_view=True):
    with sqlite3.connect(LOCAL_DB) as conn:
        flag = 1 if is_daily_view else 0
        return conn.execute(
            "SELECT id, title, is_daily, is_done, priority FROM tasks "
            "WHERE is_daily = ? ORDER BY is_done ASC, priority ASC, id DESC",
            (flag,),
        ).fetchall()


def get_task_priority(task_id):
    with sqlite3.connect(LOCAL_DB) as conn:
        res = conn.execute("SELECT priority FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return res[0] if res else "B"


def count_daily_priority_a():
    with sqlite3.connect(LOCAL_DB) as conn:
        res = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE is_daily = 1 AND priority = 'A' AND is_done = 0"
        ).fetchone()
        return res[0] if res else 0


def add_task_db(title, is_daily, priority="B"):
    with sqlite3.connect(LOCAL_DB) as conn:
        conn.execute(
            "INSERT INTO tasks (title, is_daily, priority) VALUES (?, ?, ?)",
            (title, int(is_daily), priority),
        )
    push_database()


def toggle_done_db(task_id, is_done):
    with sqlite3.connect(LOCAL_DB) as conn:
        conn.execute("UPDATE tasks SET is_done = ? WHERE id = ?", (int(is_done), task_id))
    push_database()


def set_daily_db(task_id, is_daily):
    with sqlite3.connect(LOCAL_DB) as conn:
        conn.execute("UPDATE tasks SET is_daily = ? WHERE id = ?", (int(is_daily), task_id))
    push_database()


def update_priority_db(task_id, new_priority):
    with sqlite3.connect(LOCAL_DB) as conn:
        conn.execute("UPDATE tasks SET priority = ? WHERE id = ?", (new_priority, task_id))
    push_database()


def end_of_day_db():
    with sqlite3.connect(LOCAL_DB) as conn:
        conn.execute("DELETE FROM tasks WHERE is_done = 1")
        conn.execute("UPDATE tasks SET is_daily = 0 WHERE is_daily = 1 AND is_done = 0")
    push_database()


# --- INTERFEJS (Flet) ---
def main(page: ft.Page):
    page.title = "ADHD Master & Daily"
    page.window.width = 920
    page.window.height = 720
    page.padding = 0
    page.theme_mode = ft.ThemeMode.DARK
    init_db()

    current_view = "daily"
    max_a_limit = int(get_config("max_a_limit", "3"))
    open_prio_task_id = None
    active_drag_task_id = None

    priority_colors = {
        "A": ft.Colors.RED_500,
        "B": ft.Colors.AMBER_700,
        "C": ft.Colors.BLUE_500,
    }

    tasks_column = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=8)
    status_text = ft.Text("", size=12, color=ft.Colors.GREEN_400)
    warning_text = ft.Text("", size=12, color=ft.Colors.RED_400)
    title_text = ft.Text("Daily", size=24, weight=ft.FontWeight.BOLD)

    ha_url_input = ft.TextField(
        label="Adres serwera HA (np. http://192.168.1.50:8765)",
        value=get_config("ha_url", "http://192.168.1.50:8765"),
    )
    folder_input = ft.TextField(
        label="Ścieżka folderu Dysku Google (np. G:\\Mój dysk\\ADHD)",
        value=get_config("sync_folder", ""),
    )

    def change_sync_mode(mode_name):
        set_config("sync_mode", mode_name)
        set_config("ha_url", ha_url_input.value.strip())
        set_config("sync_folder", folder_input.value.strip())
        ok, msg = pull_database()
        if not ok:
            warning_text.value = f"⚠️ {msg}"
            status_text.value = ""
        else:
            push_database()
            status_text.value = f"✓ {msg}"
            warning_text.value = ""
        refresh_view()

    # --- OKIENKA DIALOGOWE ---
    quick_dump_field = ft.TextField(
        hint_text="Wpisz myśl do zrzucenia na Master listę...",
        autofocus=True,
        on_submit=lambda e: save_quick_dump(e),
    )

    def close_quick_dialog(e):
        quick_dialog.open = False
        page.update()

    def save_quick_dump(e):
        text = quick_dump_field.value.strip()
        if text:
            add_task_db(text, is_daily=False, priority="B")
            quick_dump_field.value = ""
            quick_dialog.open = False
            status_text.value = "✓ Zapisano w Master Liście (zsynchronizowano)"
            warning_text.value = ""
            refresh_view()

    quick_dialog = ft.AlertDialog(
        title=ft.Text("⚡ Szybki zrzut do Mastera"),
        content=ft.Container(content=quick_dump_field, width=380),
        actions=[
            ft.TextButton(content="Anuluj", on_click=close_quick_dialog),
            ft.Button(
                content="Zapisz",
                bgcolor=ft.Colors.GREY_800,
                color=ft.Colors.WHITE,
                on_click=save_quick_dump,
            ),
        ],
    )

    master_title_field = ft.TextField(
        hint_text="Treść zadania...",
        autofocus=True,
        on_submit=lambda e: save_master_task(e),
    )
    selected_new_prio = "B"
    prio_select_row = ft.Row(spacing=10)

    def build_modal_prio_buttons():
        prio_select_row.controls.clear()
        for p in ("A", "B", "C"):
            is_sel = (p == selected_new_prio)
            prio_select_row.controls.append(
                ft.Container(
                    content=ft.Row(
                        [ft.Text(p, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    width=45,
                    height=36,
                    bgcolor=priority_colors[p] if is_sel else ft.Colors.GREY_800,
                    border=ft.Border.all(2, ft.Colors.WHITE) if is_sel else None,
                    border_radius=8,
                    on_click=lambda e, pr=p: select_modal_prio(pr),
                )
            )

    def select_modal_prio(p):
        nonlocal selected_new_prio
        selected_new_prio = p
        build_modal_prio_buttons()
        page.update()

    def close_master_dialog(e):
        master_dialog.open = False
        page.update()

    def save_master_task(e):
        text = master_title_field.value.strip()
        if text:
            add_task_db(text, is_daily=False, priority=selected_new_prio)
            master_title_field.value = ""
            master_dialog.open = False
            status_text.value = "✓ Dodano zadanie do Master Listy"
            warning_text.value = ""
            refresh_view()

    master_dialog = ft.AlertDialog(
        title=ft.Text("➕ Nowe zadanie (Master)"),
        content=ft.Container(
            width=380,
            content=ft.Column(
                [
                    master_title_field,
                    ft.Text("Wybierz priorytet:", size=13, color=ft.Colors.GREY_400),
                    prio_select_row,
                ],
                tight=True,
                spacing=12,
            ),
        ),
        actions=[
            ft.TextButton(content="Anuluj", on_click=close_master_dialog),
            ft.Button(
                content="Dodaj",
                bgcolor=ft.Colors.GREY_800,
                color=ft.Colors.WHITE,
                on_click=save_master_task,
            ),
        ],
    )

    page.overlay.extend([quick_dialog, master_dialog])

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

    # --- OBSŁUGA PRZECIĄGANIA ---
    def set_active_drag(task_id):
        nonlocal active_drag_task_id
        active_drag_task_id = task_id

    def resolve_dragged_task_id(e):
        if active_drag_task_id is not None:
            return active_drag_task_id
        try:
            ctrl = page.get_control(e.src_id)
            if ctrl and getattr(ctrl, "data", None) is not None:
                return int(ctrl.data)
        except Exception:
            pass
        return None

    def move_task_between_lists(task_id, target_daily):
        nonlocal active_drag_task_id
        active_drag_task_id = None
        if task_id is None:
            return
        prio = get_task_priority(task_id)
        if target_daily == 1 and prio == "A" and count_daily_priority_a() >= max_a_limit:
            warning_text.value = (
                f"⚠️ Blokada: Masz już {max_a_limit} zadania typu 'A' na Daily! "
                "Zmień priorytet tego zadania lub ukończ inne zadanie 'A'."
            )
            status_text.value = ""
            page.update()
            return

        set_daily_db(task_id, target_daily)
        warning_text.value = ""
        status_text.value = (
            "✓ Przeniesiono zadanie do Daily"
            if target_daily == 1
            else "✓ Cofnięto zadanie do Master Listy"
        )
        refresh_view()

    # --- MENU BOCZNE ---
    def switch_view(view_name):
        nonlocal current_view, open_prio_task_id
        current_view = view_name
        open_prio_task_id = None
        status_text.value = ""
        warning_text.value = ""
        if view_name in ("daily", "master"):
            pull_database()
        refresh_view()

    daily_nav_box = ft.Container(
        padding=12,
        border_radius=8,
        on_click=lambda e: switch_view("daily"),
        content=ft.Row(
            [ft.Icon(ft.Icons.WB_SUNNY_OUTLINED, size=20), ft.Text("Daily", size=15, weight=ft.FontWeight.W_500)],
            spacing=12,
        ),
    )

    master_nav_box = ft.Container(
        padding=12,
        border_radius=8,
        on_click=lambda e: switch_view("master"),
        content=ft.Row(
            [ft.Icon(ft.Icons.LIST_ALT, size=20), ft.Text("Master", size=15, weight=ft.FontWeight.W_500)],
            spacing=12,
        ),
    )

    settings_nav_box = ft.Container(
        padding=12,
        border_radius=8,
        on_click=lambda e: switch_view("settings"),
        content=ft.Row(
            [ft.Icon(ft.Icons.SETTINGS_OUTLINED, size=20), ft.Text("Ustawienia", size=15, weight=ft.FontWeight.W_500)],
            spacing=12,
        ),
    )

    def highlight_nav(box, is_hover, default_border=None):
        box.border = ft.Border.all(2, ft.Colors.AMBER_400) if is_hover else default_border
        page.update()

    daily_nav_target = ft.DragTarget(
        group="tasks",
        content=daily_nav_box,
        on_will_accept=lambda e: highlight_nav(daily_nav_box, True),
        on_leave=lambda e: highlight_nav(daily_nav_box, False),
        on_accept=lambda e: (
            highlight_nav(daily_nav_box, False),
            move_task_between_lists(resolve_dragged_task_id(e), 1),
        ),
    )

    master_nav_target = ft.DragTarget(
        group="tasks",
        content=master_nav_box,
        on_will_accept=lambda e: highlight_nav(master_nav_box, True),
        on_leave=lambda e: highlight_nav(master_nav_box, False),
        on_accept=lambda e: (
            highlight_nav(master_nav_box, False),
            move_task_between_lists(resolve_dragged_task_id(e), 0),
        ),
    )

    sync_badge = ft.Text("", size=11, color=ft.Colors.GREY_500)

    sidebar = ft.Container(
        width=200,
        bgcolor="#16181d",
        padding=15,
        content=ft.Column(
            controls=[
                ft.Text("Nawigacja", size=12, color=ft.Colors.GREY_500),
                daily_nav_target,
                master_nav_target,
                ft.Container(expand=True),
                sync_badge,
                ft.Divider(height=1, color=ft.Colors.GREY_800),
                settings_nav_box,
            ],
            expand=True,
            spacing=6,
        ),
    )

    content_column = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO)
    content_area = ft.Container(content=content_column, expand=True, padding=25)

    def toggle_prio_menu(task_id):
        nonlocal open_prio_task_id
        open_prio_task_id = None if open_prio_task_id == task_id else task_id
        refresh_view()

    def change_priority(task_id, new_prio, is_daily_task):
        nonlocal open_prio_task_id
        open_prio_task_id = None
        if is_daily_task and new_prio == "A":
            current_prio = get_task_priority(task_id)
            if current_prio != "A" and count_daily_priority_a() >= max_a_limit:
                warning_text.value = (
                    f"⚠️ Limit osiągnięty! Na Daily nie może być więcej niż {max_a_limit} zadań 'A'."
                )
                status_text.value = ""
                refresh_view()
                return

        update_priority_db(task_id, new_prio)
        warning_text.value = ""
        status_text.value = ""
        refresh_view()

    def change_done(task_id, val):
        toggle_done_db(task_id, val)
        status_text.value = ""
        refresh_view()

    def handle_end_of_day(e):
        end_of_day_db()
        status_text.value = "🌙 Koniec dnia: ukończone usunięte, niewykonane wróciły do Mastera."
        warning_text.value = ""
        refresh_view()

    def set_max_a_limit(new_limit):
        nonlocal max_a_limit
        max_a_limit = new_limit
        set_config("max_a_limit", str(new_limit))
        refresh_view()

    def manual_sync_pull(e):
        ok, msg = pull_database()
        status_text.value = f"🔄 {msg}" if ok else ""
        warning_text.value = "" if ok else f"⚠️ {msg}"
        refresh_view()

    def refresh_view():
        daily_nav_box.bgcolor = ft.Colors.GREY_800 if current_view == "daily" else None
        master_nav_box.bgcolor = ft.Colors.GREY_800 if current_view == "master" else None
        settings_nav_box.bgcolor = ft.Colors.GREY_800 if current_view == "settings" else None

        mode = get_config("sync_mode", "none")
        if mode == "ha_server":
            sync_badge.value = "🏠 Sync: Serwer HA"
            sync_badge.color = ft.Colors.GREEN_400
        elif mode == "folder":
            sync_badge.value = "📁 Sync: Folder / Dysk"
            sync_badge.color = ft.Colors.BLUE_400
        else:
            sync_badge.value = "💾 Tryb lokalny"
            sync_badge.color = ft.Colors.GREY_500

        tasks_column.controls.clear()

        if current_view == "settings":
            title_text.value = "Ustawienia"
            content_column.controls = [
                title_text,
                status_text,
                warning_text,
                ft.Divider(height=15),
                ft.Text("1. Zasady metody terapeutycznej ADHD", size=16, weight=ft.FontWeight.BOLD),
                ft.Row(
                    [
                        ft.Text("Maksymalna liczba zadań priorytetu 'A' na liście Daily:"),
                        ft.Button(
                            content="3 zadania",
                            bgcolor=ft.Colors.AMBER_700 if max_a_limit == 3 else ft.Colors.GREY_800,
                            color=ft.Colors.WHITE,
                            on_click=lambda e: set_max_a_limit(3),
                        ),
                        ft.Button(
                            content="4 zadania",
                            bgcolor=ft.Colors.AMBER_700 if max_a_limit == 4 else ft.Colors.GREY_800,
                            color=ft.Colors.WHITE,
                            on_click=lambda e: set_max_a_limit(4),
                        ),
                    ],
                    spacing=10,
                ),
                ft.Divider(height=25),
                ft.Text("2. Synchronizacja z archiwizacją (PC + Telefon)", size=16, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "Każda zmiana pobiera lokalną kopię, podmienia plik główny, a poprzedni plik wrzuca do folderu 'archive/'.",
                    size=12,
                    color=ft.Colors.GREY_400,
                ),
                ft.Divider(height=10),
                ft.Text("Opcja A: Twój serwer Home Assistant", weight=ft.FontWeight.W_500),
                ha_url_input,
                ft.Button(
                    content="🏠 Włącz synchronizację przez serwer HA",
                    bgcolor=ft.Colors.GREEN_800 if mode == "ha_server" else ft.Colors.GREY_800,
                    color=ft.Colors.WHITE,
                    on_click=lambda e: change_sync_mode("ha_server"),
                ),
                ft.Divider(height=15),
                ft.Text("Opcja B: Folder Dysku Google / Sieciowy", weight=ft.FontWeight.W_500),
                folder_input,
                ft.Button(
                    content="📁 Włącz synchronizację przez folder z archiwum",
                    bgcolor=ft.Colors.BLUE_800 if mode == "folder" else ft.Colors.GREY_800,
                    color=ft.Colors.WHITE,
                    on_click=lambda e: change_sync_mode("folder"),
                ),
                ft.Divider(height=15),
                ft.Button(
                    content="Wyłącz synchronizację (Tylko lokalnie)",
                    bgcolor=ft.Colors.GREY_800,
                    color=ft.Colors.GREY_400,
                    on_click=lambda e: change_sync_mode("none"),
                ),
            ]
            page.update()
            return

        is_daily_mode = (current_view == "daily")
        title_text.value = "Daily" if is_daily_mode else "Master Lista"

        for task_id, title, is_daily, is_done, priority in get_tasks(is_daily_view=is_daily_mode):
            prio = priority if priority in ("A", "B", "C") else "B"

            prio_items = [
                ft.Container(
                    content=ft.Row(
                        [ft.Text(prio, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=14)],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    width=36,
                    height=32,
                    bgcolor=priority_colors[prio] if not is_done else ft.Colors.GREY_700,
                    border_radius=6,
                    on_click=lambda e, tid=task_id: toggle_prio_menu(tid),
                )
            ]

            if open_prio_task_id == task_id:
                for other_p in ("A", "B", "C"):
                    if other_p != prio:
                        prio_items.append(
                            ft.Container(
                                content=ft.Row(
                                    [ft.Text(other_p, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=13)],
                                    alignment=ft.MainAxisAlignment.CENTER,
                                ),
                                width=36,
                                height=28,
                                bgcolor=priority_colors[other_p],
                                border_radius=6,
                                on_click=lambda e, tid=task_id, np=other_p, idm=is_daily_mode: change_priority(
                                    tid, np, idm
                                ),
                            )
                        )

            prio_widget = ft.Column(controls=prio_items, spacing=4, tight=True)

            task_row = ft.Container(
                bgcolor="#1e2128",
                padding=10,
                border_radius=8,
                content=ft.Row(
                    controls=[
                        prio_widget,
                        ft.Checkbox(
                            label=title,
                            value=bool(is_done),
                            expand=True,
                            on_change=lambda e, tid=task_id: change_done(tid, e.control.value),
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            )

            draggable_task = ft.Draggable(
                group="tasks",
                data=str(task_id),
                content=task_row,
                content_when_dragging=ft.Container(opacity=0.3, content=task_row),
                content_feedback=ft.Container(
                    padding=10,
                    bgcolor=ft.Colors.GREY_800,
                    border_radius=8,
                    content=ft.Text(title, color=ft.Colors.WHITE, size=14, weight=ft.FontWeight.BOLD),
                ),
                on_drag_start=lambda e, tid=task_id: set_active_drag(tid),
            )
            tasks_column.controls.append(draggable_task)

        refresh_btn = ft.IconButton(
            icon=ft.Icons.SYNC,
            tooltip="Pobierz najnowszą bazę",
            on_click=manual_sync_pull,
        )

        top_right_button = (
            ft.Button(
                content="⚡ Szybki zrzut",
                bgcolor=ft.Colors.GREY_800,
                color=ft.Colors.WHITE,
                tooltip="Zapisz nagłą myśl do Mastera",
                on_click=open_quick_modal,
            )
            if is_daily_mode
            else ft.Button(
                content="➕ Dodaj zadanie",
                bgcolor=ft.Colors.GREY_800,
                color=ft.Colors.WHITE,
                on_click=open_master_modal,
            )
        )

        default_drop_border = ft.Border.all(1, ft.Colors.GREY_800)
        drop_zone_box = ft.Container(
            padding=10,
            border_radius=8,
            bgcolor="#181a20",
            border=default_drop_border,
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.SWAP_HORIZ, size=18, color=ft.Colors.GREY_400),
                    ft.Text(
                        "Przeciągnij tutaj lub na 'Master' po lewej, aby cofnąć do Mastera"
                        if is_daily_mode
                        else "Przeciągnij tutaj lub na 'Daily' po lewej, aby przenieść na Daily",
                        size=12,
                        color=ft.Colors.GREY_400,
                    ),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        )

        drop_zone_target = ft.DragTarget(
            group="tasks",
            content=drop_zone_box,
            on_will_accept=lambda e: highlight_nav(drop_zone_box, True, default_drop_border),
            on_leave=lambda e: highlight_nav(drop_zone_box, False, default_drop_border),
            on_accept=lambda e: (
                highlight_nav(drop_zone_box, False, default_drop_border),
                move_task_between_lists(resolve_dragged_task_id(e), 0 if is_daily_mode else 1),
            ),
        )

        content_column.controls = [
            ft.Row([title_text, ft.Container(expand=True), refresh_btn, top_right_button]),
            status_text,
            warning_text,
            ft.Divider(height=10),
            drop_zone_target,
            tasks_column,
        ]

        if is_daily_mode:
            content_column.controls.append(
                ft.Row(
                    [
                        ft.Button(
                            content="🌙 Koniec dnia (Reset)",
                            bgcolor=ft.Colors.GREY_800,
                            color=ft.Colors.WHITE,
                            on_click=handle_end_of_day,
                        )
                    ],
                    alignment=ft.MainAxisAlignment.END,
                )
            )

        page.update()

    page.add(
        ft.Row(
            controls=[
                sidebar,
                ft.VerticalDivider(width=1, color=ft.Colors.GREY_800),
                content_area,
            ],
            expand=True,
            spacing=0,
        )
    )

    refresh_view()


ft.run(main)