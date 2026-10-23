# OTA — co było zepsute i co zostało naprawione

## Dlaczego aktualizacje nie działały na telefonie

Poza samym brakiem możliwości restartu procesu na Androidzie/iOS, w kodzie było
pięć błędów wykonania, które powodowały, że OTA **pobierała pliki, a potem
zgłaszała błąd** (albo wywalała cały start aplikacji):

| # | Plik | Błąd | Skutek |
|---|------|------|--------|
| 1 | `main.py` | `base_env` użyte w `load_and_run()` bez zdefiniowania | `NameError` przy każdym starcie bez świeżej aktualizacji → `main.py` kasował pobrany kod OTA i pokazywał ekran „Błąd w module aplikacji” |
| 2 | `app_logic.py` | `color=COLOR_ACCENT` (taka stała nie istnieje) | `NameError` po udanym pobraniu → komunikat „Błąd aktualizacji OTA” |
| 3 | `app_logic.py` | `textAlign="center"` zamiast `text_align=ft.TextAlign.CENTER` | `TypeError` (Flet 1.0.4) |
| 4 | `app_logic.py` | `page.controls[9].add(...)` i `{storage_folder}` | `IndexError` / `NameError` — `page.controls` ma tylko 1 element (SafeArea), a `ft.Text` nie ma metody `add` |
| 5 | `app_logic.py` | `print("[OTA] ✅ ...")` | `UnicodeEncodeError` na konsoli Windows (cp1250) → wyjątek przechwytywany jako „błąd OTA”, mimo poprawnie zapisanych plików |

Dodatkowo `get_local_version()` zwracało **pierwszą** napotkaną wersję, więc na
telefonie (kod wbudowany w APK jest tylko do odczytu) aplikacja dalej pokazywała
starą wersję i `main.py` pobierał tę samą aktualizację przy każdym starcie.

## Co robi kod teraz

1. **Brak automatycznego restartu** — na Androidzie/iOS nie da się forknąć
   procesu jak na Windows. Po pobraniu pojawia się trwała informacja
   „WYMAGANY RĘCZNY RESTART APLIKACJI” i użytkownik zamyka/otwiera aplikację.
   Nowy kod wczytuje `main.py` przy starcie (priorytet: OTA cache > dane > repo).
2. **Zapis tylko do folderów danych aplikacji** (`APP_DIR`,
   `FLET_APP_STORAGE_DATA/.master_adhd_data`, `FLET_APP_STORAGE_DATA/data`).
   Zapisujemy `app_logic_ota.py`, `version.txt` i `ota_version.txt`.
   Folder repozytorium nie jest już zaśmiecany (na PC trafiały tam pliki,
   które potem myliły `git status`).
3. **`get_local_version()` bierze najwyższą znaną wersję** ze wszystkich plików
   (`version.txt` / `ota_version.txt` w repo i w folderze danych).
4. **`main.get_installed_ota_version()` też bierze maksimum**, więc po OTA nie
   pobiera w kółko tej samej wersji.
5. **Wspólny kontekst SSL** (`get_ssl_context()`) — fallback dla Androida,
   taki sam jak w automatycznym OTA w `main.py`.
6. **Rollback OTA** — przed nadpisaniem `app_logic_ota.py` powstaje kopia
   `app_logic_ota.prev.py` (plus `ota_prev_version.txt`). Jeśli nowy kod
   wysypie się przy starcie, `main.py` automatycznie przywraca poprzednią
   wersję zamiast kasować plik i pokazywać ekran błędu.
7. **Blokada wadliwej wersji** — numer wersji, która wywaliła aplikację, trafia
   do `ota_failed_version.txt` i nie jest pobierany ponownie (koniec pętli
   „pobierz → crash → pobierz to samo”). Wydanie poprawki wymaga podbicia numeru.
8. **Brak pliku `.prev`** — wadliwy `app_logic_ota.py` jest wtedy odkładany
   jako `app_logic_ota.py.bad`, a aplikacja startuje z kodu wbudowanego.

## Bezpieczeństwo danych (poza OTA)

- Każdy zapis idzie przez `sqlite3.Connection.backup()` (spójna kopia również
  przy WAL), a nie przez `shutil.copy2`.
- `db_connection()` zawsze zamyka połączenie i ma `busy_timeout`, więc koniec
  z „database is locked” i zablokowanymi plikami na Windows.
- Snapshoty (`snapshots/tasks_*.db`) powstają przed każdym pobraniem z chmury,
  importem, końcem dnia i czyszczeniem archiwum. Rotacja: 10 najnowszych.
- Startowy pull (`force=False`) **nie nadpisuje nowszych danych lokalnych**:
  porównuje czas pliku w trybie folderowym oraz `exported_at` z
  `db_last_change` w trybie Gist.
- Minutnik liczy czas ze znacznika `timer_end_ts` w configu — przeżywa
  zminimalizowanie aplikacji i restart procesu.

## Jak wydać aktualizację

1. `version.txt` w repo ustaw na nowy numer (obecnie `1.6.17`).
2. Wypchnij `app_logic.py`, `main.py`, `test_app.py` i `version.txt` na `master`.
3. GitHub Actions zbuduje nowe APK, a zainstalowane aplikacje pobiorą nowy kod
   przy następnym uruchomieniu (lub po kliknięciu „Sprawdź i wymuś aktualizację”).
4. Na telefonie: po komunikacie o pobraniu **zamknij aplikację z listy
   uruchomionych** i otwórz ją ponownie.

## Weryfikacja

- `pytest test_app.py` → 25/25 testów przechodzi.
- Testy pokrywają m.in.: starsza chmura nie nadpisuje nowszej bazy (folder i
  Gist), rotację snapshotów, wykrywanie uszkodzonej bazy, snapshot przy
  „Końcu dnia”, rollback OTA oraz to, że start aplikacji nie przesuwa
  znacznika czasu danych.
- Test symulowanego kliknięcia przycisku OTA (bez sieci): pliki zapisane,
  brak komunikatu o błędzie, poprawna wersja wykryta.
- Test startu `main.py` z `DISABLE_OTA=1`: brak `NameError`, brak ekranu błędu.
