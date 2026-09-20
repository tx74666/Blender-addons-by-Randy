# Character Designer 0.61.42 — combined reference and joint previews

## Diagnosis and fix

The persistent-monitor refresh path introduced in .40 overwrote an already
queued, populated reference snapshot with an empty one when later dependency
notifications arrived. The joint preview rebuilt correctly, but its attempt to
restore validated Start/End frames had nothing to restore. Showing both visuals
then paid the full per-hand reference/containment checks on every slider step.
The previous test used a direct cache clear, not the complete notification sequence.

Keep the first pending request per owner/active finger (joint values are still
read live). Queue the reference snapshot before invalidation, and prevent
self-enqueue while rebuilding. Reference timers complete a pending joint proof
outside drawing before deciding whether their own full validation is necessary.
This removes the ordering race and shares already-proven unchanged reference data.

No layers are hidden, no samples dropped and no safety/geometry/weight logic
changed. Full equality checks still cover mesh, keys, attributes, normals and
weights; changed source or guide domain/records cannot reuse an old proof.
Generation/weights retain uncached write validation. No new recurring polling,
runtime services, dependencies or user settings.

## Measurements

All use one Blender 5.2 background process at a time, `--threads 1`, against a
fixed disposable copy of the saved X asset (3,418 vertices). Source file hash:
`6a6189d436f850057920fe63551796381df4b7bd45fd82552cf7882a21df9a5e`.
Embedded asset scripts were disabled. No user's open/saved asset was written.

Initial diagnosis with cProfile enabled: one pair plus rings, median 638.35 ms
on installed .41, 128.02 ms on the patched source. The latter also forced the
reference consumer to run first to exercise the formerly problematic ordering.
Reference validations over ten drag updates fell from 20 to 0. Both runs had
frame hash `46001e7708696f23dc62f051d20d7ac8760ddd0e748d47290ba5a3ecaa7731da`.

Five pairs plus active joint rings, without cProfile: baseline 1,740.53 ms versus
patched reference-first 126.59 ms median; 100 reference validations versus 0.
Frame hash was identical:
`de0ce080e848c9cfdb99859daf57c2aa7db67235b89b05376eb82f11d2e2d99a`.
The additional cached lookup in the reference-first case is included, not removed
from the patched timing. Idle updates were about .43-.53 ms in this fixture.
All runs assert unchanged mesh and blend-file hashes and expected visible counts.

These are ten-step warm CPU update samples under fluctuating workstation load,
not a promise of input-to-display latency or 60 FPS. Cold initial validation and
complete per-drag source equality remain real work. GUI rendering and the user's
live-window fluidity are not measured by this background benchmark.

## Validation / deployment

All **51 background regression cases passed** on the final source: combined
preview 6, eye/lifecycle 11, complete workflow 12, drag/proof 6, preview cache 5,
multi-selection 5 and joint defaults 6. The combined tests include real
dependency notifications and either timer order, changed source/guide data,
ten visible references, explicit Hide, Undo lifecycle callbacks and idle behavior.
The existing persistence tests save/reopen only their synthetic temporary fixtures.
No new GUI/native mouse or screen-frame-rate test was run. Compileall and diff
whitespace checks passed (existing Git LF/CRLF warnings are unchanged).

No Unity/project-scene modifications, app closures, git commit or push. Local
build and deploy use the canonical scripts; installed/X file equality is checked
after copying. The user's running Blender is not reloaded/restarted by this task.

Release `character_designer-0.61.42.zip` verified 104 files. Both the Blender 5.2
installation and X's validation add-on copy updated three runtime files; final
`deploy_local.py --check` reported zero differences for each destination. Previous
files: `C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260920-215511-16ce35a2`.
