# Master ADHD

Osobisty menedżer zadań dla osób z ADHD — **Python + Flet + SQLite**, działa na
PC (Windows) i na Androidzie (APK). Interfejs i komentarze w kodzie są po
polsku, motyw kolorystyczny to „Dark Slate”.

Aplikacja powstała wokół jednej myśli: **mniej decyzji, mniej poczucia winy,
więcej domkniętych spraw**. Zamiast długich list i liczenia „straconych serii”
daje jeden ekran dnia, krótki rytuał startu i końca dnia oraz dziennik
sukcesów, który zamienia ukończone zadania w dowód postępu.

> 🇬🇧 English version: [`README.en.md`](README.en.md)

---

## Spis treści

- [Najważniejsze funkcje](#najważniejsze-funkcje)
- [Wymagania](#wymagania)
- [Szybki start na PC](#szybki-start-na-pc)
- [Instalacja na Androidzie](#instalacja-na-androidzie)
- [Jak korzystać — widoki](#jak-korzystać--widoki)
- [Minutnik skupienia](#minutnik-skupienia)
- [Synchronizacja i kopie zapasowe](#synchronizacja-i-kopie-zapasowe)
- [Aktualizacje (OTA)](#aktualizacje-ota)
- [Gdzie są dane](#gdzie-są-dane)
- [Rozwój i testy](#rozwój-i-testy)
- [Struktura repozytorium](#struktura-repozytorium)
- [Dokumentacja szczegółowa](#dokumentacja-szczegółowa)

---

## Najważniejsze funkcje

| Obszar | Co robi |
|---|---|
| **Daily** | Jeden ekran na dziś: zadania dnia, rutyny, pasek postępu i minutnik. Szybki zrzut myśli jednym polem tekstowym. |
| **Master** | Cała baza zadań: wyszukiwanie, filtry (strefa, priorytet, status, termin), sortowanie po terminie i filtr „🕰 Dawno nietknięte”. |
| **Dziennik Sukcesów** | Archiwum ukończonych zadań — nic nie znika bezpowrotnie. |
| **Priorytety A / B / C** | Kolorowe oznaczenie wagi zadania. Limit zadań „A” na Daily (3 — zalecane — albo 4) chroni koncentrację. |
| **Rutyny (nawyki)** | Codzienne albo przypisane do dni tygodnia. Historia z miękkimi seriami — bez komunikatu „straciłeś serię”. |
| **Podkroki** | Zadanie można rozbić na kroki z własną kolejnością i strzałkami do przestawiania. |
| **Terminy** | Data opcjonalna, sortowanie i filtr w Master, spokojna karta „Nadchodzące terminy” na Daily (14/7/3 dni wcześniej). |
| **Szacowany czas** | Pole „ile to zajmie” (5/15/30/60/własne) i podsumowanie dnia „Zaplanowane: ~X” na Daily. |
| **Inbox + 2-minutowy triage** | Zrzut myśli ląduje w `📥 Inbox`; jednym ruchem przenosisz go do kategorii, priorytetu, na Daily lub z terminem. |
| **Przełom dnia** | Świadomy rytuał: zostaw / do Mastera / usuń, zamiast cichego gubienia zadań. Tryb pytający albo automatyczny. |
| **Minutnik skupienia** | Presety 5/15/25 min, zasada „5 minut na przełamanie paralizu”, globalny mini-minutnik widoczny w każdym widoku. |
| **Wibracja i dźwięk** | Wibracja na telefonie, dźwięk systemowy na Windows, sygnał przy sukcesie i końcu minutnika. |
| **Usuwanie z „Cofnij”** | Natychmiastowe usunięcie zadania/podkroku + `SnackBar` „Usunięto «…». Cofnij”. |
| **Synchronizacja** | Trzy tryby: folder lokalny, GitHub Gist (za darmo), Home Assistant. Panel rozstrzygania konfliktów. |
| **Kopie i snapshoty** | Automatyczne kopie przed każdą operacją destrukcyjną, eksport/import JSON, przywracanie snapshotu, eksport logów. |
| **OTA** | Aktualizacja kodu z GitHuba bez reinstalacji APK, z rollbackiem i blokadą wadliwej wersji. |

Szczegóły każdej funkcji: [`docs/funkcje.md`](docs/funkcje.md).

---

## Wymagania

- **PC:** Python 3.11+, biblioteka `flet` (w CI przypięta do `flet==1.0.4`) oraz `pytest` do testów.
- **Android:** gotowy plik APK z sekcji Releases — nic nie trzeba budować samemu.

W repozytorium **nie ma** `requirements.txt` ani `pyproject.toml`. Zależności
instaluje CI, a lokalnie żyją w środowisku `.venv`.

> API zależy od wersji Flet. Kod używa składni **Flet 1.x** (`ft.Colors.*`,
> `ft.Icons.*`, `ft.Button`, `ft.Alignment(x, y)`, `ft.Padding.symmetric(...)`).
> W 1.0.4 nie ma już `ft.colors` ani `ft.ElevatedButton` — mimo że tak wygląda
> większość przykładów w sieci.

---

## Szybki start na PC

```powershell
# 1. Środowisko wirtualne
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Zależności
pip install flet pytest

# 3. Uruchomienie
.\.venv\Scripts\flet.exe run main.py
```

Można też pominąć aktywację i wołać wszystko jawnymi ścieżkami:

```powershell
.\.venv\Scripts\python.exe -m pytest test_app.py -q
```

Bezpośredni start modułu (`python app_logic.py`) **nie zadziała** — punktem
wejścia jest zawsze `main.py`, który najpierw sprawdza aktualizacje OTA, potem
uruchamia właściwy interfejs.

---

## Instalacja na Androidzie

1. Otwórz [Releases](https://github.com/DemonSwans/MasterADHD/releases) i pobierz
   `MasterADHD.apk`.
2. Zainstaluj plik (konieczne zezwolenie na instalację z nieznanych źródeł).
3. Numer kompilacji (`versionCode`) rośnie z każdym wydaniem, więc nowa wersja
   nadpisuje starą bez odinstalowywania.

Kolejne aktualizacje przychodzą przez OTA (patrz niżej) — ponowna instalacja
APK jest potrzebna tylko wtedy, gdy zmienia się sam `main.py` (rzadko).

---

## Jak korzystać — widoki

Aplikacja ma cztery widoki. Na PC przełącznik jest w lewym pasku bocznym; na
telefonie (ekran < 650 px) w dolnej nawigacji. Poniżej skrót — pełny opis
znajdziesz w [`docs/funkcje.md`](docs/funkcje.md).

### Daily — co robię teraz

- Lista zadań dnia + rutyny (te przypisane do dzisiejszego dnia tygodnia).
- **Szybki zrzut:** pole „⚡ Zrzut myśli do Inbox” — wpisz i Enter. Trafia do
  `📥 Inbox`, nie zaśmieca Mastera. Opróżniasz go w sekcji triage'u na Master.
- **Postęp dnia** liczony hybrydowo: zadanie bez podkroków to 1 punkt, z
  podkrokami rośnie o ułamek po każdym kroku, rutyna to 1 punkt. Puste Daily
  pokazuje „Brak zadań na dziś” zamiast mylącego „0%”.
- **Karta „Nadchodzące terminy”** pojawia się dyskretnie, gdy coś się zbliża —
  bez wyskakujących okien, z przyciskiem „Ukryj na dziś”.
- **Przełom dnia:** gdy zaczyna się nowy dzień, pojawia się spokojny przycisk
  „Rozpocznij dzień” (albo decyzja zapada automatycznie, wg ustawień).

### Master — cała baza

- **Dodawanie:** pole „➕ Nowe zadanie do Master listy” (nazwa, strefa,
  priorytet, termin, szacunek) albo szybki wpis jednym polem.
- **Wyszukiwanie** po tytułach i podkrokach.
- **Filtry:** strefa (kategorie), priorytet (A/B/C), status (do zrobienia /
  zrobione), termin (wszystkie / ten tydzień / zaległe).
- **Sortowanie:** domyślne albo 📅 po terminie.
- **Filtr „🕰 Dawno nietknięte”:** pokazuje tylko nieukończone zadania bez zmian
  od 14+ dni i bez przyszłego terminu — żeby wychwycić „duchy”.
- **Sekcja Inbox:** ukryta, gdy pusta; tu robisz 2-minutowy triage.
- **Słońce ☀️** na karcie: żółte = zadanie jest na Daily, szare = nie. Klik lub
  przeciągnięcie (zależnie od trybu w Ustawieniach). Zadanie zostaje na obu
  listach.

### Dziennik Sukcesów (Sukcesy)

Archiwum ukończonych zadań z priorytetem i datą. Zadania trafiają tu przy
przełomie dnia (i starym „Końcu dnia”). Archiwum trzyma do 2000 wpisów, można
je wyczyścić (wtedy powstaje snapshot bazy).

### Ustawienia

Karty, mniej więcej w kolejności:

| Karta | Do czego służy |
|---|---|
| **Aktualizacje OTA** | Wersja wbudowana i aktywna, „Sprawdź i wymuś aktualizację z GitHuba”, wymuszony restart. |
| **Dźwięk i wibracja** | Jeden przełącznik wyciszający dźwięk (PC) i wibrację (Android). |
| **Terminy i przypomnienia** | Włączenie karty „Nadchodzące terminy”, ile dni wcześniej (14/7/3), przypomnienie dzień przed. |
| **Dodawanie do Daily** | Słońce: tryb „Przeciągnij (Drag & Drop)” albo „Kliknij (Tap)”. |
| **Przełom dnia** | Pytaj mnie / Auto: zostaw / Auto: do Mastera. |
| **Kopia zapasowa** | Eksport JSON, przywrócenie ostatniego snapshotu, eksport logów, import JSON (dopisz/zastąp), licznik snapshotów. |
| **Nawyki i Rutyny** | Dodawanie/edycja rutyn, dni tygodnia, pasek 7 dni, „w tym tygodniu X/Y”, miękka seria. |
| **Limit zadań „A”** | 3 (zalecane) albo 4 zadania priorytetu A na Daily. |
| **Chmura (GitHub Gist)** | Token i ID gist-a → darmowa synchronizacja. |
| **Inne opcje synchronizacji** | Home Assistant, folder lokalny, tryb lokalny, ręczny reset, zapis tylko lokalnie. |

---

## Minutnik skupienia

- Presety **5 / 15 / 25 min** (25 = klasyczne Pomodoro; domyślnie 5 min —
  „zasada 5 minut” na przełamanie paralizu startu).
- Stan minutnika (`timer_end_ts`, `timer_left`, `timer_total`) jest zapisywany
  w konfiguracji, więc **przeżywa zminimalizowanie aplikacji i restart procesu**.
- **Mini-minutnik** widać w każdym widoku: na PC w pasku bocznym, na telefonie
  nad dolną nawigacją. Ma pauzę/wznowienie i reset; kliknięcie tła wraca do Daily.
- Sygnał końca: **dźwięk na Windows, wibracja na Androidzie** (sterowane
  przełącznikiem „Dźwięk i wibracja”).

> ⚠️ Gdy aplikacja jest zminimalizowana lub telefon zablokowany, minutnik
> **nie da sygnału** — to świadoma decyzja, aplikacja nie używa powiadomień
> systemowych ani `AlarmManager`.

---

## Synchronizacja i kopie zapasowe

Tryb wybierasz w Ustawieniach (`sync_mode`). Domyślnie: **tylko lokalnie**.

| Tryb | Jak działa |
|---|---|
| `none` | Dane wyłącznie na urządzeniu. |
| `folder` | Zapis do wskazanego folderu (np. dysk sieciowy / OneDrive). Zapis natychmiastowy. |
| `gist` | GitHub Gist — darmowa chmura. Wymaga tokenu (uprawnienie `gist`) i ID gist-a. Zapis z debounce 1,5 s. |
| `ha_server` | Home Assistant pod wskazanym adresem (POST/GET). |

Zasady:

- Każdy zapis kończy się wywołaniem `push_database_async()`.
- Startowy `pull_database(force=False)` **nie nadpisuje nowszych danych
  lokalnych**, a push jest odraczany, dopóki nie wiadomo, co jest w chmurze.
- **Konflikty:** gdy i lokalna, i zdalna baza zmieniły się od ostatniej
  synchronizacji, aplikacja **nie nadpisuje** danych, tylko pokazuje banner z
  trzema decyzjami: *zachowaj lokalną*, *pobierz zdalną*, *przywróć snapshot*.
  Porównywany jest skrót treści bazy (wiersze), nie surowe bajty.

Bezpieczeństwo danych:

- Kopie robione są przez `sqlite3.Connection.backup()` (spójne także przy WAL),
  nigdy przez kopiowanie pliku.
- **Snapshoty** (`snapshots/tasks_<tag>_<czas>.db`) powstają przed każdą
  operacją destrukcyjną: pobraniem z chmury, importem, przełomem dnia,
  czyszczeniem archiwum. Rotacja: 10 najnowszych.
- Eksport JSON (czytelna kopia), import JSON, eksport logów i przywracanie
  snapshotu — wszystko z karty „Kopia zapasowa”.

Szczegóły: [`docs/synchronizacja.md`](docs/synchronizacja.md).

---

## Aktualizacje (OTA)

Kod aplikacji (`app_logic.py`) aktualizuje się sam z GitHuba — **bez
reinstalacji APK**:

- Źródło: `https://raw.githubusercontent.com/DemonSwans/MasterADHD/master`
  (pliki `version.txt` + `app_logic.py`).
- Sprawdzanie przy starcie (na splashu) oraz ręcznie z Ustawień
  („🔄 Sprawdź i wymuś aktualizację z GitHuba”).
- Uruchamiany jest **tylko kod nowszy** od wbudowanego, który się kompiluje i
  nie jest wpisany na listę wadliwych.
- Pobieranie jest atomowe; poprzednia wersja zostaje jako `.prev`.
- Błąd nowego kodu → **automatyczny rollback**; wadliwa wersja ląduje w
  `ota_failed_version.txt` i nie jest pobierana ponownie.
- Aktywacja działa w tym samym procesie (bez restartu); na Androidzie po
  komunikacie o pobraniu wystarczy zamknąć i otworzyć aplikację.

> Uwaga dla osób rozwijających: **każdy push `app_logic.py` lub `version.txt`
> na gałąź `master` trafia do użytkowników przez OTA**, niezależnie od wydania
> APK.

Szczegóły i proces wydania: [`docs/ota.md`](docs/ota.md).

---

## Gdzie są dane

Wszystko w prywatnym folderze danych:

| Platforma | Folder |
|---|---|
| Windows | `%USERPROFILE%\.master_adhd_data\` |
| Android | `FLET_APP_STORAGE_DATA/.master_adhd_data` |

Zawartość:

- `tasks.db` — zadania, rutyny, historia rutyn, archiwum.
- `app_config.db` — ustawienia i tryb synchronizacji.
- `master_adhd.log` — log aplikacji.
- `snapshots/` — kopie bezpieczeństwa.
- pliki OTA (`app_logic_ota.py`, `ota_version.txt`, `.prev`, `ota_failed_version.txt`, `ota.log`).

Aplikacja czyta bazy z folderu danych, **nie z repozytorium**. Pełny opis
schematu: [`docs/dane.md`](docs/dane.md).

---

## Rozwój i testy

```powershell
python -m pytest test_app.py -q                     # całość (109 testów, ~26 s)
python -m pytest test_app.py -q -k test_tasks_crud  # pojedynczy test
.\.venv\Scripts\flet.exe run main.py                # uruchomienie na PC
```

- Wszystkie testy trzymamy w **jednym pliku** `test_app.py` (autouse fixture
  przekierowuje bazy i pliki OTA do katalogu tymczasowego — testy nie ruszają
  folderu użytkownika).
- Pisząc kod, trzymaj się konwencji z `app_logic.py` i zasad z `AGENTS.md`
  (stałe kolorów, docstringi, `push_database_async()` po każdym zapisie).
- Podnosząc wersję: zmień **zarówno** `version.txt`, jak i `FALLBACK_VERSION`
  w `app_logic.py` (pilnuje tego test `test_version_files_are_in_sync`).

CI (`.github/workflows/build.yml`) działa przy pushu na `master`:
`pytest` → `flet build apk` → Release `latest`.

Konwencje i pełna definicja „ukończone”: [`docs/rozwoj.md`](docs/rozwoj.md)
oraz [`AGENTS.md`](AGENTS.md). Opis testów: [`docs/testy.md`](docs/testy.md).

---

## Struktura repozytorium

```
main.py                 # punkt wejścia: OTA, splash, wybór modułu, rollback
app_logic.py            # cała logika: SQLite, konfiguracja, sync, OTA, UI Flet
test_app.py             # 109 testów (jeden plik)
version.txt             # aktualna wersja (1.7.0)
assets/icon.png         # ikona aplikacji
.github/workflows/      # CI: testy → APK → Release
docs/                   # dokumentacja szczegółowa (ten katalog)
AGENTS.md               # wewnętrzna dokumentacja dla agentów AI / deweloperów
```

---

## Dokumentacja szczegółowa

| Dokument | Zawartość |
|---|---|
| [`docs/funkcje.md`](docs/funkcje.md) | Wszystkie funkcje i widoki z punktu widzenia użytkownika. |
| [`docs/instalacja.md`](docs/instalacja.md) | Instalacja na PC i Androidzie, pierwsze uruchomienie, aktualizacje. |
| [`docs/synchronizacja.md`](docs/synchronizacja.md) | Tryby synchronizacji, rozstrzyganie konfliktów, kopie. |
| [`docs/ota.md`](docs/ota.md) | Mechanizm OTA, wersjonowanie, rollback, proces wydania. |
| [`docs/dane.md`](docs/dane.md) | Schemat bazy, klucze konfiguracji, snapshoty, JSON. |
| [`docs/architektura.md`](docs/architektura.md) | Warstwy, przepływ sterowania, struktura UI, wątki. |
| [`docs/testy.md`](docs/testy.md) | Jak działa zestaw testów i jak pisać nowe. |
| [`docs/rozwoj.md`](docs/rozwoj.md) | Konwencje, definicja ukończenia, zasady zmian. |
| [`docs/historia-zmian.md`](docs/historia-zmian.md) | Historia wersji. |
| [`AGENTS.md`](AGENTS.md) | Zwięzłe fakty techniczne dla agentów (mapa plików, pułapki, backlog). |

> Dokumentacja wewnętrzna `FIX_SUMMARY.md` i `README_OTA_FIX.md` opisują stan
> v1.6.17 i **nie są już aktualne** — traktuj je jako zapis historyczny.
