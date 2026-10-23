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
def setup_and_teardown(tmp_path, monkeypatch):
    import app_logic
    # Testy nie mogą dotykać sieci ani prawdziwego folderu OTA użytkownika.
    monkeypatch.setenv("DISABLE_OTA", "1")
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

    # 2. Usunięcie zadania przez dialog
    card_after_edit = tasks_col.controls[0]
    del_btn = card_after_edit.content.controls[3]
    del_btn.on_click(None)
    assert delete_task_dialog.open is True
    delete_task_dialog.actions[1].on_click(None)
    assert delete_task_dialog.open is False
    assert len(main.get_tasks(is_daily_view=True)) == 0


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


def test_init_db_does_not_touch_cloud_timestamp():
    # Start aplikacji nie może udawać zmiany danych — inaczej pull z chmury
    # zawsze uzna lokalną bazę za nowszą i nigdy nic nie pobierze.
    main.set_config("db_last_change", "2000-01-01T00:00:00")
    main.init_db()
    assert main.get_config("db_last_change") == "2000-01-01T00:00:00"


if __name__ == "__main__":
    pytest.main(["-v", __file__])