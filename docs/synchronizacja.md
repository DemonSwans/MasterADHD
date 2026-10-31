# Synchronizacja i kopie zapasowe

Master ADHD działa w pełni offline. Synchronizacja jest opcjonalna i włączana w
Ustawieniach. Służy do przenoszenia danych między urządzeniami i do backupu.

## Spis treści

- [Tryby synchronizacji](#tryby-synchronizacji)
- [Jak działa zapis](#jak-działa-zapis)
- [Jak działa odczyt (pull)](#jak-działa-odczyt-pull)
- [Konflikty](#konflikty)
- [Kopie zapasowe i snapshoty](#kopie-zapasowe-i-snapshoty)
- [Import i eksport JSON](#import-i-eksport-json)
- [Rozwiązywanie problemów](#rozwiązywanie-problemów)

---

## Tryby synchronizacji

Klucz `sync_mode` w konfiguracji. Domyślnie `none`.

| Tryb | Nazwa w UI | Jak działa |
|---|---|---|
| `none` | Tryb lokalny | Dane tylko na urządzeniu. |
| `folder` | Dysk lokalny | Zapis do wskazanego folderu (np. OneDrive / dysk sieciowy). **Natychmiastowy.** |
| `gist` | Chmura Gist | GitHub Gist. Wymaga tokenu (uprawnienie `gist`) i ID gist-a (puste = utwórz nowy). Zapis z debounce 1,5 s. |
| `ha_server` | Serwer HA | Home Assistant pod `ha_url` (POST wysyła, GET pobiera `tasks.db`). |

W repo **nie ma** serwera HA — `server_sync.py` został usunięty. Tryb `ha_server`
zakłada, że własny endpoint odbierze i wyda plik bazy.

Zmiana trybu czyści punkty odniesienia (`_reset_sync_markers`) i zwykle wymusza
pobranie (`force=True`), żeby nie zgubić danych z chmury.

---

## Jak działa zapis

- **Każda funkcja zapisująca dane kończy się `push_database_async()`.** Nowa
  funkcja DB bez tego nie będzie się synchronizować.
- `push_database_async()`:
  - `folder` — zapis od razu (synchronicznie, szybki),
  - `gist` / `ha_server` — jeden wątek z **debounce 1,5 s** (kilka zmian pod
    rząd to jedna wysyłka),
  - podczas startowego pull-a push jest **odraczany** (`_PUSH_DEFERRED`).
- Stan zapisu trafia do badge’a w UI: „Zapisywanie…”, „Zapisano HH:MM”,
  „Błąd zapisu”, „Konflikt sync”.

---

## Jak działa odczyt (pull)

`pull_database(force=False, resolve_conflict=False)`:

- **Nie nadpisuje nowszych danych lokalnych.** Porównuje: `db_last_change`
  lokalnie z `exported_at` (Gist), `Last-Modified` (HA) lub mtime (folder).
- `force=True` pobiera zawsze (ręczna synchronizacja, zmiana trybu).
- Domyślnie po pobraniu wywoływane jest `ensure_schema_upgrade()`, więc starsza
  kopia z chmury dostaje brakujące kolumny/tabele zanim widok ją przeczyta.

Startowy pull (`force=False`) działa w tle i **blokuje wysyłkę**, dopóki nie
wiadomo, co jest w chmurze — żadna pusta baza nie nadpisze danych.

---

## Konflikty

Konflikt występuje **tylko** gdy jednocześnie:

1. istnieje punkt odniesienia (`last_synced_hash` z ostatniej udanej synchronizacji),
2. zmieniła się **i** lokalna, **i** zdalna baza,
3. skróty treści obu baz są różne.

Porównywany jest **skrót treści bazy** (liczba wierszy), nie surowe bajty —
dzięki temu `VACUUM` czy przepisanie pliku nie udają konfliktu.

Zachowanie przy konflikcie:

- Dane **nie są** nadpisywane.
- `pull_database` zwraca „Wykryto konflikt synchronizacji — wybierz wersję w panelu”.
- `push_database` odmawia wysyłki („Konflikt synchronizacji — najpierw wybierz wersję”).
- Stan zapisywany jest w configu `sync_conflict` i pokazywany jako banner w
  Daily/Master z trzema przyciskami:

| Wybór | Co robi |
|---|---|
| **Zachowaj lokalną** (`keep_local`) | Wysyła lokalną wersję do chmury. |
| **Pobierz zdalną** (`keep_remote`) | Snapshot `pre_conflict_remote`, potem pobiera zdalną (`force=True`). |
| **Przywróć snapshot** (`restore_snapshot`) | Wraca do ostatniej kopii bezpieczeństwa. |

Do tego przycisk „Cofnij” zamykający banner bez zmian.

> **Ważne dla testów i debugowania:** `_detect_conflict()` milczy, dopóki
> `last_synced_hash` jest pusty. Pierwsza synchronizacja dopiero go ustawia
> (`_mark_synced`). W trybie `folder` każdy zapis od razu pushuje i odświeża
> `last_synced_hash`, więc konflikt trudno wywołać — do testów ustaw
> `sync_mode="none"` na czas zmian lokalnych.

Kodowanie rekordów/tombstones oraz synchronizacja `app_config.db` to nadal
przyszłość (patrz backlog w [`../AGENTS.md`](../AGENTS.md)).

---

## Kopie zapasowe i snapshoty

- **Snapshoty** (`snapshots/tasks_<tag>_<czas>.db`) powstają automatycznie
  przed każdą operacją destrukcyjną: pobraniem z chmury, importem, przełomem
  dnia, czyszczeniem archiwum, przywróceniem kopii.
- Rotacja: **10 najnowszych**, sortowanie po czasie modyfikacji.
- Karta „Kopia Zapasowa i Bezpieczeństwo” w Ustawieniach pokazuje liczbę
  snapshotów i pozwala:
  - 📥 **Eksportować bazę do JSON** (czytelna kopia),
  - 🛡️ **Przywrócić ostatni snapshot** (z przed operacji destrukcyjnej),
  - 📄 **Eksportować logi**,
  - 📤 **Importować z JSON** (dopisz lub zastąp),
  - 📂 użyć najnowszego backupu jednym kliknięciem.

Kopie robione są przez `sqlite3.Connection.backup()` (spójne także przy WAL).

---

## Import i eksport JSON

- Eksport przenosi: zadania, rutyny, historię rutyn, archiwum i konfigurację.
- Import w trybie „dopisz” dokłada brakujące, w trybie „zastąp” podmienia dane.
- Starsze pliki bez `routine_log` importują się bez problemu (klucz opcjonalny);
  `routine_id` są mapowane na nowe identyfikatory.

Import zastępujący dane ma dialog potwierdzenia (jest nieodwracalny) i tworzy
snapshot.

---

## Rozwiązywanie problemów

| Objaw | Prawdopodobna przyczyna / co zrobić |
|---|---|
| Badge „Błąd zapisu” | Brak sieci / zły token / brak dostępu do folderu. Sprawdź Ustawienia → synchronizacja. |
| Badge „Konflikt sync” | Rozstrzygnij w panelu (banner w Daily/Master). |
| Dane z chmury nie pobierają się | Lokalne są nowsze — pull `force=False` ich nie nadpisze. Użyj ręcznej synchronizacji. |
| „no such column …” po pobraniu | Nie wołano `ensure_schema_upgrade()` po podmianie bazy — zgłoś jako błąd. |
| Chcesz zacząć od nowa | Eksport JSON, potem „Tryb lokalny” i czyszczenie/przywracanie snapshotu. |
