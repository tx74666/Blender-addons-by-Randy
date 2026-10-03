# Character Designer 0.69.4: Cleanup after scene data is available

Local source change on 2026-10-02.
No Git commit, push or published release.

## Behavior

Blender can enable an add-on while its data and context are restricted. The
previous cleanup ran immediately during registration, before scene objects
were available. This could interrupt registration and leave legacy generated
names visible; saving or exiting had not destroyed their recovery records.

Registration and file load now queue one persistent timer. It waits for usable
scene data before validating and migrating legacy widget and skirt names.
Duplicate registration reuses the pending task; unregistering cancels it.
Cleanup errors are reported without disabling the add-on. The existing atomic
rename/reference migration and ownership checks remain in use.

Recovery records stay in object properties and are saved with the blend file.
The fix retains geometry, weights, Shape Keys, animation, rest and pose state.
Existing animation bone and constraint names remain unchanged.

## Validation

- Four native Blender 5.2 lifecycle checks pass: actual restricted add-on enable,
  deferred-task cancellation, disable/re-enable record retention, and automatic
  cleanup of a saved legacy scene followed by idempotent cleaned save/reopen.
  The save/reopen checkpoint checks geometry, weights, Shape Keys, Actions,
  rest and pose preservation.
- A read-only check of the saved X scene confirmed that its records were intact
  and all 117 legacy object/data/collection names could be migrated. Evidence:
  X project's `validation/generated_names_followup_20261002.json`.
- With the user's authorization, the currently open X scene was cleaned:
  117 names migrated, zero resources skipped. `X.blend` was saved at 14:48:15
  local time. A backup made before cleanup has SHA-256
  `fd09788702fea9986f332bb8061bff3f35a28a1ee940e703eec2075cffa62495`.
- Seven generated-name migration checks also pass, covering readable names,
  ownership, collisions, rollback and retained skirt data.
- The actual pre-cleanup backup also passed the read-only migration check:
  117 names migrated in memory, no resources skipped, and authored geometry,
  weights, Shape Keys, animation, rest and pose state unchanged. The backup's
  disk hash remained unchanged. Evidence:
  X project's `validation/generated_names_actual_backup_20261002.json`.
- The saved, cleaned `X.blend` was reopened in an isolated background process:
  2 armatures, 10 widget resource groups and 1 skirt validated; both cleanup
  passes found zero renames and zero skipped resources. The scene file was
  untouched by this check. Evidence:
  X project's `validation/generated_names_saved_clean_20261002.json`.

## Local release

Built `dist/character_designer-0.69.4.zip` with 125 files. SHA-256:
`1a223b63e37f44fc8f42cb993ac46a9b2352067bc63afd6a0aa8d818e496aeb9`.

Deployed canonical source to the Blender 5.2 user installation and
`D:\Blender\Projects\Character\X\addons\character_designer`.
`tools/deploy_local.py --module character_designer --project-addons
D:\Blender\Projects\Character\X\addons --check` reports zero different files
in both destinations. Previous installed files were retained under
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20261002-145210-e516a426`.

The live X scene has been cleaned and saved; the existing user Blender sessions
remain open. Changes are local, with no commit, push or published release.
