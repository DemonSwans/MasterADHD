# AGENTS.md — Master ADHD

## Czym jest projekt

Osobisty menedżer zadań dla osób z ADHD: **Python + Flet 1.0.4 + SQLite**, działa na PC i Androidzie (APK). UI i komentarze po polsku, motyw „Dark Slate”. Widoki: `daily`, `master`, `archive` (Dziennik Sukcesów), `settings`.

## Komendy

```powershell
python -m pytest test_app.py -q                       # całość (109 testów, ~26 s)
python -m pytest test_app.py -q -k test_tasks_crud    # pojedynczy test
.\.venv\Scripts\flet.exe run main.py                  # uruchomienie na PC
```

- Brak `requirements.txt`/`pyproject.toml` — CI instaluje `flet==1.0.4` (przypięte, bo składnia UI zależy od wersji) i `pytest`. Lokalnie pakiety żyją w `.venv` (jest tam `flet` — obecnie 1.0.5, nie 1.0.4 jak w CI — oraz doinstalowany ręcznie `pytest`; globalny Python nie ma ani jednego). Komendy z bloku wyżej działają w aktywowanym `.venv` albo z jawnym `.venv\Scripts\python.exe -m pytest ...`. CLI `flet` istnieje tylko jako `.venv\Scripts\flet.exe`.
- Używaj API Flet 1.x: `ft.Colors.*`, `ft.Icons.*`, `ft.Button`, `ft.Alignment(x, y)`, `ft.Padding.symmetric(...)`. W 1.0.4 nie ma już `ft.colors` ani `ft.ElevatedButton` (a `ft.icons` / `ft.alignment` to tylko aliasy) — mimo że tak wygląda większość przykładów w sieci.
- CI (`.github/workflows/build.yml`), tylko przy pushu na `master`: `pytest test_app.py -v` → `flet build apk --arch arm64-v8a --project MasterADHD --no-default-excludes --build-version <version.txt> --build-number 1<wersja bez kropek>` → Release `latest`. Bez rosnącego `versionCode` Android odmawia instalacji APK na urządzeniu z poprzednią wersją.

## Mapa plików

| Plik | Odpowiedzialność |
|---|---|
| `main.py` | Entrypoint: OTA z GitHuba, splash, wybór modułu (`app_logic_ota.py` albo wbudowany `app_logic`), rollback, ekran błędu. Na końcu re-eksport symboli `app_logic` **bez** `main` (dawniej `from app_logic import *` nadpisywał entrypoint). |
| `app_logic.py` | ~5300 linii: schemat i operacje SQLite, konfiguracja, snapshoty, backup/import JSON, synchronizacja (Gist/HA/folder), logowanie, instalacja/aktywacja OTA oraz cały interfejs Flet w `main(page)`. |
| `test_app.py` | 109 testów: CRUD, rutyny (w tym dni tygodnia i historia rutyn), migracje schematu starych baz, podkroki, postęp hybrydowy, archiwum, snapshoty, integralność bazy, Gist (mock), UI PC/mobile (w tym edycja z Daily z zachowaniem kategorii, usuwanie z „Cofnij”, obszary dotykowe 44 px na 400 px), wibracja z mockiem `HapticFeedback`, mini-minutnik, OTA (wybór wersji, instalacja, aktywacja, rollback), przełom dnia (`get_pending_day_rollover`/`apply_day_rollover`, UI `overlay[10]`), Inbox z triagem, znaczniki czasu i filtr „dawno nietknięte”, szacowany czas z podsumowaniem dnia, kolejka aktualizacji UI i próg `resize`, puste Daily bez „0%”, konflikty synchronizacji z panelem decyzji oraz higiena dokumentacji (docstring każdej funkcji modułowej). `DummyPage` + tymczasowe bazy. |
| `version.txt` | Aktualna wersja (1.7.0). Musi być równa `FALLBACK_VERSION` w `app_logic.py` (pilnuje tego test). |
| `.github/workflows/build.yml` | CI: pytest → APK → Release `latest`. |
| `assets/icon.png` | Ikona aplikacji. |
| `README.md`, `README.en.md` | Dokumentacja dla użytkownika i dewelopera (polska + angielska). |
| `docs/` | Dokumentacja szczegółowa: `funkcje.md`, `instalacja.md`, `synchronizacja.md`, `ota.md`, `dane.md`, `architektura.md`, `testy.md`, `rozwoj.md`, `historia-zmian.md` (+ `docs/README.md` jako indeks). |
| `FIX_SUMMARY.md`, `README_OTA_FIX.md` | Historyczne, opisują stan 1.6.17 — nieaktualne. |
| `.junie/plans/` | Plany sprintów (historyczne), m.in. `sprint-1-dokonczenie.md`. |

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
       ├─ _maybe_start_day_rollover() → dialog przełomu dnia albo auto-tryb
       ├─ akcje → db_connection(write=True) → push_database_async()
       ├─ Ustawienia → trigger_manual_ota() → install_ota_code() + activate_downloaded_ota()
       └─ wątek startup_pull → pull_database(force=False)
```

### Dane

- Tabele: `tasks`, `routines`, `routine_log`, `completed_archive` w `tasks.db`; `config` w `app_config.db`. Podkroki to JSON w kolumnie `subtasks`.
- Kolejność kolumn `get_tasks()` (używana w testach): `0 id, 1 title, 2 is_daily, 3 is_done, 4 priority, 5 subtasks, 6 category, 7 deadline`. `deadline` to opcjonalna data ISO `YYYY-MM-DD` (bez godziny); `None` = brak terminu. Nowe pola (`created_at`/`updated_at`, `estimate_min`) mają **osobne gettery**, żeby nie ruszać tej kolejności.
- Terminy: `add_task_db(..., deadline=...)`, `set_task_deadline_db()`, `get_task_deadline()`, `update_task_details_db(..., deadline=DEADLINE_UNSET)` (sentinel = nie zmieniaj, `None` = wyczyść). Sortowanie/filtr w Master: `master_sort_mode` (`DEFAULT`/`DEADLINE`) i `master_deadline_filter` (`ALL`/`WEEK`/`OVERDUE`) w config. Przypomnienia: `deadline_reminders_enabled`, `deadline_reminder_days` (14/7/3), `deadline_reminder_repeat_day_before`, `deadline_card_hidden_date`.
- Znaczniki czasu: kolumny `tasks.created_at` / `tasks.updated_at` (ISO z sekundami). `add_task_db()` ustawia oba; każda funkcja modyfikująca zadanie (`toggle_done_db`, `update_priority_db`, `update_task_details_db`, `edit_task_title_db`, `update_subtasks_db`, `set_daily_db`, `set_task_deadline_db`, `update_task_category_db`, triage, operacje na podkrokach) podnosi `updated_at` przez `now_iso()`. Odczyt: `get_task_timestamps(task_id)` — `get_tasks()` zachowuje kolejność 0–7. Stare wiersze dostają datę migracji w `_backfill_task_timestamps()`. Filtr Master „🕰 Dawno nietknięte”: `is_stale_task(...)` / `get_stale_task_ids(days=STALE_TASK_DAYS)` (14 dni) — tylko nieukończone, bez zmian od 14+ dni i bez przyszłego terminu; stan przełącznika w config `master_stale_only` (`0`/`1`). `updated_at` nie jest używany do wykrywania konfliktów synchronizacji — rozstrzyga je skrót treści bazy (patrz „Synchronizacja”).
- Przełom dnia: `get_pending_day_rollover()` (tylko czyta — `None`, gdy `last_reset_date` == dziś albo brak znacznika) i `apply_day_rollover(decisions, mode)` (snapshot `pre_day_rollover`, archiwizuje zrobione dokładnie raz, usuwa je z `tasks`, ustawia `last_reset_date` = dziś, `push_database_async()`). Decyzje to mapa `{task_id: "keep"|"master"|"remove"}`; zadanie bez decyzji = „keep”. Tryb w config `day_rollover_mode` = `ask` (domyślnie) / `auto_keep` / `auto_master` (`get_day_rollover_mode()` normalizuje śmieci do `ask`). `end_of_day_db()` zostaje bez zmian (zgodność i starsze testy).
- Inbox: stała `INBOX_CATEGORY = "📥 Inbox"` (bez zmian schematu). Szybki zrzut (`save_quick_dump`, Daily `save_inline_input`) trafia do Inbox z `priority="C"`, `is_daily=False`. `get_inbox_tasks()` filtruje po kategorii, a `triage_inbox_task_db(task_id, category, priority, is_daily, deadline)` przenosi zadanie do wybranej kategorii (`Inbox` spada do `Ogólne`) i kończy `push_database_async()`. Zadania z Inbox są wykluczone z listy Master i pokazywane w sekcji triage'u (ukrytej, gdy pusta).
- Rutyny: `routines.routine_days` to JSON z intami `0..6` (0 = poniedziałek … 6 = niedziela, konwencja `datetime.weekday()`); `[]` = codziennie, a rutyny zapisane przed tą kolumną są traktowane jak codzienne. `get_routines()` zwraca krotki `(id, title, is_done, days)`, zapis przez `add_routine_db(title, days=None)` / `update_routine_db(id, title, days=None)` (ta druga obsługuje edycję z Ustawień), a widok Daily filtruje rutyny przez `get_daily_routines()`. JSON export/import przenosi `days`.
- Historia rutyn: tabela `routine_log(id, routine_id, date, done, UNIQUE(routine_id, date))`, data ISO `YYYY-MM-DD`. `toggle_routine_db(routine_id, is_done, day=None)` robi UPSERT wpisu na dziś (`ON CONFLICT ... DO UPDATE`) **oraz** ustawia `routines.is_done` (szybki stan bieżący). `is_done` resetują przełom dnia i `end_of_day_db()`, ale historia zostaje. `delete_routine_db()` kasuje też wpisy historii usuwanej rutyny. Odczyt: `get_routine_log_map(routine_id=None, since=None)` → `{routine_id: {date_iso: done}}` (zawężenie `routine_id` nadal zwraca zewnętrzną mapę po id). Czyste helpery: `routine_week_view(days, log_map, today)` → `(7 wpisów, zrobione, zaplanowane)`, `routine_current_streak(days, log_map, today)` (miękka seria — dzisiejszy niepełny dzień i dni niezaplanowane nie przerywają) i `build_routine_week_stripe(entries)`. Ustawienia pokazują pasek 7 dni + „w tym tygodniu: X/Y” + „seria: N dni z rzędu” (od 2). **Nigdy nie komunikujemy „straciłeś serię”.** JSON export/import przenosi `routine_log` (klucz opcjonalny; import mapuje stare `routine_id` na nowe `lastrowid`).
- Szacowany czas: kolumna `tasks.estimate_min` (INTEGER, nullable). `add_task_db(..., estimate_min=None)` ustawia ją przy tworzeniu, `update_task_details_db(..., estimate=ESTIMATE_UNSET)` zmienia/czyści (sentinel = nie ruszaj, `None` = usuń), `set_task_estimate_db(task_id, min)` to samo w jednej akcji. Helpery: `normalize_estimate_min` (śmieci/<=0/`True` → `None`, nigdy nie rzuca), `format_duration_pl` („1 h 40 min”), `sum_estimates(map, tasks)` → `(suma_minut, liczba_bez_szacunku)`, `get_task_estimate(task_id)`, `get_estimates_map()`. `TASK_ROW_COLUMNS` zawiera `estimate_min`, więc „Cofnij” po usunięciu zachowuje szacunek. Dialog dodawania (Master) i edycji mają sekcję 5/15/30/60/własne/„Bez szacunku”; karta postępu na Daily pokazuje „Zaplanowane: ~X h Y min · bez szacunku: n” (tylko gdy suma > 0; jedna liczba, bez wykresów).
- Folder danych: `%USERPROFILE%\.master_adhd_data\` (Android: `FLET_APP_STORAGE_DATA/.master_adhd_data`). Tam też `master_adhd.log`, `snapshots/`, pliki OTA.
- Zapis wyłącznie przez `db_connection(write=True)` (WAL, `busy_timeout`, `RLock`, zawsze `close()`). `write=True` przy `LOCAL_DB` przesuwa `db_last_change` — bez tego zmiana nie jest widoczna dla rozstrzygania konfliktów synchronizacji.
- Migracje schematu: `ensure_schema(conn)` dodaje brakujące kolumny (`tasks.subtasks`, `tasks.category`, `tasks.deadline`, `tasks.created_at`, `tasks.updated_at`, `tasks.estimate_min`, `routines.routine_days`) przez `PRAGMA table_info` + `ALTER TABLE`, tworzy tabelę `routine_log` (`CREATE TABLE IF NOT EXISTS`) i uzupełnia znaczniki czasu starych wierszy (`_backfill_task_timestamps`). Wywołują ją `init_db()`, `export_database_to_json_dict()`, `import_database_from_json_dict()`, `add_routine_db()`, `update_routine_db()` i `toggle_routine_db()`. **Każde miejsce, które podmienia plik `LOCAL_DB` bajtami innej bazy (pull Gist/HA/folder, `_restore_db_from_bytes`, `restore_pre_sync_backup`), musi po tym wywołać `ensure_schema_upgrade()`** — inaczej stara kopia bez nowej kolumny/tabeli wywala `SELECT` w widoku (`no such column` / `no such table: routine_log`).
- Kopie przez `backup_database()` (`sqlite3.Connection.backup()`), nigdy `shutil.copy2`.
- Snapshoty: `snapshots/tasks_<tag>_<czas>.db` obok `LOCAL_DB`, rotacja `SNAPSHOT_LIMIT=10`, sortowanie po mtime.

### Synchronizacja

Trzy tryby w `config["sync_mode"]`: `gist`, `ha_server`, `folder` (domyślnie `none`).

- Każda funkcja zapisująca dane kończy się `push_database_async()` — **nowa funkcja DB bez tego nie będzie się synchronizować**.
- `push_database_async()`: folder zapisuje od razu, Gist/HA przez wątek z debounce 1,5 s; podczas startowego pulla push jest odraczany (`_PUSH_DEFERRED`).
- `pull_database(force=False, resolve_conflict=False)` nie nadpisuje nowszych danych lokalnych (`db_last_change`, `exported_at`, `Last-Modified`, mtime); `force=True` pobiera zawsze.
- `ha_server` wysyła/odbiera `tasks.db` pod `ha_url` (POST/GET). W repo nie ma serwera — `server_sync.py` został usunięty.
- Stan zapisu → `SYNC_STATE` + badge w UI (`register_sync_status_callback`); `set_sync_status("conflict", ...)` daje gałąź `"conflict"` w `on_sync_state`.
- Konflikty synchronizacji (wariant minimalny): `last_synced_hash` + `last_synced_at` (config) to punkt odniesienia ostatniej udanej synchronizacji, zapisywany przez `_mark_synced()` (wywoływane przed każdym sukcesem w `push_database`/`pull_database`), a czyszczony przez `_reset_sync_markers()` przy zmianie trybu. `_detect_conflict(remote_hash, label)` porównuje **skrót treści** bazy (`_sqlite_content_hash`/`_db_content_hash`/`_content_hash_of_bytes` liczą wiersze, nie surowe bajty — brak fałszywych trafień od układu stron SQLite) i zgłasza konflikt tylko gdy jednocześnie: istnieje punkt odniesienia, zmieniła się i lokalna, i zdalna, a skróty są różne. Brak punktu odniesienia / identyczne treści / zmiana tylko jednej strony → brak konfliktu. Wykryty konflikt zapisuje `set_sync_conflict_state()` do configu `sync_conflict` (JSON: `detected_at`, `label`, `local_hash`, `remote_hash`, `choice_pending`) i **nie nadpisuje** danych; `pull_database` zwraca wtedy `(True, "Wykryto konflikt synchronizacji — wybierz wersję w panelu")`, a `push_database` odmawia (`(False, "Konflikt synchronizacji — najpierw wybierz wersję")`). `get_sync_conflict()` czyta oczekujący konflikt, `clear_sync_conflict()` go kasuje. `resolve_sync_conflict(choice)` przyjmuje jedną z `SYNC_CONFLICT_CHOICES = ("keep_local", "keep_remote", "restore_snapshot")`: `keep_local` wysyła lokalną wersję, `keep_remote` robi snapshot `pre_conflict_remote` i pobiera zdalną z `force=True`, `restore_snapshot` wraca do ostatniej kopii bezpieczeństwa. UI: banner w Daily/Master (`_resolve_conflict_click`) z trzema przyciskami i „Cofnij”. Kodowanie rekordów/tombstones i synchronizacja `app_config.db` to nadal przyszłość.

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
- `page.overlay` ma 11 dialogów; kolejność jest kontraktem testów (`[3]` edycja zadania, `[4]` usuwanie, `[5]` edycja podkroku, `[9]` edycja rutyny, `[10]` przełom dnia) — nowe okna tylko na końcu listy. Dialog usuwania (`overlay[4]`) **istnieje nadal** (kontrakt testów i operacje zbiorcze), ale karty zadań go nie otwierają. Dialog „Koniec dnia” (`overlay[7]`) też został jako kontrakt — wyparty przez `day_rollover_dialog`, `open_end_of_day_dialog` nie ma już wołających.
- Usuwanie zadania/podkroku z karty jest **natychmiastowe** (`delete_task_now` / `delete_subtask_now`) i pokazuje `ft.SnackBar` „Usunięto «…». Cofnij”. Cofnięcie przywraca pełny wiersz zadania (`restore_task_row_db`, `INSERT OR IGNORE` — to samo `id`, kategoria, priorytet, termin, podkroki) albo podkrok na tej samej pozycji (`restore_subtask_db`); drugie kliknięcie „Cofnij” nie duplikuje rekordu. Dialog zostaje tylko dla akcji nieodwracalnych zbiorczo (np. czyszczenie archiwum, import zastępujący dane). `open_delete_task_dialog` nie ma już wołających — trzyma go tylko kontrakt `overlay[4]`.
- Wibracja: `init_haptic_service(page)` dodaje `ft.HapticFeedback` do `page.services` **tylko na platformach mobilnych** (`page.platform`); `trigger_haptic_feedback(page, service, kind)` wywołuje metodę przez `page.run_task` i respektuje `sound_enabled`. Brak klasy/usługi/uprawnień = ciche pominięcie (log `debug`), nigdy crash. Na Windows `None`. Sygnały: `timer_done`→`vibrate`, `task_done`→`light_impact`, `undo`→`selection_click`. Używać razem z `play_sound_feedback` (dźwięk na PC).
- Dotyk (mobile): stała `TOUCH_TARGET_MIN = 44`. W `refresh_view` jest lokalny helper `_touch(default)`, który na telefonie zwraca 44, a na PC `default`. Wszystkie akcje-ikony w kartach (edytuj/usuń/dodaj krok, strzałki kroków) oraz nagłówek, szybkie dodawanie i przyciski minutnika są nim objęte — test `test_mobile_touch_targets_at_400px`.
- Kontrast: `contrast_ratio(fg, bg)` (WCAG 2.1) i `readable_text_color(bg, preferred, fallback, minimum=4.5)` — metadane kart używają tego drugiego, więc na ciemnym tle zostaje `COLOR_TEXT_SECONDARY`, a przy zmianie tła degraduje się do `COLOR_TEXT_PRIMARY`.
- Mini-minutnik (`_build_mini_timer`, `update_mini_timer`): jedno źródło stanu timera, dwa wystąpienia (PC w sidebarze, mobile nad dolną nawigacją w `mini_timer_mob_wrapper`). Chip zawiera pauzę/wznowienie i reset, więc działają z **każdego widoku**; klik w tło chipa wraca do Daily. `mini_timer_mob_wrapper.visible` chowa też `Divider`. Na mobile chip ma 44 px wysokości.
- Korzeń to `page.controls[0]` (SafeArea) → `.content` (`root_layout`) → `.content` (`Row`/`Column`). Testy indeksują dalej: sidebar `controls[1]` Daily, `[2]` Master, `[3]` Dziennik; dolna nawigacja `[0]` Daily, `[1]` Master, `[2]` Dziennik (test mylnie nazywa ją `settings_mob_box`), `[3]` Ustawienia; `content_area` to `Row[2]` na PC i `Column[0]` na mobile.
- **Wszystkie testy trzymaj w jednym pliku `test_app.py`** — także nowe; na razie nie dzielimy ich na osobne pliki (`test2_app.py` nie istnieje i nie jest uruchamiany przez CI). Autouse fixture przekierowuje bazy do `tmp_path` przez `app_logic.LOCAL_DB`/`CONFIG_DB` (patchuj `app_logic`, nie tylko `main`) **oraz wszystkie ścieżki OTA z `main`** (`OTA_DIR`, `OTA_*_FILE`, reset `_PREPARED_CACHE`), więc nie pisz do folderu użytkownika.
- Nie zapisuj plików OTA do folderu repozytorium — tylko do folderu danych.
- `main.py` jest zamrożony w APK: zmiana w nim działa dopiero po zainstalowaniu nowego APK. Zmiany w `app_logic.py` docierają przez OTA.
- **Kod zawsze z dokumentacją.** Każda zmiana kodu musi mieć dokumentację — nie jest ukończona bez niej. Nowa funkcja/kolumna schematu/klucz configu/ścieżka zapisu: docstring lub komentarz w `app_logic.py` wyjaśniający cel i kontrakt (moduł ma opis na górze, a **każda funkcja modułowa** docstring — pilnuje tego `test_module_level_functions_have_docstrings`), oraz aktualizacja odpowiedniej sekcji tego pliku (tabele i „Mapa plików”, „Dane”/„Synchronizacja”/„OTA”, „Znane pułapki”, „Backlog” przy nowych pomysłach). Jeśli zmienia się zachowanie widoczne dla użytkownika albo publiczne API modułu, dopisz też notkę do sekcji, która je opisuje. Dokumentacja jest częścią definicji ukończenia. Zmiany widoczne dla użytkownika opisuj też w `README.md` i w odpowiedniej stronie `docs/` (indeks: `docs/README.md`), a wersję dopisz do `docs/historia-zmian.md`.
- **Zmiany schematu i zapisu.** Każda zmiana schematu = `ensure_schema()` + `ensure_schema_upgrade()` po każdej podmianie pliku bazy + eksport/import JSON + test migracji starej bazy; nowe kolumny dopisuj na końcu `SELECT` w `get_tasks()` (testy używają pozycji 0–7) albo dodaj osobny getter. Każdy zapis do bazy kończy się `push_database_async()`.
- **Łagodna degradacja.** Wszystko opcjonalne (brak wsparcia platformy, uprawnień, pliku, sieci) ma logować i działać jak dotąd, nigdy nie wywalać aplikacji. Naprawiając znaną pułapkę, usuń ją z „Znanych pułapek” i dopisz, co ją zastąpiło.
ZASADA GIT: Po wykonaniu git commit i git push NIGDY nie uruchamiaj gh run watch ani nie czekaj na zakończenie GitHub Actions / builda APK. Zakończ odpowiedź od razu po git push.
## Znane pułapki (stan obecny)

- **Nie wracaj do `from app_logic import *` w `main.py`.** Nadpisywało `main` i `ft.run(main)` uruchamiał `app_logic.main`, więc OTA nie działała wcale (numer wersji się zmieniał, kod nie). Teraz jest re-eksport pomijający `main`; pilnuje tego `test_entrypoint_is_not_shadowed`.
- `activate_downloaded_ota()` woła `module.main(page)` w tym samym procesie. Stare wątki (timer, sync) mogą jeszcze chwilę żyć — dlatego przed podmianą UI wołane jest `flush_pending_sync(timeout=2.0)`, a `page.overlay`/`page.controls` są czyszczone.
- Edycja zadania z widoku Daily przekazuje `current_category` (PC i mobile), więc dropdown pokazuje prawdziwą kategorię i zapis jej nie nadpisuje; pilnuje tego test regresyjny.
- `init_db()` nie może ruszać `db_last_change` (jest test) — inaczej pull z chmury nigdy nic nie pobierze.
- **`no such column: routine_days`** (crash widoku po starcie/pullu) = baza bez nowej kolumny, np. kopia z chmury/snapshotu sprzed 1.6.21. `get_routines()` sama domyka schemat i ponawia `SELECT` (ostatecznie czyta bez dni tygodnia), ale każde nowe miejsce podmieniające plik `LOCAL_DB` musi wołać `ensure_schema_upgrade()`. Uwaga: stary wzorzec `ALTER TABLE ... except sqlite3.OperationalError: pass` połykał także „database is locked”, więc migracja mogła cicho nie wykonać się wcale — `_ensure_column()` sprawdza `PRAGMA table_info` i loguje realne błędy.
- W APK, które użytkownicy mają **przed** wersją 1.6.19, działa stary, zepsuty loader. Pierwsza naprawa wymaga jednorazowej instalacji nowego APK; dopiero potem OTA działa automatycznie.
- **Minutnik po zminimalizowaniu aplikacji nie da sygnału.** To świadoma decyzja: aplikacja nie używa powiadomień systemowych ani `AlarmManager`. Nie dodawaj `flet-notifications`/`flet-audio` „przy okazji” i nie traktuj braku sygnału w tle jako regresji. W otwartej aplikacji sygnałem ma być wibracja/dźwięk.
- **Przełom dnia a `end_of_day_db()`.** Stara funkcja `end_of_day_db()` nadal istnieje i zachowuje się jak dawniej (niezrobione dzienne → `is_daily=0`) — korzystają z niej starsze testy i „Koniec dnia” z dialogu `overlay[7]`. Nowy UI (Daily: przycisk „🌅 Nowy dzień / Koniec dnia”, karta w Ustawieniach) używa `day_rollover_dialog` i `apply_day_rollover(...)`, gdzie niezrobione zadania zostają na Daily, dopóki użytkownik nie zdecyduje. Nie podmieniaj `end_of_day_db()` na nową logikę — złamie to `test_end_of_day`.
- **Pierwsze uruchomienie nie pokazuje dialogu przełomu.** Gdy `last_reset_date` jest pusty (świeża instalacja), `_maybe_start_day_rollover()` cicho ustawia go na dziś i kończy — bez modala. Dialog pojawia się dopiero, gdy data faktycznie się zmieni.
- **Sekcja Inbox to `ft.Container`, nie `ft.Column`.** Testy znajdują kolumnę kart przez `[c for c in content_col.controls if isinstance(c, ft.Column)][0]`; gdyby sekcja Inbox była `Column` na Master, podmieniłaby `tasks_column`. Trzymaj ją jako kontener, a listę Inbox w środku.
- Kolejność `content_column.controls` na Daily jest kontraktem: `[2]` = karta minutnika, `[3]` = karta postępu. Nie wstawiaj nowych elementów przed nimi (`test_archive_and_timer_ui`, `test_mobile_touch_targets_at_400px`).
- **Konflikt synchronizacji potrzebuje punktu odniesienia.** `_detect_conflict()` milczy, dopóki `last_synced_hash` jest pusty (pierwsza synchronizacja ustawia go przez `_mark_synced()`). Skrót liczy wiersze (`_sqlite_content_hash`), nie surowe bajty, więc sam `VACUUM`/przepisanie pliku nie udaje konfliktu. Przy testach konfliktów ustaw `sync_mode="none"` na czas zmian lokalnych — w trybie `folder` każdy zapis od razu pushuje i `last_synced_hash` się aktualizuje, więc konflikt nie powstanie.
- **`on_resize` reaguje tylko na próg `is_mobile()` (650 px).** `on_page_resize` porównuje nowy stan z `last_is_mobile`; nie dodawaj debounce/`page.run_task` — test liczy wywołania `refresh_view` i zmiana 950→949 nie może przebudowywać widoku.

---

## Backlog (P3 — pomysły, nie plan)

- **Zadania cykliczne** poza rutynami („co poniedziałek”, „co miesiąc opłaty”) — pole `repeat_rule` + generowanie następnego wystąpienia.
- **Poranne podsumowanie w aplikacji** (karta na Daily „Dziś: 3 zadania, 2 terminy”) — bez powiadomień systemowych.
- **Start dnia: wybierz 3** — krótki rytuał przenoszenia z Mastera na Daily, zamiast ręcznego przeciągania.
- **Udostępnianie do aplikacji / dyktowanie** (Android share target, speech-to-text) — zbadać wykonalność w Flet 1.0.x; wysoka wartość przy zrzucie myśli.
- **Widget na ekran główny** — prawdopodobnie poza Flet; nie planować bez researchu.
- **Eksport dla terapeuty** (Markdown/CSV z ostatnich 30 dni) — proste, jeśli będzie historia rutyn.
- **Ustawienia dostępności**: większa czcionka, wysoki kontrast, ograniczenie animacji.
- **Wieloplikowe OTA** (zip z modułami) — dopiero gdy `app_logic.py` przekroczy ~6000 linii; wymaga zmiany loadera w `main.py` (APK).
- **Body doubling / sesje współpracy** — poza zakresem, chyba że pojawi się realna potrzeba.

## Definicja ukończenia (dla każdego zadania)

- [ ] Kod w `app_logic.py` (lub nowym pliku) + brak zmian wymagających `main.py`, albo jasno opisane, że potrzebny jest APK.
- [ ] Testy: nowe przypadki dopisane do `test_app.py` (jeden plik) + cały `pytest` zielony (109+).
- [ ] Sprawdzone na PC (950 px) i mobile (400 px) — układ, dialogi, `page.overlay`.
- [ ] Migracja schematu + eksport/import JSON, jeśli zmieniono dane.
- [ ] `push_database_async()` w każdej nowej ścieżce zapisu.
- [ ] Wersja podniesiona w `version.txt` i `FALLBACK_VERSION`.
- [ ] Dokumentacja: docstring/komentarz w kodzie + aktualizacja `AGENTS.md` (tabele, config, pułapki, sekcje tematyczne) + `README.md`/`docs/` przy zmianach widocznych dla użytkownika + wpis w `docs/historia-zmian.md`.
- [ ] Brak nowych cichych `except: pass`; błędy trafiają do `LOG`.
