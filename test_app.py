import base64
import gc
import json
import os
import urllib.request
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

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self):
        pass

    def get_control(self, src_id):
        return None


# --- PRZYGOTOWANIE I SPRZĄTANIE ŚRODOWISKA ---
@pytest.fixture(autouse=True)
def setup_and_teardown(tmp_path):
    import app_logic
    test_db = str(tmp_path / "test_tasks.db")
    test_config = str(tmp_path / "test_config.db")

    app_logic.LOCAL_DB = main.LOCAL_DB = test_db
    app_logic.CONFIG_DB = main.CONFIG_DB = test_config

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


# --- 4. TEST MATEMATYKI PASKA POSTĘPU ---
def test_progress_calculation():
    assert main.calculate_progress_logic([], []) == 0.0

    fake_tasks = [(1, "Projekt", 1, 0, "A", [{"title": "K1", "is_done": 0}, {"title": "K2", "is_done": 0}])]
    fake_routines = [(1, "Leki", 1)]
    assert main.calculate_progress_logic(fake_tasks, fake_routines, {}) == 0.5

    fake_tasks_done = [(1, "Projekt", 1, 0, "A", [{"title": "K1", "is_done": 1}, {"title": "K2", "is_done": 0}])]
    assert main.calculate_progress_logic(fake_tasks_done, fake_routines, {1: 0}) == 1.0
    assert main.calculate_progress_logic(fake_tasks_done, fake_routines, {1: 1}) == 0.5


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


# --- 10. TEST CHMURY GITHUB GIST (Automatyczne tworzenie i pobieranie) ---
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


if __name__ == "__main__":
    pytest.main(["-v", __file__])