# Character Designer 0.62.10 — reuse existing Fingers tools

Body Setup's Fingers page duplicated the existing finger tool's reference checks,
Apply/Confirm flow and preview, producing a long diagnostic list and per-bone XYZ
labels. Fingers is now navigation to the existing panel only. Capture Detection
and its eye toggle retain their original behavior.

Changes relative to 0.62.9:
- `body_calibration.py`: active confirmation parts are Arms and Legs. This updates
  the shared gate used by Body Plan, Generate and the advanced limb builder.
- `body_calibration_ui.py`: keep Fingers navigation; omit its duplicate status,
  preset, Show Directions, validation, Apply/Confirm and Details controls.
- `body_calibration_overlay.py`: return before cache/reference processing for
  Fingers; remove the repeated finger-bone XYZ display.
- `__init__.py`: version 0.62.10.
- Existing saved enum numbers, explicit legacy Fingers service APIs, captures,
  confirmations and reference records remain compatible. Original finger safety
  validation and Generate's existing eye-closing behavior are unchanged.

Actual Blender 5.2.0 LTS validation, isolated factory-startup processes, serial:
- Arms/wrist integration: 9 tests passed, including original Fingers panel
  routing, no duplicate reference traversal, empty Body overlay and unchanged eye.
- Calibration integration: 29 tests passed, including unconfigured real finger
  bones reporting ERROR in the legacy check while Generate/Update/Remove succeed
  and preserve native Rest, mesh, keys, weights and historical finger records.
- Display/cache: 13 tests passed, including legacy finger reference invalidation.
- UI routes: 4 checks passed.
- Independent read-only review: no blockers in the three runtime diffs.

Installed locally and hot-refreshed in the user's Blender. All 110 package files
match both installation targets. Live snapshot comparisons confirmed original
Rest, pose, mesh coordinates, weights, Shape Keys, finger records and object
inventory unchanged; original .blend was not saved. No commit or push.

Full animation acceptance was not repeated for this UI/gate change (未验证).
No new performance timing claim; the removed Body finger reference traversal is
covered by regression assertions.

Package: `dist/character_designer-0.62.10.zip`

SHA256: `f975ff0b4673e51c752d8d4e5e7dc9c059974b536e9e5c40be27abbc0f721970`

Detailed logs and live install evidence:
`D:\Blender\Projects\Character\X\task_artifacts\body_calibration_20260927\fingers_0_62_10`

Previous deployed files backup:
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260927-225654-6c377363`
