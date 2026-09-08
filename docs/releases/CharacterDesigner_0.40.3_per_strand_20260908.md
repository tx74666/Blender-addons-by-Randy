# Character Designer 0.40.3 — independent hair chains

Validated on 2026-09-08 with Blender 5.2.0 LTS, hash fbe6228777e7.

## Result

New hair versions always create one deform chain per captured strand. The
Grouped selector and group-edit operators are removed from the main workflow.
Shared-chain plans are rejected before changes occur, including callers that
try to submit legacy member plans through the Per Strand API.

Strand detection, capture, recapture, weighting, Mirror controls and version
switching remain. Old Grouped scenes keep their guides, rigs, weights and
animation. Their captured native strands can generate independent new versions.
Legacy capture/group records and version readers remain for compatibility.

The capture path reuses a mesh edge list for the batch. Old missing, duplicated
or malformed group guides no longer block independent capture or generation.
An invisible source in Edit Mode now produces a visibility-specific error,
without changing the user's collection visibility.

Shared procedural motion is a subsequent feature, not part of this release.

## Validation

The following canonical suites passed using background Blender, factory
startup, disabled file auto-execution and `--python-exit-code 1`:

- `tests/test_hair_bones_ui_blender.py`: registration, retired RNA/operator
  removal, ignored legacy mode data, independent generation and recapture.
- `tests/test_hair_bones_groups_blender.py`: 7 capture/legacy-record checks,
  including malformed guides and save/reopen.
- `tests/test_hair_bones_variants_blender.py`: 8 version, atomic rejection,
  rollback, hidden-layer and native reopen checks.
- `tests/test_hair_bones_mirror_controls_blender.py`: 8 checks, including
  independent left/right controls, one center chain and native reopen.
- `tests/test_hair_bones_rig_blender.py`: 9 rig and native reopen checks.
- `tests/test_hair_bones_legacy_compat_blender.py`: uses the frozen 0.40.2 ZIP
  to actually create and save a Grouped scene, then opens it in 0.40.3. Existing
  weights, shape-key animation, rig animation and guides are preserved; four
  captured strands produce four new independent chains and old/new versions
  remain switchable. Keep `dist/character_designer-0.40.2.zip` for this fixture.
- `tests/test_addons_together_blender.py`: both RR Helper / Character Designer
  registration orders passed.

The existing X integration test also passed against canonical 0.40.3 sources
on a private copy of the current on-disk X.blend. The source Hair collection
was hidden in its View Layer, so only that collection path and the source were
shown inside the private, unsaved test process.

| Hair3 measurement | Result |
| --- | --- |
| Captured half-mesh strands | 7 |
| Full generated chains | 13: 6 left, 6 right, 1 center |
| Deform bones | 52, plus 1 attachment bone |
| Maximum bind-position error | 4.91e-7 |
| Maximum head-follow error | 3.94e-7 |
| Opposite-side movement when testing one side | 0 on both sides |
| Source mesh and original main rig | Preserved |

The private input file's SHA-256 remained
`4d1df4df1589840418c601e7a4c6d3a17ad95adbf14492c17c223833648c09a1`.
The test did not save changes to the current scene.

A separate fresh process using saved user preferences loaded the installed
0.40.3 package, validated registration, confirmed retired hair settings absent,
and reported zero Forearm Twist errors. Other installed polygoniq modules
printed log-rotation file-lock messages because another Blender was running;
the Character Designer startup assertions and process exit succeeded.

## Package and deployment

- Package: `dist/character_designer-0.40.3.zip`, 25 shipped files.
- SHA-256: `9836322a0fcfe3826d1bf821c96d82c15063a556ea280883be6640f875da8de7`.
- Ran `python tools/build_releases.py`.
- Ran `python tools/deploy_local.py --project-addons D:\Blender\Projects\Character\X\addons`.
- Repeated with `--check`: zero differing files in the Blender installation
  and X validation copy; RR Helper unchanged.
- Previous deployment files backed up under
  `C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260908-135315-77b4e328`.
- Refreshed the running X Blender with its Refresh Add-on button and visually
  verified `One independent chain per strand.`, no Grouped selector, and the
  existing 13-chain version still displayed. Did not save or close the scene.
- `git diff --check` passed. Changes are local and uncommitted, not pushed.
