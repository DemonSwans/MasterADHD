# Dokumentacja Master ADHD

Indeks dokumentacji projektu. Zacznij od [`../README.md`](../README.md), jeśli
chcesz tylko uruchomić lub zainstalować aplikację.

## Dla użytkownika

| Dokument | Zawartość |
|---|---|
| [`funkcje.md`](funkcje.md) | Wszystkie funkcje i widoki, krok po kroku. |
| [`instalacja.md`](instalacja.md) | Instalacja na PC i Androidzie, pierwsze uruchomienie. |
| [`synchronizacja.md`](synchronizacja.md) | Chmura, folder, Home Assistant, konflikty i kopie. |

## Dla dewelopera

| Dokument | Zawartość |
|---|---|
| [`architektura.md`](architektura.md) | Warstwy, przepływ sterowania, struktura UI, wątki, kontrakty testów. |
| [`dane.md`](dane.md) | Schemat SQLite, klucze konfiguracji, migracje, JSON, snapshoty. |
| [`ota.md`](ota.md) | Mechanizm OTA, wersjonowanie, rollback, proces wydania. |
| [`testy.md`](testy.md) | Zestaw testów, `DummyPage`, fixture, jak pisać nowe przypadki. |
| [`rozwoj.md`](rozwoj.md) | Konwencje kodu, definicja ukończenia, zasady zmian. |

## Historia

| Dokument | Zawartość |
|---|---|
| [`historia-zmian.md`](historia-zmian.md) | Historia wersji od 1.6.x do 1.7.0. |
| [`../AGENTS.md`](../AGENTS.md) | Zwięzłe fakty techniczne, znane pułapki, backlog (dla agentów AI). |
| [`../FIX_SUMMARY.md`](../FIX_SUMMARY.md) | Archiwalne: podsumowanie zmian v1.6.17. |
| [`../README_OTA_FIX.md`](../README_OTA_FIX.md) | Archiwalne: naprawa OTA sprzed 1.6.19. |

## Konwencje tej dokumentacji

- Język: **polski** (spójnie z UI i kodem). README ma dodatkowo wersję
  angielską: [`../README.en.md`](../README.en.md).
- Fakty techniczne trzymamy w jednym miejscu i linkujemy, zamiast kopiować.
  Twarde kontrakty (kolejność `page.overlay`, indeksy kontrolek) opisuje
  `architektura.md` i `../AGENTS.md`.
- Każda zmiana widoczna dla użytkownika powinna zaktualizować `funkcje.md`
  i `historia-zmian.md`.
