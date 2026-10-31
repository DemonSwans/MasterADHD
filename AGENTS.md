# AGENTS.md — Master ADHD

## Czym jest projekt

Osobisty menedżer zadań dla osób z ADHD: **Python + Flet 1.0.4 + SQLite**, działa na PC i Androidzie (APK). UI i komentarze po polsku, motyw „Dark Slate”. Widoki: `daily`, `master`, `archive` (Dziennik Sukcesów), `settings`.

## Komendy

```powershell
python -m pytest test_app.py -q                       # całość (67 testów, ~16 s)
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
| `app_logic.py` | ~4400 linii: schemat i operacje SQLite, konfiguracja, snapshoty, backup/import JSON, synchronizacja (Gist/HA/folder), logowanie, instalacja/aktywacja OTA oraz cały interfejs Flet w `main(page)`. |
| `test_app.py` | 67 testów: CRUD, rutyny (w tym dni tygodnia), migracje schematu starych baz, podkroki, postęp, archiwum, snapshoty, integralność bazy, Gist (mock), UI PC/mobile (w tym edycja z Daily z zachowaniem kategorii, usuwanie z „Cofnij”, obszary dotykowe 44 px na 400 px), wibracja z mockiem `HapticFeedback`, mini-minutnik, OTA (wybór wersji, instalacja, aktywacja, rollback). `DummyPage` + tymczasowe bazy. |
| `version.txt` | Aktualna wersja (1.6.25). Musi być równa `FALLBACK_VERSION` w `app_logic.py` (pilnuje tego test). |
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
- Kolejność kolumn `get_tasks()` (używana w testach): `0 id, 1 title, 2 is_daily, 3 is_done, 4 priority, 5 subtasks, 6 category, 7 deadline`. `deadline` to opcjonalna data ISO `YYYY-MM-DD` (bez godziny); `None` = brak terminu.
- Terminy: `add_task_db(..., deadline=...)`, `set_task_deadline_db()`, `get_task_deadline()`, `update_task_details_db(..., deadline=DEADLINE_UNSET)` (sentinel = nie zmieniaj, `None` = wyczyść). Sortowanie/filtr w Master: `master_sort_mode` (`DEFAULT`/`DEADLINE`) i `master_deadline_filter` (`ALL`/`WEEK`/`OVERDUE`) w config. Przypomnienia: `deadline_reminders_enabled`, `deadline_reminder_days` (14/7/3), `deadline_reminder_repeat_day_before`, `deadline_card_hidden_date`.
- Rutyny: `routines.routine_days` to JSON z intami `0..6` (0 = poniedziałek … 6 = niedziela, konwencja `datetime.weekday()`); `[]` = codziennie, a rutyny zapisane przed tą kolumną są traktowane jak codzienne. `get_routines()` zwraca krotki `(id, title, is_done, days)`, zapis przez `add_routine_db(title, days=None)` / `update_routine_db(id, title, days=None)` (ta druga obsługuje edycję z Ustawień), a widok Daily filtruje rutyny przez `get_daily_routines()`. JSON export/import przenosi `days`.
- Folder danych: `%USERPROFILE%\.master_adhd_data\` (Android: `FLET_APP_STORAGE_DATA/.master_adhd_data`). Tam też `master_adhd.log`, `snapshots/`, pliki OTA.
- Zapis wyłącznie przez `db_connection(write=True)` (WAL, `busy_timeout`, `RLock`, zawsze `close()`). `write=True` przy `LOCAL_DB` przesuwa `db_last_change` — bez tego zmiana nie jest widoczna dla rozstrzygania konfliktów synchronizacji.
- Migracje schematu: `ensure_schema(conn)` dodaje brakujące kolumny (`tasks.subtasks`, `tasks.category`, `routines.routine_days`) przez `PRAGMA table_info` + `ALTER TABLE`. Wywołują ją `init_db()`, `export_database_to_json_dict()`, `import_database_from_json_dict()`, `add_routine_db()` i `update_routine_db()`. **Każde miejsce, które podmienia plik `LOCAL_DB` bajtami innej bazy (pull Gist/HA/folder, `_restore_db_from_bytes`, `restore_pre_sync_backup`), musi po tym wywołać `ensure_schema_upgrade()`** — inaczej stara kopia bez nowej kolumny wywala `SELECT` w widoku (`no such column`).
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
- `page.overlay` ma 10 dialogów; kolejność jest kontraktem testów (`[3]` edycja zadania, `[4]` usuwanie, `[5]` edycja podkroku, `[9]` edycja rutyny) — nowe okna tylko na końcu listy. Dialog usuwania (`overlay[4]`) **istnieje nadal** (kontrakt testów i operacje zbiorcze), ale karty zadań go nie otwierają.
- Usuwanie zadania/podkroku z karty jest **natychmiastowe** (`delete_task_now` / `delete_subtask_now`) i pokazuje `ft.SnackBar` „Usunięto «…». Cofnij”. Cofnięcie przywraca pełny wiersz zadania (`restore_task_row_db`, `INSERT OR IGNORE` — to samo `id`, kategoria, priorytet, termin, podkroki) albo podkrok na tej samej pozycji (`restore_subtask_db`); drugie kliknięcie „Cofnij” nie duplikuje rekordu. Dialog zostaje tylko dla akcji nieodwracalnych zbiorczo (np. czyszczenie archiwum, import zastępujący dane). `open_delete_task_dialog` nie ma już wołających — trzyma go tylko kontrakt `overlay[4]`.
- Wibracja: `init_haptic_service(page)` dodaje `ft.HapticFeedback` do `page.services` **tylko na platformach mobilnych** (`page.platform`); `trigger_haptic_feedback(page, service, kind)` wywołuje metodę przez `page.run_task` i respektuje `sound_enabled`. Brak klasy/usługi/uprawnień = ciche pominięcie (log `debug`), nigdy crash. Na Windows `None`. Sygnały: `timer_done`→`vibrate`, `task_done`→`light_impact`, `undo`→`selection_click`. Używać razem z `play_sound_feedback` (dźwięk na PC).
- Dotyk (mobile): stała `TOUCH_TARGET_MIN = 44`. W `refresh_view` jest lokalny helper `_touch(default)`, który na telefonie zwraca 44, a na PC `default`. Wszystkie akcje-ikony w kartach (edytuj/usuń/dodaj krok, strzałki kroków) oraz nagłówek, szybkie dodawanie i przyciski minutnika są nim objęte — test `test_mobile_touch_targets_at_400px`.
- Kontrast: `contrast_ratio(fg, bg)` (WCAG 2.1) i `readable_text_color(bg, preferred, fallback, minimum=4.5)` — metadane kart używają tego drugiego, więc na ciemnym tle zostaje `COLOR_TEXT_SECONDARY`, a przy zmianie tła degraduje się do `COLOR_TEXT_PRIMARY`.
- Mini-minutnik (`_build_mini_timer`, `update_mini_timer`): jedno źródło stanu timera, dwa wystąpienia (PC w sidebarze, mobile nad dolną nawigacją w `mini_timer_mob_wrapper`). Chip zawiera pauzę/wznowienie i reset, więc działają z **każdego widoku**; klik w tło chipa wraca do Daily. `mini_timer_mob_wrapper.visible` chowa też `Divider`. Na mobile chip ma 44 px wysokości.
- Korzeń to `page.controls[0]` (SafeArea) → `.content` (`root_layout`) → `.content` (`Row`/`Column`). Testy indeksują dalej: sidebar `controls[1]` Daily, `[2]` Master, `[3]` Dziennik; dolna nawigacja `[0]` Daily, `[1]` Master, `[2]` Dziennik (test mylnie nazywa ją `settings_mob_box`), `[3]` Ustawienia; `content_area` to `Row[2]` na PC i `Column[0]` na mobile.
- **Wszystkie testy trzymaj w jednym pliku `test_app.py`** — także nowe; na razie nie dzielimy ich na osobne pliki (`test2_app.py` nie istnieje i nie jest uruchamiany przez CI). Autouse fixture przekierowuje bazy do `tmp_path` przez `app_logic.LOCAL_DB`/`CONFIG_DB` (patchuj `app_logic`, nie tylko `main`) **oraz wszystkie ścieżki OTA z `main`** (`OTA_DIR`, `OTA_*_FILE`, reset `_PREPARED_CACHE`), więc nie pisz do folderu użytkownika.
- Nie zapisuj plików OTA do folderu repozytorium — tylko do folderu danych.
- `main.py` jest zamrożony w APK: zmiana w nim działa dopiero po zainstalowaniu nowego APK. Zmiany w `app_logic.py` docierają przez OTA.
ZASADA GIT: Po wykonaniu git commit i git push NIGDY nie uruchamiaj gh run watch ani nie czekaj na zakończenie GitHub Actions / builda APK. Zakończ odpowiedź od razu po git push.
## Znane pułapki (stan obecny)

- **Nie wracaj do `from app_logic import *` w `main.py`.** Nadpisywało `main` i `ft.run(main)` uruchamiał `app_logic.main`, więc OTA nie działała wcale (numer wersji się zmieniał, kod nie). Teraz jest re-eksport pomijający `main`; pilnuje tego `test_entrypoint_is_not_shadowed`.
- `activate_downloaded_ota()` woła `module.main(page)` w tym samym procesie. Stare wątki (timer, sync) mogą jeszcze chwilę żyć — dlatego przed podmianą UI wołane jest `flush_pending_sync(timeout=2.0)`, a `page.overlay`/`page.controls` są czyszczone.
- Edycja zadania z widoku Daily przekazuje `current_category` (PC i mobile), więc dropdown pokazuje prawdziwą kategorię i zapis jej nie nadpisuje; pilnuje tego test regresyjny.
- `init_db()` nie może ruszać `db_last_change` (jest test) — inaczej pull z chmury nigdy nic nie pobierze.
- **`no such column: routine_days`** (crash widoku po starcie/pullu) = baza bez nowej kolumny, np. kopia z chmury/snapshotu sprzed 1.6.21. `get_routines()` sama domyka schemat i ponawia `SELECT` (ostatecznie czyta bez dni tygodnia), ale każde nowe miejsce podmieniające plik `LOCAL_DB` musi wołać `ensure_schema_upgrade()`. Uwaga: stary wzorzec `ALTER TABLE ... except sqlite3.OperationalError: pass` połykał także „database is locked”, więc migracja mogła cicho nie wykonać się wcale — `_ensure_column()` sprawdza `PRAGMA table_info` i loguje realne błędy.
- W APK, które użytkownicy mają **przed** wersją 1.6.19, działa stary, zepsuty loader. Pierwsza naprawa wymaga jednorazowej instalacji nowego APK; dopiero potem OTA działa automatycznie.
- **Minutnik po zminimalizowaniu aplikacji nie da sygnału.** To świadoma decyzja: aplikacja nie używa powiadomień systemowych ani `AlarmManager`. Nie dodawaj `flet-notifications`/`flet-audio` „przy okazji” i nie traktuj braku sygnału w tle jako regresji. W otwartej aplikacji sygnałem ma być wibracja/dźwięk (zadanie 1.1).

---

## Plan rozwoju (ADHD-first)

Plan spisany przy **v1.6.23** (2026-10-29). Numeracja linii dotyczy tej wersji i z czasem się przesunie — szukaj po nazwach funkcji, nie po numerach. Kolejność sprintów jest celowa: najpierw to, co użytkownik odczuje natychmiast, potem rytuały dnia, potem dane i niezawodność. **Nie zaczynaj sprintu 2, zanim sprint 1 nie jest wdrożony i przetestowany.**

### Co już działa i ma zostać

Szybki zrzut myśli (`save_quick_dump`), limit zadań „A” (`max_a_limit`), rutyny z dniami tygodnia, podkroki z kolejnością, spokojna karta terminów (bez wyskakujących okien), archiwum sukcesów, snapshoty + rotacja, OTA z rollbackiem, trzy tryby synchronizacji. Tego nie przeprojektowujemy — nowe funkcje mają się do tego dopinać.

### Zasady pracy z planem

1. **Jeden sprint = jedna wersja.** Po sprincie podnieś `version.txt` **i** `FALLBACK_VERSION` w `app_logic.py`, uruchom `python -m pytest test_app.py -q`, zaktualizuj tabelę statusów niżej i dopisz zmiany do AGENTS.md.
2. **Zmiany w `main.py` wymagają nowego APK.** Wszystko, co ma dotrzeć przez OTA, musi być w `app_logic.py` (albo w nowym pliku ładowanym przez `app_logic`). Jeśli sprint wymaga `main.py`, zaplanuj wydanie APK i napisz to w opisie.
3. **Każda zmiana schematu** = `ensure_schema()` + `ensure_schema_upgrade()` po każdej podmianie pliku bazy + eksport/import JSON + test migracji starej bazy. Nowe kolumny dopisuj **na końcu** `SELECT` w `get_tasks()` (testy używają pozycji 0–7) albo dodaj osobny getter.
4. **Każdy zapis do bazy** kończy się `push_database_async()`.
5. **Nowe okna** dopisuj na końcu `page.overlay` (obecnie 10 dialogów; indeksy 3/4/5/9 są kontraktem testów).
6. **Nie zwiększaj liczby widoków bez potrzeby** — testy indeksują sidebar i dolną nawigację. Preferuj pełnoekranowe nakładki i sekcje w istniejących widokach.
7. **Wszystko opcjonalne musi degradować się łagodnie**: brak wsparcia platformy, brak uprawnień, brak pliku → log + działanie jak dziś, nigdy crash.
8. **Naprawiając znaną pułapkę**, usuń ją z sekcji „Znane pułapki” i dopisz, co ją zastąpiło.
9. **Nie dodawaj `except Exception: pass`** w nowym kodzie — używaj `LOG.warning/exception` z kontekstem.

### Tablica sprintów

| Sprint | Cel | Rozmiar | Status |
|---|---|---|---|
| 1. Mniej tarcia | Cofanie pomyłek, czytelność, timer widoczny w każdym widoku | S/M | gotowy (1.6.25) — 1.1–1.5 wdrożone |
| 2. Rytuał dnia i „Co teraz?” | Mniej decyzji, jedno zadanie na raz, świadomy przełom dnia | M | do zrobienia |
| 3. Pamięć i refleksja | Historia rutyn, statystyki, czas szacowany, tygodniowy przegląd | M | do zrobienia |
| 4. Niezawodność i bezpieczeństwo | Wątki, postęp, podpis OTA, konflikty synchronizacji | M/L | do zrobienia |

---

## Sprint 1 — „Mniej tarcia”

**Cel:** osoba z ADHD może natychmiast cofnąć pomyłkę, widzi timer niezależnie od widoku, a edycja z Daily nie psuje kategorii. Wszystko na telefonie ma być klikalne palcem, nie paznokciem.

### 1.1. Wibracja w otwartej aplikacji (bez powiadomień systemowych)

`P2 · S · Status: gotowe (1.6.25)`

**Decyzja.** Świadomie **nie dodajemy powiadomień systemowych** ani harmonogramu alarmów. Nie dodawać `flet-notifications`, `flet-audio` ani podobnych pakietów, nie zmieniać `requirements.txt`/`build.yml`, nie prosić o uprawnienia do powiadomień. Minutnik po zminimalizowaniu lub zablokowaniu telefonu pozostanie niemy — to zaakceptowane ograniczenie, nie bug.

**Problem.** `play_sound_feedback()` (app_logic.py:1211) gra wyłącznie przez `winsound` na Windows. Na Androidzie wpisuje `LOG.debug` i kończy — w otwartej aplikacji nie ma żadnego potwierdzenia końca timera ani ukończenia zadania. Wibracja nie jest używana nigdzie.

**Co zrobić.**

1. Dodać `ft.HapticFeedback` do `page.services` (przez `getattr(ft, "HapticFeedback", None)`, bo w CI jest Flet 1.0.4) i wywoływać krótką wibrację przy: końcu timera (gdy aplikacja jest otwarta), oznaczeniu zadania/rutyny, „Cofnij”.
2. Metody są `async` — wywoływać przez `page.run_task(...)`; całość w `try/except` + `LOG.debug`. Brak wsparcia urządzenia = ciche pominięcie, nie crash.
3. Na Windows zostawić `winsound` i dodać wibrację tylko na platformach mobilnych.
4. W Ustawieniach wykorzystać istniejące `sound_enabled`; zmienić etykietę na „Dźwięk i wibracja”, bez nowych kluczy configu.
5. Nie ruszać logiki terminów — karta „Nadchodzące terminy” zostaje jedynym przypomnieniem (bez powiadomień systemowych).

**Kryteria akceptacji.**

- Na Androidzie timer kończący się przy otwartej aplikacji powoduje wibrację.
- Oznaczenie zadania daje krótką wibrację, jeśli urządzenie ją wspiera.
- Brak wsparcia/uprawnień nie powoduje crasha; na PC bez zmian.
- Zero nowych zależności. Jeśli runtime APK nie ma `HapticFeedback`, funkcja po prostu nie działa — bez crasha i bez nowego wydania.

**Testy.** Mock `HapticFeedback` → wibracja wołana na koniec timera; brak atrybutu w `ft` → brak wyjątku; `sound_enabled=0` wyłącza wibrację.

**Ograniczenie.** Po zminimalizowaniu/zablokowaniu telefonu timer nie da sygnału. To świadoma decyzja użytkownika — nie dodawaj powiadomień systemowych „przy okazji” i nie zgłaszaj tego jako regresji (wpis w „Znanych pułapkach”).

### 1.2. „Cofnij” zamiast okna potwierdzenia

`P1 · S/M · Status: gotowe (1.6.24)`

**Problem.** Usuwanie zadania wymaga przejścia przez dialog (`open_delete_task_dialog`, app_logic.py:2509), podobnie czyszczenie archiwum. To dwa dodatkowe kliknięcia przy każdej pomyłce — a przy ADHD pomyłki są częste, więc tarcie sumuje się szybciej niż ryzyko przypadkowego usunięcia.

**Co zrobić.**

1. Usuwanie zadania/podkroku: natychmiastowa akcja + `ft.SnackBar` „Usunięto «...». Cofnij” przez ~6 s. SnackBar z akcją „Cofnij” przywraca rekord (najprościej: zapisz pełny wiersz przed `DELETE` i wstaw go ponownie z tym samym `id`, albo użyj miękkiego usuwania — patrz sprint 4.4).
2. Zostawić dialog tylko dla operacji nieodwracalnych zbiorczo (np. „Wyczyść całe archiwum”, import zastępujący dane).
3. Ograniczyć stos cofania do ostatniej akcji — nie budować pełnej historii (to sprint 4.4).
4. Upewnić się, że cofnięcie przywraca kategorię, priorytet, termin i podkroki.

**Kryteria akceptacji.**

- Usunięcie zadania jednym kliknięciem, cofnięcie przywraca je w tej samej liście z pełnymi danymi.
- Po 6 s SnackBar znika i akcja jest ostateczna.
- Na mobile SnackBar nie zasłania dolnej nawigacji (test na 400 px).

**Testy.** Usuń → cofnij → `get_tasks()` zawiera rekord z tym samym `id` i polami; drugie kliknięcie „Cofnij” nie duplikuje rekordu.

### 1.3. Bug: edycja z Daily nadpisuje kategorię

`P0 · S · Status: gotowe (1.6.24)`

**Problem.** Karty na Daily wołają `open_edit_task_dialog(tid, tit)` bez `current_category` (app_logic.py:3660 dla PC i 3785 dla mobile). Domyślna wartość parametru to „Ogólne”, więc zapis kasuje prawdziwą kategorię. W Masterze i w karcie przypomnień jest już poprawione (przekazywane `cat`).

**Co zrobić.** Przekazać `current_category=category` w obu miejscach, dodać test regresyjny i usunąć odpowiednią pułapkę z AGENTS.md po wdrożeniu.

**Kryteria akceptacji.** Edycja zadania z kategorią „💼 Praca” otwiera dropdown z tą wartością i po zapisie niczego nie zmienia.

**Testy.** `test_ui_edit_task_from_daily_keeps_category` — otwórz dialog z karty Daily, zapisz bez zmian, sprawdź `category` w bazie.

### 1.4. Dotyk i czytelność

`P1 · S · Status: gotowe (1.6.25)`

**Problem.** Ikony edycji/usuwania mają 28×28 px (app_logic.py:3653–3664 i odpowiednik mobile), a drobne teksty 10–12 px. Na telefonie to proszenie się o pomyłkę, zwłaszcza przy pośpiechu i drżeniu rąk.

**Co zrobić.**

1. Wszystkie akcje podstawowe (zrobione, edytuj, usuń, dodaj, timer) mają minimum 44×44 px obszaru klikalnego; sama ikona może zostać mała, ale `width/height` kontenera ma być ≥ 44.
2. Podnieść bazową wielkość tekstu zadań i rutyn do 14 px, metadanych do 12 px; nie powiększać wszystkiego — tylko to, co się czyta.
3. Sprawdzić kontrast `COLOR_TEXT_SECONDARY` na `COLOR_BG_CARD` (docelowo ≥ 4.5:1).
4. Nie usuwać gęstości PC — zmiany mają dotyczyć głównie `is_mobile()`.

**Kryteria akceptacji.** Na 400 px żaden element akcji nie ma mniej niż 44 px; lista nadal mieści 5–6 zadań na ekranie bez przewijania w nieskończoność.

### 1.5. Globalny mini-minutnik

`P1 · S/M · Status: gotowe (1.6.25)`

**Problem.** Timer jest widoczny tylko na Daily, a jego stan (`timer_seconds_left`) żyje w `main(page)`. Po przejściu do Master użytkownik traci poczucie czasu — a to jedna z głównych trudności w ADHD.

**Co zrobić.** Dodać mały wskaźnik (mm:ss + ikona) w sidebarze na PC i w pasku dolnym/nagłówku na mobile, widoczny we wszystkich widokach. Kliknięcie wraca do Daily. Źródłem prawdy pozostaje istniejący stan timera i `update_timer_ui()`; nie tworzyć drugiego wątku.

**Kryteria akceptacji.** Start timera → przejście do Master → licznik nadal odlicza i jest widoczny; pauza/reset działają z każdego widoku.

---

## Sprint 2 — „Rytuał dnia i Co teraz?”

**Cel:** zmniejszyć liczbę decyzji. Dzień ma się zaczynać i kończyć jednym świadomym gestem, a w ciągu dnia ma być widoczne **jedno** zadanie, nie lista.

### 2.1. Świadomy przełom dnia (bez gubienia zadań)

`P0 · M · Status: do zrobienia`

**Problem.** `end_of_day_db()` (app_logic.py:1710) przenosi zrobione zadania do archiwum, a **niezrobione dzienne zadania zdejmuje z Daily** (`is_daily=0`) bez pytania. Jeśli użytkownik nie kliknie „Koniec dnia”, karta nowego dnia pojawia się dopiero przy kolejnym uruchomieniu i tylko informuje, że „ostatni reset był wcześniej” (app_logic.py:4335). Stan dnia się rozjeżdża, postęp traci sens, a zadania znikają z pola widzenia.

**Dlaczego to ważne dla ADHD.** Najgorsze, co może zrobić aplikacja, to cicho zgubić rzecz, którą użytkownik miał zrobić — wtedy albo o niej zapomni, albo dostanie poczucie porażki. Potrzebny jest krótki, spokojny rytuał z jasnym wyborem.

**Co zrobić.**

1. Przy starcie (i najlepiej po powrocie z tła) sprawdzić `last_reset_date`; jeśli jest inny niż dziś, pokazać **jeden** spokojny ekran „Nowy dzień” — bez czerwieni, bez wyrzutów.
2. Ekran pokazuje: zrobione wczoraj (ile pójdzie do Dziennika Sukcesów) i listę niezrobionych z Daily z wyborem: **„Zostaw na dziś”**, **„Wróć do Mastera”**, **„Usuń z Daily”**; plus akcje zbiorcze („wszystkie zostaw”, „wszystkie do Mastera”).
3. „Koniec dnia” ma robić to samo, ale wywołane ręcznie; dodać opcję „Zamknij dzień i pokaż podsumowanie” (bez automatycznego kasowania zadań bez decyzji).
4. Przed jakąkolwiek zmianą stanu zrobić snapshot (`make_local_snapshot("pre_day_rollover")`).
5. Nowy klucz configu: `day_rollover_mode` = `ask` (domyślnie) / `auto_keep` / `auto_master`.
6. Nie przenosić niezrobionych zadań do `completed_archive` — to archiwum sukcesów, nie kosz.

**Kryteria akceptacji.**

- Po zmianie daty przy starcie pojawia się ekran wyboru; anulowanie niczego nie zmienia.
- Wybór „Zostaw na dziś” pozostawia `is_daily=1`; „Do Mastera” ustawia `is_daily=0`; „Usuń z Daily” też `is_daily=0`, ale bez usuwania zadania.
- Zrobione zadania trafiają do archiwum dokładnie raz (bez duplikatów przy ponownym otwarciu ekranu).
- Snapshot powstaje przed zmianą.

**Testy.** Symuluj `last_reset_date` z przeszłości → UI pokazuje ekran; każdy wybór sprawdzony w bazie; powtórne uruchomienie nie duplikuje archiwum.

### 2.2. Tryb „Co teraz?” (fokus na jednym zadaniu)

`P1 · M · Status: do zrobienia`

**Problem.** Daily to lista: 3 × A, B, C, rutyny, przypomnienia, filtry. Dla osoby z ADHD lista sama w sobie jest źródłem paraliżu decyzyjnego. Aplikacja ma limit „A”, ale nie ma ekranu, który mówi: **to jedno zadanie, teraz, pierwszy krok, start**.

**Co zrobić.**

1. Przycisk „▶ Skup się” na karcie zadania (i duży przycisk na górze Daily: „Co teraz?” wybierający pierwsze niezrobione A).
2. Pełnoekranowa nakładka (nowy dialog **na końcu** `page.overlay`): tytuł zadania, lista podkroków z zaznaczaniem, przycisk „Zaczynam” uruchamiający timer 5/15/25, przycisk „Zrobione” → automatycznie następne A albo spokojne „na dziś wystarczy”.
3. Ukryć wszystko inne: brak sidebaru, brak liczników, brak kategorii. Jedno zadanie = jeden ekran.
4. Po ukończeniu: krótki, konkretny komunikat („Zrobione. 12 minut. Dobra robota.”) — bez przesadnego entuzjazmu, który bywa irytujący.
5. Dodać pole „pierwszy mikro-krok” w tworzeniu/edycji zadania (opcjonalne, jedna linia) i pokazywać je w trybie fokusu nad listą podkroków. Jeśli puste — podpowiedź „Co da się zrobić w 2 minuty?”.
6. Timer w trybie fokusu korzysta z istniejącego stanu timera; sygnałem końca jest wibracja z 1.1 (jeśli wdrożona).

**Kryteria akceptacji.**

- Z Daily jednym kliknięciem wchodzi się w fokus na wybranym zadaniu.
- Widoczne jest tylko jedno zadanie i jego podkroki.
- „Zrobione” oznacza zadanie i proponuje następne; zamknięcie wraca do poprzedniego widoku bez utraty stanu.
- Na mobile przycisk „Zaczynam” jest osiągalny kciukiem (dół ekranu).

**Testy.** Otwarcie/zamknięcie nakładki; „Zrobione” ustawia `is_done=1`; następne A jest wybierane zgodnie z priorytetem; pusty stan (brak A) pokazuje komunikat, nie błąd.

### 2.3. Inbox i 2-minutowy triage

`P1 · M · Status: do zrobienia`

**Problem.** `save_quick_dump` (app_logic.py:2283) wrzuca każdą myśl od razu do Master jako priorytet B i kategorię „Ogólne”. Master puchnie, a użytkownik później nie wie, co jest ważne. Zrzut myśli ma być szybki, ale porządkowanie nie może się dziać w tym samym momencie.

**Co zrobić.**

1. Nowa kategoria systemowa „📥 Inbox” (bez zmiany schematu) — szybki zrzut trafia tam, `priority="C"`, `is_daily=False`.
2. Osobna sekcja/ekran „Inbox (n)” z jedną kartą naraz i trzema decyzjami: priorytet (A/B/C), „na dziś / do Mastera”, termin (skróty Dziś/Jutro/Za tydzień/bez). Po decyzji karta znika, następna wchodzi.
3. Wejście do Inboxu z badge’em liczby; jeśli Inbox pusty — chowamy sekcję.
4. Nie zmieniać istniejącego `quick_dump` w sposób, który wymaga nowego okna przy każdym zapisie — ma zostać jedno pole i Enter.

**Kryteria akceptacji.**

- Zrzut trafia do „📥 Inbox” i nie zaśmieca Mastera.
- Triage przenosi zadanie do wybranej kategorii/priorytetu i (opcjonalnie) na Daily.
- Licznik Inboxu spada; pusty Inbox nie pokazuje pustej sekcji.

**Testy.** Zrzut → kategoria Inbox; triage → poprawne pola; stare zadania z „Ogólne” nie są ruszane.

---

## Sprint 3 — „Pamięć i refleksja”

**Cel:** aplikacja ma pamiętać, co się udało, pokazywać postęp bez presji i pomagać w wycenie czasu. To wspiera motywację i poczucie sprawczości, nie karze za przerwy.

### 3.1. Historia rutyn i miękkie serie

`P1 · M · Status: do zrobienia`

**Problem.** `routines.is_done` to pojedynczy boolean resetowany w `end_of_day_db()`. Po resecie nie ma śladu, że rutyna była robiona wczoraj, tydzień temu ani przez ostatni miesiąc. Nie da się pokazać „3 tygodnie z rzędu bierzesz leki” ani wychwycić, że rutyna wypadła z nawyku.

**Co zrobić.**

1. Tabela `routine_log(id INTEGER PRIMARY KEY, routine_id INTEGER, date TEXT, done INTEGER, UNIQUE(routine_id, date))` w `tasks.db`. Data ISO `YYYY-MM-DD`.
2. `toggle_routine_db()` zapisuje wpis na dziś (done 1/0) zamiast tylko aktualizować `routines.is_done`; `is_done` zostaje jako szybki stan bieżący.
3. `ensure_schema()` tworzy tabelę, `init_db()`/`ensure_schema_upgrade()` ją dopieszczają; eksport/import JSON przenosi historię (nowy klucz, wstecznie kompatybilny — brak klucza = pusta historia).
4. `end_of_day_db()` **nie kasuje** historii; resetuje tylko `is_done`.
5. UI: przy rutynie w Ustawieniach mały pasek 7 dni (zielone/szare kropki) i tekst „w tym tygodniu: 4/7”. Na Daily — bez zmiany, żeby nie zaśmiecać.
6. Seria liczona jako „dni z rzędu, w których rutyna była zaplanowana i zrobiona”; przerwa w dzień, w który rutyna i tak nie wypada, nie przerywa serii. **Bez komunikatu „straciłeś serię”** — najwyżej „wróć do rytmu”.

**Kryteria akceptacji.**

- Po kilku dniach symulowanych zmian historia jest kompletna, mimo `end_of_day`.
- Stara baza bez tabeli migruje się automatycznie i nie wywala widoku.
- Eksport → import w nowej bazie odtwarza historię.
- Rutyna codzienna i rutyna „wt/śr” liczą serię poprawnie.

**Testy.** Migracja starej bazy; toggle w dwóch dniach; reset dnia nie czyści logu; JSON roundtrip; seria z przerwą w dzień niezaplanowany.

### 3.2. `created_at` / `updated_at` i „zadania duchy”

`P1 · M · Status: do zrobienia`

**Problem.** `tasks` nie ma żadnego znacznika czasu. Nie wiadomo, od kiedy zadanie leży w Masterze, ani kiedy ostatnio je ruszano. Przy ADHD Master z czasem staje się cmentarzyskiem, którego nikt nie chce otwierać.

**Co zrobić.**

1. Dodać kolumny `created_at`, `updated_at` (ISO, nullable dla starych wierszy; przy migracji wypełnić `datetime.now()`).
2. Ustawiać `created_at` w `add_task_db()`, `updated_at` w każdej funkcji modyfikującej (`toggle_done_db`, `update_priority_db`, `update_task_details_db`, `edit_task_title_db`, `update_subtasks_db`, `set_daily_db`, `set_task_deadline_db`).
3. **Nie zmieniać kolejności** istniejących kolumn w `get_tasks()`; nowe pola dodać na końcu albo osobnym `get_task_timestamps(task_id)`.
4. W Masterze dodać filtr/sekcję „Dawno nietknięte (14+ dni)” lub cotygodniowy przegląd: „To leży od 3 tygodni. Nadal ważne? [Tak / Do archiwum / Usuń]”. Ton: neutralny, bez wyrzutów.
5. Wykorzystać `updated_at` później do wykrywania konfliktów synchronizacji (sprint 4.4).

**Kryteria akceptacji.**

- Nowe zadanie ma `created_at`; edycja podnosi `updated_at`; odhaczenie też.
- Stare wiersze dostają sensowną datę migracji.
- „Zadania duchy” nie pokazują zadań świeżych ani tych z przyszłym terminem.

**Testy.** CRUD aktualizuje znaczniki; migracja starych wierszy; filtr 14 dni.

### 3.3. Tygodniowy przegląd i notatka sukcesu

`P2 · M · Status: do zrobienia`

**Problem.** „Dziennik Sukcesów” to lista tytułów. Nie ma chwili refleksji, a to właśnie ona buduje poczucie sprawczości. Jednocześnie zbyt rozbudowana gamifikacja przy ADHD potrafi zaszkodzić.

**Co zrobić.**

1. Przy pierwszym uruchomieniu w nowym tygodniu (config: `weekly_review_last_shown`) spokojna karta w aplikacji: ile zadań ukończono, które rutyny były najczęstsze, 3 ostatnie sukcesy z archiwum. Bez powiadomień systemowych.
2. Jedno pole tekstowe: „Co w tym tygodniu poszło dobrze?” — zapis do nowej tabeli `weekly_notes(week_start, note)` lub do `completed_archive` jako wpis typu `note`. Wybrać prostsze; jeśli tabela, to migracja + JSON.
3. „Koniec dnia” dodatkowo pyta (opcjonalnie, jedną linią) „Co dziś poszło dobrze?” — wpis trafia do Dziennika Sukcesów jako notatka, nie zadanie.
4. Zero rankingu, zero porównań z innymi, zero „passa przepadła”. Tylko liczby i pytanie.

**Kryteria akceptacji.** Przegląd pojawia się raz w tygodniu i można go odłożyć; notatka jest widoczna w Dzienniku; brak danych nie pokazuje pustych wykresów.

### 3.4. Szacowany czas i „time blindness”

`P2 · M · Status: do zrobienia`

**Problem.** Osoba z ADHD często nie wie, ile zajmie zadanie, i planuje dzień na 12 godzin. Aplikacja ma termin, ale nie ma pojęcia „ile to potrwa”.

**Co zrobić.**

1. Opcjonalne pole `estimate_min` (INTEGER, nullable) w `tasks`; wpisywane w dialogu dodawania/edycji jako 5/15/30/60 min lub własne.
2. Na Daily pokazać sumę: „Zaplanowane: ~1 h 40 min” obok postępu.
3. Przy starcie timera w trybie fokusu podpowiedzieć `estimate_min` jako domyślny preset.
4. Po ukończeniu zapisać rzeczywisty czas (jeśli timer był użyty) i pokazać różnicę tylko w podsumowaniu tygodnia — bez oceniania.
5. Nie dodawać wykresów na główny ekran; to ma być jedna liczba.

**Kryteria akceptacji.** Zadanie z szacunkiem pokazuje czas; suma dnia aktualizuje się po zmianach; brak szacunku nie psuje sumy (traktowane jako 0, z informacją „bez szacunku: n”).

---

## Sprint 4 — „Niezawodność i bezpieczeństwo”

**Cel:** wyeliminować ciche utraty danych i losowe błędy UI, które najbardziej bolą, gdy użytkownik jest już przeciążony. Ten sprint nie dodaje widowiskowych funkcji — stabilizuje wszystko powyżej.

### 4.1. Aktualizacje UI z jednego wątku + rozsądny refresh

`P1 · M · Status: do zrobienia`

**Problem.** `on_sync_state()` (app_logic.py:2889) jest wywoływane z wątku synchronizacji (`set_sync_status` → callback) i ustawia kontrolki oraz `page.update()`. `timer_worker` robi to co 0,5 s. W tym samym czasie `refresh_view()` (app_logic.py:2989) podmienia całe `content_column.controls`. To wyścig, który może skończyć się losowym wyjątkiem albo „mrugnięciem” interfejsu. Dodatkowo `page.on_resize = lambda e: refresh_view()` (app_logic.py:4375) przebudowuje cały widok przy każdym pikselu zmiany rozmiaru, a część handlerów woła `refresh_view()` podwójnie (np. app_logic.py:3063, 3449).

**Co zrobić.**

1. Wprowadzić jeden punkt wejścia do UI: `schedule_ui_update(callback)` — kolejkuje aktualizację i wykonuje ją w bezpiecznym momencie (np. `page.run_thread`/`run_task` + lock), zamiast `page.update()` z każdego wątku.
2. `on_sync_state` i `update_timer_ui` mają tylko aktualizować wartości i kolejkować odświeżenie, nie wołać `page.update()` bezpośrednio.
3. `on_resize`: debounce 200–300 ms i przebudowa **tylko** gdy zmienia się próg `is_mobile()` (650 px), nie przy każdej zmianie szerokości.
4. Usunąć podwójne `refresh_view()` z handlerów; tam gdzie to możliwe, odświeżać tylko zmienioną sekcję (np. pasek postępu, listę zadań), nie całe drzewo.
5. Zachować scroll i fokus pola szybkiego dodawania przy odświeżeniu (jeśli się da; jeśli nie — przynajmniej nie czyścić wartości).

**Kryteria akceptacji.**

- Symulacja: sync w tle + timer + refresh nie powoduje wyjątków.
- Zmiana rozmiaru okna nie wywołuje dziesiątek przebudów (test licznika wywołań).
- Wpisywany tekst w polu szybkiego dodawania nie znika przy odświeżeniu wywołanym synchronizacją.

**Testy.** `DummyPage` z licznikiem `update()`; wątek wołający callback; resize 950→949→948 nie przebudowuje, 950→400 przebudowuje raz.

### 4.2. Postęp dnia uwzględniający podkroki

`P1 · S · Status: do zrobienia`

**Problem.** `calculate_progress_logic()` (app_logic.py:1732) liczy `total_points = len(tasks) + len(routines)`, a zadanie z podkrokami daje punkt na podstawie „aktywnego” podkroku, nie proporcji. Zadanie z 10 podkrokami waży tyle samo co jedno kliknięcie rutyny, a częściowe postępy są nieintuicyjne. To zaniża lub zawyża poczucie sprawczości.

**Co zrobić.**

1. Wybrać jedną, jasną formułę i udokumentować ją w AGENTS.md. Rekomendacja: punktacja hybrydowa — zadanie bez podkroków = 1 punkt; zadanie z podkrokami = 1 punkt × (ukończone podkroki / wszystkie), rutyna = 1 punkt. Dzięki temu pasek rośnie płynnie, ale ukończenie całości nadal jest wyraźne.
2. Puste listy → 0%, bez dzielenia przez zero; gdy brak zadań i rutyn, ukryć pasek albo pokazać „Brak zadań na dziś”.
3. Ujednolicić z etykietą „% ukończone”; nie pokazywać 100%, jeśli zostały niezrobione podkroki.
4. Dodać testy skrajne: 0 elementów, samo rutyny, zadanie z 1/3 podkrokami, wszystko zrobione.

**Kryteria akceptacji.** Postęp rośnie po każdym podkroku; 100% tylko gdy wszystko naprawdę zrobione; brak wyjątków dla pustych list.

### 4.3. OTA: podpis, snapshot przed aktywacją, mniej duplikacji

`P1 · M/L · Status: do zrobienia`

**Problem.** `check_and_download_ota()` w `main.py` używa `ssl._create_unverified_context()` (celowo, dla mobilnych środowisk), a jedyną weryfikacją pobranego kodu jest `compile()` — czyli składnia. Kod z GitHuba jest wykonywany z pełnym dostępem do bazy użytkownika. Rollback istnieje, ale pierwsze uruchomienie wadliwej wersji może już zmienić dane. Dodatkowo logika OTA jest zduplikowana w `main.py` i w `trigger_manual_ota()`/`install_ota_code()` (app_logic.py:2154, 855), więc obie ścieżki mogą się rozjechać.

**Co zrobić.**

1. **Podpis wydania.** Przygotować parę kluczy Ed25519; publikować obok `app_logic.py` plik `app_logic.py.sig`; klucz publiczny wbudować w kod. Weryfikować podpis przed zapisem i przed uruchomieniem. Ponieważ `main.py` jest zamrożony, pełna ochrona startu wymaga nowego APK; w `app_logic` zabezpieczyć przynajmniej ścieżkę ręczną i kolejne wersje.
2. **Snapshot przed pierwszym uruchomieniem nowej wersji.** Na początku `main(page)` (przed `init_db()`) sprawdzić config `last_ota_run_version`; jeśli różni się od bieżącej wersji, zrobić `make_local_snapshot(f"pre_ota_{wersja}")` i zapisać nową wartość. Dzięki temu migracja lub błąd nowego kodu ma punkt powrotu.
3. **Nie pobierać kodu po niezabezpieczonym TLS.** Najpierw próbować normalnej weryfikacji certyfikatów; fallback `unverified` tylko za zgodą użytkownika i z ostrzeżeniem w logu/UI. Jeśli to niemożliwe na Androidzie — przynajmniej podpis z pkt 1.
4. **Wydania etapowe.** Dodać `channel` w configu (`stable`/`beta`) i numer `min_supported`; `beta` pobiera z gałęzi testowej. Domyślnie `stable`.
5. **Jedna implementacja.** Wydzielić wspólne funkcje (`download_release`, `verify_release`, `install_release`) do `app_logic.py`; `main.py` może je wołać tylko po nowym APK, ale ręczna ścieżka i testy korzystają z jednego kodu.
6. **Atomowość i kopie** już są — nie regresować (`_write_text_atomic`, `app_logic_ota.prev.py`, `ota_failed_version.txt`).
7. **Testy:** podmieniony plik bez ważnego podpisu jest odrzucany; ważny przechodzi; snapshot powstaje przy zmianie wersji; `DISABLE_OTA=1` nadal omija sieć i cache.

**Kryteria akceptacji.** Nie da się zainstalować przez OTA kodu bez poprawnego podpisu (przynajmniej w ścieżce ręcznej i nowych wersjach); pierwszy start nowej wersji zostawia snapshot; brak duplikatów logiki pobierania.

**Ryzyka.** Podpisywanie wymaga dyscypliny przy każdym pushu na `master` (OTA działa z tej gałęzi!). Jeśli podpis nie zostanie wygenerowany, użytkownicy zostaną na starej wersji — to bezpieczne, ale trzeba to komunikować. Wdrożenie wymaga nowego APK z kluczem publicznym.

### 4.4. Konflikty synchronizacji bez cichej utraty danych

`P1 · L · Status: do zrobienia`

**Problem.** Synchronizacja podmienia cały plik `tasks.db` i rozstrzyga „nowszy wygrywa” po `db_last_change`/`exported_at`/mtime. Dwa urządzenia offline → jedno nadpisze zmiany drugiego. Snapshot przed pullem istnieje, ale użytkownik nie dostaje żadnego sygnału, że coś zginęło. `app_config.db` (kategorie, limity, ustawienia przypomnień) w ogóle nie jest synchronizowany.

**Co zrobić.**

1. **Minimalny wariant (najpierw):** zapisywać `last_synced_hash` i `last_synced_at`; przy pullu sprawdzić, czy lokalna baza zmieniła się od ostatniej synchronizacji i czy zdalna też. Jeśli obie — nie nadpisywać automatycznie, tylko pokazać banner „Wykryto konflikt. Przywróć snapshot / nadpisz lokalnie / nadpisz zdalnie”.
2. **Docelowy wariant:** eksport/import na poziomie rekordów z `updated_at` i `deleted_at` (tombstones), scalanie po `id` + znaczniku, konflikt pokazywany jako kopia „konflikt — wybierz wersję”. To duża zmiana; nie łączyć jej ze sprintem 3.
3. **Config:** opcjonalnie synchronizować wybrane klucze (bez `gist_token`!) w osobnym pliku JSON, nie całą bazę configu.
4. **Testy:** dwie bazy z różnymi zmianami od wspólnego stanu → brak cichego nadpisania, banner, oba stany odtwarzalne.

**Kryteria akceptacji.** Żadna ścieżka pulla nie nadpisuje lokalnych zmian bez ostrzeżenia; użytkownik może wrócić do stanu sprzed konfliktu jednym kliknięciem; tokeny nie są wysyłane.

### 4.5. Higiena testów, logów i dokumentacji

`P2 · S · Status: do zrobienia`

**Problem.** AGENTS.md już się rozjechał (52 testy, nie 42; ~4400 linii, nie 3800 — poprawione przy spisaniu planu). W kodzie jest sporo `except Exception: pass`, które ukrywają błędy migracji, dźwięku i UI. Brakuje testów na zachowania z tego planu.

**Co zrobić.**

1. Przejrzeć `except ...: pass` w `app_logic.py`; w miejscach krytycznych (migracje, backup, OTA, sync, UI) zamienić na log z kontekstem.
2. Dodać testy regresyjne do każdej naprawy z tego planu (kategoria z Daily, resize, rollover, postęp, wibracje z mockiem).
3. Zaktualizować AGENTS.md po każdym sprincie: nowe tabele, config keys, zmiany w `page.overlay`, nowe zależności.
4. Rozważyć rozbicie `app_logic.py` na funkcje czyste (bez `page`) testowalne bez `DummyPage` — ale **nie w trakcie sprintu funkcjonalnego**; osobne zadanie, bo OTA wymaga jednego pliku.

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
- [ ] Testy: nowe przypadki dopisane do `test_app.py` (jeden plik) + cały `pytest` zielony (67+).
- [ ] Sprawdzone na PC (950 px) i mobile (400 px) — układ, dialogi, `page.overlay`.
- [ ] Migracja schematu + eksport/import JSON, jeśli zmieniono dane.
- [ ] `push_database_async()` w każdej nowej ścieżce zapisu.
- [ ] Wersja podniesiona w `version.txt` i `FALLBACK_VERSION`.
- [ ] AGENTS.md zaktualizowany (tabele, config, pułapki, ten plan — status sprintu).
- [ ] Brak nowych cichych `except: pass`; błędy trafiają do `LOG`.
