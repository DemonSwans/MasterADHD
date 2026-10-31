# Sprint 1 — „Mniej tarcia”: dokończenie (ZAMKNIĘTY, v1.6.25)

Plik rozlicza Sprint 1. Wszystkie punkty są **wykonane i przetestowane**
(`.\.venv\Scripts\python.exe -m pytest test_app.py -q` → 67 zielonych).
Wszystkie zmiany są w `app_logic.py` (OTA) — `main.py` nietknięty, więc nie
trzeba nowego APK.

## Podsumowanie stanu

| Punkt | Status | Dowód / test |
|---|---|---|
| 1.1 Wibracja | **gotowe** | `test_haptic_calls_vibrate_for_timer_and_task`, `test_haptic_missing_attribute_does_not_crash`, `test_haptic_respects_sound_enabled`, `test_haptic_not_attached_on_windows`, `test_settings_sound_card_mentions_vibration` |
| 1.2 „Cofnij” zamiast dialogu | **gotowe** | `test_ui_delete_task_now_undo_restores_fields`, `test_ui_delete_subtask_now_undo_restores_position`, `test_ui_master_delete_task_now_undo` |
| 1.3 Kategoria z Daily | **gotowe** | `test_ui_edit_task_from_daily_keeps_category`, `test_ui_edit_task_from_daily_without_subtasks_keeps_category` |
| 1.4 Dotyk i czytelność | **gotowe** | `test_mobile_touch_targets_at_400px`, `test_secondary_text_contrast_on_cards` |
| 1.5 Globalny mini-minutnik | **gotowe** | `test_mini_timer_visible_and_touch_size_on_mobile`, `test_mini_timer_hidden_without_active_timer_mobile`, `test_mini_timer_reset_works_from_master_view` |

## Co dokładnie zrobiono

### 1.1 Wibracja (opcjonalna, bez powiadomień systemowych)

- `init_haptic_service(page)` (dawniej) + rozszerzenie UI: etykieta w Ustawieniach
  zmieniona na **„Dźwięk i wibracja”**, przełącznik „Dźwięk i wibracja przy
  sukcesie i końcu minutnika”, opis wyjaśnia zachowanie na Windows i telefonie.
- Wibracja wywoływana przez `trigger_haptic_feedback(...)` przy końcu minutnika,
  oznaczeniu zadania/rutyny i „Cofnij”; respektuje `sound_enabled`.
- **Testy z mockiem** `_FakeHaptic` (podstawiany za `ft.HapticFeedback`):
  mapowanie `timer_done→vibrate`, `task_done→light_impact`, `undo→selection_click`;
  brak klasy → `None` bez wyjątku; `sound_enabled=0` wycisza; na `windows` usługa
  nie jest przywiązywana. `DummyPage` dostała `services`, `platform` i `run_task`.

### 1.2 „Cofnij” zamiast okna potwierdzenia

- Karty Daily (z podkrokami i bez) oraz Master wołają `delete_task_now`;
  usuwanie kroku woła `delete_subtask_now` (wcześniejszy `delete_subtask_action`
  usunięty — nie ma go już w kodzie, więc klik „usuń krok” nie rzuca `NameError`).
- Dialog usuwania został w `page.overlay[4]` jako kontrakt testów.
  `open_delete_task_dialog` **nie ma już żadnego wołającego** (poza definicją) —
  potwierdzone `grep`; zostaje na przyszłe operacje zbiorcze.
- Cofnięcie przywraca pełny wiersz (`restore_task_row_db`) lub krok na tej samej
  pozycji (`restore_subtask_db`); powtórne „Cofnij” nie duplikuje.

### 1.3 Kategoria przy edycji z Daily

- Obie gałęzie Daily przekazują `current_category=category` (z default-argiem
  w lambdzie). Testy wariantu z podkrokami i bez.

### 1.4 Dotyk i czytelność

- W `refresh_view` dodano lokalny helper `_touch(default)` → `44` na mobile,
  `default` na PC. Objęte nim: edytuj/usuń/dodaj krok, strzałki kroków, akcje
  nagłówka, szybkie dodawanie oraz play/reset minutnika.
- Dodano `readable_text_color(bg, preferred, fallback, minimum=4.5)`, który
  używa `contrast_ratio()` (WCAG 2.1); metadane kart na ciemnym tle zostają
  `COLOR_TEXT_SECONDARY`, a przy zmianie tła degradują się do `COLOR_TEXT_PRIMARY`.
- Test 400 px sprawdza, że każdy `IconButton` w kartach i minutniku ma ≥ 44 px;
  test kontrastu pilnuje progu 4.5:1.

### 1.5 Globalny mini-minutnik

- Chip (`_build_mini_timer`/`update_mini_timer`) ma teraz **pauzę/wznowienie** i
  **reset**, więc działają z każdego widoku; klik w tło chipa wraca do Daily.
- Na mobile chip ma ≥ 44 px wysokości, a jego przyciski ≥ 44 px.
- `mini_timer_mob_wrapper.visible` chowa razem licznik **i** `Divider` — samotny
  pasek nie wisi już nad dolną nawigacją.
- Testy: widoczność/rozmiar na mobile, ukrycie gdy brak minutnika, reset z Master.

## Uwagi

- Nowe `IconButton` w chipie nie zmieniają indeksów `root_layout.controls`
  (`[1]` to nadal `mini_timer_mob_wrapper`).
- Zero nowych zależności; `main.py` bez zmian → wszystko dociera przez OTA.
- Wersja podniesiona do **1.6.25** (`version.txt` + `FALLBACK_VERSION`).

## Weryfikacja

```powershell
.\.venv\Scripts\python.exe -m pytest test_app.py -q
```

Kryterium: cały plik zielony (67 testów), bez nowych cichych `except: pass`.
