# Character Designer 0.62.16 — one Body workflow

Body Controls no longer has Setup / Controls tabs. The Arms / Legs / Fingers
checklist remains in place; Generate appears immediately below Fingers when
all applicable parts are confirmed. Generated rigs immediately expose the
existing posing controls and Update / Remove actions. Default / Custom remains
unchanged at the user's request, pending discussion. Show Directions is now
Preview Directions, and that toggle remains reachable after generation.

Fingers reuses the original Capture tool's saved confirmations. All recognized
native/survey finger keys and actual saved slots must have matching source,
bank key, internal capture data, confirmation and no pending capture. Empty
preallocated slots do not invent missing anatomy. Explicit Character Setup Body
takes precedence over unrelated candidate meshes. No BMesh, geometry, Roll audit,
additional Finger Apply or Confirm is introduced. Only an incomplete selected
Fingers page shows a short explanation. The Generate operator repeats the check
at execution time for first build; existing Update / Remove remains accessible.

The original calibration, Direct IK solver and Rest-preservation code are
unchanged in this release. Legacy saved tab values remain registered, ignored
by routing, and are not migrated. Advanced native bone mapping is still available.

## Actual validation

Blender 5.2.0 LTS, sequential disposable factory-startup processes:

- New workflow / Finger completeness and execution gates: 13 tests passed.
- Arms / wrist integration and UI routing: 12 passed; rerun after the final
  post-generation preview-toggle fix, 12 passed again.
- Hands compatibility: 10 passed.
- Display / cache: 15 passed.
- Calibration / Generate / Update / Remove / rollback: 30 passed.
- Body Setup UI end-to-end script: passed, including actual Generate twice,
  original posing controls, damaged-inventory Remove access, and native Rest
  preservation after Remove.
- Main UI page routing: 4 checks passed.

The initial calibration suite run had one outdated test Layout proxy lacking
split(). The proxy was updated; all 30 passed on rerun. The failed log is retained
as initial_layout_proxy_failure.log. Runtime changes passed independent read-only
review after correcting two P2 cases: empty preallocated slots and explicit Body
selection being blocked by an unrelated multiply bound mesh.

Installed canonical sources in both Blender 5.2 and X/addons; --check reports
110 files, zero differences for each target. Refreshed only changed functions
in the running Blender, preserving registered state and the Twist session.
CoshaRig shows Arms / Legs / Fingers CONFIRMED and Generate directly below the
checklist. This layout was visually inspected in the actual window.

Live before/after verification preserved native Rest, pose matrices, mesh
coordinates, weights, Shape Keys, Finger records, selection, preset, eye state
and objects. The original .blend was not saved; no Apply or Generate was run
on the user's original character. Ten warm saved-Finger-state queries averaged
about 0.77 ms on that character; this measures metadata queries only, not total
viewport rendering or Forearm Twist performance.

Full real-character motion acceptance and undo/redo of generation were not
repeated for this UI release (未验证). Background fixture coverage is not a
substitute for real-character motion acceptance.

## Delivery

Runtime changes from 0.62.14: __init__.py, body_calibration.py,
body_calibration_ui.py, body_calibration_hands.py, body_setup_ui.py, limb_ik.py.
0.62.15 is the intermediate immutable package; 0.62.16 additionally keeps the
preview visibility switch available after Generate.

Evidence directory:
`D:\Blender\Projects\Character\X\task_artifacts\body_calibration_20260927\unified_0_62_15`

The directory's manifest.json records the exact package/source-and-tests ZIP,
SHA256 hashes, Git base and per-file hashes; changes_from_0.62.14.patch contains
the runtime diff. The directory retains its initial working version name.

Deployment backup before this work:
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260927-234202-d3284f68`

Local, uncommitted, not pushed. Unrelated existing repository changes preserved.
