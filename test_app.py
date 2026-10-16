import os
import shutil
import pytest
import flet as ft
import main

TEST_DB = "test_tasks.db"
TEST_CONFIG = "test_config.db"
TEST_SYNC_DIR = "test_sync_folder"


class DummyWindow:
    width = 0
    height = 0


class DummyPage:
    def __init__(self):
        self.title = ""
        self.window = DummyWindow()
        self.padding = 0
        self.theme_mode = None
        self.overlay = []
        self.controls = []

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self):
        pass

    def launch_url(self, url):
        pass


@pytest.fixture(autouse=True)
def setup_and_teardown(monkeypatch):
    main.LOCAL_DB = TEST_DB
    main.CONFIG_DB = TEST_CONFIG
    monkeypatch.setattr(main, "check_for_updates", lambda page: None)

    if os.path.exists(TEST_DB): os.remove(TEST_DB)
    if os.path.exists(TEST_CONFIG): os.remove(TEST_CONFIG)
    if os.path.exists(TEST_SYNC_DIR): shutil.rmtree(TEST_SYNC_DIR)

    main.init_db()
    yield

    if os.path.exists(TEST_DB): os.remove(TEST_DB)
    if os.path.exists(TEST_CONFIG): os.remove(TEST_CONFIG)
    if os.path.exists(TEST_SYNC_DIR): shutil.rmtree(TEST_SYNC_DIR)


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


def test_subtasks():
    main.add_task_db("Projekt", is_daily=1, priority="A")
    task_id = main.get_tasks(is_daily_view=True)[0][0]

    steps = [{"title": "Krok 1", "is_done": 0}, {"title": "Krok 2", "is_done": 1}]
    main.update_subtasks_db(task_id, steps)

    saved_steps = main.get_tasks(is_daily_view=True)[0][5]
    assert len(saved_steps) == 2
    assert saved_steps[1]["title"] == "Krok 2"
    assert saved_steps[1]["is_done"] == 1


def test_progress_calculation():
    assert main.calculate_progress_logic([], []) == 0.0

    fake_tasks = [(1, "Projekt", 1, 0, "A", [{"title": "K1", "is_done": 0}, {"title": "K2", "is_done": 0}])]
    fake_routines = [(1, "Leki", 1)]
    assert main.calculate_progress_logic(fake_tasks, fake_routines, {}) == 0.5

    fake_tasks_done = [(1, "Projekt", 1, 0, "A", [{"title": "K1", "is_done": 1}, {"title": "K2", "is_done": 0}])]
    assert main.calculate_progress_logic(fake_tasks_done, fake_routines, {1: 0}) == 1.0
    assert main.calculate_progress_logic(fake_tasks_done, fake_routines, {1: 1}) == 0.5


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


def test_sync_security():
    main.set_config("sync_mode", "folder")
    main.set_config("sync_folder", "")
    ok, msg = main.pull_database()
    assert not ok
    assert "Nie podano ścieżki" in msg

    main.set_config("sync_folder", "/nieistniejaca/sciezka/12345")
    ok, msg = main.pull_database()
    assert not ok

    os.makedirs(TEST_SYNC_DIR, exist_ok=True)
    corrupted_file = os.path.join(TEST_SYNC_DIR, "tasks.db")
    with open(corrupted_file, "wb") as f:
        f.write(b"To nie jest baza SQLite, tylko smieci")

    main.set_config("sync_folder", TEST_SYNC_DIR)
    ok, msg = main.pull_database()
    assert not ok
    assert "uszkodzony" in msg.lower()


def test_folder_sync_and_archive():
    os.makedirs(TEST_SYNC_DIR, exist_ok=True)
    main.set_config("sync_mode", "folder")
    main.set_config("sync_folder", TEST_SYNC_DIR)

    main.add_task_db("Zadanie do backupu", is_daily=1, priority="A")
    remote_db = os.path.join(TEST_SYNC_DIR, "tasks.db")
    assert os.path.exists(remote_db)

    main.add_task_db("Drugie zadanie", is_daily=0, priority="B")
    archive_dir = os.path.join(TEST_SYNC_DIR, "archive")
    assert os.path.exists(archive_dir)
    assert len(os.listdir(archive_dir)) >= 1


def test_ui_rendering_and_right_drag_handle():
    main.add_task_db("Zadanie Zwykle", is_daily=1, priority="A")
    main.add_task_db("Projekt z Krokami", is_daily=1, priority="B")

    tasks = main.get_tasks(is_daily_view=True)
    for t in tasks:
        if t[1] == "Projekt z Krokami":
            main.update_subtasks_db(t[0], [{"title": "Krok 1", "is_done": 1}, {"title": "Krok 2", "is_done": 0}])

    main.add_routine_db("Wypić wodę")
    r_id = main.get_routines()[0][0]
    main.toggle_routine_db(r_id, 1)

    page = DummyPage()
    main.main(page)
    assert len(page.controls) > 0

    main_row = page.controls[0]
    content_col = main_row.controls[2].content

    # Sprawdzenie, czy karta zadania to zwykły Container, a na prawym końcu wiersza jest Draggable (uchwyt)
    tasks_col = next(c for c in content_col.controls if isinstance(c, ft.Column) and c.expand)
    first_task_card = tasks_col.controls[0]
    assert isinstance(first_task_card, ft.Container)
    assert isinstance(first_task_card.content.controls[-1], ft.Draggable)

    sidebar_col = main_row.controls[0].content
    master_nav_box = sidebar_col.controls[2].content
    settings_nav_box = sidebar_col.controls[6]

    master_nav_box.on_click(None)
    settings_nav_box.on_click(None)