import base64
import gc
import json
import os
import sqlite3
import sys
import threading
import urllib.request
from datetime import date, datetime, timedelta
import pytest
import flet as ft
import main


# --- SYMULATOR STRONY I ZDARZEŃ FLET ---
class DummyWindow:
    width = 950
    height = 760


class DummyEvent:
    def __init__(self, src_id="1", control_val=True):
        self.src_id = src_id
        self.control = type("Ctrl", (), {"value": control_val})()


class DummyPage:
    def __init__(self, width=950):
        self.title = ""
        self.width = width
        self.window = DummyWindow()
        self.window.width = width
        self.padding = 0
        self.bgcolor = None
        self.theme_mode = None
        self.overlay = []
        self.controls = []
        self.on_resize = None
        # Wibracja: domyślnie „PC”, więc init_haptic_service zwraca None i testy
        # zachowują się jak dotąd. Test wibracji ustawia platform="android".
        self.services = []
        self.platform = "windows"

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self):
        pass

    def get_control(self, src_id):
        return None

    def run_task(self, handler, *args, **kwargs):
        """Minimalny odpowiednik `page.run_task` — wykonuje coroutine bez pętli asyncio."""
        result = handler(*args, **kwargs) if callable(handler) else handler
        if hasattr(result, "__await__"):
            try:
                result.send(None)
            except StopIteration:
                pass
        return result


# --- PRZYGOTOWANIE I SPRZĄTANIE ŚRODOWISKA ---
@pytest.fixture(autouse=True)
def setup_and_teardown(tmp_path, monkeypatch):
    import app_logic
    # Testy nie mogą dotykać sieci ani prawdziwego folderu OTA użytkownika.
    monkeypatch.setenv("DISABLE_OTA", "1")
    test_db = str(tmp_path / "test_tasks.db")
    test_config = str(tmp_path / "test_config.db")

    app_logic.LOCAL_DB = main.LOCAL_DB = test_db
    app_logic.CONFIG_DB = main.CONFIG_DB = test_config

    # Cały stan OTA (pliki, logi) ląduje w tmp_path, nie w profilu użytkownika.
    ota_dir = tmp_path / "ota"
    ota_dir.mkdir()
    monkeypatch.setattr(main, "OTA_DIR", str(ota_dir))
    monkeypatch.setattr(main, "OTA_LOGIC_FILE", str(ota_dir / "app_logic_ota.py"))
    monkeypatch.setattr(main, "OTA_VERSION_FILE", str(ota_dir / "ota_version.txt"))
    monkeypatch.setattr(main, "OTA_PREV_LOGIC_FILE", str(ota_dir / "app_logic_ota.prev.py"))
    monkeypatch.setattr(main, "OTA_PREV_VERSION_FILE", str(ota_dir / "ota_prev_version.txt"))
    monkeypatch.setattr(main, "OTA_FAILED_VERSION_FILE", str(ota_dir / "ota_failed_version.txt"))
    monkeypatch.setattr(main, "OTA_LOG_FILE", str(ota_dir / "ota.log"))
    monkeypatch.delenv("MASTER_ADHD_BUNDLED_VERSION", raising=False)
    main._PREPARED_CACHE.update({"key": None, "path": None, "version": ""})

    main.init_db()

    yield tmp_path

    gc.collect()


# --- 1. TESTY BAZY ZADAŃ I PRIORYTETÓW ---
def test_tasks_crud():
    main.add_task_db("Zadanie Master", is_daily=0, priority="B")
    main.add_task_db("Zadanie Daily", is_daily=1, priority="A")

    daily = main.get_tasks(is_daily_view=True)
    master = main.get_tasks(is_daily_view=False)

    assert len(daily) == 1
    assert daily[0][1] == "Zadanie Daily"
    assert len(master) == 2

    task_id = daily[0][0]
    assert main.get_task_priority(task_id) == "A"
    assert main.count_daily_priority_a() == 1

    main.update_priority_db(task_id, "C")
    assert main.get_task_priority(task_id) == "C"
    assert main.count_daily_priority_a() == 0


def test_task_edit_and_delete():
    main.add_task_db("Stary Tytuł", is_daily=1, priority="B")
    task = main.get_tasks(is_daily_view=True)[0]
    task_id = task[0]
    assert task[1] == "Stary Tytuł"

    # Edycja tytułu
    main.edit_task_title_db(task_id, "Nowy Lepszy Tytuł")
    updated = main.get_tasks(is_daily_view=True)[0]
    assert updated[1] == "Nowy Lepszy Tytuł"

    # Usunięcie zadania
    main.delete_task_db(task_id)
    assert len(main.get_tasks(is_daily_view=True)) == 0
    assert len(main.get_tasks(is_daily_view=False)) == 0


# --- 2. TESTY RUTYN (NAWYKÓW) ---
def test_routines_crud():
    main.add_routine_db("Leki")
    main.add_routine_db("Woda")

    routines = main.get_routines()
    assert len(routines) == 2
    assert routines[0][1] == "Leki"
    assert routines[0][2] == 0

    main.toggle_routine_db(routines[0][0], True)
    assert main.get_routines()[0][2] == 1

    main.delete_routine_db(routines[0][0])
    assert len(main.get_routines()) == 1


def test_routine_days_crud():
    # Rutyna codzienna (brak dni) oraz rutyna na konkretne dni tygodnia.
    main.add_routine_db("Woda")
    main.add_routine_db("Leki", days=[1, 3])  # wtorek i czwartek

    by_title = {r[1]: r[3] for r in main.get_routines()}
    assert by_title["Woda"] == []
    assert by_title["Leki"] == [1, 3]
    assert main.routine_days_label([1, 3]) == "Wt, Cz"

    # Edycja rutyny: tytuł i dni można zmienić później.
    rid = [r[0] for r in main.get_routines() if r[1] == "Leki"][0]
    main.update_routine_db(rid, "Leki poranne", days=[0, 2, 4])
    updated = {r[1]: r[3] for r in main.get_routines()}
    assert updated["Leki poranne"] == [0, 2, 4]
    assert "Leki" not in updated


def test_routine_days_normalized():
    # Odporne na śmieci: duplikaty, dni spoza zakresu, wartości zapisane jako JSON-string.
    main.add_routine_db("Duplikaty", days=[1, 1, 7, -2, "2"])
    assert main.get_routines()[0][3] == [1, 2]
    assert main.routine_days_label("[]") == ""
    assert main.routine_days_label(None) == ""


def test_daily_routines_filtered_by_weekday():
    from datetime import datetime
    today = datetime.now().weekday()
    tomorrow = (today + 1) % 7

    main.add_routine_db("Dzisiejsza", days=[today])
    main.add_routine_db("Codzienna")
    main.add_routine_db("Inny dzien", days=[tomorrow])

    daily = [r[1] for r in main.get_daily_routines()]
    assert "Dzisiejsza" in daily
    assert "Codzienna" in daily
    assert "Inny dzien" not in daily


def test_routine_days_json_roundtrip():
    main.add_routine_db("Leki", days=[0, 3])
    exported = main.export_database_to_json_dict()
    routine = [r for r in exported["routines"] if r["title"] == "Leki"][0]
    assert routine["days"] == [0, 3]

    main.import_database_from_json_dict(exported, replace_existing=True)
    restored = {r[1]: r[3] for r in main.get_routines()}
    assert restored["Leki"] == [0, 3]


# --- 2a. MIGRACJA SCHEMATU (stare bazy bez routine_days) ---
def _make_old_schema_db(path, routine_title="Leki"):
    """Baza w schemacie sprzed dni tygodnia — tak wygląda kopia ze starej wersji."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "is_daily INTEGER DEFAULT 0, is_done INTEGER DEFAULT 0, priority TEXT DEFAULT 'B')")
        conn.execute(
            "CREATE TABLE routines (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL, is_done INTEGER DEFAULT 0)")
        conn.execute(
            "CREATE TABLE completed_archive (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL, priority TEXT DEFAULT 'B', completed_at TEXT NOT NULL)")
        conn.execute("INSERT INTO routines (title, is_done) VALUES (?, 0)", (routine_title,))
        conn.commit()
    finally:
        conn.close()


def _routines_columns(path):
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(routines)").fetchall()}
    finally:
        conn.close()


def test_init_db_migrates_old_routines_schema(tmp_path, monkeypatch):
    """Start aplikacji na bazie ze starej wersji musi dodać kolumnę routine_days."""
    import app_logic

    old_db = str(tmp_path / "old_tasks.db")
    _make_old_schema_db(old_db)
    monkeypatch.setattr(app_logic, "LOCAL_DB", old_db)
    monkeypatch.setattr(main, "LOCAL_DB", old_db)

    main.init_db()

    routines = main.get_routines()
    assert [r[1] for r in routines] == ["Leki"]
    assert routines[0][3] == []  # brak dni = codziennie
    assert "routine_days" in _routines_columns(old_db)


def test_get_routines_self_heals_old_schema(tmp_path, monkeypatch):
    """SELECT na starej bazie (np. po pullu z chmury) nie może wywalić widoku."""
    import app_logic

    old_db = str(tmp_path / "pulled_tasks.db")
    _make_old_schema_db(old_db, "Woda")
    monkeypatch.setattr(app_logic, "LOCAL_DB", old_db)
    monkeypatch.setattr(main, "LOCAL_DB", old_db)

    routines = main.get_routines()  # wcześniej: sqlite3.OperationalError

    assert [r[1] for r in routines] == ["Woda"]
    assert routines[0][3] == []
    assert "routine_days" in _routines_columns(old_db)
    # Po migracji zapis i odczyt dni działa normalnie.
    main.update_routine_db(routines[0][0], "Woda", days=[2])
    assert main.get_routines()[0][3] == [2]


def test_restore_from_bytes_migrates_schema(tmp_path, monkeypatch):
    """Baza pobrana z chmury (stary schemat) jest migrowana po podmianie pliku."""
    import app_logic

    pulled_db = str(tmp_path / "gist_tasks.db")
    _make_old_schema_db(pulled_db, "Medytacja")
    with open(pulled_db, "rb") as f:
        data = f.read()

    ok, _ = app_logic._restore_db_from_bytes(data, "testowa baza")
    assert ok

    routines = main.get_routines()
    assert [r[1] for r in routines] == ["Medytacja"]
    assert "routine_days" in _routines_columns(app_logic.LOCAL_DB)


# --- 3. TESTY PODKROKÓW (SUBTASKS) ---
def test_subtasks():
    main.add_task_db("Projekt", is_daily=1, priority="A")
    task_id = main.get_tasks(is_daily_view=True)[0][0]

    steps = [{"title": "Krok 1", "is_done": 0}, {"title": "Krok 2", "is_done": 1}]
    main.update_subtasks_db(task_id, steps)

    saved_steps = main.get_tasks(is_daily_view=True)[0][5]
    assert len(saved_steps) == 2
    assert saved_steps[1]["title"] == "Krok 2"
    assert saved_steps[1]["is_done"] == 1


def test_subtask_edit_and_delete():
    main.add_task_db("Zadanie z Krokami", is_daily=0, priority="B")
    task_id = main.get_tasks(is_daily_view=False)[0][0]

    steps = [{"title": "Krok A", "is_done": 0}, {"title": "Krok B", "is_done": 0}]
    main.update_subtasks_db(task_id, steps)

    # Edycja kroku
    main.edit_subtask_title_db(task_id, 0, "Krok A Poprawiony")
    task = main.get_tasks(is_daily_view=False)[0]
    assert task[5][0]["title"] == "Krok A Poprawiony"

    # Usunięcie kroku
    main.delete_subtask_db(task_id, 0)
    task_after_delete = main.get_tasks(is_daily_view=False)[0]
    assert len(task_after_delete[5]) == 1
    assert task_after_delete[5][0]["title"] == "Krok B"


def test_subtask_reorder():
    main.add_task_db("Projekt z kolejnością", is_daily=0, priority="B")
    task_id = main.get_tasks(is_daily_view=False)[0][0]
    steps = [{"title": "Krok 1", "is_done": 0}, {"title": "Krok 2", "is_done": 0},
             {"title": "Krok 3", "is_done": 0}]
    main.update_subtasks_db(task_id, steps)

    # W górę: Krok 1 -> pozycja 2
    assert main.move_subtask_db(task_id, 0, 1) == 1
    titles = [s["title"] for s in main.get_tasks(is_daily_view=False)[0][5]]
    assert titles == ["Krok 2", "Krok 1", "Krok 3"]

    # W dół: Krok 3 -> pozycja 2
    assert main.move_subtask_db(task_id, 2, -1) == 1
    titles = [s["title"] for s in main.get_tasks(is_daily_view=False)[0][5]]
    assert titles == ["Krok 2", "Krok 3", "Krok 1"]

    # Ruchy poza zakresem nic nie zmieniają
    assert main.move_subtask_db(task_id, 0, -1) is None
    assert main.move_subtask_db(task_id, 2, 1) is None
    titles = [s["title"] for s in main.get_tasks(is_daily_view=False)[0][5]]
    assert titles == ["Krok 2", "Krok 3", "Krok 1"]


def test_subtask_reorder_arrows_ui():
    main.add_task_db("Projekt z krokami", is_daily=0, priority="B")
    task_id = main.get_tasks(is_daily_view=False)[0][0]
    main.update_subtasks_db(task_id, [{"title": "Alfa", "is_done": 0},
                                     {"title": "Beta", "is_done": 0},
                                     {"title": "Gamma", "is_done": 0}])

    page = DummyPage(width=950)
    main.main(page)

    root = page.controls[0].content
    sidebar_col = root.content.controls[0].content
    # Przejście na Master listę
    sidebar_col.controls[2].content.on_click(None)

    def get_subtask_rows():
        content_col = root.content.controls[2].content
        tasks_col = [c for c in content_col.controls if isinstance(c, ft.Column)][0]
        card = tasks_col.controls[0]
        return card.content.controls[1:]

    rows = get_subtask_rows()
    assert len(rows) == 3
    # Rząd kroku: checkbox, tytuł, strzałka w górę, strzałka w dół, edycja, usuń
    assert rows[0].content.controls[2].disabled is True
    assert rows[0].content.controls[3].disabled is False
    assert rows[2].content.controls[3].disabled is True

    # Przesuń "Alfa" w dół strzałką
    rows[0].content.controls[3].on_click(None)
    titles = [s["title"] for s in main.get_tasks(is_daily_view=False)[0][5]]
    assert titles == ["Beta", "Alfa", "Gamma"]

    # Przesuń "Gamma" w górę strzałką
    get_subtask_rows()[2].content.controls[2].on_click(None)
    titles = [s["title"] for s in main.get_tasks(is_daily_view=False)[0][5]]
    assert titles == ["Beta", "Gamma", "Alfa"]


# --- 4. TEST MATEMATYKI PASKA POSTĘPU ---
def test_progress_calculation():
    assert main.calculate_progress_logic([], []) == 0.0

    fake_tasks = [(1, "Projekt", 1, 0, "A", [{"title": "K1", "is_done": 0}, {"title": "K2", "is_done": 0}])]
    fake_routines = [(1, "Leki", 1)]
    assert main.calculate_progress_logic(fake_tasks, fake_routines, {}) == 0.5

    # Sprint 4.2: punktacja hybrydowa — 1/2 podkroków = 0.5 punktu za zadanie.
    fake_tasks_done = [(1, "Projekt", 1, 0, "A", [{"title": "K1", "is_done": 1}, {"title": "K2", "is_done": 0}])]
    assert main.calculate_progress_logic(fake_tasks_done, fake_routines, {}) == 0.75
    assert main.calculate_progress_logic(fake_tasks_done, fake_routines, {1: 0}) == 0.75
    assert main.calculate_progress_logic(fake_tasks_done, fake_routines, {1: 1}) == 0.75


def test_progress_hybrid_edges():
    # Brak elementów → 0.0 (bez dzielenia przez zero).
    assert main.calculate_progress_logic([], []) == 0.0
    # Samo rutyny: 3 z 4 zrobione → 0.75.
    routines = [(1, "A", 1), (2, "B", 1), (3, "C", 1), (4, "D", 0)]
    assert main.calculate_progress_logic([], routines) == 0.75
    # Zadanie bez podkroków = 1 punkt, gdy ukończone.
    plain = [(1, "Z", 1, 1, "A", [])]
    assert main.calculate_progress_logic(plain, []) == 1.0
    # 1/3 podkroków.
    third = [(1, "Z", 1, 0, "A", [{"title": "a", "is_done": 1},
                                 {"title": "b", "is_done": 0},
                                 {"title": "c", "is_done": 0}])]
    assert main.calculate_progress_logic(third, []) == pytest.approx(1 / 3)
    # Wszystko zrobione → 100% (podkroki + zadanie + rutyna).
    full_tasks = [(1, "Z", 1, 1, "A", [{"title": "a", "is_done": 1}, {"title": "b", "is_done": 1}])]
    assert main.calculate_progress_logic(full_tasks, [(1, "R", 1)]) == 1.0
    # Zadanie z podkrokami nie daje 100%, dopóki zostały niezrobione podkroki.
    partial = [(1, "Z", 1, 1, "A", [{"title": "a", "is_done": 1}, {"title": "b", "is_done": 0}])]
    assert main.calculate_progress_logic(partial, []) < 1.0


# --- 5. TEST RESETU NA KONIEC DNIA ---
def test_end_of_day():
    main.add_task_db("Zrobione", is_daily=1, priority="A")
    main.add_task_db("Niezrobione", is_daily=1, priority="B")
    main.add_routine_db("Rutyna")

    tasks = main.get_tasks(is_daily_view=True)
    for t in tasks:
        if t[1] == "Zrobione":
            main.toggle_done_db(t[0], 1)

    r_id = main.get_routines()[0][0]
    main.toggle_routine_db(r_id, 1)

    main.end_of_day_db()

    assert len(main.get_tasks(is_daily_view=True)) == 0
    master = main.get_tasks(is_daily_view=False)
    assert len(master) == 1
    assert master[0][1] == "Niezrobione"
    assert main.get_routines()[0][2] == 0


# --- 6. TEST BEZPIECZEŃSTWA SYNCHRONIZACJI ---
def test_sync_security(setup_and_teardown):
    sync_dir = str(setup_and_teardown / "sync_sec")

    main.set_config("sync_mode", "folder")
    main.set_config("sync_folder", "")
    ok, msg = main.pull_database()
    assert not ok
    assert "Nie podano ścieżki" in msg

    main.set_config("sync_folder", "/nieistniejaca/sciezka/12345")
    ok, msg = main.pull_database()
    assert not ok

    os.makedirs(sync_dir, exist_ok=True)
    corrupted_file = os.path.join(sync_dir, "tasks.db")
    with open(corrupted_file, "wb") as f:
        f.write(b"To nie jest baza SQLite, tylko smieci")

    main.set_config("sync_folder", sync_dir)
    ok, msg = main.pull_database()
    assert not ok
    assert "uszkodzony" in msg.lower()


# --- 7. TEST SYNCHRONIZACJI DYSKOWEJ I ARCHIWIZACJI ---
def test_folder_sync_and_archive(setup_and_teardown):
    sync_dir = str(setup_and_teardown / "sync_arch")
    os.makedirs(sync_dir, exist_ok=True)

    main.set_config("sync_mode", "folder")
    main.set_config("sync_folder", sync_dir)

    main.add_task_db("Zadanie do backupu", is_daily=1, priority="A")
    remote_db = os.path.join(sync_dir, "tasks.db")
    assert os.path.exists(remote_db)

    main.add_task_db("Drugie zadanie", is_daily=0, priority="B")
    archive_dir = os.path.join(sync_dir, "archive")
    assert os.path.exists(archive_dir)
    assert len(os.listdir(archive_dir)) >= 1


# --- 8. TEST PORÓWNYWANIA WERSJI ---
def test_version_comparison():
    assert main.is_newer_version("1.4.0", "1.3.0") is True
    assert main.is_newer_version("1.4.0", "1.4.0") is False
    assert main.is_newer_version("1.3.0", "1.4.0") is False
    assert main.is_newer_version("2.0", "1.9.9") is True


# --- 9. TEST INTERFEJSU PC I TELEFONU + WYWOŁANIE LAMBD ---
def test_interactive_callbacks_and_drag_drop():
    main.add_task_db("Zadanie Testowe", is_daily=1, priority="B")
    task_id = main.get_tasks(is_daily_view=True)[0][0]

    page_pc = DummyPage(width=950)
    main.main(page_pc)

    root_pc = page_pc.controls[0].content
    assert isinstance(root_pc.content, ft.Row)

    sidebar_col = root_pc.content.controls[0].content
    daily_pc_target = sidebar_col.controls[1]
    master_pc_target = sidebar_col.controls[2]

    dummy_ev = DummyEvent(src_id=str(task_id))
    for target in (daily_pc_target, master_pc_target):
        target.on_will_accept(dummy_ev)
        target.on_leave(dummy_ev)
        target.on_accept(dummy_ev)

    page_mob = DummyPage(width=400)
    main.main(page_mob)

    root_mob = page_mob.controls[0].content
    assert isinstance(root_mob.content, ft.Column)

    bottom_nav_row = root_mob.content.controls[2].content
    daily_mob_target = bottom_nav_row.controls[0]
    master_mob_target = bottom_nav_row.controls[1]
    settings_mob_box = bottom_nav_row.controls[2]

    for target in (daily_mob_target, master_mob_target):
        target.on_will_accept(dummy_ev)
        target.on_leave(dummy_ev)
        target.on_accept(dummy_ev)

    master_mob_target.content.on_click(None)
    settings_mob_box.on_click(None)
    daily_mob_target.content.on_click(None)


def test_ui_edit_delete_dialogs():
    main.add_task_db("Zadanie Dialogowe", is_daily=1, priority="B")
    task_id = main.get_tasks(is_daily_view=True)[0][0]
    steps = [{"title": "Krok 1", "is_done": 0}]
    main.update_subtasks_db(task_id, steps)

    page = DummyPage(width=950)
    main.main(page)

    assert len(page.overlay) >= 6
    edit_task_dialog = page.overlay[3]
    delete_task_dialog = page.overlay[4]
    edit_subtask_dialog = page.overlay[5]

    # 1. Edycja zadania przez dialog
    root_pc = page.controls[0].content
    content_col = root_pc.content.controls[2].content
    tasks_col = [c for c in content_col.controls if isinstance(c, ft.Column)][0]
    card = tasks_col.controls[0]
    edit_btn = card.content.controls[2]
    edit_btn.on_click(None)
    assert edit_task_dialog.open is True

    edit_field = edit_task_dialog.content.controls[0] if isinstance(edit_task_dialog.content, ft.Column) else edit_task_dialog.content
    edit_field.value = "Zadanie Przemianowane"
    edit_task_dialog.actions[1].on_click(None)
    assert edit_task_dialog.open is False
    assert main.get_tasks(is_daily_view=True)[0][1] == "Zadanie Przemianowane"

    # 2. Usunięcie zadania z karty: natychmiast + SnackBar „Cofnij”
    #    Dialog w overlay[4] zostaje (kontrakt testów), ale karty go nie otwierają.
    card_after_edit = tasks_col.controls[0]
    del_btn = card_after_edit.content.controls[3]
    del_btn.on_click(None)
    assert delete_task_dialog.open is False
    assert len(main.get_tasks(is_daily_view=True)) == 0

    snack = page.overlay[-1]
    assert isinstance(snack, ft.SnackBar)
    assert snack.action.label == "Cofnij"
    snack.action.on_click(None)
    restored = main.get_tasks(is_daily_view=True)
    assert len(restored) == 1
    assert restored[0][0] == task_id
    assert restored[0][1] == "Zadanie Przemianowane"

    # Drugie kliknięcie „Cofnij” nie duplikuje rekordu.
    snack.action.on_click(None)
    assert len(main.get_tasks(is_daily_view=True)) == 1


def _ui_tasks_column(page):
    """Kolumna kart z aktualnie wyświetlanego widoku (PC)."""
    root = page.controls[0].content
    content_col = root.content.controls[2].content
    return [c for c in content_col.controls if isinstance(c, ft.Column)][0]


def _iter_controls(control):
    """Rekurencyjnie przechodzi drzewo kontrolek Flet (controls + content)."""
    if control is None or isinstance(control, str):
        return
    yield control
    children = []
    kids = getattr(control, "controls", None)
    if isinstance(kids, list):
        children.extend(kids)
    inner = getattr(control, "content", None)
    if inner is not None:
        children.append(inner)
    for child in children:
        yield from _iter_controls(child)


def test_ui_edit_task_from_daily_keeps_category():
    """Daily (gałąź z podkrokami) nie może nadpisać kategorii przy zapisie."""
    main.add_task_db("Zadanie z kategorią", is_daily=1, priority="A", category="💼 Praca")
    task_id = main.get_tasks(is_daily_view=True)[0][0]
    main.update_subtasks_db(task_id, [{"title": "Krok 1", "is_done": 0}])

    page = DummyPage(width=950)
    main.main(page)

    card = _ui_tasks_column(page).controls[0]
    edit_btn = card.content.controls[2]  # Daily subtask branch
    edit_btn.on_click(None)

    edit_task_dialog = page.overlay[3]
    assert edit_task_dialog.open is True
    dropdown = edit_task_dialog.content.controls[1]
    assert dropdown.value == "💼 Praca"

    # Zapis bez zmian treści i kategorii.
    edit_task_dialog.actions[1].on_click(None)
    row = main.get_tasks(is_daily_view=True)[0]
    assert row[0] == task_id
    assert row[6] == "💼 Praca"


def test_ui_edit_task_from_daily_without_subtasks_keeps_category():
    """Daily (gałąź bez podkroków) też musi przekazać kategorię do dialogu."""
    main.add_task_db("Zadanie bez kroków", is_daily=1, priority="A", category="🏠 Dom")
    task_id = main.get_tasks(is_daily_view=True)[0][0]

    page = DummyPage(width=950)
    main.main(page)

    card = _ui_tasks_column(page).controls[0]
    edit_btn = card.content.controls[3]  # Daily non-subtask branch
    edit_btn.on_click(None)

    edit_task_dialog = page.overlay[3]
    assert edit_task_dialog.open is True
    assert edit_task_dialog.content.controls[1].value == "🏠 Dom"

    edit_task_dialog.actions[1].on_click(None)
    row = main.get_tasks(is_daily_view=True)[0]
    assert row[0] == task_id
    assert row[6] == "🏠 Dom"


def test_ui_delete_task_now_undo_restores_fields():
    """Karta Daily usuwa od razu, a „Cofnij” przywraca pełny rekord."""
    main.add_task_db("Do usunięcia", is_daily=1, priority="A", category="💼 Praca",
                     deadline="2026-12-31")
    task_id = main.get_tasks(is_daily_view=True)[0][0]

    page = DummyPage(width=950)
    main.main(page)

    card = _ui_tasks_column(page).controls[0]
    del_btn = card.content.controls[4]  # Daily non-subtask branch
    del_btn.on_click(None)

    assert len(main.get_tasks(is_daily_view=True)) == 0
    snack = page.overlay[-1]
    assert isinstance(snack, ft.SnackBar)
    assert snack.action.label == "Cofnij"
    assert snack.duration == 6000

    snack.action.on_click(None)
    rows = main.get_tasks(is_daily_view=True)
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == task_id
    assert row[1] == "Do usunięcia"
    assert row[2] == 1
    assert row[4] == "A"
    assert row[6] == "💼 Praca"
    assert row[7] == "2026-12-31"

    # Powtórne „Cofnij” nie duplikuje rekordu.
    snack.action.on_click(None)
    assert len(main.get_tasks(is_daily_view=True)) == 1


def test_ui_delete_subtask_now_undo_restores_position():
    """Usunięcie kroku na karcie Master + „Cofnij” przywraca go na tę samą pozycję."""
    main.add_task_db("Z krokami", is_daily=0, priority="A", category="Ogólne")
    task_id = main.get_tasks(is_daily_view=False)[0][0]
    main.update_subtasks_db(task_id, [
        {"title": "Krok 1", "is_done": 0},
        {"title": "Krok 2", "is_done": 1},
    ])

    page = DummyPage(width=950)
    main.main(page)

    # Przełącz na Master: sidebar[2].
    root = page.controls[0].content
    sidebar_col = root.content.controls[0].content
    sidebar_col.controls[2].content.on_click(None)

    card = _ui_tasks_column(page).controls[0]
    assert isinstance(card.content, ft.Column)
    subtask_row = card.content.controls[1].content
    close_btn = subtask_row.controls[5]
    close_btn.on_click(None)

    subs = json.loads(main.get_task_row(task_id)[5])
    assert [s["title"] for s in subs] == ["Krok 2"]

    snack = page.overlay[-1]
    assert isinstance(snack, ft.SnackBar)
    snack.action.on_click(None)

    subs = json.loads(main.get_task_row(task_id)[5])
    assert [s["title"] for s in subs] == ["Krok 1", "Krok 2"]
    assert subs[1]["is_done"] == 1

    # Powtórne „Cofnij” nie duplikuje kroku.
    snack.action.on_click(None)
    assert len(json.loads(main.get_task_row(task_id)[5])) == 2


def test_ui_master_delete_task_now_undo():
    """Karta Master usuwa od razu i pozwala przywrócić zadanie."""
    main.add_task_db("Master do usunięcia", is_daily=0, priority="A", category="Ogólne")
    task_id = main.get_tasks(is_daily_view=False)[0][0]

    page = DummyPage(width=950)
    main.main(page)

    root = page.controls[0].content
    sidebar_col = root.content.controls[0].content
    sidebar_col.controls[2].content.on_click(None)

    card = _ui_tasks_column(page).controls[0]
    del_btn = card.content.controls[0].controls[5]  # prio, chk, title, krok, edit, del, drag
    del_btn.on_click(None)

    assert len(main.get_tasks(is_daily_view=False)) == 0
    snack = page.overlay[-1]
    assert isinstance(snack, ft.SnackBar)

    snack.action.on_click(None)
    rows = main.get_tasks(is_daily_view=False)
    assert len(rows) == 1
    assert rows[0][0] == task_id
    assert rows[0][1] == "Master do usunięcia"


def test_mobile_task_card_layout_and_actions():
    main.add_task_db("Zadanie Mobilne", is_daily=1, priority="A")
    task_id = main.get_tasks(is_daily_view=True)[0][0]
    steps = [{"title": "Krok Mobilny", "is_done": 0}]
    main.update_subtasks_db(task_id, steps)

    page_mob = DummyPage(width=400)
    main.main(page_mob)

    root_mob = page_mob.controls[0].content
    content_area = root_mob.content.controls[0]
    content_col = content_area.content
    tasks_col = [c for c in content_col.controls if isinstance(c, ft.Column)][0]
    card = tasks_col.controls[0]

    # Na telefonie zawartość karty to Column (dwurzędowy nagłówek zapobiegający nakładaniu)
    assert isinstance(card.content, ft.Column)
    top_row = card.content.controls[0]
    action_row = card.content.controls[1]
    assert isinstance(top_row, ft.Row)
    assert isinstance(action_row, ft.Row)

    # Przełącz na Master listę
    bottom_nav = root_mob.content.controls[2].content
    master_mob_target = bottom_nav.controls[1]
    master_mob_target.content.on_click(None)

    # Sprawdzenie karty na Masterze na telefonie
    master_tasks_col = [c for c in content_col.controls if isinstance(c, ft.Column)][0]
    master_card = master_tasks_col.controls[0]
    assert isinstance(master_card.content, ft.Column)
    # Rząd 1: prio, chk, text, drag_handle
    assert len(master_card.content.controls[0].controls) == 4
    # Rząd 2: action_row z Daily badge i przyciskami
    assert len(master_card.content.controls[1].controls) >= 3


# --- 9a. SPRINT 1: WIBRACJA, DOTYK 44 px, MINI-MINUTNIK ---
class _FakeHaptic:
    """Rejestruje wywołania metod wibracji (odpowiednik ft.HapticFeedback)."""

    def __init__(self):
        self.calls = []

    def vibrate(self):
        self.calls.append("vibrate")

    def light_impact(self):
        self.calls.append("light_impact")

    def selection_click(self):
        self.calls.append("selection_click")

    def heavy_impact(self):
        self.calls.append("heavy_impact")


def test_haptic_calls_vibrate_for_timer_and_task(monkeypatch):
    """Mock HapticFeedback → krótka wibracja na koniec timera i przy sukcesie."""
    monkeypatch.setattr(ft, "HapticFeedback", _FakeHaptic, raising=False)
    page = DummyPage(width=400)
    page.platform = "android"

    service = main.init_haptic_service(page)
    assert isinstance(service, _FakeHaptic)

    assert main.trigger_haptic_feedback(page, service, "timer_done") is True
    assert service.calls == ["vibrate"]

    assert main.trigger_haptic_feedback(page, service, "task_done") is True
    assert service.calls[-1] == "light_impact"

    assert main.trigger_haptic_feedback(page, service, "undo") is True
    assert service.calls[-1] == "selection_click"


def test_haptic_missing_attribute_does_not_crash(monkeypatch):
    """Flet bez HapticFeedback → ciche pominięcie, żadnego wyjątku."""
    monkeypatch.setattr(ft, "HapticFeedback", None, raising=False)
    page = DummyPage(width=400)
    page.platform = "android"

    assert main.init_haptic_service(page) is None
    assert main.trigger_haptic_feedback(page, None, "timer_done") is False


def test_haptic_respects_sound_enabled(monkeypatch):
    """sound_enabled=0 wycisza też wibrację."""
    monkeypatch.setattr(ft, "HapticFeedback", _FakeHaptic, raising=False)
    main.set_config("sound_enabled", "0")
    page = DummyPage(width=400)
    page.platform = "android"

    service = main.init_haptic_service(page)
    assert isinstance(service, _FakeHaptic)
    assert main.trigger_haptic_feedback(page, service, "timer_done") is False
    assert service.calls == []


def test_haptic_not_attached_on_windows():
    """Na PC (windows) nie przywiązujemy usługi wibracji — zachowanie jak dotąd."""
    page = DummyPage(width=950)
    assert main.init_haptic_service(page) is None


def test_settings_sound_card_mentions_vibration():
    """Etykieta w Ustawieniach mówi o dźwięku i wibracji (Sprint 1.1)."""
    page = DummyPage(width=950)
    main.main(page)

    root = page.controls[0].content
    sidebar_col = root.content.controls[0].content
    sidebar_col.controls[8].on_click(None)  # Ustawienia
    content_col = root.content.controls[2].content

    texts = [
        c.value for ctrl in content_col.controls
        for c in _iter_controls(ctrl) if isinstance(c, ft.Text)
    ]
    assert any("Dźwięk i wibracja" in (t or "") for t in texts)


def test_mobile_touch_targets_at_400px():
    """Na 400 px każda akcja-icon ma obszar klikalny ≥ 44 px (Sprint 1.4)."""
    main.add_task_db("Z krokami", is_daily=1, priority="A")
    main.add_task_db("Bez kroków", is_daily=1, priority="B")
    task_id = main.get_tasks(is_daily_view=True)[0][0]
    main.update_subtasks_db(task_id, [{"title": "Krok 1", "is_done": 0}])

    page = DummyPage(width=400)
    main.main(page)

    root = page.controls[0].content
    content_col = root.content.controls[0].content
    tasks_col = [c for c in content_col.controls if isinstance(c, ft.Column)][0]

    checked = 0
    for card in tasks_col.controls:
        for ctrl in _iter_controls(card):
            if isinstance(ctrl, ft.IconButton):
                assert ctrl.width is not None and ctrl.width >= 44, ctrl.tooltip
                assert ctrl.height is not None and ctrl.height >= 44, ctrl.tooltip
                checked += 1
    assert checked >= 4  # edytuj/usuń zadania + akcje kroków

    # Minutnik: play i reset też są klikalne palcem.
    timer_card = content_col.controls[2]
    timer_buttons = [c for c in _iter_controls(timer_card) if isinstance(c, ft.IconButton)]
    assert timer_buttons
    for b in timer_buttons:
        assert b.width >= 44 and b.height >= 44


def test_secondary_text_contrast_on_cards():
    """Metadane kart mają kontrast ≥ 4.5:1; helper realnie używa contrast_ratio()."""
    assert main.contrast_ratio(main.COLOR_TEXT_SECONDARY, main.COLOR_BG_CARD) >= 4.5
    assert main.readable_text_color(main.COLOR_BG_CARD) == main.COLOR_TEXT_SECONDARY
    # Na jasnym tle ciemny tekst jest nieczytelny → fallback do jasnego.
    assert main.readable_text_color("#ffffff") == main.COLOR_TEXT_PRIMARY
    # Zła wartość koloru nie może wywalić UI.
    assert main.readable_text_color("not-a-color") == main.COLOR_TEXT_PRIMARY


def test_mini_timer_visible_and_touch_size_on_mobile():
    """Globalny licznik z pauzą/resetem jest widoczny i klikalny palcem (1.5)."""
    main.add_task_db("Zadanie", is_daily=1, priority="A")
    # Częściowo zużyty minutnik bez wątku: stan „w pauzie”.
    main.set_config("timer_total", "300")
    main.set_config("timer_running", "0")
    main.set_config("timer_left", "180")

    page = DummyPage(width=400)
    main.main(page)

    root = page.controls[0].content
    wrapper = root.content.controls[1]
    chip = wrapper.controls[1]

    assert wrapper.visible is True
    assert chip.visible is True
    assert chip.height >= 44
    assert chip.content.controls[1].value == "03:00"  # odtworzone z configu

    play_btn = chip.content.controls[2]
    reset_btn = chip.content.controls[3]
    assert play_btn.width >= 44 and play_btn.height >= 44
    assert reset_btn.width >= 44 and reset_btn.height >= 44
    assert callable(play_btn.on_click)
    assert callable(reset_btn.on_click)


def test_mini_timer_hidden_without_active_timer_mobile():
    """Bez minutnika chip i Divider są schowane — nic nie wisi nad nawigacją."""
    page = DummyPage(width=400)
    main.main(page)

    root = page.controls[0].content
    wrapper = root.content.controls[1]
    assert wrapper.visible is False
    assert wrapper.controls[1].visible is False


def test_mini_timer_reset_works_from_master_view():
    """Reset minutnika działa z Master (bez wracania do Daily)."""
    main.set_config("timer_total", "300")
    main.set_config("timer_running", "0")
    main.set_config("timer_left", "120")

    page = DummyPage(width=400)
    main.main(page)

    root = page.controls[0].content
    bottom_nav = root.content.controls[2].content
    bottom_nav.controls[1].content.on_click(None)  # przełącz na Master

    wrapper = root.content.controls[1]
    chip = wrapper.controls[1]
    reset_btn = chip.content.controls[3]
    reset_btn.on_click(None)

    assert main.get_config("timer_left") == "300"
    assert main.get_config("timer_running") == "0"
    assert chip.visible is False


# --- 10. TEST DZIENNIKA SUKCESÓW (ARCHIWUM WYKONANYCH ZADAŃ) ---
def test_completed_archive_lifecycle():
    main.add_task_db("Sukces 1", is_daily=1, priority="A")
    main.add_task_db("W trakcie", is_daily=1, priority="B")

    tasks = main.get_tasks(is_daily_view=True)
    task_done_id = [t[0] for t in tasks if t[1] == "Sukces 1"][0]
    main.toggle_done_db(task_done_id, 1)

    # Przed resetem archiwum jest puste
    assert len(main.get_completed_archive()) == 0

    # Reset dnia przenosi zrobione zadanie do archiwum
    main.end_of_day_db()

    archive = main.get_completed_archive()
    assert len(archive) == 1
    assert archive[0][1] == "Sukces 1"
    assert archive[0][2] == "A"
    assert len(archive[0][3]) > 0  # timestamp istnieje

    # Czyszczenie archiwum
    main.clear_completed_archive_db()
    assert len(main.get_completed_archive()) == 0


def test_archive_and_timer_ui():
    main.add_task_db("Ukończony Projekt", is_daily=1, priority="A")
    tasks = main.get_tasks(is_daily_view=True)
    main.toggle_done_db(tasks[0][0], 1)
    main.end_of_day_db()

    page = DummyPage(width=950)
    main.main(page)

    # 1. Sprawdzenie Minutnika Skupienia na widoku Daily
    root_pc = page.controls[0].content
    content_col = root_pc.content.controls[2].content
    timer_card = content_col.controls[2]
    assert isinstance(timer_card, ft.Container)

    # 2. Przejście do Dziennika Sukcesów przez nawigację
    sidebar_col = root_pc.content.controls[0].content
    archive_pc_nav = sidebar_col.controls[3]
    archive_pc_nav.on_click(None)

    # Weryfikacja kontrolek Dziennika Sukcesów
    assert "Dziennik Sukcesów" in content_col.controls[0].controls[0].controls[1].value
    # Lista sukcesów zawiera 1 zarchiwizowane zadanie
    archive_list_col = [c for c in content_col.controls if isinstance(c, ft.Column)][0]
    assert len(archive_list_col.controls) == 1


# --- 11. TEST CHMURY GITHUB GIST (Automatyczne tworzenie i pobieranie) ---
def test_gist_cloud_sync(monkeypatch):
    main.add_task_db("Zadanie Chmurowe", is_daily=1, priority="A")
    main.set_config("sync_mode", "gist")
    main.set_config("gist_token", "ghp_test_token_123")
    main.set_config("gist_id", "")

    cloud_storage = {}

    class MockResponse:
        def __init__(self, status, data_dict):
            self.status = status
            self._data = json.dumps(data_dict).encode("utf-8")

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def mock_urlopen(req, timeout=5):
        if req.method == "POST":
            body = json.loads(req.data.decode("utf-8"))
            cloud_storage["content"] = body["files"]["tasks.db.b64"]["content"]
            return MockResponse(201, {"id": "nowe_id_chmury_999"})
        elif req.method == "GET":
            return MockResponse(200, {"files": {"tasks.db.b64": {"content": cloud_storage["content"]}}})
        return MockResponse(200, {})

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    # 1. Wysyłamy bazę do chmury (powinno samo utworzyć Gist i zapisać ID)
    ok_push, msg_push = main.push_database()
    assert ok_push is True
    assert main.get_config("gist_id") == "nowe_id_chmury_999"

    # 2. Pobieramy bazę z chmury po ID i sprawdzamy integralność
    ok_pull, msg_pull = main.pull_database()
    assert ok_pull is True
    assert "Chmury" in msg_pull


# --- 11. TEST WIDOKU DARK SLATE I SZYBKIEGO WPROWADZANIA ---
def test_dark_slate_ui_and_quick_input():
    page = DummyPage(width=950)
    main.main(page)

    # Sprawdzenie koloru tła strony (Dark Slate)
    assert page.bgcolor == "#0d1117"

    # Dodanie zadania przez logikę bazy i odświeżenie
    main.add_task_db("Nowa szybka myśl", is_daily=0, priority="B")
    master_tasks = main.get_tasks(is_daily_view=False)
    assert len(master_tasks) >= 1
    assert master_tasks[0][1] == "Nowa szybka myśl"


# --- 12. TEST EKSPORTU/IMPORTU JSON I LOKALNEGO SNAPSHOTU ---
def test_json_export_import_and_snapshot(tmp_path):
    main.add_task_db("Zadanie do JSON", is_daily=1, priority="A", category="💼 Praca")
    main.add_routine_db("Rutyna JSON")

    # 1. Eksport do słownika
    data_dict = main.export_database_to_json_dict()
    assert "tasks" in data_dict
    assert len(data_dict["tasks"]) == 1
    assert data_dict["tasks"][0]["title"] == "Zadanie do JSON"
    assert data_dict["tasks"][0]["category"] == "💼 Praca"

    # 2. Eksport do pliku
    json_file = str(tmp_path / "test_backup.json")
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(data_dict, f, ensure_ascii=False)
    assert os.path.exists(json_file)

    # 3. Import ze słownika z czyszczeniem
    data_dict["tasks"].append({
        "title": "Importowane z JSON",
        "is_daily": 0,
        "is_done": 0,
        "priority": "C",
        "subtasks": [],
        "category": "🎯 Osobiste"
    })
    ok_imp, msg_imp = main.import_database_from_json_dict(data_dict, replace_existing=True)
    assert ok_imp is True
    all_master = main.get_tasks(is_daily_view=False)
    assert len(all_master) == 2
    assert any(t[1] == "Importowane z JSON" and t[6] == "🎯 Osobiste" for t in all_master)

    # 4. Snapshot bazy
    ok_snap, snap_path = main.make_local_snapshot()
    assert ok_snap is True
    assert os.path.exists(snap_path)

    ok_rest, msg_rest = main.restore_pre_sync_backup()
    assert ok_rest is True


# --- 13. TEST STREF PROJEKTOWYCH (KATEGORII) I DŹWIĘKÓW ---
def test_task_categories_and_sound():
    main.add_task_db("Projekt Domowy", is_daily=0, priority="B", category="🏠 Dom")
    tasks = main.get_tasks(is_daily_view=False)
    assert len(tasks) == 1
    assert tasks[0][1] == "Projekt Domowy"
    assert tasks[0][6] == "🏠 Dom"

    # Zmiana kategorii
    main.update_task_category_db(tasks[0][0], "💼 Praca")
    updated = main.get_tasks(is_daily_view=False)
    assert updated[0][6] == "💼 Praca"

    # Dźwięki zwrotne nie mogą rzucać wyjątków
    main.play_sound_feedback("task_done")
    main.play_sound_feedback("timer_done")


# --- 14. TESTY ZABEZPIECZEŃ DANYCH (starsza chmura, snapshoty, integralność) ---
def test_pull_does_not_overwrite_newer_local_data(tmp_path):
    sync_dir = tmp_path / "dysk"
    sync_dir.mkdir()
    main.set_config("sync_mode", "folder")
    main.set_config("sync_folder", str(sync_dir))

    # Lokalnie powstaje świeże zadanie -> lokalna baza jest nowsza.
    main.add_task_db("Najnowsze lokalne", is_daily=1, priority="A")
    assert any(t[1] == "Najnowsze lokalne" for t in main.get_tasks(is_daily_view=True))

    # Na "dysku" leży starsza baza (sama tabela-znacznik, bez naszego zadania).
    import sqlite3
    remote = sync_dir / "tasks.db"
    conn = sqlite3.connect(str(remote))
    conn.execute("CREATE TABLE marker (x INTEGER)")
    conn.commit()
    conn.close()
    old_time = os.path.getmtime(main.LOCAL_DB) - 3600
    os.utime(str(remote), (old_time, old_time))

    ok, msg = main.pull_database(force=False)
    assert ok is True
    assert "nowsza" in msg.lower()
    # Nic nie zostało nadpisane.
    assert any(t[1] == "Najnowsze lokalne" for t in main.get_tasks(is_daily_view=True))


def test_gist_pull_skips_older_cloud(monkeypatch):
    main.add_task_db("Lokalny skarb", is_daily=1, priority="A")

    # Bajty "chmury" to poprawna kopia bazy, ale z o wiele starszym exported_at.
    tmp_copy = str(main.LOCAL_DB) + ".cloud_copy"
    ok, _ = main.backup_database(tmp_copy)
    assert ok is True
    with open(tmp_copy, "rb") as f:
        cloud_b64 = base64.b64encode(f.read()).decode("utf-8")
    os.remove(tmp_copy)

    old_backup = json.dumps({"exported_at": "2000-01-01T00:00:00"})

    class MockResponse:
        status = 200

        def __init__(self, payload):
            self._data = json.dumps(payload).encode("utf-8")

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def mock_urlopen(req, timeout=5):
        return MockResponse({
            "files": {
                "tasks.db.b64": {"content": cloud_b64},
                "backup_tasks.json": {"content": old_backup},
            }
        })

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    main.set_config("sync_mode", "gist")
    main.set_config("gist_token", "ghp_test_token_123")
    main.set_config("gist_id", "istniejace_id")

    ok, msg = main.pull_database(force=False)
    assert ok is True
    assert "starsza" in msg.lower()
    assert any(t[1] == "Lokalny skarb" for t in main.get_tasks(is_daily_view=True))


def test_snapshot_rotation_and_integrity_check(tmp_path):
    folder = main.get_snapshot_dir()
    for i in range(6):
        path = os.path.join(folder, f"tasks_test_20240101_00000{i}.db")
        with open(path, "wb") as f:
            f.write(b"x")
    main.prune_snapshots(keep=3)
    remaining = [p for p in main.list_snapshots() if os.path.basename(p).startswith("tasks_test_")]
    assert len(remaining) == 3
    # Zostają najnowsze (sortowanie po nazwie malejąco).
    assert os.path.basename(remaining[0]) == "tasks_test_20240101_000005.db"

    # Uszkodzony plik nie jest uznawany za bazę.
    bad = tmp_path / "uszkodzona.db"
    bad.write_bytes(b"to nie jest baza sqlite")
    ok, status = main.check_db_integrity(str(bad))
    assert ok is False
    assert status


def test_end_of_day_creates_snapshot_and_remembers_date():
    main.add_task_db("Do zamknięcia", is_daily=1, priority="A")
    task_id = main.get_tasks(is_daily_view=True)[0][0]
    main.toggle_done_db(task_id, 1)

    before = len(main.list_snapshots())
    main.end_of_day_db()
    after = len(main.list_snapshots())
    assert after > before
    assert main.get_config("last_reset_date", "") != ""
    assert len(main.get_tasks(is_daily_view=True)) == 0
    assert any(a[1] == "Do zamknięcia" for a in main.get_completed_archive())


def test_ota_rollback_and_failed_version(tmp_path, monkeypatch):
    ota_logic = tmp_path / "app_logic_ota.py"
    ota_prev = tmp_path / "app_logic_ota.prev.py"
    ota_ver = tmp_path / "ota_version.txt"
    ota_prev_ver = tmp_path / "ota_prev_version.txt"
    ota_failed = tmp_path / "ota_failed_version.txt"

    monkeypatch.setattr(main, "OTA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "OTA_LOGIC_FILE", str(ota_logic))
    monkeypatch.setattr(main, "OTA_PREV_LOGIC_FILE", str(ota_prev))
    monkeypatch.setattr(main, "OTA_VERSION_FILE", str(ota_ver))
    monkeypatch.setattr(main, "OTA_PREV_VERSION_FILE", str(ota_prev_ver))
    monkeypatch.setattr(main, "OTA_FAILED_VERSION_FILE", str(ota_failed))

    # Symulacja: nowy kod jest zepsuty, ale mamy kopię poprzedniej wersji.
    ota_logic.write_text("def broken(:\n", encoding="utf-8")
    ota_prev.write_text("# poprzednia wersja\n", encoding="utf-8")
    ota_ver.write_text("9.9.9", encoding="utf-8")
    ota_prev_ver.write_text("1.0.0", encoding="utf-8")

    main.mark_failed_ota_version("9.9.9")
    assert main.get_failed_ota_version() == "9.9.9"

    assert main.rollback_ota() is True
    assert ota_logic.read_text(encoding="utf-8") == "# poprzednia wersja\n"
    assert ota_ver.read_text(encoding="utf-8") == "1.0.0"

    main.clear_failed_ota_version()
    assert main.get_failed_ota_version() == ""


def test_entrypoint_is_not_shadowed():
    """Regresja: `from app_logic import *` nadpisywało main i OTA nigdy nie działała."""
    import app_logic

    assert main.main is not app_logic.main
    assert main.main.__module__ == "main"


def test_version_files_are_in_sync():
    """FALLBACK_VERSION jest awaryjnym numerem w APK — musi zgadzać się z version.txt."""
    import app_logic

    repo_dir = os.path.dirname(os.path.abspath(main.__file__))
    with open(os.path.join(repo_dir, "version.txt"), encoding="utf-8") as f:
        repo_version = f.read().strip()
    assert app_logic.FALLBACK_VERSION == repo_version


def test_disabled_ota_ignores_ota_cache(tmp_path, monkeypatch):
    """DISABLE_OTA=1 (testy, diagnostyka) nie może uruchamiać pliku z folderu danych."""
    cache = tmp_path / "app_logic_ota.py"
    cache.write_text("raise RuntimeError('stary cache nie może się wykonać')\n", encoding="utf-8")
    (tmp_path / "ota_version.txt").write_text("99.0.0", encoding="utf-8")
    monkeypatch.setattr(main, "OTA_LOGIC_FILE", str(cache))
    monkeypatch.setattr(main, "OTA_VERSION_FILE", str(tmp_path / "ota_version.txt"))

    page = DummyPage(width=950)
    main.load_and_run(page)

    assert isinstance(page.controls[0], ft.SafeArea)


def test_newer_ota_cache_is_executed(tmp_path, monkeypatch):
    """Pobrany, nowszy kod OTA musi faktycznie się uruchomić (nie tylko zmienić numeru)."""
    cache = tmp_path / "app_logic_ota.py"
    cache.write_text(
        "import flet as ft\n\n\n"
        "def main(page):\n"
        "    page.controls.clear()\n"
        "    page.add(ft.Text('NOWY KOD OTA'))\n",
        encoding="utf-8",
    )
    (tmp_path / "ota_version.txt").write_text("99.0.0", encoding="utf-8")
    monkeypatch.setattr(main, "OTA_LOGIC_FILE", str(cache))
    monkeypatch.setattr(main, "OTA_VERSION_FILE", str(tmp_path / "ota_version.txt"))
    monkeypatch.setattr(main, "check_and_download_ota", lambda *a, **k: (False, ""))
    monkeypatch.delenv("DISABLE_OTA", raising=False)

    original = sys.modules.get("app_logic")
    try:
        page = DummyPage(width=950)
        main.load_and_run(page)
        assert page.controls[0].value == "NOWY KOD OTA"
        assert main.get_installed_ota_version() == "99.0.0"
    finally:
        sys.modules["app_logic"] = original
        sys.modules.pop("app_logic_ota", None)


def test_older_ota_cache_is_ignored(tmp_path, monkeypatch):
    """Plik OTA starszy od wbudowanego nie może przesłaniać świeżego kodu z APK."""
    cache = tmp_path / "app_logic_ota.py"
    cache.write_text("raise RuntimeError('nie powinno się wykonać')\n", encoding="utf-8")
    (tmp_path / "ota_version.txt").write_text("0.0.1", encoding="utf-8")
    monkeypatch.setattr(main, "OTA_LOGIC_FILE", str(cache))
    monkeypatch.setattr(main, "OTA_VERSION_FILE", str(tmp_path / "ota_version.txt"))
    monkeypatch.setattr(main, "check_and_download_ota", lambda *a, **k: (False, ""))
    monkeypatch.delenv("DISABLE_OTA", raising=False)

    page = DummyPage(width=950)
    main.load_and_run(page)

    assert isinstance(page.controls[0], ft.SafeArea)
    assert main.get_prepared_ota() == (None, "")


def test_manual_ota_install_and_activate(tmp_path, monkeypatch):
    """Ręczna aktualizacja ma działać od razu, bez restartu aplikacji."""
    import app_logic

    monkeypatch.setattr(app_logic, "APP_DIR", str(tmp_path))
    new_code = (
        "import flet as ft\n\n\n"
        "def main(page):\n"
        "    page.controls.clear()\n"
        "    page.add(ft.Text('AKTYWNE OTA'))\n"
    )
    ok, msg = app_logic.install_ota_code(new_code, "99.1.0")
    assert ok is True, msg
    assert (tmp_path / "app_logic_ota.py").exists()
    assert (tmp_path / "version.txt").read_text(encoding="utf-8") == "99.1.0"
    assert (tmp_path / "ota_version.txt").read_text(encoding="utf-8") == "99.1.0"

    original = sys.modules.get("app_logic")
    try:
        page = DummyPage(width=950)
        ok_activate, _ = app_logic.activate_downloaded_ota(page, "99.1.0")
        assert ok_activate is True
        assert page.controls[0].value == "AKTYWNE OTA"
        assert sys.modules["app_logic"] is not original
    finally:
        sys.modules["app_logic"] = original
        sys.modules.pop("app_logic_ota", None)


def test_install_ota_rejects_broken_code(tmp_path, monkeypatch):
    import app_logic

    monkeypatch.setattr(app_logic, "APP_DIR", str(tmp_path))
    ok, msg = app_logic.install_ota_code("def zepsuty(:\n", "9.9.9")
    assert ok is False
    assert "składni" in msg
    assert not (tmp_path / "app_logic_ota.py").exists()


def _iter_controls(control):
    yield control
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _iter_controls(content)
    for child in getattr(control, "controls", None) or []:
        yield from _iter_controls(child)


def _contains_label(control, needle):
    for item in _iter_controls(control):
        label = getattr(item, "content", None)
        if isinstance(label, str) and needle in label:
            return True
        value = getattr(item, "value", None)
        if isinstance(value, str) and needle in value:
            return True
    return False


def _find_clickable(root, needle):
    """Najbliższa klikalna kontrolka, której napis zawiera `needle`."""
    if callable(getattr(root, "on_click", None)) and _contains_label(root, needle):
        return root
    content = getattr(root, "content", None)
    if content is not None and not isinstance(content, str):
        found = _find_clickable(content, needle)
        if found is not None:
            return found
    for child in getattr(root, "controls", None) or []:
        found = _find_clickable(child, needle)
        if found is not None:
            return found
    return None


def test_settings_ota_button_swaps_interface(tmp_path, monkeypatch):
    """Klik „Sprawdź i wymuś aktualizację” ma podmienić interfejs, a nie tylko numer wersji."""
    import app_logic

    page = DummyPage(width=950)
    main.main(page)

    settings_nav = _find_clickable(page.controls[0], "Ustawienia")
    assert settings_nav is not None
    settings_nav.on_click(None)

    update_btn = _find_clickable(page.controls[0], "Sprawdź i wymuś aktualizację")
    assert update_btn is not None and callable(update_btn.on_click)

    monkeypatch.setattr(app_logic, "APP_DIR", str(tmp_path))
    new_code = (
        "import flet as ft\n\n\n"
        "def main(page):\n"
        "    page.controls.clear()\n"
        "    page.add(ft.Text('NOWA WERSJA Z OTA'))\n"
    )

    class Resp:
        status = 200
        headers = {}

        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=5, context=None):
        if "version.txt" in req.full_url:
            return Resp(b"99.2.0")
        return Resp(new_code.encode("utf-8"))

    monkeypatch.setattr(app_logic.urllib.request, "urlopen", fake_urlopen)

    original = sys.modules.get("app_logic")
    try:
        update_btn.on_click(None)
    finally:
        sys.modules["app_logic"] = original
        sys.modules.pop("app_logic_ota", None)

    assert page.controls[0].value == "NOWA WERSJA Z OTA"


def test_init_db_does_not_touch_cloud_timestamp():
    # Start aplikacji nie może udawać zmiany danych — inaczej pull z chmury
    # zawsze uzna lokalną bazę za nowszą i nigdy nic nie pobierze.
    main.set_config("db_last_change", "2000-01-01T00:00:00")
    main.init_db()
    assert main.get_config("db_last_change") == "2000-01-01T00:00:00"


def _control_contains_text(control, needle):
    """Rekurencyjnie szuka tekstu w kontrolkach Flet (do asercji UI)."""
    if isinstance(control, str):
        return needle in control
    text = getattr(control, "text", None) or getattr(control, "value", None)
    if isinstance(text, str) and needle in text:
        return True
    content = getattr(control, "content", None)
    if isinstance(content, str) and needle in content:
        return True
    if content is not None and _control_contains_text(content, needle):
        return True
    for child in getattr(control, "controls", None) or []:
        if _control_contains_text(child, needle):
            return True
    return False


# --- TERMINY ZADAŃ (deadline) ---
def test_deadline_parsing_and_formatting():
    assert main.parse_deadline_input("2026-11-24") == "2026-11-24"
    assert main.parse_deadline_input("24.11.2026") == "2026-11-24"
    assert main.parse_deadline_input("24/11/26") == "2026-11-24"
    assert main.parse_deadline_input("24.11", today=date(2026, 9, 1)) == "2026-11-24"
    # Data bez roku, która już minęła, oznacza kolejny rok.
    assert main.parse_deadline_input("24.11", today=date(2026, 12, 1)) == "2027-11-24"
    assert main.parse_deadline_input(None) is None
    assert main.parse_deadline_input("") is None
    with pytest.raises(ValueError):
        main.parse_deadline_input("31.02.2026")
    assert main.normalize_deadline("bzdura") is None
    assert main.format_deadline_pl("2026-11-24") == "24.11.2026"
    assert main.format_deadline_pl("2026-11-24", short=True) == "24.11"
    assert main.format_deadline_pl(None) == ""


def test_deadline_urgency_and_chip_text():
    today = date(2026, 11, 20)
    assert main.days_to_deadline("2026-11-24", today=today) == 4
    assert main.deadline_urgency("2026-11-24", today=today) == "later"
    assert main.deadline_urgency("2026-11-22", today=today) == "soon"
    assert main.deadline_urgency("2026-11-20", today=today) == "today"
    assert main.deadline_urgency("2026-11-19", today=today) == "overdue"
    assert main.deadline_urgency(None, today=today) == "none"
    # Daleki termin pokazuje samą datę, bez „za N dni”.
    assert main.deadline_chip_text("2026-12-24", today=today) == "termin 24.12"
    assert "za 2 dni" in main.deadline_chip_text("2026-11-22", today=today)
    assert "po terminie" in main.deadline_chip_text("2026-11-19", today=today)


def test_deadline_db_crud_and_details_update():
    main.add_task_db("Z terminem", is_daily=0, deadline="2026-11-24")
    task = main.get_tasks(is_daily_view=False)[0]
    assert len(task) == 8
    assert task[7] == "2026-11-24"
    task_id = task[0]
    assert main.get_task_deadline(task_id) == "2026-11-24"

    main.set_task_deadline_db(task_id, "2026-12-01")
    assert main.get_task_deadline(task_id) == "2026-12-01"

    # Zapisz tytuł, kategorię i termin jednym wywołaniem.
    main.update_task_details_db(task_id, "Nowy tytuł", category="Praca", deadline="2026-12-24")
    updated = main.get_tasks(is_daily_view=False)[0]
    assert updated[1] == "Nowy tytuł"
    assert updated[6] == "Praca"
    assert updated[7] == "2026-12-24"

    # DEADLINE_UNSET zostawia termin bez zmian, None go czyści.
    main.update_task_details_db(task_id, "Bez zmian terminu")
    assert main.get_task_deadline(task_id) == "2026-12-24"
    main.update_task_details_db(task_id, "Bez terminu", deadline=None)
    assert main.get_task_deadline(task_id) is None

    # Niepoprawny termin jest bezpiecznie odrzucany przy dodawaniu.
    main.add_task_db("Zły termin", is_daily=0, deadline="31.02.2026")
    bad = [t for t in main.get_tasks(is_daily_view=False) if t[1] == "Zły termin"][0]
    assert bad[7] is None


def test_deadline_migration_adds_column(tmp_path, monkeypatch):
    import app_logic
    old_db = str(tmp_path / "stara_baza.db")
    conn = sqlite3.connect(old_db)
    conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                 "is_daily INTEGER DEFAULT 0, is_done INTEGER DEFAULT 0, priority TEXT DEFAULT 'B', "
                 "subtasks TEXT DEFAULT '[]', category TEXT DEFAULT 'Ogólne')")
    conn.execute("CREATE TABLE routines (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                 "is_done INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE completed_archive (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, "
                 "priority TEXT, completed_at TEXT)")
    conn.execute("INSERT INTO tasks (title) VALUES ('Stare zadanie')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(app_logic, "LOCAL_DB", old_db)
    monkeypatch.setattr(main, "LOCAL_DB", old_db)

    assert main.ensure_schema_upgrade(old_db) is True
    with sqlite3.connect(old_db) as check:
        columns = [r[1] for r in check.execute("PRAGMA table_info(tasks)").fetchall()]
    assert "deadline" in columns
    tasks = main.get_tasks(is_daily_view=False)
    assert tasks[0][1] == "Stare zadanie"
    assert tasks[0][7] is None


def test_deadline_json_export_import_roundtrip(tmp_path, monkeypatch):
    import app_logic
    main.add_task_db("Z terminem JSON", is_daily=0, deadline="2026-11-24")
    data = main.export_database_to_json_dict()
    exported = [t for t in data["tasks"] if t["title"] == "Z terminem JSON"][0]
    assert exported["deadline"] == "2026-11-24"

    # Stary backup bez pola deadline importuje się bez błędu.
    data["tasks"].append({"title": "Stary backup", "is_daily": 0, "is_done": 0,
                          "priority": "B", "subtasks": [], "category": "Ogólne"})
    old_db = str(tmp_path / "import.db")
    monkeypatch.setattr(app_logic, "LOCAL_DB", old_db)
    monkeypatch.setattr(main, "LOCAL_DB", old_db)
    main.init_db()
    main.import_database_from_json_dict(data, replace_existing=True)
    imported = {t[1]: t[7] for t in main.get_tasks(is_daily_view=False)}
    assert imported["Z terminem JSON"] == "2026-11-24"
    assert imported["Stary backup"] is None


def test_deadline_sorting_filtering_and_reminder_window():
    today = date(2026, 11, 20)

    def task(tid, title, done, deadline):
        return (tid, title, 0, done, "B", [], "Ogólne", deadline)

    tasks = [
        task(1, "Później", 0, "2026-12-24"),
        task(2, "Bez terminu", 0, None),
        task(3, "Zaległe", 0, "2026-11-19"),
        task(4, "Blisko", 0, "2026-11-22"),
        task(5, "Zrobione z terminem", 1, "2026-11-21"),
    ]
    ordered = [t[1] for t in main.sort_tasks_by_deadline(tasks)]
    assert ordered == ["Zaległe", "Blisko", "Później", "Bez terminu", "Zrobione z terminem"]

    week = [t[1] for t in main.filter_tasks_by_deadline(tasks, "WEEK", today=today)]
    assert week == ["Blisko", "Zrobione z terminem"]
    overdue = [t[1] for t in main.filter_tasks_by_deadline(tasks, "OVERDUE", today=today)]
    assert overdue == ["Zaległe"]
    assert [t[1] for t in main.filter_tasks_by_deadline(tasks, "ALL", today=today)] == [t[1] for t in tasks]


def test_deadline_reminders_from_database_and_hide_today():
    today = date.today()
    main.add_task_db("Tuż przed terminem", is_daily=0,
                     deadline=(today + timedelta(days=1)).isoformat())
    main.add_task_db("Po terminie", is_daily=0,
                     deadline=(today - timedelta(days=2)).isoformat())
    main.add_task_db("Daleko", is_daily=0,
                     deadline=(today + timedelta(days=30)).isoformat())
    main.add_task_db("Zrobione", is_daily=0,
                     deadline=(today + timedelta(days=1)).isoformat())
    done_task = [t for t in main.get_tasks(is_daily_view=False) if t[1] == "Zrobione"][0]
    main.toggle_done_db(done_task[0], 1)

    due = main.get_deadline_tasks(within_days=7, include_overdue=True)
    due_titles = [t[1] for t in due]
    assert "Tuż przed terminem" in due_titles
    assert "Po terminie" in due_titles
    assert "Daleko" not in due_titles
    assert "Zrobione" not in due_titles

    assert main.should_show_deadline_card() is True
    main.hide_deadline_card_today()
    assert main.should_show_deadline_card() is False
    main.set_config("deadline_card_hidden_date", "")

    main.set_config("deadline_reminders_enabled", "0")
    assert main.should_show_deadline_card() is False
    main.set_config("deadline_reminders_enabled", "1")
    assert main.should_show_deadline_card() is True


def test_ui_edit_dialog_sets_deadline():
    main.add_task_db("Zadanie z terminem", is_daily=1, priority="B")
    task_id = main.get_tasks(is_daily_view=True)[0][0]

    page = DummyPage(width=950)
    main.main(page)

    # Otwieramy dialog przez przycisk edycji na karcie Daily (bez podkroków:
    # prio, checkbox, tytuł, edycja, usuń, uchwyt).
    root_pc = page.controls[0].content
    content_col = root_pc.content.controls[2].content
    tasks_col = [c for c in content_col.controls if isinstance(c, ft.Column)][0]
    tasks_col.controls[0].content.controls[3].on_click(None)

    edit_task_dialog = page.overlay[3]
    assert edit_task_dialog.open is True
    assert isinstance(edit_task_dialog.content, ft.Column)
    deadline_section = edit_task_dialog.content.controls[2]
    assert isinstance(deadline_section, ft.Column)

    # Kolejność pól: tytuł, kategoria, termin — testy używają controls[0].
    assert isinstance(edit_task_dialog.content.controls[0], ft.TextField)

    preset_row = deadline_section.controls[1]
    next_week_btn = [c for c in preset_row.controls if _control_contains_text(c, "Za tydzień")][0]
    next_week_btn.on_click(None)

    edit_task_dialog.content.controls[0].value = "Zadanie z terminem"
    edit_task_dialog.actions[1].on_click(None)

    expected = (date.today() + timedelta(days=7)).isoformat()
    assert main.get_task_deadline(task_id) == expected


def test_ui_master_deadline_filter_and_daily_reminder_card():
    today = date.today()
    main.add_task_db("Blisko", is_daily=0, deadline=(today + timedelta(days=2)).isoformat())
    main.add_task_db("Daleko", is_daily=0, deadline=(today + timedelta(days=30)).isoformat())
    main.set_config("master_deadline_filter", "WEEK")

    page = DummyPage(width=950)
    main.main(page)
    root_pc = page.controls[0].content
    content_col = root_pc.content.controls[2].content

    # Karta przypomnień na Daily jest kontenerem, więc nie psuje kontraktu
    # „pierwsza ft.Column w content_area to lista zadań”.
    assert any(isinstance(c, ft.Column) for c in content_col.controls)
    assert any(_control_contains_text(c, "Nadchodzące terminy") for c in content_col.controls)

    # Master: filtr „Ten tydzień” ukrywa odległy termin.
    sidebar_col = root_pc.content.controls[0].content
    sidebar_col.controls[2].content.on_click(None)
    tasks_col = [c for c in content_col.controls if isinstance(c, ft.Column)][0]
    assert len(tasks_col.controls) == 1

    # Sortowanie po terminie jest zapamiętywane w configu.
    main.set_config("master_sort_mode", "DEADLINE")
    assert main.get_config("master_sort_mode") == "DEADLINE"


def test_ui_settings_has_deadline_card():
    page = DummyPage(width=950)
    main.main(page)
    root_pc = page.controls[0].content
    sidebar_col = root_pc.content.controls[0].content
    sidebar_col.controls[-1].on_click(None)
    content_col = root_pc.content.controls[2].content
    assert any(_control_contains_text(c, "Terminy i przypomnienia") for c in content_col.controls)


# --- 14. SPRINT 2.1: ŚWIADOMY PRZEŁÓW DNIA ---
def test_day_rollover_pending_does_not_change_anything():
    main.add_task_db("Zrobione wczoraj", is_daily=1, priority="A")
    main.add_task_db("Niezrobione wczoraj", is_daily=1, priority="B")
    tasks = main.get_tasks(is_daily_view=True)
    done_id = [t[0] for t in tasks if t[1] == "Zrobione wczoraj"][0]
    undone_id = [t[0] for t in tasks if t[1] == "Niezrobione wczoraj"][0]
    main.toggle_done_db(done_id, 1)
    main.set_config("last_reset_date", "2020-01-01")

    info = main.get_pending_day_rollover()
    assert info is not None
    assert info["done_count"] == 1
    assert [t[0] for t in info["undone_daily"]] == [undone_id]
    # Nic się nie zmienia, dopóki nie ma decyzji.
    assert main.get_config("last_reset_date") == "2020-01-01"
    assert len(main.get_tasks(is_daily_view=False)) == 2
    assert len(main.get_completed_archive()) == 0


def test_apply_day_rollover_archives_once_and_honours_choices():
    main.add_task_db("Zostaje", is_daily=1, priority="A")
    main.add_task_db("Do Mastera", is_daily=1, priority="B")
    main.add_task_db("Zrobione", is_daily=1, priority="C")
    ids = {t[1]: t[0] for t in main.get_tasks(is_daily_view=True)}
    main.toggle_done_db(ids["Zrobione"], 1)
    main.set_config("last_reset_date", "2020-01-01")

    result = main.apply_day_rollover(decisions={ids["Zostaje"]: "keep",
                                                ids["Do Mastera"]: "master"})
    assert result["archived"] == 1
    assert result["kept"] == 1
    assert result["to_master"] == 1
    assert main.get_config("last_reset_date") == date.today().isoformat()

    archive = main.get_completed_archive()
    assert len(archive) == 1 and archive[0][1] == "Zrobione"

    rows = {t[1]: t for t in main.get_tasks(is_daily_view=False)}
    assert rows["Zostaje"][2] == 1        # nadal na Daily
    assert rows["Do Mastera"][2] == 0     # wróciło do Mastera
    assert "Zrobione" not in rows         # zrobione opuszcza listę

    # Powtórne zamknięcie nie duplikuje archiwum (dzień już domknięty).
    assert main.apply_day_rollover() is None
    assert len(main.get_completed_archive()) == 1


def test_day_rollover_mode_defaults_and_auto_master():
    assert main.get_day_rollover_mode() == main.DAY_ROLLOVER_ASK
    main.set_config("day_rollover_mode", "bzdura")
    assert main.get_day_rollover_mode() == main.DAY_ROLLOVER_ASK

    main.add_task_db("Niezrobione", is_daily=1, priority="B")
    main.set_config("last_reset_date", "2020-01-01")
    main.set_config("day_rollover_mode", main.DAY_ROLLOVER_AUTO_MASTER)
    result = main.apply_day_rollover(mode=main.DAY_ROLLOVER_AUTO_MASTER)
    assert result["to_master"] == 1
    assert main.get_tasks(is_daily_view=False)[0][2] == 0


def test_day_rollover_ui_dialog_open_and_later():
    main.add_task_db("Zadanie dnia", is_daily=1, priority="B")
    main.set_config("last_reset_date", "2020-01-01")
    page = DummyPage(width=950)
    main.main(page)

    dialog = page.overlay[10]
    assert dialog.open is True
    assert "Nowy dzień" in (dialog.title.value or "")
    assert _control_contains_text(dialog.content, "Zrobione wczoraj")
    # Na Daily pojawia się spokojne wejście do rytuału.
    assert _find_clickable(page.controls[0], "Rozpocznij dzień") is not None

    # „Później” nic nie zmienia — dzień zostaje otwarty.
    dialog.actions[0].on_click(None)
    assert dialog.open is False
    assert main.get_config("last_reset_date") == "2020-01-01"
    assert len(main.get_tasks(is_daily_view=True)) == 1


def test_day_rollover_ui_confirm_moves_task_to_master():
    main.add_task_db("Do decyzji", is_daily=1, priority="B")
    task_id = main.get_tasks(is_daily_view=True)[0][0]
    main.set_config("last_reset_date", "2020-01-01")
    page = DummyPage(width=950)
    main.main(page)

    dialog = page.overlay[10]
    choice_container = [c for c in dialog.content.controls
                        if isinstance(c, ft.Container) and isinstance(c.content, ft.Column)][0]
    pills = choice_container.content.controls[1].controls
    pills[1].on_click(None)  # „Wróć do Mastera”
    dialog.actions[1].on_click(None)  # „Zamknij dzień”

    assert dialog.open is False
    row = [t for t in main.get_tasks(is_daily_view=False) if t[0] == task_id][0]
    assert row[2] == 0
    assert main.get_config("last_reset_date") == date.today().isoformat()


# --- 15. SPRINT 2.3: INBOX I 2-MINUTOWY TRIAGE ---
def test_quick_dump_goes_to_inbox():
    page = DummyPage(width=950)
    main.main(page)

    root = page.controls[0].content
    content_col = root.content.controls[2].content
    quick_bar = content_col.controls[1]
    field = quick_bar.content.controls[0]
    add_btn = quick_bar.content.controls[1]
    field.value = "Myśl do posortowania"
    add_btn.on_click(None)

    inbox = main.get_inbox_tasks()
    assert len(inbox) == 1
    assert inbox[0][1] == "Myśl do posortowania"
    assert inbox[0][2] == 0      # is_daily
    assert inbox[0][4] == "C"    # priorytet


def test_inbox_hidden_from_master_and_section_visible():
    main.add_task_db("Z Inbox", is_daily=0, priority="C", category=main.INBOX_CATEGORY)
    main.add_task_db("Zwykłe zadanie", is_daily=0, priority="B")

    page = DummyPage(width=950)
    main.main(page)
    root = page.controls[0].content
    sidebar_col = root.content.controls[0].content
    sidebar_col.controls[2].content.on_click(None)  # Master

    content_col = root.content.controls[2].content
    tasks_col = [c for c in content_col.controls if isinstance(c, ft.Column)][0]
    texts = [c.value for c in _iter_controls(tasks_col) if isinstance(c, ft.Text)]
    assert "Zwykłe zadanie" in texts
    assert "Z Inbox" not in texts
    # Sekcja Inbox widoczna z licznikiem i nazwą zadania.
    assert any(_control_contains_text(c, "Inbox (1)") for c in content_col.controls)
    assert any(_control_contains_text(c, "Z Inbox") for c in content_col.controls)


def test_triage_inbox_task_db_moves_and_clears():
    main.add_task_db("Do triage", is_daily=0, priority="C", category=main.INBOX_CATEGORY)
    task_id = main.get_inbox_tasks()[0][0]
    main.triage_inbox_task_db(task_id, category="💼 Praca", priority="A",
                              is_daily=True, deadline="2026-12-01")
    row = [t for t in main.get_tasks(is_daily_view=False) if t[0] == task_id][0]
    assert row[6] == "💼 Praca"
    assert row[4] == "A"
    assert row[2] == 1
    assert row[7] == "2026-12-01"
    assert main.get_inbox_tasks() == []

    # Kategoria „Inbox” jest niedozwolona — zadanie nie utknie w Inbox.
    main.add_task_db("Drugie", is_daily=0, priority="C", category=main.INBOX_CATEGORY)
    other_id = main.get_inbox_tasks()[0][0]
    main.triage_inbox_task_db(other_id, category=main.INBOX_CATEGORY)
    moved = [t for t in main.get_tasks(is_daily_view=False) if t[0] == other_id][0]
    assert moved[6] == "Ogólne"


def test_inbox_ui_confirm_button_applies_triage():
    main.add_task_db("Inbox UI", is_daily=0, priority="C", category=main.INBOX_CATEGORY)
    task_id = main.get_inbox_tasks()[0][0]

    page = DummyPage(width=950)
    main.main(page)
    root = page.controls[0].content
    sidebar_col = root.content.controls[0].content
    sidebar_col.controls[2].content.on_click(None)  # Master

    confirm_btn = _find_clickable(page.controls[0], "Zatwierdź")
    assert confirm_btn is not None
    confirm_btn.on_click(None)

    assert main.get_inbox_tasks() == []
    row = [t for t in main.get_tasks(is_daily_view=False) if t[0] == task_id][0]
    assert row[6] == "Ogólne"


def test_settings_day_rollover_card_sets_mode():
    page = DummyPage(width=950)
    main.main(page)

    root_pc = page.controls[0].content
    sidebar_col = root_pc.content.controls[0].content
    sidebar_col.controls[-1].on_click(None)  # Ustawienia
    content_col = root_pc.content.controls[2].content
    assert any(_control_contains_text(c, "Przełom dnia") for c in content_col.controls)

    pill = _find_clickable(page.controls[0], "Auto: do Mastera")
    assert pill is not None
    pill.on_click(None)
    assert main.get_day_rollover_mode() == main.DAY_ROLLOVER_AUTO_MASTER


# --- 15. SPRINT 3.2: ZNACZNIKI CZASU I „ZADANIA DUCHY” ---
def test_task_timestamps_created_and_updated():
    main.add_task_db("Zegar", is_daily=0, priority="B")
    task_id = main.get_tasks(is_daily_view=False)[0][0]
    created, updated = main.get_task_timestamps(task_id)
    assert created and updated
    assert created == updated

    # Starszy wiersz: cofamy updated_at ręcznie, akcja ma go podnieść.
    with sqlite3.connect(main.LOCAL_DB) as conn:
        conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?",
                     ("2000-01-01T00:00:00", task_id))
    main.toggle_done_db(task_id, 1)
    created_after, bumped = main.get_task_timestamps(task_id)
    assert bumped > "2000-01-01T00:00:00"
    # created_at nie zmienia się przy edycji.
    assert created_after == created

    assert main.get_task_timestamps(999999) is None


def test_task_timestamps_migration_backfills_old_rows(tmp_path, monkeypatch):
    import app_logic
    old_db = str(tmp_path / "stara_bez_czasu.db")
    conn = sqlite3.connect(old_db)
    conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                 "is_daily INTEGER DEFAULT 0, is_done INTEGER DEFAULT 0, priority TEXT DEFAULT 'B', "
                 "subtasks TEXT DEFAULT '[]', category TEXT DEFAULT 'Ogólne', deadline TEXT)")
    conn.execute("CREATE TABLE routines (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                 "is_done INTEGER DEFAULT 0, routine_days TEXT DEFAULT '[]')")
    conn.execute("CREATE TABLE completed_archive (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, "
                 "priority TEXT, completed_at TEXT)")
    conn.execute("INSERT INTO tasks (title) VALUES ('Stare bez czasu')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(app_logic, "LOCAL_DB", old_db)
    monkeypatch.setattr(main, "LOCAL_DB", old_db)

    assert main.ensure_schema_upgrade(old_db) is True
    with sqlite3.connect(old_db) as check:
        columns = [r[1] for r in check.execute("PRAGMA table_info(tasks)").fetchall()]
    assert "created_at" in columns and "updated_at" in columns
    created, updated = main.get_task_timestamps(main.get_tasks(is_daily_view=False)[0][0])
    assert created and updated


def test_stale_task_detection_rules():
    now = datetime(2026, 10, 31, 12, 0, 0)
    old = (now - timedelta(days=20)).isoformat(timespec="seconds")
    fresh = (now - timedelta(days=2)).isoformat(timespec="seconds")
    assert main.is_stale_task(old, now=now) is True
    assert main.is_stale_task(fresh, now=now) is False
    # Zrobione nigdy nie jest „duchem”.
    assert main.is_stale_task(old, is_done=1, now=now) is False
    # Przyszły termin wyklucza; zaległy nie.
    assert main.is_stale_task(old, deadline="2026-12-01", now=now) is False
    assert main.is_stale_task(old, deadline="2026-10-01", now=now) is True
    # Brak znacznika = nie duch (bez danych nie straszymy).
    assert main.is_stale_task(None, now=now) is False


def test_stale_task_ids_from_database():
    main.add_task_db("Świeże", is_daily=0, priority="B")
    main.add_task_db("Duch", is_daily=0, priority="B")
    main.add_task_db("Duch z terminem", is_daily=0, priority="B", deadline="2030-01-01")
    ids = {t[1]: t[0] for t in main.get_tasks(is_daily_view=False)}
    with sqlite3.connect(main.LOCAL_DB) as conn:
        conn.execute("UPDATE tasks SET updated_at = ? WHERE id IN (?, ?)",
                     ("2000-01-01T00:00:00", ids["Duch"], ids["Duch z terminem"]))

    stale = main.get_stale_task_ids()
    assert ids["Duch"] in stale
    assert ids["Świeże"] not in stale
    assert ids["Duch z terminem"] not in stale


def test_task_timestamps_json_export_import(tmp_path, monkeypatch):
    import app_logic
    main.add_task_db("Z czasem", is_daily=0, priority="B")
    exported = [t for t in main.export_database_to_json_dict()["tasks"] if t["title"] == "Z czasem"][0]
    assert exported["created_at"] and exported["updated_at"]

    data = main.export_database_to_json_dict()
    # Stary backup bez znaczników importuje się i dostaje datę migracji.
    data["tasks"].append({"title": "Stary backup", "is_daily": 0, "is_done": 0,
                          "priority": "B", "subtasks": [], "category": "Ogólne"})
    old_db = str(tmp_path / "import_czas.db")
    monkeypatch.setattr(app_logic, "LOCAL_DB", old_db)
    monkeypatch.setattr(main, "LOCAL_DB", old_db)
    main.init_db()
    main.import_database_from_json_dict(data, replace_existing=True)
    imported = {t[1]: t[0] for t in main.get_tasks(is_daily_view=False)}
    created, updated = main.get_task_timestamps(imported["Stary backup"])
    assert created and updated


def test_ui_master_stale_filter_pill():
    main.add_task_db("Świeże", is_daily=0, priority="B")
    main.add_task_db("Duch", is_daily=0, priority="B")
    ids = {t[1]: t[0] for t in main.get_tasks(is_daily_view=False)}
    with sqlite3.connect(main.LOCAL_DB) as conn:
        conn.execute("UPDATE tasks SET updated_at = '2000-01-01T00:00:00' WHERE id = ?",
                     (ids["Duch"],))

    page = DummyPage(width=950)
    main.main(page)
    root = page.controls[0].content
    sidebar_col = root.content.controls[0].content
    sidebar_col.controls[2].content.on_click(None)  # Master

    content_col = root.content.controls[2].content
    tasks_col = [c for c in content_col.controls if isinstance(c, ft.Column)][0]
    texts = [c.value for c in _iter_controls(tasks_col) if isinstance(c, ft.Text)]
    assert "Świeże" in texts and "Duch" in texts

    pill = _find_clickable(page.controls[0], "Dawno nietknięte")
    assert pill is not None
    pill.on_click(None)
    assert main.get_config("master_stale_only") == "1"

    content_col = page.controls[0].content.content.controls[2].content
    tasks_col = [c for c in content_col.controls if isinstance(c, ft.Column)][0]
    texts = [c.value for c in _iter_controls(tasks_col) if isinstance(c, ft.Text)]
    assert "Duch" in texts and "Świeże" not in texts


# --- 16. SPRINT 3.1: HISTORIA RUTYN I MIĘKKIE SERIE ---
def test_routine_log_records_history_and_survives_end_of_day():
    main.add_routine_db("Leki")
    rid = main.get_routines()[0][0]
    day = date(2026, 10, 27)
    main.toggle_routine_db(rid, True, day=day)
    assert main.get_routine_log_map()[rid][day.isoformat()] == 1

    # Odznaczenie tego samego dnia nadpisuje wpis (bez duplikatu).
    main.toggle_routine_db(rid, False, day=day)
    assert main.get_routine_log_map()[rid][day.isoformat()] == 0

    main.toggle_routine_db(rid, True, day=day)
    main.end_of_day_db()  # stary reset: is_done -> 0, historia musi zostać
    assert main.get_routines()[0][2] == 0
    assert main.get_routine_log_map()[rid][day.isoformat()] == 1


def test_routine_history_survives_day_rollover():
    main.add_routine_db("Woda")
    rid = main.get_routines()[0][0]
    main.toggle_routine_db(rid, True)  # dziś
    main.set_config("last_reset_date", "2020-01-01")

    main.apply_day_rollover()

    assert main.get_routines()[0][2] == 0
    assert main.get_routine_log_map()[rid][date.today().isoformat()] == 1


def test_routine_week_view_and_soft_streak():
    today = date(2026, 10, 28)  # środa
    days, done_count, scheduled = main.routine_week_view(
        [], {"2026-10-26": 1, "2026-10-27": 1, "2026-10-28": 1}, today=today)
    assert scheduled == 7 and done_count == 3
    assert days[0]["label"] == "Pn" and days[6]["label"] == "Nd"
    assert days[2]["is_today"] is True
    assert days[0]["done"] is True and days[3]["done"] is False

    # Trzy dni z rzędu (Pn–Śr), potem przerwa -> seria = 3.
    assert main.routine_current_streak([], {"2026-10-26": 1, "2026-10-27": 1, "2026-10-28": 1},
                                       today=today) == 3

    # Dzisiejszy dzień jeszcze się nie skończył i nie zrywa serii.
    assert main.routine_current_streak(
        [], {"2026-10-26": 1, "2026-10-27": 1}, today=today) == 2

    # Dzień, w którym rutyna nie wypada, nie przerywa serii (Pn, Śr; przerwa Cz).
    assert main.routine_current_streak(
        [0, 2], {"2026-10-26": 1, "2026-10-28": 1}, today=date(2026, 10, 29)) == 2

    # Rutyna Pn/Śr: zaplanowane 2 dni w tygodniu.
    week, done_w, sched_w = main.routine_week_view([0, 2], {"2026-10-26": 1}, today=today)
    assert sched_w == 2 and done_w == 1
    assert week[3]["scheduled"] is False


def test_routine_log_migration_old_db(tmp_path, monkeypatch):
    import app_logic

    old_db = str(tmp_path / "old_routine_log.db")
    _make_old_schema_db(old_db)
    monkeypatch.setattr(app_logic, "LOCAL_DB", old_db)
    monkeypatch.setattr(main, "LOCAL_DB", old_db)
    main.init_db()

    rid = main.get_routines()[0][0]
    assert main.get_routine_log_map() == {}
    main.toggle_routine_db(rid, True, day=date(2026, 10, 20))
    assert main.get_routine_log_map()[rid]["2026-10-20"] == 1

    conn = sqlite3.connect(old_db)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()
    assert "routine_log" in tables


def test_routine_log_json_roundtrip(tmp_path, monkeypatch):
    import app_logic

    main.add_routine_db("Meds", days=[0, 2])
    rid = main.get_routines()[0][0]
    main.toggle_routine_db(rid, True, day=date(2026, 10, 26))
    main.toggle_routine_db(rid, True, day=date(2026, 10, 28))

    exported = main.export_database_to_json_dict()
    assert {e["date"] for e in exported["routine_log"]} == {"2026-10-26", "2026-10-28"}

    new_db = str(tmp_path / "import_routine_log.db")
    monkeypatch.setattr(app_logic, "LOCAL_DB", new_db)
    monkeypatch.setattr(main, "LOCAL_DB", new_db)
    main.init_db()
    main.import_database_from_json_dict(exported, replace_existing=True)

    new_rid = main.get_routines()[0][0]
    log = main.get_routine_log_map(new_rid).get(new_rid, {})
    assert log["2026-10-26"] == 1 and log["2026-10-28"] == 1


def test_settings_routine_history_ui():
    main.add_routine_db("Leki")
    rid = main.get_routines()[0][0]
    main.toggle_routine_db(rid, True)

    page = DummyPage(width=950)
    main.main(page)
    root = page.controls[0].content
    sidebar_col = root.content.controls[0].content
    sidebar_col.controls[-1].on_click(None)  # Ustawienia

    content_col = root.content.controls[2].content
    assert _control_contains_text(content_col, "w tym tygodniu: 1/7")


# --- 17. SPRINT 3.4: SZACOWANY CZAS (estimate_min) ---
def test_estimate_normalization_and_formatting():
    assert main.normalize_estimate_min("45") == 45
    assert main.normalize_estimate_min(45.0) == 45
    assert main.normalize_estimate_min(" 30 ") == 30
    assert main.normalize_estimate_min(0) is None
    assert main.normalize_estimate_min(-5) is None
    assert main.normalize_estimate_min("abc") is None
    assert main.normalize_estimate_min(None) is None
    assert main.normalize_estimate_min(True) is None

    assert main.format_duration_pl(100) == "1 h 40 min"
    assert main.format_duration_pl(60) == "1 h"
    assert main.format_duration_pl(45) == "45 min"
    assert main.format_duration_pl(0) == ""

    total, missing = main.sum_estimates({1: 30, 2: None, 3: 0}, [(1,), (2,), (3,)])
    assert total == 30 and missing == 2


def test_task_estimate_db_crud():
    main.add_task_db("Szacowane", is_daily=1, priority="A", estimate_min=30)
    task_id = main.get_tasks(is_daily_view=True)[0][0]
    assert main.get_task_estimate(task_id) == 30

    main.set_task_estimate_db(task_id, 90)
    assert main.get_task_estimate(task_id) == 90

    # Sentinel nie rusza szacunku, konkretna wartość go nadpisuje.
    main.update_task_details_db(task_id, "Szacowane", estimate=main.ESTIMATE_UNSET)
    assert main.get_task_estimate(task_id) == 90
    main.update_task_details_db(task_id, "Szacowane", estimate=15)
    assert main.get_task_estimate(task_id) == 15

    main.set_task_estimate_db(task_id, None)
    assert main.get_task_estimate(task_id) is None


def test_estimate_migration_old_db(tmp_path, monkeypatch):
    import app_logic

    old_db = str(tmp_path / "old_estimate.db")
    conn = sqlite3.connect(old_db)
    conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                 "is_daily INTEGER DEFAULT 0, is_done INTEGER DEFAULT 0, priority TEXT DEFAULT 'B', "
                 "subtasks TEXT DEFAULT '[]', category TEXT DEFAULT 'Ogólne', deadline TEXT, "
                 "created_at TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE routines (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                 "is_done INTEGER DEFAULT 0, routine_days TEXT DEFAULT '[]')")
    conn.execute("CREATE TABLE completed_archive (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, "
                 "priority TEXT, completed_at TEXT)")
    conn.execute("INSERT INTO tasks (title) VALUES ('Stare bez szacunku')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(app_logic, "LOCAL_DB", old_db)
    monkeypatch.setattr(main, "LOCAL_DB", old_db)
    assert main.ensure_schema_upgrade(old_db) is True

    with sqlite3.connect(old_db) as check:
        columns = [r[1] for r in check.execute("PRAGMA table_info(tasks)").fetchall()]
    assert "estimate_min" in columns
    assert main.get_task_estimate(main.get_tasks(is_daily_view=False)[0][0]) is None


def test_estimate_json_roundtrip(tmp_path, monkeypatch):
    import app_logic

    main.add_task_db("Z szacunkiem", is_daily=0, priority="B", estimate_min=75)
    exported = main.export_database_to_json_dict()
    task = [t for t in exported["tasks"] if t["title"] == "Z szacunkiem"][0]
    assert task["estimate_min"] == 75
    # Stary backup bez szacunku importuje się i nie wysypuje.
    assert main.normalize_estimate_min(task.get("estimate_min")) == 75

    new_db = str(tmp_path / "import_estimate.db")
    monkeypatch.setattr(app_logic, "LOCAL_DB", new_db)
    monkeypatch.setattr(main, "LOCAL_DB", new_db)
    main.init_db()
    main.import_database_from_json_dict(exported, replace_existing=True)
    new_id = main.get_tasks(is_daily_view=False)[0][0]
    assert main.get_task_estimate(new_id) == 75


def test_undo_restores_estimate():
    main.add_task_db("Do cofnięcia", is_daily=1, priority="A", estimate_min=45)
    task_id = main.get_tasks(is_daily_view=True)[0][0]

    row = main.delete_task_with_undo_db(task_id)
    assert main.get_task_estimate(task_id) is None
    assert main.restore_task_row_db(row) is True
    assert main.get_task_estimate(task_id) == 45


def test_daily_estimate_summary_ui():
    main.add_task_db("Krótkie", is_daily=1, priority="B", estimate_min=60)
    main.add_task_db("Średnie", is_daily=1, priority="B", estimate_min=30)
    main.add_task_db("Bez szacunku", is_daily=1, priority="C")

    page = DummyPage(width=950)
    main.main(page)
    root = page.controls[0].content
    content_col = root.content.controls[2].content

    assert _control_contains_text(content_col, "Zaplanowane: ~1 h 30 min")
    assert _control_contains_text(content_col, "bez szacunku: 1")


def test_ui_edit_dialog_sets_estimate():
    main.add_task_db("Z szacunkiem", is_daily=1, priority="B", estimate_min=15)
    task_id = main.get_tasks(is_daily_view=True)[0][0]

    page = DummyPage(width=950)
    main.main(page)
    root = page.controls[0].content
    content_col = root.content.controls[2].content
    tasks_col = [c for c in content_col.controls if isinstance(c, ft.Column)][0]
    # Daily bez podkroków: prio, checkbox, tytuł, edycja...
    tasks_col.controls[0].content.controls[3].on_click(None)

    dialog = page.overlay[3]
    assert dialog.open is True
    # Kontrakt kolejności: 0 tytuł, 1 kategoria, 2 termin, 3 szacunek.
    assert isinstance(dialog.content.controls[2], ft.Column)
    assert isinstance(dialog.content.controls[3], ft.Column)

    btn_30 = [c for c in _iter_controls(dialog.content.controls[3])
              if getattr(c, "text", None) == "30 min"
              or getattr(c, "content", None) == "30 min"][0]
    btn_30.on_click(None)
    dialog.actions[1].on_click(None)
    assert main.get_task_estimate(task_id) == 30


# --- SPRINT 4.1: jeden bezpieczny punkt aktualizacji UI + próg resize ---
def _content_col(page):
    """Kolumna treści niezależnie od układu PC (Row) / mobile (Column)."""
    root = page.controls[0].content
    if isinstance(root.content, ft.Row):
        return root.content.controls[2].content
    return root.content.controls[0].content


class _QueuedPage(DummyPage):
    """Strona, która nie wykonuje `run_thread` od razu (do testu koalescencji)."""

    def __init__(self, width=950):
        super().__init__(width)
        self.pending = []

    def run_thread(self, handler, *args, **kwargs):
        self.pending.append(handler)


def test_ui_update_scheduler_coalesces():
    page = _QueuedPage()
    calls = []

    def sink():
        calls.append(1)

    main.schedule_ui_update(page, sink)
    main.schedule_ui_update(page, sink)
    main.schedule_ui_update(page, sink)
    # Ten sam callback nie dubluje się, dopóki poprzednia aktualizacja czeka.
    assert len(page.pending) == 1
    page.pending[0]()
    assert calls == [1]


def test_ui_update_scheduler_runs_without_runner():
    page = DummyPage()  # brak run_thread -> fallback na run_task (natychmiast)
    calls = []
    main.schedule_ui_update(page, lambda: calls.append("a"))
    main.schedule_ui_update(page, lambda: calls.append("b"))
    assert calls == ["a", "b"]


def test_ui_update_scheduler_thread_safe():
    page = DummyPage(width=950)
    main.main(page)
    errors = []

    def worker():
        try:
            for _ in range(20):
                main.set_sync_status("ok", "test")
        except Exception as exc:  # pragma: no cover - tylko raport błędu
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert errors == []


def test_resize_rebuilds_only_on_mobile_threshold():
    main.add_task_db("Zadanie", is_daily=1, priority="B")
    page = DummyPage(width=950)
    main.main(page)
    progress_before = _content_col(page).controls[3]

    # 950 -> 949 -> 948 to wciąż PC: bez przebudowy.
    page.width = 949
    page.on_resize(None)
    assert _content_col(page).controls[3] is progress_before
    page.width = 948
    page.on_resize(None)
    assert _content_col(page).controls[3] is progress_before

    # Przekroczenie progu 650 px przebudowuje widok raz.
    page.width = 400
    page.on_resize(None)
    assert _content_col(page).controls[3] is not progress_before


def test_quick_input_survives_refresh():
    main.add_task_db("Zadanie", is_daily=1, priority="B")
    page = DummyPage(width=950)
    main.main(page)
    content_col = _content_col(page)
    quick_bar = content_col.controls[1]
    field = quick_bar.content.controls[0]
    field.value = "niedokończona myśl"

    # Odświeżenie wywołane zmianą progu nie czyści pola szybkiego dodawania.
    page.width = 400
    page.on_resize(None)
    field_after = _content_col(page).controls[1].content.controls[0]
    assert field_after.value == "niedokończona myśl"


# --- SPRINT 4.2: puste Daily nie pokazuje mylącego 0% ---
def test_daily_progress_empty_shows_no_tasks_text():
    page = DummyPage(width=950)
    main.main(page)
    progress_card = _content_col(page).controls[3]
    assert _control_contains_text(progress_card, "Brak zadań na dziś")


# --- SPRINT 4.4: konflikty synchronizacji bez cichej utraty danych ---
def test_sync_conflict_state_helpers():
    assert main.get_sync_conflict() is None
    main.set_sync_conflict_state("rh", "lh", "gist")
    conflict = main.get_sync_conflict()
    assert conflict is not None
    assert conflict["remote_hash"] == "rh"
    assert conflict["choice_pending"] is True

    ok, _ = main.resolve_sync_conflict("nieznany")
    assert ok is False

    main.clear_sync_conflict()
    assert main.get_sync_conflict() is None


def test_sync_conflict_blocks_push(tmp_path):
    sync_dir = tmp_path / "blockpush"
    sync_dir.mkdir()
    main.set_config("sync_mode", "folder")
    main.set_config("sync_folder", str(sync_dir))
    main.set_sync_conflict_state("r", "l", "test")

    ok, msg = main.push_database()
    assert ok is False
    assert "konflikt" in msg.lower()


def test_sync_conflict_detection_and_resolution(tmp_path):
    sync_dir = tmp_path / "sync_conf"
    sync_dir.mkdir()
    remote = sync_dir / "tasks.db"

    main.set_config("sync_mode", "folder")
    main.set_config("sync_folder", str(sync_dir))

    # 1. Bazowy stan: zadanie + push do folderu (punkt odniesienia synchronizacji).
    main.add_task_db("Bazowe", is_daily=0, priority="B")
    assert remote.exists()

    # 2. Inne urządzenie zmienia zdalną bazę.
    conn = sqlite3.connect(str(remote))
    conn.execute("INSERT INTO tasks (title, is_daily, priority) VALUES ('Zdalne', 0, 'B')")
    conn.commit()
    conn.close()
    future = os.path.getmtime(str(remote)) + 10
    os.utime(str(remote), (future, future))

    # 3. Lokalna zmiana bez auto-pushu (na chwilę tryb lokalny).
    main.set_config("sync_mode", "none")
    main.add_task_db("Lokalne", is_daily=0, priority="B")
    main.set_config("sync_mode", "folder")

    # 4. Pull wykrywa konflikt i NIE nadpisuje lokalnej bazy.
    ok, msg = main.pull_database(force=False)
    assert ok is True
    assert "konflikt" in msg.lower()
    assert main.get_sync_conflict() is not None
    titles = [t[1] for t in main.get_tasks(is_daily_view=False)]
    assert "Lokalne" in titles
    assert "Zdalne" not in titles

    # 5. Push jest zablokowany, dopóki konflikt nie zostanie rozstrzygnięty.
    push_ok, push_msg = main.push_database()
    assert push_ok is False
    assert "konflikt" in push_msg.lower()

    # 6. Rozstrzygnięcie: pobierz wersję zdalną.
    ok, msg = main.resolve_sync_conflict("keep_remote")
    assert ok is True
    assert main.get_sync_conflict() is None
    titles = [t[1] for t in main.get_tasks(is_daily_view=False)]
    assert "Zdalne" in titles
    assert "Lokalne" not in titles

    # 7. Po rozstrzygnięciu zapis znów działa.
    push_ok, _ = main.push_database()
    assert push_ok is True


def test_sync_conflict_keep_local_clears_and_pushes(tmp_path):
    sync_dir = tmp_path / "sync_keep"
    sync_dir.mkdir()
    main.set_config("sync_mode", "folder")
    main.set_config("sync_folder", str(sync_dir))
    main.set_sync_conflict_state("r", "l", "test")

    ok, msg = main.resolve_sync_conflict("keep_local")
    assert ok is True
    assert main.get_sync_conflict() is None
    # Lokalna baza trafiła do folderu.
    assert os.path.exists(str(sync_dir / "tasks.db"))


def test_sync_conflict_banner_visible():
    main.set_sync_conflict_state("remote_hash", "local_hash", "folder")
    page = DummyPage(width=950)
    main.main(page)
    content_col = _content_col(page)
    assert _control_contains_text(content_col, "Wykryto konflikt synchronizacji")


if __name__ == "__main__":
    pytest.main(["-v", __file__])