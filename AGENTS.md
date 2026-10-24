# AGENTS.md — Master ADHD

## Czym jest projekt

Osobisty menedżer zadań dla osób z ADHD: **Python + Flet 1.0.4 + SQLite**, działa na PC i Androidzie (APK). UI i komentarze po polsku, motyw „Dark Slate”. Widoki: `daily`, `master`, `archive` (Dziennik Sukcesów), `settings`.

## Komendy

```powershell
python -m pytest test_app.py -q                       # całość (33 testy, ~4 s)
python -m pytest test_app.py -q -k test_tasks_crud    # pojedynczy test
.\.venv\Scripts\flet.exe run main.py                  # uruchomienie na PC
```

- Brak `requirements.txt`/`pyproject.toml` — CI instaluje `flet==1.0.4` (przypięte, bo składnia UI zależy od wersji) i `pytest`. Lokalnie `flet==1.0.4` jest i w globalnym Pythonie, i w `.venv`, ale CLI `flet` nie jest na PATH (tylko `.venv\Scripts\flet.exe`).
- Używaj API Flet 1.x: `ft.Colors.*`, `ft.Icons.*`, `ft.Button`, `ft.Alignment(x, y)`, `ft.Padding.symmetric(...)`. W 1.0.4 nie ma już `ft.colors` ani `ft.ElevatedButton` (a `ft.icons` / `ft.alignment` to tylko aliasy) — mimo że tak wygląda większość przykładów w sieci.
- CI (`.github/workflows/build.yml`), tylko przy pushu na `master`: `pytest test_app.py -v` → `flet build apk --arch arm64-v8a --project MasterADHD --no-default-excludes --build-version <version.txt> --build-number 1<wersja bez kropek>` → Release `latest`. Bez rosnącego `versionCode` Android odmawia instalacji APK na urządzeniu z poprzednią wersją.

## Mapa plików

| Plik | Odpowiedzialność |
|---|---|
| `main.py` | Entrypoint: OTA z GitHuba, splash, wybór modułu (`app_logic_ota.py` albo wbudowany `app_logic`), rollback, ekran błędu. Na końcu re-eksport symboli `app_logic` **bez** `main` (dawniej `from app_logic import *` nadpisywał entrypoint). |
| `app_logic.py` | ~3600 linii: schemat i operacje SQLite, konfiguracja, snapshoty, backup/import JSON, synchronizacja (Gist/HA/folder), logowanie, instalacja/aktywacja OTA oraz cały interfejs Flet w `main(page)`. |
| `test_app.py` | 33 testy: CRUD, podkroki, postęp, archiwum, snapshoty, integralność bazy, Gist (mock), UI PC/mobile, OTA (wybór wersji, instalacja, aktywacja, rollback). `DummyPage` + tymczasowe bazy. |
| `version.txt` | Aktualna wersja (1.6.19). Musi być równa `FALLBACK_VERSION` w `app_logic.py` (pilnuje tego test). |
| `.github/workflows/build.yml` | CI: pytest → APK → Release `latest`. |
| `assets/icon.png` | Ikona aplikacji. |
| `FIX_SUMMARY.md`, `README_OTA_FIX.md` | Historyczne, opisują stan 1.6.17 — nieaktualne. |
| `.junie/plans/redesign-app-ui-ux.md` | Plan modernizacji UI (kontekst dla zmian w interfejsie). |

Artefakty tylko lokalne (gitignore, nie są źródłem): `app_logic_ota.py`, `ota_version.txt`, `*.db`, `snapshots/`, `test_sync_folder/`. Aplikacja czyta bazy z folderu danych, nie z repo.

## Przepływ

```text
main.py::main → load_and_run()          (jedyny loader; nie ma drugiej ścieżki)
  ├─ splash + check_and_download_ota()  → GitHub raw: version.txt + app_logic.py
  ├─ get_prepared_ota()                 → app_logic_ota.py, tylko gdy nowszy od wbudowanego
  ├─ _run_app_logic()                   → import modułu (OTA albo wbudowany)
  └─ app_logic.main(page)
       ├─ init_db() (+ init_config_db())
       ├─ refresh_view() → przebudowa kontrolek
       ├─ akcje → db_connection(write=True) → push_database_async()
       ├─ Ustawienia → trigger_manual_ota() → install_ota_code() + activate_downloaded_ota()
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

- Źródło: `GITHUB_RAW_BASE` — ta sama wartość w `main.py` i `app_logic.py` (`https://raw.githubusercontent.com/DemonSwans/MasterADHD/master`).
- **Skutek uboczny**: każdy push `app_logic.py` / `version.txt` na `master` trafia do użytkowników przez OTA, niezależnie od wydania APK.
- Dwie ścieżki, jedna implementacja:
  - start: `main.load_and_run()` → `check_and_download_ota()` → `get_prepared_ota()` → `_run_app_logic()`;
  - Ustawienia: `trigger_manual_ota()` → `install_ota_code()` → `activate_downloaded_ota()` (podmiana UI w tym samym procesie, bez restartu).
- **Kod OTA uruchamiany jest tylko wtedy, gdy jest nowszy od wbudowanego** (`is_newer_version(ota_ver, get_bundled_version())`). Stary plik z folderu danych nie przesłania świeżego kodu z APK. Dodatkowo musi się kompilować i nie być wpisany w `ota_failed_version.txt`.
- `get_local_version()` czyta pliki wersji **tylko obok działającego modułu** (`__file__`), a `get_installed_ota_version()` w `main.py` zwraca wersję kodu, który faktycznie się uruchomi. Numer w UI nie może pochodzić z pliku, którego nie wykonano.
- `main.py` przekazuje wersję wbudowaną przez `MASTER_ADHD_BUNDLED_VERSION`, żeby podpis w Ustawieniach był prawdziwy także pod kodem OTA.
- Pobieranie jest atomowe (`.tmp` + `os.replace` + `fsync`), poprzednia wersja → `app_logic_ota.prev.py` + `ota_prev_version.txt`. Log OTA: `OTA_DIR/ota.log` (na Androidzie `print()` ginie).
- Błąd startu → `rollback_ota()`; wadliwa wersja → `ota_failed_version.txt` (blokada ponownego pobrania).
- Podnosząc wersję: zmień `version.txt` **oraz** `FALLBACK_VERSION` w `app_logic.py` (test `test_version_files_are_in_sync` tego pilnuje).
- `DISABLE_OTA=1` wyłącza OTA całkowicie (sieć **i** cache) — testy tego wymagają, nie usuwaj z fixture.

## Zasady dla agentów

- Nowe widżety: używaj stałych `COLOR_BG_MAIN/CARD/SURFACE/BORDER`, `COLOR_PRIO_A/B/C`, `COLOR_SUCCESS`, `COLOR_TEXT_PRIMARY/SECONDARY` z `app_logic.py`, nie literalnych hexów.
- `is_mobile()` to szerokość < 650 px (`page.width` lub `page.window.width`); testy używają 950 (PC) i 400 (mobile). PC: `Row(sidebar, content_area)`, mobile: `Column(content_area, bottom_nav)`.
- `page.overlay` ma 9 dialogów; kolejność jest kontraktem testów (`[3]` edycja zadania, `[4]` usuwanie, `[5]` edycja podkroku) — nowe okna tylko na końcu listy.
- Korzeń to `page.controls[0]` (SafeArea) → `.content` (`root_layout`) → `.content` (`Row`/`Column`). Testy indeksują dalej: sidebar `controls[1]` Daily, `[2]` Master, `[3]` Dziennik; dolna nawigacja `[0]` Daily, `[1]` Master, `[2]` Dziennik (test mylnie nazywa ją `settings_mob_box`), `[3]` Ustawienia; `content_area` to `Row[2]` na PC i `Column[0]` na mobile.
- Nowe testy dopisuj do `test_app.py`; autouse fixture przekierowuje bazy do `tmp_path` przez `app_logic.LOCAL_DB`/`CONFIG_DB` (patchuj `app_logic`, nie tylko `main`) **oraz wszystkie ścieżki OTA z `main`** (`OTA_DIR`, `OTA_*_FILE`, reset `_PREPARED_CACHE`), więc nie pisz do folderu użytkownika.
- Nie zapisuj plików OTA do folderu repozytorium — tylko do folderu danych.
- `main.py` jest zamrożony w APK: zmiana w nim działa dopiero po zainstalowaniu nowego APK. Zmiany w `app_logic.py` docierają przez OTA.

## Znane pułapki (stan obecny)

- **Nie wracaj do `from app_logic import *` w `main.py`.** Nadpisywało `main` i `ft.run(main)` uruchamiał `app_logic.main`, więc OTA nie działała wcale (numer wersji się zmieniał, kod nie). Teraz jest re-eksport pomijający `main`; pilnuje tego `test_entrypoint_is_not_shadowed`.
- `activate_downloaded_ota()` woła `module.main(page)` w tym samym procesie. Stare wątki (timer, sync) mogą jeszcze chwilę żyć — dlatego przed podmianą UI wołane jest `flush_pending_sync(timeout=2.0)`, a `page.overlay`/`page.controls` są czyszczone.
- Edycja zadania z widoku Daily (`open_edit_task_dialog(tid, tit)` bez `current_category`) ustawia dropdown na „Ogólne” i nadpisuje kategorię przy zapisie.
- `init_db()` nie może ruszać `db_last_change` (jest test) — inaczej pull z chmury nigdy nic nie pobierze.
- W APK, które użytkownicy mają **przed** wersją 1.6.19, działa stary, zepsuty loader. Pierwsza naprawa wymaga jednorazowej instalacji nowego APK; dopiero potem OTA działa automatycznie.
