# Instalacja i aktualizacje

## Spis treści

- [PC (Windows)](#pc-windows)
- [Android (APK)](#android-apk)
- [Pierwsze uruchomienie](#pierwsze-uruchomienie)
- [Aktualizacje](#aktualizacje)
- [Odinstalowanie / dane](#odinstalowanie--dane)

---

## PC (Windows)

### Wymagania

- Python 3.11 lub nowszy.
- Biblioteka `flet` (kod pisany pod **1.0.4**, lokalnie bywa 1.0.5).
- (opcjonalnie) `pytest` do uruchamiania testów.

### Kroki

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install flet pytest
.\.venv\Scripts\flet.exe run main.py
```

Bez aktywacji środowiska:

```powershell
.\.venv\Scripts\python.exe -m pytest test_app.py -q
.\.venv\Scripts\flet.exe run main.py
```

### Ważne

- Punktem wejścia jest **`main.py`**. Uruchomienie `python app_logic.py`
  spowoduje, że zabraknie konfiguracji OTA i splash-a — nie rób tego.
- W repozytorium nie ma `requirements.txt` ani `pyproject.toml`; zależności
  instaluje CI (`flet==1.0.4` + `pytest`), a lokalnie trzymaj je w `.venv`.
- Kod używa składni Flet 1.x. `ft.colors`, `ft.icons`, `ft.alignment` to tylko
  aliasy, a `ft.ElevatedButton` już nie istnieje — używaj `ft.Colors.*`,
  `ft.Icons.*`, `ft.Alignment(...)`, `ft.Button`.

### Zmienne środowiskowe

| Zmienna | Znaczenie |
|---|---|
| `DISABLE_OTA=1` | Całkowicie wyłącza OTA (sieć **i** cache). Używane w testach. |
| `MASTER_ADHD_BUNDLED_VERSION` | Ustawiane przez `main.py`; wersja kodu wbudowanego pokazywana w Ustawieniach. |

---

## Android (APK)

1. Wejdź w [Releases](https://github.com/DemonSwans/MasterADHD/releases).
2. Pobierz `MasterADHD.apk` z wydania `latest`.
3. Zezwól na instalację z nieznanych źródeł i zainstaluj.
4. Pierwsze uruchomienie utworzy folder danych i bazy.

APK jest budowane wyłącznie dla `arm64-v8a` (nowsze telefony). Numer kompilacji
(`versionCode`) rośnie z każdym wydaniem, więc nowszy APK nadpisuje starszy bez
odinstalowywania.

> **Wyjątek historyczny:** jeśli masz APK **starsze niż 1.6.19**, działa w nim
> zepsuty loader OTA. Pierwsza naprawa wymaga jednorazowej instalacji nowego
> APK — dopiero kolejne aktualizacje przyjdą automatycznie.

---

## Pierwsze uruchomienie

1. Splash sprawdza OTA (jeśli nie masz `DISABLE_OTA=1`).
2. Tworzone są bazy `tasks.db` i `app_config.db` w folderze danych.
3. Aplikacja startuje na widoku **Daily**.
4. **Nie ma dialogu przełomu dnia** — przy świeżej instalacji data startowa jest
   ustawiana po cichu. Dialog pojawi się dopiero, gdy faktycznie zmieni się data.

Jeśli chcesz mieć dane na kilku urządzeniach, skonfiguruj synchronizację —
patrz [`synchronizacja.md`](synchronizacja.md).

---

## Aktualizacje

Dwie niezależne drogi:

| Co | Jak | Kiedy |
|---|---|---|
| Kod aplikacji (`app_logic.py`) | **OTA** z GitHuba | Przy każdym starcie albo ręcznie z Ustawień. |
| Powłoka aplikacji (`main.py`, ikona, uprawnienia) | Nowy **APK** | Tylko gdy zmienia się `main.py` — rzadko. |

Szczegóły OTA i proces wydania nowej wersji opisuje [`ota.md`](ota.md).

Po komunikacie o pobranej aktualizacji na Androidzie wystarczy **zamknąć
aplikację z listy uruchomionych i otworzyć ponownie**. Na PC aktywacja z
Ustawień podmienia interfejs bez restartu.

---

## Odinstalowanie / dane

Usunięcie aplikacji **nie usuwa** folderu danych poza piaskownicą systemową,
jeśli kopiujesz go ręcznie. Aby zacząć od zera:

1. Zrób eksport JSON / snapshot na wszelki wypadek (Ustawienia → Kopia zapasowa).
2. Usuń folder danych:
   - Windows: `%USERPROFILE%\.master_adhd_data\`
   - Android: `FLET_APP_STORAGE_DATA/.master_adhd_data`

Aby przenieść dane na inne urządzenie: użyj eksportu/importu JSON albo
synchronizacji (Gist / folder).
