# AGENTS.md — Master ADHD

## Czym jest projekt

Osobisty menedżer zadań dla osób z ADHD: **Python + Flet 1.0.4 + SQLite**, działa na PC i Androidzie (APK). UI i komentarze po polsku, motyw „Dark Slate”. Widoki: `daily`, `master`, `archive` (Dziennik Sukcesów), `settings`.

## Komendy

```powershell
python -m pytest test_app.py -q                       # całość (25 testów, ~4 s)
python -m pytest test_app.py -q -k test_tasks_crud    # pojedynczy test
.\.venv\Scripts\flet.exe run main.py                  # uruchomienie na PC
```

- Brak `requirements.txt`/`pyproject.toml` — CI instaluje `flet` i `pytest` bez przypięcia wersji. Lokalnie `flet==1.0.4` jest i w globalnym Pythonie, i w `.venv`, ale CLI `flet` nie jest na PATH (tylko `.venv\Scripts\flet.exe`).
- Używaj API Flet 1.x: `ft.Colors.*`, `ft.Icons.*`, `ft.Button`, `ft.Alignment(x, y)`, `ft.Padding.symmetric(...)`. W 1.0.4 nie ma już `ft.colors` ani `ft.ElevatedButton` (a `ft.icons` / `ft.alignment` to tylko aliasy) — mimo że tak wygląda większość przykładów w sieci.
- CI (`.github/workflows/build.yml`), tylko przy pushu na `master`: `pytest test_app.py -v` → `flet build apk --arch arm64-v8a --project MasterADHD --no-default-excludes` → Release `latest`.

## Mapa plików

| Plik | Odpowiedzialność |
|---|---|
| `main.py` | Entrypoint: OTA z GitHuba, splash, import `app_logic` (cache OTA albo wbudowany), rollback, ekran błędu. Na końcu `from app_logic import *` dla testów. |
| `app_logic.py` | ~3400 linii: schemat i operacje SQLite, konfiguracja, snapshoty, backup/import JSON, synchronizacja (Gist/HA/folder), logowanie oraz cały interfejs Flet w `main(page)`. |
| `test_app.py` | 25 testów: CRUD, podkroki, postęp, archiwum, snapshoty, integralność bazy, Gist (mock), UI PC/mobile, rollback OTA. `DummyPage` + tymczasowe bazy. |
| `version.txt` | Aktualna wersja (1.6.18). Czytana przez `app_logic` i OTA. |
| `.github/workflows/build.yml` | CI: pytest → APK → Release `latest`. |
| `assets/icon.png` | Ikona aplikacji. |
| `FIX_SUMMARY.md`, `README_OTA_FIX.md` | Historyczne, opisują stan 1.6.17 — nieaktualne. |
| `.junie/plans/redesign-app-ui-ux.md` | Plan modernizacji UI (kontekst dla zmian w interfejsie). |

Artefakty tylko lokalne (gitignore, nie są źródłem): `app_logic_ota.py`, `ota_version.txt`, `*.db`, `snapshots/`, `test_sync_folder/`. Aplikacja czyta bazy z folderu danych, nie z repo.

## Przepływ

```text
main.py::main → load_and_run()
  ├─ check_and_download_ota()   → GitHub raw: version.txt + app_logic.py
  ├─ app_logic_ota.py / .prev / ota_failed_version.txt (w folderze danych)
  └─ import modułu → app_logic.main(page)
       ├─ init_db() (+ init_config_db())
       ├─ refresh_view() → przebudowa kontrolek
       ├─ akcje → db_connection(write=True) → push_database_async()
       └─ wątek startup_pull → pull_database(force=False)
```

### Dane

- Tabele: `tasks`, `routines`, `completed_archive` w `tasks.db`; `config` w `app_config.db`. Podkroki to JSON w kolumnie `subtasks`.
- Kolejność kolumn `get_tasks()` (używana w testach): `0 id, 1 title, 2 is_daily, 3 is_done, 4 priority, 5 subtasks, 6 category`.
- Folder danych: `%USERPROFILE%\.master_adhd_data\` (Android: `FLET_APP_STORAGE_DATA/.master_adhd_data`). Tam też `master_adhd.log`, `snapshots/`, pliki OTA.
- Zapis wyłącznie przez `db_connection(write=True)` (WAL, `busy_timeout`, `RLock`, zawsze `close()`). `write=True` przy `LOCAL_DB` przesuwa `db_last_change` — bez tego zmiana nie jest widoczna dla rozstrzygania konfliktów synchronizacji.
- Kopie przez `backup_database()` (`sqlite3.Connection.backup()`), nigdy `shutil.copy2`.
- Snapshoty: `snapshots/tasks_<tag>_<czas>.db` obok `LOCAL_DB`, rotacja `SNAPSHOT_LIMIT=10`, sortowanie po mtime.

### Synchronizacja

Trzy tryby w `config["sync_mode"]`: `gist`, `ha_server`, `folder` (domyślnie `none`).

- Każda funkcja zapisująca dane kończy się `push_database_async()` — **nowa funkcja DB bez tego nie będzie się synchronizować**.
- `push_database_async()`: folder zapisuje od razu, Gist/HA przez wątek z debounce 1,5 s; podczas startowego pulla push jest odraczany (`_PUSH_DEFERRED`).
- `pull_database(force=False)` nie nadpisuje nowszych danych lokalnych (`db_last_change`, `exported_at`, `Last-Modified`, mtime); `force=True` pobiera zawsze.
- `ha_server` wysyła/odbiera `tasks.db` pod `ha_url` (POST/GET). W repo nie ma serwera — `server_sync.py` został usunięty.
- Stan zapisu → `SYNC_STATE` + badge w UI (`register_sync_status_callback`).

### OTA

- Źródło: `https://raw.githubusercontent.com/DemonSwans/MasterADHD/master`.
- **Skutek uboczny**: każdy push `app_logic.py` / `version.txt` na `master` trafia do użytkowników przez OTA, niezależnie od wydania APK.
- `get_local_version()` bierze **maksimum** z `version.txt`/`ota_version.txt` w repo i w folderze danych → podnoszenie wersji rób w `version.txt`; `FALLBACK_VERSION` (1.6.9) jest tylko awaryjne.
- Pobrany kod jest sprawdzany przez `compile()`, zapisywany jako `app_logic_ota.py`, poprzedni jako `app_logic_ota.prev.py` + `ota_prev_version.txt`.
- Błąd startu → `rollback_ota()`; wadliwa wersja → `ota_failed_version.txt` (blokada ponownego pobrania).
- Testy ustawiają `DISABLE_OTA=1` — nie usuwaj tego z fixture.

## Zasady dla agentów

- Nowe widżety: używaj stałych `COLOR_BG_MAIN/CARD/SURFACE/BORDER`, `COLOR_PRIO_A/B/C`, `COLOR_SUCCESS`, `COLOR_TEXT_PRIMARY/SECONDARY` z `app_logic.py`, nie literalnych hexów.
- `is_mobile()` to szerokość < 650 px (`page.width` lub `page.window.width`); testy używają 950 (PC) i 400 (mobile). PC: `Row(sidebar, content_area)`, mobile: `Column(content_area, bottom_nav)`.
- `page.overlay` ma 9 dialogów; kolejność jest kontraktem testów (`[3]` edycja zadania, `[4]` usuwanie, `[5]` edycja podkroku) — nowe okna tylko na końcu listy.
- Korzeń to `page.controls[0]` (SafeArea) → `.content` (`root_layout`) → `.content` (`Row`/`Column`). Testy indeksują dalej: sidebar `controls[1]` Daily, `[2]` Master, `[3]` Dziennik; dolna nawigacja `[0]` Daily, `[1]` Master, `[2]` Dziennik (test mylnie nazywa ją `settings_mob_box`), `[3]` Ustawienia; `content_area` to `Row[2]` na PC i `Column[0]` na mobile.
- Nowe testy dopisuj do `test_app.py`; autouse fixture przekierowuje bazy do `tmp_path` przez `app_logic.LOCAL_DB`/`CONFIG_DB` (patchuj `app_logic`, nie tylko `main`), więc nie pisz do folderu użytkownika.
- Nie zapisuj plików OTA do folderu repozytorium — tylko do folderu danych.

## Znane pułapki (stan obecny)

- **`from app_logic import *` na końcu `main.py` nadpisuje `main`.** `ft.run(main)` i `main.main(page)` w testach uruchamiają `app_logic.main`, więc OTA/splash/rollback w `load_and_run()` nie są w ogóle testowane.
- OTA ma dwie implementacje: `main.py` (`check_and_download_ota`) i `trigger_manual_ota` w `app_logic.py`. Ręczna nie zapisuje `ota_prev_version.txt`, więc po rollbacku wersja jest nieznana; dodatkowo pisze `version.txt` do folderu danych.
- `load_and_run()` ładuje **każdy** istniejący `app_logic_ota.py` (priorytet 3) bez porównania wersji z wbudowaną — stary cache przesłania świeży kod.
- Edycja zadania z widoku Daily (`open_edit_task_dialog(tid, tit)` bez `current_category`) ustawia dropdown na „Ogólne” i nadpisuje kategorię przy zapisie.
- `init_db()` nie może ruszać `db_last_change` (jest test) — inaczej pull z chmury nigdy nic nie pobierze.
