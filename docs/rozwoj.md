# Zasady rozwoju

Konwencje i reguły obowiązujące przy zmianach w Master ADHD. Dokument jest
rozwinięciem `AGENTS.md` i definicji ukończenia.

## Spis treści

- [Środowisko i uruchamianie](#środowisko-i-uruchamianie)
- [Styl kodu](#styl-kodu)
- [Zmiany schematu i zapisu](#zmiany-schematu-i-zapisu)
- [Wersjonowanie](#wersjonowanie)
- [Dokumentacja](#dokumentacja)
- [CI i wydania](#ci-i-wydania)
- [Definicja ukończenia](#definicja-ukończenia)
- [Znane pułapki (skrót)](#znane-pułapki-skrót)

---

## Środowisko i uruchamianie

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install flet pytest
.\.venv\Scripts\flet.exe run main.py
python -m pytest test_app.py -q
```

- Brak `requirements.txt`/`pyproject.toml` — CI instaluje `flet==1.0.4` i `pytest`.
- Używaj API Flet 1.x: `ft.Colors.*`, `ft.Icons.*`, `ft.Button`,
  `ft.Alignment(x, y)`, `ft.Padding.symmetric(...)`. Nie ma `ft.colors` ani
  `ft.ElevatedButton` (aliasy `ft.icons`/`ft.alignment` istnieją, ale nie są
  kanonem).

---

## Styl kodu

- **Język:** UI i komentarze po polsku.
- **Kolory:** wyłącznie stałe `COLOR_BG_MAIN/CARD/SURFACE/BORDER`,
  `COLOR_PRIO_A/B/C`, `COLOR_SUCCESS`, `COLOR_TEXT_PRIMARY/SECONDARY`. Żadnych
  literalnych hexów w nowych widżetach.
- **UI mobilne:** każda akcja-ikona ≥ `TOUCH_TARGET_MIN` (44 px); w
  `refresh_view` używaj helpera `_touch(default)`.
- **Błędy:** żadnych nowych cichych `except: pass`. Łagodna degradacja ma
  logować (`LOG.warning`/`LOG.exception`/`LOG.debug`) i działać jak dotąd —
  brak sieci, uprawnień, usługi nigdy nie może wywalić aplikacji.
- Funkcje modyfikujące dane zwracają sensowne wartości i nie rzucają na
  śmieciowym wejściu (np. `normalize_estimate_min`).
- **Docstringi:** `app_logic.py` ma na górze opis modułu, a **każda funkcja
  modułowa** (poziom 0, nie domknięcia w `main`) ma krótki docstring: cel,
  kontrakt argumentów/zwrotu i — gdy to pułapka — ostrzeżenie („nie podmieniaj,
  złamie test”). Pilnuje tego `test_module_level_functions_have_docstrings`.

---

## Zmiany schematu i zapisu

1. **Schemat:** dodaj kolumnę w `ensure_schema()` (przez `PRAGMA table_info` +
   `ALTER TABLE`, nie „ślepy” try/except) i zadbaj o `ensure_schema_upgrade()`
   po **każdym** miejscu podmieniającym plik `LOCAL_DB` (pull, restore, snapshot).
2. **Kolejność `get_tasks()`** (pozycje 0–7) jest kontraktem testów. Nowe pola
   dodawaj **na końcu** `SELECT` albo przez osobny getter.
3. **Eksport/import JSON** musi przenosić nowe pole (klucz opcjonalny przy
   imporcie starszych plików).
4. **Test migracji** starej bazy (baza bez nowej kolumny) jest obowiązkowy.
5. **Każdy zapis do bazy** kończy się `push_database_async()` — inaczej zmiana
   nie trafi do synchronizacji.

---

## Wersjonowanie

- Podnosząc wersję zmień **`version.txt`** oraz **`FALLBACK_VERSION`** w
  `app_logic.py`. Test `test_version_files_are_in_sync` tego pilnuje.
- Format wersji: `MAJOR.MINOR.PATCH` (obecnie `1.7.0`).
- Numer `versionCode` w APK to `1<wersja bez kropek>` (np. `1.7.0` → `170`) i
  **musi rosnąć**, inaczej Android odmawia instalacji na starszej wersji.

---

## Dokumentacja

Dokumentacja jest częścią definicji ukończenia. Zmieniając kod:

- dodaj docstring/komentarz w `app_logic.py` (cel i kontrakt nowej funkcji,
  kolumny, klucza configu, ścieżki zapisu),
- zaktualizuj odpowiednią stronę w `docs/` (`funkcje.md`, `dane.md`,
  `synchronizacja.md`, `ota.md`, `architektura.md`),
- dopisz wpis do `historia-zmian.md`,
- zaktualizuj `AGENTS.md` (mapa plików, tabele, znane pułapki),
- przy nowych pomysłach dopisz pozycję do backlogu w `AGENTS.md`.

---

## CI i wydania

`.github/workflows/build.yml`, tylko przy pushu na `master`:

```
pytest test_app.py -v
flet build apk --arch arm64-v8a --project MasterADHD --no-default-excludes \
  --build-version <version.txt> --build-number 1<wersja bez kropek>
gh release create latest MasterADHD.apk ...
```

- `main.py` jest **zamrożony w APK** — zmiany w nim działają dopiero po
  instalacji nowego APK.
- Zmiany w `app_logic.py` docierają przez **OTA** — i to automatycznie po
  pushu na `master` (nie trzeba czekać na build APK).
- **ZASADA GIT:** po `git commit` i `git push` **nie** uruchamiaj
  `gh run watch` ani nie czekaj na zakończenie GitHub Actions. Zakończ pracę
  zaraz po pushu.

---

## Definicja ukończenia

- [ ] Kod w `app_logic.py` (lub nowym pliku) + brak zmian wymagających `main.py`,
  albo jasno opisane, że potrzebny jest APK.
- [ ] Testy: nowe przypadki w `test_app.py` (jeden plik) + cały `pytest` zielony (109+).
- [ ] Sprawdzone na PC (950 px) i mobile (400 px) — układ, dialogi, `page.overlay`.
- [ ] Migracja schematu + eksport/import JSON, jeśli zmieniono dane.
- [ ] `push_database_async()` w każdej nowej ścieżce zapisu.
- [ ] Wersja podniesiona w `version.txt` i `FALLBACK_VERSION`.
- [ ] Dokumentacja: docstring/komentarz w kodzie + aktualizacja `docs/` i `AGENTS.md`.
- [ ] Brak nowych cichych `except: pass`; błędy trafiają do `LOG`.

---

## Znane pułapki (skrót)

Pełna lista z uzasadnieniami i historią jest w [`../AGENTS.md`](../AGENTS.md).
Najważniejsze:

- **Nie wracaj do `from app_logic import *` w `main.py`** — nadpisywało `main`
  i psuło OTA. Jest re-eksport pomijający `main` (`test_entrypoint_is_not_shadowed`).
- **`init_db()` nie może ruszać `db_last_change`** — inaczej pull z chmury
  nigdy nic nie pobierze.
- **`no such column: routine_days`** = baza bez nowej kolumny po podmianie pliku.
  Zawsze wołaj `ensure_schema_upgrade()`.
- **Sekcja Inbox to `ft.Container`, nie `ft.Column`** — inaczej podmienia
  `tasks_column` w testach.
- **Kolejność `content_column.controls` na Daily** (`[2]` minutnik, `[3]`
  postęp) to kontrakt testów.
- **`on_resize` reaguje tylko na próg 650 px** — bez debounce.
- **Minutnik w tle nie da sygnału** — to świadoma decyzja (brak powiadomień
  systemowych), nie regresja.
- **`end_of_day_db()` zostaje** (starsze testy, dialog `overlay[7]`); nowy UI
  używa `apply_day_rollover()`.
