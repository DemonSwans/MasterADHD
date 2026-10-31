# Dane i schemat bazy

Wszystkie dane leżą w prywatnym folderze aplikacji:

| Platforma | Folder |
|---|---|
| Windows | `%USERPROFILE%\.master_adhd_data\` |
| Android | `FLET_APP_STORAGE_DATA/.master_adhd_data` |

Gdy nie da się utworzyć folderu, aplikacja schodzi do `"."` (bieżący katalog) i
loguje problem — nigdy nie wywala się z tego powodu.

## Spis treści

- [Pliki](#pliki)
- [Baza `tasks.db`](#baza-tasksdb)
- [Baza `app_config.db`](#baza-app_configdb)
- [Klucze konfiguracji](#klucze-konfiguracji)
- [Zapis i spójność](#zapis-i-spójność)
- [Migracje schematu](#migracje-schematu)
- [Snapshoty](#snapshoty)
- [Eksport / import JSON](#eksport--import-json)

---

## Pliki

```
.master_adhd_data/
├── tasks.db                  # dane zadań, rutyn, archiwum
├── app_config.db             # konfiguracja
├── master_adhd.log           # log aplikacji
├── snapshots/
│   └── tasks_<tag>_<czas>.db # kopie (max 10)
├── app_logic_ota.py          # pobrany kod OTA (jeśli nowszy)
├── app_logic_ota.prev.py     # poprzednia wersja OTA
├── ota_version.txt           # wersja pobranego kodu
├── ota_prev_version.txt
├── ota_failed_version.txt    # wadliwe wersje (blokada ponownego pobrania)
└── ota.log
```

---

## Baza `tasks.db`

### `tasks`

| Kolumna | Typ | Opis |
|---|---|---|
| `id` | INTEGER PK | Identyfikator. |
| `title` | TEXT | Treść zadania. |
| `is_daily` | INTEGER | 1 = na liście Daily, 0 = tylko Master. |
| `is_done` | INTEGER | 1 = ukończone. |
| `priority` | TEXT | `A` / `B` / `C`. |
| `subtasks` | TEXT (JSON) | Lista kroków: `[{"title": str, "is_done": 0/1}, ...]`. |
| `category` | TEXT | Strefa; domyślnie `Ogólne`. |
| `deadline` | TEXT | Data ISO `YYYY-MM-DD` lub `NULL`. |
| `created_at` | TEXT | ISO z sekundami. |
| `updated_at` | TEXT | ISO z sekundami; podnoszony przy każdej modyfikacji. |
| `estimate_min` | INTEGER | Szacowany czas w minutach lub `NULL`. |

**Kolejność `get_tasks()`** jest kontraktem testów:
`0 id, 1 title, 2 is_daily, 3 is_done, 4 priority, 5 subtasks, 6 category, 7 deadline`.
Nowe pola (`created_at`/`updated_at`, `estimate_min`) mają **osobne gettery**,
żeby nie ruszać tej kolejności (`get_task_timestamps`, `get_task_estimate`,
`get_estimates_map`). Nowe kolumny dopisuj na końcu `SELECT` albo dodawaj getter.

### `routines`

| Kolumna | Typ | Opis |
|---|---|---|
| `id` | INTEGER PK | |
| `title` | TEXT | Nazwa rutyny. |
| `is_done` | INTEGER | Szybki stan bieżący (resetowany przy przełomie dnia). |
| `routine_days` | TEXT (JSON) | Lista intów `0..6` (0 = poniedziałek, konwencja `datetime.weekday()`); `[]` = codziennie. |

### `routine_log`

| Kolumna | Typ | Opis |
|---|---|---|
| `id` | INTEGER PK | |
| `routine_id` | INTEGER | |
| `date` | TEXT | ISO `YYYY-MM-DD`. |
| `done` | INTEGER | 0/1. |

Ograniczenie `UNIQUE(routine_id, date)` — wpis dnia robiony jako UPSERT.

Historię kasuje usunięcie rutyny (`delete_routine_db`). Reset `is_done` (przełom
dnia, „Koniec dnia”) **nie** kasuje historii.

### `completed_archive`

Archiwum ukończonych zadań (Dziennik Sukcesów). Trzymane do
`ARCHIVE_LIMIT = 2000` wpisów; starsze przycinane (`prune_archive`).

---

## Baza `app_config.db`

Jedna tabela `config(key TEXT, value TEXT)`. Wartości tekstowe; liczby i JSON
są serializowane.

- Zapis: `set_config(key, value)`, odczyt: `get_config(key, default)`.

---

## Klucze konfiguracji

| Klucz | Znaczenie |
|---|---|
| `sync_mode` | `none` (domyślnie) / `folder` / `gist` / `ha_server`. |
| `sync_folder`, `gist_token`, `gist_id`, `ha_url` | Adresy/uwierzytelnianie synchronizacji. |
| `last_synced_hash`, `last_synced_at` | Punkt odniesienia ostatniej udanej synchronizacji (wykrywanie konfliktów). |
| `sync_conflict` | JSON z oczekującym konfliktem (`detected_at`, `label`, `local_hash`, `remote_hash`, `choice_pending`). |
| `folder_push_mtime`, `last_push_at`, `db_last_change` | Znaczniki czasu do rozstrzygania „kto nowszy”. |
| `max_a_limit` | Limit zadań „A” na Daily (3/4). |
| `daily_add_mode` | `drag` / `tap` (jak trafia na Daily). |
| `sound_enabled` | `1`/`0` — dźwięk i wibracja. |
| `day_rollover_mode` | `ask` / `auto_keep` / `auto_master` (`ask` domyślnie; śmieci normalizowane do `ask`). |
| `last_reset_date` | Data ostatniego przełomu dnia. |
| `timer_running`, `timer_end_ts`, `timer_left`, `timer_total` | Stan minutnika (przeżywa restart). |
| `deadline_reminders_enabled` | Czy pokazywać kartę „Nadchodzące terminy”. |
| `deadline_reminder_days` | Próg: `14` / `7` / `3` dni. |
| `deadline_reminder_repeat_day_before` | `1`/`0` — przypomnienie dzień przed. |
| `deadline_card_hidden_date` | Data, na którą ukryto kartę terminów („Ukryj na dziś”). |
| `master_sort_mode` | `DEFAULT` / `DEADLINE`. |
| `master_deadline_filter` | `ALL` / `WEEK` / `OVERDUE`. |
| `master_stale_only` | `0`/`1` — filtr „🕰 Dawno nietknięte”. |

---

## Zapis i spójność

- Zapis **wyłącznie** przez `db_connection(write=True)`: WAL, `busy_timeout=5000`,
  jeden `RLock`, zawsze `close()`.
- `write=True` na `LOCAL_DB` przesuwa `db_last_change` — bez tego zmiana nie jest
  widoczna dla rozstrzygania konfliktów synchronizacji.
- `init_db()` **nie** rusza `db_last_change` (jest test) — inaczej start blokuje
  pobieranie z chmury.
- Kopie przez `sqlite3.Connection.backup()`, nigdy `shutil.copy2`.
- `check_db_integrity()` + `ensure_db_health()`: uszkodzoną bazę odkłada jako
  `.corrupt_*` i próbuje przywrócić snapshot.

---

## Migracje schematu

`ensure_schema(conn)` (z `PRAGMA table_info` + `ALTER TABLE`) dodaje brakujące
kolumny: `tasks.subtasks`, `tasks.category`, `tasks.deadline`,
`tasks.created_at`, `tasks.updated_at`, `tasks.estimate_min`,
`routines.routine_days`, tworzy tabelę `routine_log` i uzupełnia znaczniki
czasu starych wierszy (`_backfill_task_timestamps`).

Wołają ją: `init_db()`, `export_database_to_json_dict()`,
`import_database_from_json_dict()`, `add_routine_db()`, `update_routine_db()`,
`toggle_routine_db()`.

> **Reguła:** każde miejsce, które podmienia plik `LOCAL_DB` bajtami innej bazy
> (pull Gist/HA/folder, `_restore_db_from_bytes`, `restore_pre_sync_backup`),
> **musi** po tym wywołać `ensure_schema_upgrade()`. Inaczej stara kopia bez
> nowej kolumny wywala widok (`no such column` / `no such table: routine_log`).

---

## Snapshoty

- Ścieżka: `snapshots/tasks_<tag>_<czas>.db` obok bazy.
- Rotacja: `SNAPSHOT_LIMIT = 10`, sortowanie po mtime (nie po nazwie).
- Powstają przed: pobraniem z chmury (`pre_sync`), przełomem dnia
  (`pre_day_rollover`), „Końcem dnia”, importem, czyszczeniem archiwum,
  przywróceniem kopii (`pre_conflict_remote`, `pre_sync`).
- Funkcje: `make_local_snapshot`, `list_snapshots`, `prune_snapshots`,
  `restore_pre_sync_backup`, `restore_snapshot_file`.

---

## Eksport / import JSON

- `export_database_to_json_dict()` / `export_database_to_json_file()` — czytelna
  kopia: zadania, rutyny, historia rutyn, archiwum, konfiguracja.
- `import_database_from_json_dict(data, replace_existing=False)` — import
  dopisujący lub zastępujący. Starsze `routine_id` są mapowane na nowe
  `lastrowid`.
- `latest_json_backup()` — najnowszy plik kopii (przycisk „Użyj najnowszego backupu”).

Klucz `routine_log` jest **opcjonalny** — import starszych plików bez historii
działa normalnie.
