# Testy

Cały zestaw testów żyje w **jednym pliku** `test_app.py` (109 testów, ~26 s).
Nie dziel go na osobne pliki — CI uruchamia dokładnie ten plik.

## Spis treści

- [Uruchamianie](#uruchamianie)
- [Jak działają testy](#jak-działają-testy)
- [DummyPage](#dummypage)
- [Fixture i izolacja](#fixture-i-izolacja)
- [Zakres pokrycia](#zakres-pokrycia)
- [Jak dopisać test](#jak-dopisać-test)

---

## Uruchamianie

```powershell
python -m pytest test_app.py -q                     # całość
python -m pytest test_app.py -q -k test_tasks_crud  # pojedynczy test
python -m pytest test_app.py -v                     # jak w CI
```

Na Windows CLI `flet` istnieje tylko jako `.venv\Scripts\flet.exe`. Testy
uruchamiaj w aktywnym `.venv` albo jawnie: `.venv\Scripts\python.exe -m pytest ...`.

---

## Jak działają testy

- Testy logiki wołają funkcje `app_logic` na tymczasowej bazie w `tmp_path`.
- Testy UI budują interfejs przez `app_logic.main(page)` z podstawionym
  `DummyPage`, a następnie szukają kontrolek po indeksach (patrz
  [`architektura.md`](architektura.md) → „UI: struktura i kontrakty”).
- Sieć (Gist) jest mockowana.

---

## DummyPage

`DummyPage` naśladuje atrybuty `ft.Page` potrzebne do zbudowania widoku:
`controls`, `overlay`, `services`, `update()`, `run_task()`, `platform`,
`width`/`window.width`, `client_storage` itd. Dzięki temu testy nie potrzebują
działającego serwera Flet.

Typowe wzorce w testach:

```python
app_logic.main(page)
content_col = page.controls[0].content.content
overlay = page.overlay          # dialogi, kontrakt indeksów
```

---

## Fixture i izolacja

Autouse fixture:

- przekierowuje bazy do `tmp_path` przez `app_logic.LOCAL_DB` / `app_logic.CONFIG_DB`
  (**patchuj `app_logic`**, nie tylko `main`),
- przekierowuje **wszystkie** ścieżki OTA z `main` (`OTA_DIR`, pliki `OTA_*`,
  reset `_PREPARED_CACHE`),
- ustawia `DISABLE_OTA=1` tam, gdzie trzeba.

Dzięki temu testy **nie piszą** do folderu użytkownika ani nie zostawiają plików
w repozytorium.

> Nie usuwaj `DISABLE_OTA=1` z fixture — część testów tego wymaga.

---

## Zakres pokrycia

Testy pogrupowane tematycznie:

| Obszar | Przykładowe przypadki |
|---|---|
| CRUD zadań | `test_tasks_crud`, `test_task_edit_and_delete` |
| Priorytety / kategorie / dźwięk | `test_task_categories_and_sound` |
| Rutyny | `test_routines_crud`, `test_routine_days_crud`, `test_daily_routines_filtered_by_weekday` |
| Historia rutyn i serie | `test_routine_log_records_history_and_survives_end_of_day`, `test_routine_week_view_and_soft_streak` |
| Podkroki | `test_subtasks`, `test_subtask_edit_and_delete`, `test_subtask_reorder` |
| Postęp | `test_progress_calculation`, `test_progress_hybrid_edges`, `test_daily_progress_empty_shows_no_tasks_text` |
| Migracje schematu | `test_init_db_migrates_old_routines_schema`, `test_deadline_migration_adds_column`, `test_estimate_migration_old_db`, `test_restore_from_bytes_migrates_schema` |
| Terminy | `test_deadline_db_crud_and_details_update`, `test_deadline_sorting_filtering_and_reminder_window` |
| Szacowany czas | `test_estimate_normalization_and_formatting`, `test_task_estimate_db_crud`, `test_undo_restores_estimate` |
| Znaczniki czasu / duchy | `test_task_timestamps_created_and_updated`, `test_stale_task_detection_rules` |
| Inbox / triage | `test_quick_dump_goes_to_inbox`, `test_triage_inbox_task_db_moves_and_clears` |
| Przełom dnia | `test_apply_day_rollover_archives_once_and_honours_choices`, `test_day_rollover_ui_confirm_moves_task_to_master` |
| Archiwum | `test_completed_archive_lifecycle` |
| Snapshoty / integralność | `test_snapshot_rotation_and_integrity_check`, `test_json_export_import_and_snapshot` |
| Synchronizacja | `test_sync_security`, `test_folder_sync_and_archive`, `test_pull_does_not_overwrite_newer_local_data` |
| Konflikty sync | `test_sync_conflict_state_helpers`, `test_sync_conflict_blocks_push`, `test_sync_conflict_banner_visible` |
| UI PC/mobile | `test_mobile_task_card_layout_and_actions`, `test_mobile_touch_targets_at_400px`, `test_ui_edit_delete_dialogs` |
| Cofnij (undo) | `test_ui_delete_task_now_undo_restores_fields`, `test_ui_delete_subtask_now_undo_restores_position` |
| Wibracja | `test_haptic_calls_vibrate_for_timer_and_task`, `test_haptic_not_attached_on_windows` |
| Mini-minutnik | `test_mini_timer_visible_and_touch_size_on_mobile`, `test_mini_timer_reset_works_from_master_view` |
| Kolejka UI / resize | `test_ui_update_scheduler_coalesces`, `test_resize_rebuilds_only_on_mobile_threshold` |
| Kontrast | `test_secondary_text_contrast_on_cards` |
| OTA | `test_ota_rollback_and_failed_version`, `test_manual_ota_install_and_activate`, `test_older_ota_cache_is_ignored` |
| Wersje | `test_version_files_are_in_sync`, `test_entrypoint_is_not_shadowed` |
| Gist (mock) | `test_gist_cloud_sync`, `test_gist_pull_skips_older_cloud` |
| Higiena dokumentacji | `test_module_level_functions_have_docstrings` |

---

## Jak dopisać test

1. Dodaj funkcję `def test_...(…):` w `test_app.py`. Dla logiki z bazą użyj
   `tmp_path`/`monkeypatch`, dla UI — `DummyPage`.
2. Nie zakładaj kolejności globalnej — każdy test dostaje czyste bazy.
3. Jeśli testujesz konflikt synchronizacji, ustaw `sync_mode="none"` na czas
   zmian lokalnych (inaczej tryb `folder` od razu pushuje i odświeża
   `last_synced_hash`).
4. Uruchom **cały** plik, nie tylko nowy test.
5. Zaktualizuj licznik w tabelach dokumentacji (109+).

Testy migracji starej bazy piszemy zawsze przy zmianie schematu — budują bazę
bez nowej kolumny i sprawdzają, że aplikacja ją doda i nie wywali się po
podmianie pliku (`no such column`).
