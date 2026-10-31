# Architektura

Master ADHD to jedna aplikacja Flet z całym interfejsem i logiką w
`app_logic.py`, uruchamiana przez cienki entrypoint `main.py`. Ten dokument
opisuje warstwy, przepływ sterowania i twarde kontrakty, które łatwo przypadkiem
złamać.

## Spis treści

- [Mapa plików](#mapa-plików)
- [Warstwy](#warstwy)
- [Przepływ startu](#przepływ-startu)
- [UI: struktura i kontrakty](#ui-struktura-i-kontrakty)
- [Wątki i aktualizacje UI](#wątki-i-aktualizacje-ui)
- [Motyw i dostępność](#motyw-i-dostępność)

---

## Mapa plików

| Plik | Odpowiedzialność |
|---|---|
| `main.py` | Entrypoint: OTA z GitHuba, splash, wybór modułu (`app_logic_ota.py` albo wbudowany `app_logic`), rollback, ekran błędu. Na końcu re-eksport symboli `app_logic` **bez** `main`. |
| `app_logic.py` | ~5300 linii: schemat i operacje SQLite, konfiguracja, snapshoty, backup/import JSON, synchronizacja (Gist/HA/folder), logowanie, instalacja/aktywacja OTA oraz cały interfejs Flet w `main(page)`. |
| `test_app.py` | 109 testów (jeden plik). |
| `version.txt` | Aktualna wersja; musi być równa `FALLBACK_VERSION` w `app_logic.py`. |
| `.github/workflows/build.yml` | CI: pytest → APK → Release `latest`. |
| `assets/icon.png` | Ikona aplikacji. |
| `docs/` | Ta dokumentacja. |
| `AGENTS.md` | Zwięzła dokumentacja techniczna dla agentów. |

Artefakty tylko lokalne (gitignore): `app_logic_ota.py`, `ota_version.txt`,
`*.db`, `snapshots/`, `test_sync_folder/`.

`FIX_SUMMARY.md` i `README_OTA_FIX.md` są historyczne (stan 1.6.17) — nieaktualne.

---

## Warstwy

`app_logic.py` czyta się od góry jako warstwy, od najbardziej niezawodnych:

1. **Infrastruktura** — `get_app_dir`, ścieżki (`LOCAL_DB`, `CONFIG_DB`, `LOG_FILE`),
   logowanie do pliku, odczyt wersji, kontekst SSL.
2. **Dane** — `db_connection`, `init_db`, `ensure_schema` / `ensure_schema_upgrade`,
   operacje CRUD, migracje, JSON export/import.
3. **Bezpieczeństwo danych** — integralność, `backup_database`, snapshoty.
4. **Synchronizacja** — `pull_database` / `push_database`, kolejka z debounce,
   wykrywanie i rozstrzyganie konfliktów.
5. **OTA** — `install_ota_code`, `activate_downloaded_ota`, rollback.
6. **Logika domenowa (czyste helpery)** — terminy, szacunki, rutyny (paski i
   serie), postęp, kontrast kolorów. Funkcje bez efektów ubocznych, łatwe do testów.
7. **UI Flet** — wszystko w `main(page)` i zagnieżdżonych funkcjach.

Warstwy 1–6 można testować bez Flet-a; UI testowany jest przez `DummyPage`.

---

## Przepływ startu

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

Kluczowa zasada: **każda akcja modyfikująca dane przechodzi przez
`db_connection(write=True)`, a następnie `push_database_async()`**. To jedna
ścieżka dla wszystkich zapisów.

---

## UI: struktura i kontrakty

Struktura kontrolek i `page.overlay` to **kontrakt testów** — nie zmieniaj jej
pochopnie.

### Korzeń

```
page.controls[0]              # SafeArea
  └─ .content                 # root_layout
       └─ .content            # PC: Row(sidebar, content_area)
                              # mobile: Column(content_area, mini_timer, bottom_nav)
```

- `content_area` to `Row[2]` na PC i `Column[0]` na mobile.
- Pasek boczny (PC): `controls[1]` Daily, `[2]` Master, `[3]` Sukcesy,
  dalej mini-minutnik, numer wersji, badge, Ustawienia.
- Dolna nawigacja (mobile): `[0]` Daily, `[1]` Master, `[2]` Sukcesy,
  `[3]` Ustawienia.
- `is_mobile()` = szerokość < **650 px** (`page.width` lub `page.window.width`).
  Testy używają 950 (PC) i 400 (mobile).

### Kontrolki poziomu modułu `main(page)`

`content_column`, `content_area`, `tasks_column`, `routines_row`, `title_text`,
`status_text`, `warning_text`, `progress_bar`, `progress_text`, oraz kontrolki
mini-minutnika i nawigacji. `refresh_view()` przebudowuje zawartość
`content_column` zależnie od `current_view`.

### Kolejność na Daily

`content_column.controls`: `[2]` karta minutnika, `[3]` karta postępu. Nie
wstawiaj nowych elementów przed nimi (jest na to test).

### Dialogi (`page.overlay`, 11 pozycji)

| Indeks | Dialog |
|---|---|
| `[3]` | edycja zadania |
| `[4]` | usuwanie zadania |
| `[5]` | edycja podkroku |
| `[7]` | „Koniec dnia” (historyczny kontrakt) |
| `[9]` | edycja rutyny |
| `[10]` | przełom dnia |

Nowe okna dopisuj **na końcu** listy.

---

## Wątki i aktualizacje UI

- **Minutnik** działa w osobnym wątku i liczy czas ze znacznika `timer_end_ts`,
  więc przeżywa minimalizację i restart.
- **Synchronizacja** ma jeden wątek roboczy z debounce 1,5 s.
- Aktualizacje z wątków **nie dotykają `page` bezpośrednio**. Trafiają do
  kolejki: `schedule_ui_update(page, callback)` → `_drain_ui_queue()` /
  `flush_ui_queue(page)`. To jeden bezpieczny punkt aktualizacji UI.
- `on_page_resize` reaguje **tylko** na próg `is_mobile()` (650 px): porównuje
  nowy stan z `last_is_mobile`. Nie dodawaj debounce ani `page.run_task` —
  test liczy wywołania `refresh_view`, a zmiana 950→949 nie może przebudowywać
  widoku.
- `flush_pending_sync(timeout=…)` jest wołane przed podmianą UI przez OTA i przy
  zamykaniu, żeby nie zgubić zapisu w fazie debounce.

---

## Motyw i dostępność

Stałe kolorów (`app_logic.py`) — używaj ich, nie literalnych hexów:

| Stała | Wartość | Rola |
|---|---|---|
| `COLOR_BG_MAIN` | `#0d1117` | tło główne |
| `COLOR_BG_CARD` | `#161b22` | karty |
| `COLOR_BG_SURFACE` | `#21262d` | powierzchnie |
| `COLOR_BG_BORDER` | `#30363d` | obramowania |
| `COLOR_PRIO_A` | `#ef4444` | priorytet A |
| `COLOR_PRIO_B` | `#f59e0b` | priorytet B |
| `COLOR_PRIO_C` | `#3b82f6` | priorytet C |
| `COLOR_SUCCESS` | `#10b981` | sukces |
| `COLOR_TEXT_PRIMARY` | `#f0f6fc` | tekst główny |
| `COLOR_TEXT_SECONDARY` | `#8b949e` | tekst pomocniczy |
| `COLOR_SUN_ACTIVE` / `COLOR_SUN_INACTIVE` | `#facc15` / `#6b7280` | słońce Daily |

- **Dotyk:** `TOUCH_TARGET_MIN = 44`. W `refresh_view` lokalny helper
  `_touch(default)` zwraca 44 na telefonie, a na PC wartość domyślną. Objęte nim
  są akcje-ikony w kartach, nagłówek, szybkie dodawanie i przyciski minutnika.
- **Kontrast:** `contrast_ratio(fg, bg)` (WCAG 2.1) i
  `readable_text_color(bg, preferred, fallback, minimum=4.5)`. Metadane kart
  używają drugiego z nich — na ciemnym tle zostaje `COLOR_TEXT_SECONDARY`, a
  przy zmianie tła degraduje się do `COLOR_TEXT_PRIMARY`.
- **Mini-minutnik:** jedno źródło stanu, dwa wystąpienia (`_build_mini_timer`):
  PC w pasku bocznym, mobile w `mini_timer_mob_wrapper`. Chip ma pauzę/wznowienie
  i reset (działa z każdego widoku), a jego tło wraca do Daily.
