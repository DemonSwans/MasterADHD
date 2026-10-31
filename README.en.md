# Master ADHD

A personal task manager for people with ADHD — **Python + Flet + SQLite**, running
on Windows and Android (APK). The UI and source comments are in Polish; the theme
is called "Dark Slate". This is an English summary of the main documentation —
the full docs live in [`docs/`](docs/README.md) (Polish).

> 🇵🇱 Polish version: [`README.md`](README.md)

---

## What it is

Master ADHD is built around one idea: **fewer decisions, less guilt, more
finished things**. Instead of long lists and "streak lost" counters it gives you
a single screen for today, a short start/end-of-day ritual, and a success journal
that turns completed tasks into visible progress.

## Key features

| Area | What it does |
|---|---|
| **Daily** | Today's tasks, routines, progress bar and focus timer. One-line thought dump. |
| **Master** | Full task database: search, filters (zone, priority, status, deadline), deadline sorting and a "🕰 Long untouched" filter. |
| **Success Journal** | Archive of completed tasks — nothing vanishes for good. |
| **Priorities A / B / C** | Colour-coded weight. A cap of "A" tasks on Daily (3 recommended, or 4) protects focus. |
| **Routines** | Daily or weekday-specific habits with history and *soft* streaks (no "you lost your streak"). |
| **Subtasks** | Break a task into ordered steps with up/down arrows. |
| **Deadlines** | Optional date, Master sorting/filtering, a calm "Upcoming deadlines" card (14/7/3 days ahead). |
| **Time estimates** | 5/15/30/60/custom minutes, with a daily summary. |
| **Inbox + 2-minute triage** | Thought dumps go to `📥 Inbox`; one flow moves them to a zone, priority, Daily or a deadline. |
| **Day rollover** | A deliberate ritual: keep / move to Master / remove — instead of silently dropping tasks. |
| **Focus timer** | 5/15/25 min presets, a global mini-timer visible in every view. |
| **Haptics & sound** | Vibration on Android, system sound on Windows on success and timer end. |
| **Undo** | Instant delete of a task/subtask with a "Cofnij" snackbar. |
| **Sync** | Local folder, GitHub Gist (free) or Home Assistant, with a conflict-resolution panel. |
| **Backups** | Automatic snapshots before destructive actions, JSON export/import, snapshot restore, log export. |
| **OTA** | Code updates pulled from GitHub without reinstalling the APK, with rollback. |

## Requirements

- **PC:** Python 3.11+, `flet` (CI pins `flet==1.0.4`), `pytest` for tests.
- **Android:** the prebuilt APK from Releases.

There is **no** `requirements.txt`/`pyproject.toml`. The code targets the
**Flet 1.x** API (`ft.Colors.*`, `ft.Icons.*`, `ft.Button`, `ft.Alignment(x, y)`,
`ft.Padding.symmetric(...)`); `ft.colors` and `ft.ElevatedButton` no longer exist
in 1.0.4.

## Run on PC

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install flet pytest
.\.venv\Scripts\flet.exe run main.py
```

Always start through `main.py` — it handles OTA and the splash screen first.

## Install on Android

1. Open [Releases](https://github.com/DemonSwans/MasterADHD/releases) and download
   `MasterADHD.apk`.
2. Allow installation from unknown sources and install.
3. The build code grows with every release, so a newer APK upgrades the old one
   in place.

Later updates arrive via OTA — reinstalling the APK is only needed when `main.py`
itself changes (rare).

## Views

There are four views. On PC you switch them in the left sidebar; on phone
(< 650 px wide) in the bottom navigation.

- **Daily** — today's tasks and routines, a thought-dump field, hybrid day
  progress, the calm deadlines card, and the day-rollover prompt.
- **Master** — all tasks with search, filters, deadline sorting, the
  "🕰 Long untouched" filter, and the Inbox triage section.
- **Successes** — the archive of completed tasks.
- **Settings** — OTA, sound/vibration, deadlines, Daily add mode, day rollover,
  backup, routines, A-tasks limit, and sync options.

Full feature description: [`docs/funkcje.md`](docs/funkcje.md) (Polish).

## Data location

| Platform | Folder |
|---|---|
| Windows | `%USERPROFILE%\.master_adhd_data\` |
| Android | `FLET_APP_STORAGE_DATA/.master_adhd_data` |

It contains `tasks.db`, `app_config.db`, `master_adhd.log`, `snapshots/`, and the
OTA files. The app never reads databases from the repository.

## Sync & backups

Choose a mode in Settings (`sync_mode`, default `none`): `folder`, `gist` or
`ha_server`. Every write ends with `push_database_async()`; a startup pull never
overwrites newer local data. When both local and remote changed, the app does
**not** overwrite anything — it shows a banner to pick *keep local* / *fetch
remote* / *restore snapshot*. Data safety uses `sqlite3.Connection.backup()`
snapshots (last 10 kept). See [`docs/synchronizacja.md`](docs/synchronizacja.md).

## OTA

`app_logic.py` updates itself from
`https://raw.githubusercontent.com/DemonSwans/MasterADHD/master`. Only newer,
compilable, non-blocked code runs; the previous version is kept as `.prev` and
bad versions are blacklisted. A failure triggers automatic rollback. See
[`docs/ota.md`](docs/ota.md).

> ⚠️ Every push of `app_logic.py` or `version.txt` to `master` reaches users via
> OTA, regardless of the APK release.

## Development & tests

```powershell
python -m pytest test_app.py -q                     # 109 tests, ~26 s
python -m pytest test_app.py -q -k test_tasks_crud  # single test
```

All tests live in the single file `test_app.py`. Conventions and the definition
of done: [`docs/rozwoj.md`](docs/rozwoj.md), [`docs/testy.md`](docs/testy.md) and
[`AGENTS.md`](AGENTS.md).

CI (`.github/workflows/build.yml`) runs on push to `master`:
`pytest` → `flet build apk` → Release `latest`.

## Repository layout

```
main.py                 # entrypoint: OTA, splash, module choice, rollback
app_logic.py            # all logic: SQLite, config, sync, OTA, Flet UI
test_app.py             # 109 tests (single file)
version.txt             # current version (1.7.0)
assets/icon.png         # app icon
.github/workflows/      # CI: tests -> APK -> Release
docs/                   # detailed documentation (Polish)
AGENTS.md               # internal technical notes for AI agents
```

## Licence

Personal project; no licence file is included in the repository.
