# Funkcje aplikacji

Opis wszystkich możliwości Master ADHD z perspektywy użytkownika. Fakty
techniczne (schemat, klucze configu) znajdziesz w [`dane.md`](dane.md).

## Spis treści

- [Filozofia: dlaczego tak](#filozofia-dlaczego-tak)
- [Zadania](#zadania)
- [Priorytety i limit „A”](#priorytety-i-limit-a)
- [Strefy (kategorie)](#strefy-kategorie)
- [Podkroki](#podkroki)
- [Terminy](#terminy)
- [Szacowany czas](#szacowany-czas)
- [Rutyny (nawyki)](#rutyny-nawyki)
- [Inbox i 2-minutowy triage](#inbox-i-2-minutowy-triage)
- [Przełom dnia](#przełom-dnia)
- [Postęp dnia](#postęp-dnia)
- [Minutnik skupienia](#minutnik-skupienia)
- [Dziennik Sukcesów](#dziennik-sukcesów)
- [Usuwanie z „Cofnij”](#usuwanie-z-cofnij)
- [Dźwięk i wibracja](#dźwięk-i-wibracja)
- [Dostępność i układ](#dostępność-i-układ)

---

## Filozofia: dlaczego tak

Aplikacja jest projektowana pod ADHD-first:

- **Mniej decyzji.** Daily pokazuje tylko to, co na dziś; reszta czeka w Masterze.
- **Mniej poczucia winy.** Nie ma liczników „straconych serii”. Seria rutyny
  jest „miękka”: dzisiejszy niepełny dzień i dni niezaplanowane jej nie przerywają.
- **Nic nie znika po cichu.** Przełom dnia to świadomy wybór, ukończone zadania
  trafiają do Dziennika Sukcesów, usunięcie ma „Cofnij”.
- **Jedno zadanie na raz.** Limit priorytetu „A”, spokojna karta terminów bez
  wyskakujących okien, jeden globalny minutnik.

---

## Zadania

Zadanie ma: tytuł, strefę (kategorię), priorytet (A/B/C), opcjonalny termin,
opcjonalny szacowany czas oraz listę podkroków.

- Dodawanie z **Mastera**: pole „➕ Nowe zadanie do Master listy” (Enter) albo
  pełny dialog z polem tekstowym, strefą, priorytetem, terminem i szacunkiem.
- Dodawanie z **Daily**: pole „⚡ Zrzut myśli do Inbox” — trafia do Inbox z
  priorytetem C.
- Edycja: ikona ołówka na karcie. Edycja z Daily **zachowuje** aktualną strefę
  (nie nadpisuje jej „Ogólnym”).
- Oznaczanie „zrobione”: checkbox albo klik w tytuł.
- Słońce ☀️ przenosi zadanie między Masterem a Daily (żółte = na Daily).

---

## Priorytety i limit „A”

- **A** (czerwony) — kluczowe, **B** (bursztynowy) — zwykłe, **C** (niebieski) — poboczne.
- Klik na plakietkę priorytetu rozwija szybki wybór A/B/C.
- **Limit zadań „A” na Daily** ustawiasz w Ustawieniach: **3 (zalecane)** albo 4.
  Próba dodania piątego/drugiego nadmiarowego kończy się spokojnym komunikatem,
  bez zmiany danych.
- Rutyny **nie** obciążają limitu „A”.

---

## Strefy (kategorie)

- Domyślne: `Ogólne`, `💼 Praca`, `🏠 Dom`, `🎯 Osobiste`.
- Można dodawać własne strefy; własna kategoria nie jest po cichu podmieniana
  przy edycji (lista stref łączy wartości wbudowane z tymi w bazie).
- `📥 Inbox` to strefa systemowa szybkich zrzutów.

---

## Podkroki

- Każde zadanie można rozbić na kroki („👣 Krok”).
- Kroki mają własną kolejność — strzałki **↑ / ↓** przestawiają je w obrębie zadania.
- Można je edytować i usuwać (z „Cofnij”, które przywraca je na tę samą pozycję).
- Zadanie z podkrokami rośnie w postępie dnia po każdym zaznaczonym kroku.

---

## Terminy

- Opcjonalna data `YYYY-MM-DD` (bez godziny). Pole przyjmuje „dziś”, „jutro”,
  „za tydzień” albo konkretną datę.
- Na karcie widać dyskretny chip terminu; zmienia kolor wg pilności (po terminie
  / wkrótce / odległy). Klik otwiera edycję.
- **Master:** sortowanie „📅 Termin” oraz filtr „Ten tydzień / Zaległe”.
- **Daily:** karta „Nadchodzące terminy” — cicha, maksymalnie 3 pozycje, z
  „Ukryj na dziś”. Nigdy nie wyskakuje jako okno.
- W Ustawieniach: włącz/wyłącz kartę, próg (14 / 7 / 3 dni) i opcja
  „przypomnij też dzień przed terminem”.

---

## Szacowany czas

- Można podać, ile zadanie zajmie: presety **5 / 15 / 30 / 60 min**, własna
  wartość albo „Bez szacunku”.
- Daily pokazuje podsumowanie: **„Zaplanowane: ~X h Y min · bez szacunku: n”**
  (tylko gdy suma > 0).
- Wartości są odporne na śmieci: zero, liczby ujemne i `True` traktowane są jak
  brak szacunku, nigdy nie wywalają aplikacji.

---

## Rutyny (nawyki)

- Rutyna to nawyk powtarzalny: „Leki”, „Woda”, „Spacer”.
- Dni tygodnia: zaznaczasz dni, w których rutyna ma się pokazywać na Daily
  (**odznaczone wszystkie = codziennie**). Rutyny z przed wprowadzenia tej
  funkcji są traktowane jak codzienne.
- Zaznaczenie rutyny = krótki sygnał (dźwięk/wibracja); odznaczenie jest ciche.
- **Historia:** tabela `routine_log` pamięta każdy dzień. Ustawienia pokazują
  pasek 7 dni, „w tym tygodniu: X/Y” oraz **miękką serię** („seria: N dni z
  rzędu”, pokazywaną od 2 dni).
- Usunięcie rutyny kasuje także jej historię.
- Historię rutyn przenosi eksport/import JSON.

> **Zasada:** nigdy nie komunikujemy „straciłeś serię”.

---

## Inbox i 2-minutowy triage

- Szybki zrzut myśli (`⚡`) ląduje w `📥 Inbox` z priorytetem **C**, poza Daily.
- Zadania z Inboxa nie zaśmiecają listy Master — są w osobnej sekcji triage'u
  (ukrytej, gdy Inbox jest pusty).
- Triage: wybierasz strefę, priorytet, czy na Daily, opcjonalnie termin i
  zatwierdzasz. Zadanie przenosi się do wybranej strefy (`Inbox` spada do
  `Ogólne`).

---

## Przełom dnia

Gdy zmienia się data, aplikacja nie gubi zadań po cichu:

- Pojawia się spokojna karta „Nowy dzień” z przyciskiem **„Rozpocznij dzień”**.
- Dla każdego niezrobionego zadania decydujesz: **zostaw** na Daily, **przenieś
  do Mastera** albo **usuń z Daily**. Przyciski zbiorcze: „Wszystkie zostaw”,
  „Wszystkie do Mastera”.
- Zrobione zadania trafiają **dokładnie raz** do Dziennika Sukcesów i są usuwane
  z listy zadań.
- Tryb pracy (Ustawienia → „Przełom dnia”): **Pytaj mnie** (domyślnie),
  **Auto: zostaw**, **Auto: do Mastera**.
- Przed operacją powstaje snapshot `pre_day_rollover`.
- Przy **pierwszym uruchomieniu** (świeża instalacja) dialog się nie pokazuje —
  data startowa jest ustawiana po cichu.

---

## Postęp dnia

Pasek postępu na Daily używa jednej, jasnej reguły (punktacja hybrydowa):

- zadanie bez podkroków = 1 punkt, gdy ukończone;
- zadanie z podkrokami = ułamek ukończonych kroków;
- rutyna = 1 punkt, gdy zaznaczona;
- brak zadań i rutyn → puste Daily pokazuje **„Brak zadań na dziś”**
  (bez mylącego „0%”), a setka pojawia się dopiero, gdy wszystko jest zrobione.

---

## Minutnik skupienia

- Presety **5 / 15 / 25 min** (domyślnie 5 — zasada „przełam paraliz startu”).
- Pauza / wznowienie, reset, pasek postępu.
- Stan jest zapisywany w konfiguracji, więc minutnik przeżywa zminimalizowanie
  aplikacji i restart procesu (po restarcie wznawia się sam).
- **Globalny mini-minutnik** widoczny w każdym widoku (PC: pasek boczny,
  mobile: nad dolną nawigacją). Klik w tło wraca do Daily.
- Sygnał końca: dźwięk na Windows, wibracja na Androidzie.
- ⚠️ Gdy aplikacja jest w tle / telefon zablokowany, sygnału **nie** będzie —
  aplikacja nie używa powiadomień systemowych.

---

## Dziennik Sukcesów

- Widok „Sukcesy”: lista ukończonych zadań z priorytetem i datą.
- Trafiają tu zadania domknięte przy przełomie dnia oraz starym „Końcu dnia”.
- Licznik „🏆 Łącznie ukończonych zadań”.
- Limit 2000 wpisów (starsze są przycinane). Czyszczenie archiwum jest
  nieodwracalne i dlatego ma dialog potwierdzenia + snapshot bazy.
- Pusty widok ma zachętę, a nie pustkę.

---

## Usuwanie z „Cofnij”

- Usunięcie zadania lub podkroku z karty jest **natychmiastowe** i pokazuje
  `SnackBar`: „Usunięto «…». Cofnij”.
- Cofnięcie przywraca **pełny** wiersz zadania (to samo `id`, strefa, priorytet,
  termin, podkroki, szacunek) albo podkrok na tej samej pozycji.
- Drugie kliknięcie „Cofnij” nie duplikuje rekordu.
- Dialog potwierdzenia został tylko dla akcji zbiorczych/nieodwracalnych
  (np. czyszczenie archiwum, import zastępujący dane).

---

## Dźwięk i wibracja

- Jeden przełącznik „Dźwięk i wibracja” w Ustawieniach wycisza oba sygnały.
- **Windows:** krótki dźwięk systemowy.
- **Android:** wibracja (Flet nie ma tam modułu audio) — `vibrate` na koniec
  minutnika, `light_impact` na ukończenie zadania, `selection_click` przy „Cofnij”.
- Brak wsparcia platformy / uprawnień jest cicho pomijany — nigdy nie wywala
  aplikacji.

---

## Dostępność i układ

- **PC (≥ 650 px):** pasek boczny po lewej (Daily / Master / Sukcesy /
  Ustawienia + mini-minutnik + numer wersji + badge synchronizacji).
- **Mobile (< 650 px):** jedna kolumna treści + dolna nawigacja.
- Na telefonie elementy akcji mają co najmniej **44 px** wysokości/szerokości.
- Kontrast tekstów metadanych jest dobierany automatycznie (WCAG 2.1): na
  ciemnym tle zostaje kolor drugorzędny, a przy zmianie tła degraduje się do
  czytelnego koloru podstawowego.
- Motyw „Dark Slate”, paleta bez literalnych hexów w kodzie (stałe `COLOR_*`).
