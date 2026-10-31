# OTA — aktualizacje kodu z GitHuba

OTA (Over-The-Air) pozwala zaktualizować logikę aplikacji **bez reinstalacji
APK**. Aktualizowany jest plik `app_logic.py`; powłoka (`main.py`) jest zamrożona
w APK i zmienia się tylko przy nowym wydaniu.

## Spis treści

- [Źródło i zasada działania](#źródło-i-zasada-działania)
- [Dwie ścieżki, jedna implementacja](#dwie-ścieżki-jedna-implementacja)
- [Warunki uruchomienia nowego kodu](#warunki-uruchomienia-nowego-kodu)
- [Bezpieczeństwo: rollback i blokada](#bezpieczeństwo-rollback-i-blokada)
- [Wersjonowanie](#wersjonowanie)
- [Pliki OTA](#pliki-ota)
- [Proces wydania nowej wersji](#proces-wydania-nowej-wersji)
- [Wyłączanie OTA](#wyłączanie-ota)

---

## Źródło i zasada działania

`GITHUB_RAW_BASE` — ta sama wartość w `main.py` i `app_logic.py`:

```
https://raw.githubusercontent.com/DemonSwans/MasterADHD/master
```

Splash pobiera `version.txt` i `app_logic.py`, zapisuje je **atomowo**
(`.tmp` + `os.replace` + `fsync`) w folderze danych i uruchamia nowszy kod.

> ⚠️ **Skutek uboczny:** każdy push `app_logic.py` lub `version.txt` na gałąź
> `master` trafia do użytkowników przez OTA, niezależnie od wydania APK.

---

## Dwie ścieżki, jedna implementacja

| Moment | Łańcuch |
|---|---|
| **Start** | `main.load_and_run()` → `check_and_download_ota()` → `get_prepared_ota()` → `_run_app_logic()` |
| **Ustawienia** | `trigger_manual_ota()` → `install_ota_code()` → `activate_downloaded_ota()` (podmiana UI w tym samym procesie, bez restartu) |

`activate_downloaded_ota()` woła `module.main(page)` w tym samym procesie. Stare
wątki (minutnik, sync) mogą jeszcze chwilę żyć — dlatego przed podmianą UI
wywoływane jest `flush_pending_sync(timeout=2.0)`, a `page.overlay`/`page.controls`
są czyszczone.

---

## Warunki uruchomienia nowego kodu

Kod OTA uruchomi się **tylko gdy**:

1. jest **nowszy** od wbudowanego
   (`is_newer_version(ota_ver, get_bundled_version())`) — stary plik z folderu
   danych nie przesłania świeżego kodu z APK,
2. **kompiluje się**,
3. nie jest wpisany w `ota_failed_version.txt`.

`get_local_version()` czyta pliki wersji **tylko obok działającego modułu**
(`__file__`), a `get_installed_ota_version()` w `main.py` zwraca wersję kodu,
który faktycznie się uruchomi. Numer w UI nie może pochodzić z pliku, którego
nie wykonano.

`main.py` przekazuje wersję wbudowaną przez `MASTER_ADHD_BUNDLED_VERSION`, żeby
podpis w Ustawieniach był prawdziwy także pod kodem OTA.

---

## Bezpieczeństwo: rollback i blokada

- Przed nadpisaniem `app_logic_ota.py` powstaje kopia `app_logic_ota.prev.py`
  + `ota_prev_version.txt`.
- Błąd startu nowego kodu → **`rollback_ota()`** przywraca poprzednią wersję.
- Wadliwa wersja trafia do `ota_failed_version.txt` i **nie jest pobierana
  ponownie** (koniec pętli „pobierz → crash → pobierz to samo”).
- Brak pliku `.prev` → wadliwy kod ląduje jako `app_logic_ota.py.bad`, a
  aplikacja startuje z kodu wbudowanego.
- Log OTA: `OTA_DIR/ota.log` (na Androidzie `print()` ginie).

---

## Wersjonowanie

- Numer wersji trzymany jest w **dwóch** plikach:
  - `version.txt` (repo / folder danych),
  - `FALLBACK_VERSION` w `app_logic.py`.
- Test `test_version_files_are_in_sync` pilnuje, żeby były równe — podnosząc
  wersję, zmień **oba**.
- Wersja bieżąca: **1.7.0**.

---

## Pliki OTA

| Plik | Rola |
|---|---|
| `app_logic_ota.py` | Pobrany, aktywny kod OTA. |
| `app_logic_ota.prev.py` | Poprzednia wersja (rollback). |
| `ota_version.txt` | Wersja pobranego kodu. |
| `ota_prev_version.txt` | Wersja pliku `.prev`. |
| `ota_failed_version.txt` | Zablokowane (wadliwe) wersje. |
| `ota.log` | Log operacji OTA. |

Wszystkie pliki OTA leżą **tylko w folderze danych** — nigdy w repozytorium.

---

## Proces wydania nowej wersji

1. Wprowadź zmiany w `app_logic.py` (i testach).
2. Podnieś numer w `version.txt` **oraz** `FALLBACK_VERSION` w `app_logic.py`.
3. Uruchom `python -m pytest test_app.py -q` — musi być zielone.
4. Zaktualizuj dokumentację (`README.md`, `docs/`, `AGENTS.md`).
5. `git push` na `master`.
6. CI (`.github/workflows/build.yml`) zrobi: `pytest` → `flet build apk`
   → Release `latest`.
7. Zainstalowane aplikacje pobiorą nowy kod przy następnym starcie (albo z
   przycisku w Ustawieniach).

> Po commicie i pushu **nie czekaj** na GitHub Actions — build APK nie jest
> potrzebny do tego, żeby kod trafił przez OTA (`app_logic.py` + `version.txt`
> czyta się z `raw.githubusercontent.com`).

Parametry builda: `--arch arm64-v8a`, `--project MasterADHD`,
`--no-default-excludes`, `--build-version <version.txt>`,
`--build-number 1<wersja bez kropek>`. Numer `versionCode` **musi rosnąć**,
inaczej Android odmawia instalacji nowego APK na urządzeniu ze starszą wersją.

---

## Wyłączanie OTA

- `DISABLE_OTA=1` wyłącza OTA całkowicie — **sieć i cache**. Wymagane przez
  testy, nie usuwaj z fixture.
- Aby wymusić uruchomienie wersji wbudowanej (np. diagnostyka), usuń
  `app_logic_ota.py` i `ota_version.txt` z folderu danych.
