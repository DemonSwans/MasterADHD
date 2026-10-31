# Historia zmian

Zapis wersji i najważniejszych zmian. Wersja bieżąca: **1.7.0**.

## 1.7.x — Niezawodność i bezpieczeństwo

| Wersja | Data | Zmiany |
|---|---|---|
| **1.7.0** | 2026-10-31 | Sprint 4: kolejka aktualizacji UI + próg `resize` (4.1), hybrydowy postęp dnia (4.2), panel rozstrzygania konfliktów synchronizacji (4.4), higiena logów i testów (4.5). Pozycja 4.3 pominięta decyzją. |
| 1.6.28 | 2026-10-31 | Historia rutyn z miękkimi seriami (3.1) i szacowany czas zadań (3.4). |
| 1.6.27 | 2026-10-31 | Znaczniki czasu zadań i filtr Master „🕰 Dawno nietknięte” (3.2). |
| 1.6.26 | 2026-10-31 | Świadomy przełom dnia (2.1) i Inbox z 2-minutowym triagem (2.3). |
| 1.6.25 | 2026-10-31 | Sprint 1 „Mniej tarcia”: wibracja, dotyk 44 px, globalny mini-minutnik, usuwanie z „Cofnij”, poprawki edycji z Daily. |
| 1.6.24 | 2026-10-31 | Plan rozwoju ADHD-first + aktualizacja `AGENTS.md`. |
| 1.6.23 | 2026-10-29 | Terminy zadań, sortowanie i filtr w Master, spokojne przypomnienia na Daily. |
| 1.6.22 | 2026-10-29 | Naprawa: brak kolumny `routine_days` po podmianie bazy. |
| 1.6.21 | 2026-10-29 | Rutyny z dniami tygodnia + dialog edycji rutyny. |
| 1.6.20 | 2026-10-25 | Przestawianie podkroków strzałkami. |
| 1.6.19 | 2026-10-24 | Naprawa OTA: pobierany kod naprawdę się uruchamia. Usunięcie `server_sync.py` + `AGENTS.md`. |

## 1.6.x — Fundament

| Wersja | Data | Zmiany |
|---|---|---|
| 1.6.17 | 2026-10-23 | Warstwa danych (`db_connection`, `backup_database`, integralność), rozstrzyganie „kto nowszy” w synchronizacji, snapshoty + rotacja, minutnik odporny na restart, rollback OTA i blokada wadliwej wersji. |
| 1.6.9 | 2026-10-20 | Kategorie zadań, filtry Master listy, kopie JSON, dźwięk. |
| 1.6.8 | 2026-10-19 | Minutnik skupienia i Dziennik Sukcesów (archiwum). |
| 1.6.7 | 2026-10-19 | Układ zadań na telefonie. |
| 1.6.6 | 2026-10-19 | Edycja i usuwanie zadań i podkroków. |
| 1.6.4 | 2026-10-19 | OTA: dynamiczne przeładowanie modułu, kontekst SSL, ręczne wymuszenie aktualizacji, przycisk restartu. |
| 1.6.1 | 2026-10-19 | OTA: obsługa błędów, fallback, dopracowany mechanizm aktualizacji. |

## Dokumenty historyczne

- [`../FIX_SUMMARY.md`](../FIX_SUMMARY.md) — podsumowanie zmian v1.6.17.
- [`../README_OTA_FIX.md`](../README_OTA_FIX.md) — naprawa OTA sprzed 1.6.19.

Oba opisują stan z października 2026 i **nie są już aktualne**; traktuj je jako
zapis historyczny.

## Jak dodawać wpisy

Przy każdym podniesieniu wersji dopisz wiersz w odpowiedniej sekcji:

```
| **X.Y.Z** | RRRR-MM-DD | Krótki opis zmian (1–2 zdania). |
```

Zakresy sekcji: `1.7.x` (niezawodność), `1.6.x` (fundament). Kolejne główne
wersje dostają własną sekcję na górze.
