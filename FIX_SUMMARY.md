# MasterADHD — podsumowanie zmian (v1.6.17)

## 1. Warstwa danych (app_logic.py)
- `db_connection()` — jedno miejsce otwierania bazy: `busy_timeout=5000`,
  `RLock`, commit/rollback i **zawsze `close()`** (koniec „database is locked”
  i zablokowanych plików na Windows).
- `backup_database()` — spójne kopie przez `sqlite3.Connection.backup()`
  zamiast `shutil.copy2` (kopiuje też dane z pliku `-wal`).
- `check_db_integrity()` + `ensure_db_health()` — przy uszkodzonej bazie plik
  jest odkładany jako `.corrupt_*`, a aplikacja próbuje przywrócić snapshot.
- `_touch_db_change()` — znacznik `db_last_change` przesuwa się **tylko przy
  realnej zmianie danych**; samo `init_db()` go nie rusza, więc start aplikacji
  nie blokuje pobierania z chmury.

## 2. Synchronizacja

- `pull_database(force=False)` na starcie **nie nadpisuje nowszych danych**:
  - folder: porównanie mtime pliku i `folder_push_mtime`,
  - Gist: `exported_at` z `backup_tasks.json` vs `db_last_change`,
  - HA: nagłówek `Last-Modified`.
- `force=True` (ręczna synchronizacja / zmiana trybu) pobiera zawsze.
- Zapis: folder synchronicznie (szybki), Gist/HA przez jeden wątek z debounce
  1,5 s. Stan zapisu trafia do badge’a („Zapisywanie…”, „Zapisano HH:MM”,
  „Błąd zapisu”).
- Startowy pull działa w tle i **blokuje wysyłkę**, dopóki nie wiadomo, co jest
  w chmurze (żadna pusta baza nie nadpisze danych).
- `flush_pending_sync()` przy zamykaniu czeka także na zapis w fazie debounce.

## 3. Snapshoty i kopie

- Snapshoty w `snapshots/tasks_<tag>_<czas>.db`, rotacja 10 najnowszych
  (sortowanie po mtime, nie po nazwie).
- Snapshot przed: pobraniem z chmury, importem JSON, „Końcem dnia”,
  czyszczeniem archiwum i przywróceniem kopii.
- Eksport JSON, import JSON (dopisz/zastąp), eksport logów, przywracanie
  snapshotu — wszystko z poziomu Ustawień.
- Archiwum sukcesów ograniczone do 2000 wpisów.

## 4. Minutnik

- Liczony od `timer_end_ts` (`time.time()`), bez dryfu starego `sleep(1)`.
- Przeżywa zminimalizowanie aplikacji i restart procesu; pauza też jest
  zapamiętywana (`timer_left`).
- Po restarcie z aktywnym minutnikiem wątek wznawia się automatycznie.

## 5. OTA i main.py

- Rollback: przed nadpisaniem `app_logic_ota.py` powstaje
  `app_logic_ota.prev.py` + `ota_prev_version.txt`.
- Błąd nowego kodu → automatyczny powrót do poprzedniej wersji.
- Wadliwa wersja trafia do `ota_failed_version.txt` i nie jest pobierana
  ponownie (koniec pętli crash → pobierz to samo).
- Brak `.prev` → wadliwy plik ląduje jako `app_logic_ota.py.bad`.
- OTA nie zapisuje już niczego do folderu repozytorium.

## 6. UI

- „Koniec dnia” ma dialog potwierdzenia; operacja tworzy snapshot.
- Podpowiedź „Nowy dzień!” z przyciskiem zamknięcia poprzedniego dnia.
- Dodawanie własnej strefy/kategorii; edycja nie gubi kategorii spoza listy.
- Karta backupu: liczba snapshotów, eksport JSON, przywracanie, logi, import.
- Karta OTA pokazuje wersję wbudowaną i aktywną (OTA).
- Informacja, że dźwięki systemowe działają tylko na Windows.

## 7. Wykończenie (v1.6.17)

- Usunięto tag na karcie zadania z samym słoneczkiem (`Daily` + ikona `WB_SUNNY`).
  Użytkownik nie chce tego oznaczenia w kartach zadań — flaga `is_daily` nadal
  kontroluje listę, ale ikoniczny tag zniknął.

## 8. Testy

- `pytest test_app.py` → **25/25**.
- Nowe testy: starsza chmura nie nadpisuje nowszej bazy (folder + Gist),
  rotacja snapshotów, wykrywanie uszkodzonej bazy, snapshot przy końcu dnia,
  rollback OTA, brak przesuwania `db_last_change` przy starcie.
