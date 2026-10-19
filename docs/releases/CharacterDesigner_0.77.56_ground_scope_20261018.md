# Character Designer 0.77.56 · Grounding Scope

Validation record, 2026-10-18. The frozen package passes 29 native fixture
checks and four pure UI checks. Saved-X grounding, signed movement and
save/reopen passed `AUTHORED_INPUTS_AND_BOUNDED_PLACEMENT_V1`; this accepts exact
authored inputs and bounded placement, not invariant evaluated IK matrices.
Both installed copies match the approved package. The artist's UI state was
saved without invoking Ground or Rebuild.

## Behavior and narrow scope

**Rig > Body > Character Placement > Ground Character** moves the recognized
character so its currently displayed, evaluated mesh bottom reaches world Z=0.
**Move Character** applies the signed world-Z distance. The main rig, its
recognized attached rigs, bound meshes and descendants move together through
their deduplicated roots; local bone Rest and pose inputs remain unchanged.
Rebuilding controls and grounding are independent actions. Rebuild does not
automatically ground or move the character.

The fixed X input contained `Cosha.001`, still parented to `CoshaRig` but linked
to no scene or collection. The former traversal rejected it as a child outside
the current scene. Placement now skips a branch only if its entire descendant
subtree belongs to no scene. It does not remove that branch, change its parent,
edit its local transform or alter its data. Such a branch cannot define the
displayed character's ground height.

If any descendant belongs to a scene, the existing refusal remains. This
includes direct children in another scene, scene-less intermediaries leading
to another scene, and intermediaries leading back into the current scene.
There is no new permissive cross-scene traversal or guessed collection owner.

In this saved input, the recognized current-scene scope is 18 objects: one
`CoshaRig`, six meshes and 11 Dress curves. `CoshaRig` is the only movement
root. Shoes already belongs below that rig, so its existing Object Action is
admitted by the unchanged child-object rule. This release does not enable
animated detached roots or add a delta-transform placement scheme. `_admit`,
the numerical limits, original skeleton protection and mesh-shape verification
are unchanged.

## Approved source and fixture validation

The base is accepted `dist/character_designer-0.77.55.zip`, SHA256
`5b29d508f2bd15fad25470b3d645804beb743d2640c225fa37f209ce49817001`.
Candidate01 contains 202 files. Its only changed files are `__init__.py`
(version 0.77.56) and `character_ground.py`. Full-module AST comparison proves
that Ground changes only `_scope` and the added `_scene_less_subtree` helper;
the other 200 files retain the accepted .55 bytes.

Manifest SHA256:
`cf091c23a1bce533104ba2f0548f6cae5944cfba9bec2f21926134fc802624f4`.
Ground source SHA256:
`d9b2135337828a2e32054cc35ba46745b61e2ce9e21b5efef3d91826f583c222`.
Paused .48 watcher/provenance/display optimizations remain excluded. The
canonical initializer's only task mutation is its version tuple; the frozen
initializer comes directly from accepted .55 plus that version change.

| Check group | Result |
| --- | --- |
| New orphan-subtree scope and cross-scene refusal fixtures | 5 / 5 pass |
| Existing whole-character movement, dependency and rollback fixtures | 24 / 24 pass |
| Frozen Ground UI, without geometry evaluation during drawing/polling | 4 / 4 pass |

Native checks use disposable Blender 5.2 factory-startup processes and the
verified frozen source. The UI result uses the frozen-subject V3 runner.
Neither group opens or saves the artist's file. Scope refusal checks require
unchanged placement, hierarchy and authored data; orphan acceptance retains
native IDs and local inputs instead of deleting the orphan.

## Accepted fixed X integration

The immutable input is `input_saved_X.blend`, 34,544,216 bytes, SHA256
`ab14236e94d4ae7e41bf581f15eb3ff6084930fd82ade97cf55ecd54ea2dba47`.
It is a copy of a user-saved disk state, not a snapshot of newer unsaved work.
The V3 report passed with all three operations, save and reopen actually
completed. Each stage retains the same 19 mandatory authored-state,
object-movement and mesh-shape checks. Raw meshes and curves, original Rest,
local pose inputs, shapes and the declared fingerprints for 13 Actions were
preserved; all 18 scoped world transforms moved once and all six admitted
mesh outputs stayed inside the unchanged numerical bounds.

| Stage | Requested world-Z move | Evaluated lowest Z afterwards |
| --- | --- | --- |
| Ground | `-0.07365912199020386` | `-3.110617399215698e-7` |
| Signed move | `+0.123` | `0.12299999594688416` |
| Ground again | `-0.12299999594688416` | `-3.110617399215698e-7` |
| Save and reopen disposable result | No additional move | `-3.110617399215698e-7` |

`result_v3.blend` is the separate validated result, 34,352,045 bytes, SHA256
`1f88d3382cb251b6f828c09bdd28d1ec00304159063c69528fd944b2fa608d1c`.
The versioned writer passed check-only validation and created
[acceptance_summary_v2.json](D:/Blender/Projects/Character/X/validation/character_ground_scope_20261018/acceptance_summary_v2.json),
SHA256 `ed4c3f2a9cfdff8bc28849cb1648576e55283b3602618717ccea22efd69f779d`.
Save/reopen compares names, bindings and content; old process-native pointers
are not required to survive reopening.

The first integration attempt retained complete per-keyframe RNA snapshots and
grew to approximately 2.75 GB. It was stopped for resource control while free
memory approached 400 MB; this is neither a product failure nor an accepted
integration result. Its report and resource-stop receipt remain preserved.
V2 and V3 instead stream fingerprints of the established .55 Action-key fields
plus recorded metadata for 13 Actions. This does not cover every keyframe RNA
property. The baseline and later comparisons use that same declared policy.

## Evaluated-output limits and preserved failures

V2 remains failed: its signed-move focus-matrix check measured
`2.7179718017578125e-5` against `2e-5`, although its other 19 checks passed.
The historical gate is not increased or relabeled as satisfied. V2 stopped
before Ground again or save/reopen; the accepted sequence above belongs to V3.

The lean follow-up `pose_float_report.json` measured zero evaluated-pose
residual in passive and both tagged no-motion cases. Actual translation
produced small output differences: Ground's focus matrix maximum was
`2.0168721675872803e-5`, and the signed move maximum was
`7.137656211853027e-6`. The Ground case included a right hand/finger basis
difference of about `0.00117` degrees and endpoint differences of a few
`1e-6`. These are observed translation-dependent solver outputs; no-motion
jitter is not an explanation supported by this diagnostic. Its separate
fresh-load/evaluation sequence also had a different starting ground height,
so it does not exactly reproduce V2's offender values or issue full asset
acceptance.

The reviewed V3 probe uses schema `x.saved-character-ground-scope/2` and the
explicit policy `AUTHORED_INPUTS_AND_BOUNDED_PLACEMENT_V1`. Its 19 mandatory
authored-state, object-movement and mesh-shape checks retain their existing
meaning and bounds. The historical focus-matrix `2e-5` result stays unchanged
as a diagnostic that may be false; the probe does not claim exact evaluated
hand/foot pose or every evaluated matrix component within `2e-5`. Per-bone
physical metrics and a cumulative return diagnostic disclose the residual.
In accepted V3, signed move and Ground again retain a focus-matrix maximum of
`2.7179718017578125e-5`, so the historical component diagnostic is still false.
This does not become an exact evaluated-pose claim because V3 passes its
separately named policy. Authored-state, world-translation and mesh bounds are
unchanged. The report retains per-bone physical and cumulative return metrics.

## Package and delivery

The built `dist/character_designer-0.77.56.zip` has SHA256
`3b6090682cde3d773c477d7031be1e116a8a4e06f6014c96263a46a2e63a7b5b`.
`deployment_check.json` records successful verification of both Blender 5.2's
user add-on installation and X's validation add-on copy: 202 files and zero
differences against the same frozen projection at each destination.

In the live artist window, PID40116 / window 124194646, Refresh Add-on was
clicked. Its Refreshing state completed and the refresh button disappeared.
The Rig tab and `CoshaRig` were then selected; **Move Character** and **Ground
Character** were visible and enabled under **Rig > Body > Character Placement**.
`ui_refresh_observation.json` records this visual completion, not an in-process
Python version query or full scene fingerprint.

Ground and Rebuild were not clicked in the artist scene. After selection/UI
changes, native Ctrl+S displayed `Saved "X.blend"`, cleared the dirty title
marker and opened no Save As dialog. `artist_ui_save_receipt.json` records
34,549,456 bytes and SHA256
`77de13d659cccec7b7af808f33a92423adcfff53699206c54d36f19465bd76d6`.
This artist save is separate from the immutable tested input and confirms no
artist grounding operation. All disposable background Blender helpers exited.
No FPS/performance result, Git commit, push or publication is claimed.

## Local evidence

Evidence lives in
`D:/Blender/Projects/Character/X/validation/character_ground_scope_20261018/`:

- `candidate01/release_projection.json` and `freeze_provenance.json` pin the
  complete frozen source and approved AST scope.
- `candidate01/scope_report.json`, `original_report.json` and `ui_report.json`
  record the 5, 24 and 4 passing checks.
- `input_pin.json` and `saved_scope_v2.json` identify the input and character
  scope.
- `deployment_check.json` verifies both installed copies;
  `ui_refresh_observation.json` distinguishes visual refresh completion from
  the still-unperformed artist grounding. `artist_ui_save_receipt.json`
  separately records the completed native save.
- `pose_float_report.json` and `evaluated_pose_acceptance_audit.md` retain the
  diagnostic and independent policy review; `saved_ground_v3_report.json`
  records the accepted, explicitly limited V3 sequence.
- `acceptance_summary_v2.json` and `write_acceptance_v2.py` bind the final
  policy, preserved failures, fixture checks, result and delivery receipts.
- `saved_ground_report.json` and `saved_probe_resource_stop.json` preserve the
  interrupted first attempt; `saved_ground_v2_report.json` retains the V2
  signed-move output-check failure.
- `prepare_release.py`, the original/V2/V3 test runners and integration scripts
  retain reproduction entry points. Replays must use the pinned manifest and
  disposable output files, never overwrite the artist's work.
