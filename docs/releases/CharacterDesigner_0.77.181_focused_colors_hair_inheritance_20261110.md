# Character Designer 0.77.181 — focused Bone Colors and Hair inheritance

Completed local delivery on 2026-11-11, extending complete180_02. Final complete
projection SHA256: `5a7e7bf060ca633d2781aae818bbb8e13d5877f5fcf204cca98e405a4d5fb076`.
Acceptance: `D:/Blender/Projects/Character/X/validation/hair2_20261110/acceptance181_01.json`,
SHA256 `816b099e44f28630565fbd834dd04bea6413bd55440781eb5f2cc5ab9f062067`. Official ZIP SHA256
`5e155b009dd1ecb5134400adb3f569e8cbc71e8e05e3302467ccf4de0cbb3bac`;
CRC/payload268 exact; official5.2/X deployment plus matchingcheck268/0diff.

Open Colors opens one Bone Colors dialog containing Body, Hair and Skirt color
swatches. The extra Properties-window shortcut is removed; the old Properties
Colors panel is hidden. Editing a normal swatch derives its Selected and Active
highlights. Opening the dialog does not initialize or replace a character scheme.

Hair resegmentation previously created replacement bones with default display
colors; importing that sampled version faithfully brought gray colors back into
the character. New versions now inherit the current exact source strand's native
Bone and Pose colors, their precedence and raw restoration records. Equal counts
preserve each ordered segment. Changed counts take the old Rest interval that
contains each new segment's sampling midpoint. This maps display colors only;
sampling, weights, geometry, animation generation and solver math are unchanged.

Replacing an existing sampled version now retains the character's latest colors,
including manual edits. The retained version is not rewritten. Legacy palette
UUID/configuration/backups remain authoritative; foreign or corrupt ownership
refuses. A selected-Hair replacement failure also recovers the captured raw author
components after context evaluation. Reconstructed selected native pointers and
their former enumeration positions remain outside the existing rollback promise.

The independently prepared181 payload extends complete180_02 with six files:
`__init__.py`, `README.md`, `bone_display.py`, `color_center.py`,
`hair_resegment.py` and `hair_replace.py`; the other262 of268 files are exact.
No unrelated canonical candidate is included. Automatic sorting from180 remains.

Validation evidence under
`D:/Blender/Projects/Character/X/validation/hair2_20261110`:

- `color_native04_result.json`: eight native cases, including same/changed counts,
  native/Pose precedence, manual colors, legacyv1, stale-gray version import,
  corrupt ownership refusal, actual replacement/Restore and fault rollback.
  `color_native181_01_result.json` repeats the unchanged eight cases against the
  complete merged181 payload, including180's actual Hair builder; all passed.
- `popup_adopter06/result.json`: nine native cases. Blender requires re-registering
  only Open Colors to activate new native draw/invoke callbacks. The same Python
  class, package tuples, other RNA owners and24 captured swatch closure owners
  remain. Fault rollback and explicit post-success rollback were tested.
- `core_combined02/result.json` and `core_combined_lazy02/result.json`: exact
  complete180-to-review181 code transactions,18 and17 cases. Loaded Hair code is
  updated in place; absent lazy Hair replacement stays absent with zero module
  execution. Unknown modules, globals, callback/metadata owners refuse.
- `missing_repair_native02_result.json`: exact missing-only repair, injected
  rollback and count-mismatch refusal. Byte-quantized BoneColor values are checked
  against an independent same-build native storage oracle without tolerance changes.
- `artist_repair_colors01.json`: the requested current-RAM repair colored only
  30 Front and112 Back Pose bones. Existing32 Side colors remained exact. The raw
  saved scheme, native Bone colors and bounded context/raw/cached-pose/Action-slot
  witness were retained. The prior missing colors were DEFAULT at both levels.
  `artist_missing_colors_before01.json` retains the first-color recovery input.

Original failed harnesses and actual failures remain: source observer API errors,
native quantization assertion, initial sampling/rollback harness errors and the
real selected-Pose rollback gap. They are not relabeled by later passing checks.
See `color_inheritance_notes.md` and the individual receipts for phase limits.

The private cases do not certify full artist geometry/weights/all-key arrays,
solver dynamics, playback/FPS or global package lifecycle/handler-order idempotence.
Inherited release limitations and the closed playback-FPS investigation remain.
No scene/preferences save, restart, full reload, Restore Main Rig, bake/export,
Unity or Git upload is authorized by this release.

Final artist181 was already loaded at a newly observed boundary; its refresh time
and initiator are unknown. This task performed source/RNA/24getter+24setter closure
audit only, with zero live code writes or RNA lifecycle, and no full reload.
The user then explicitly requested distinct categories:30 Front became cyan,
32 Side retained purple exactly,112 Back became coral. Palette UUID, raw recovery,
other groups, native Bone colors and bounded pose/Action-slot/context witnesses
matched. The focused dialog was physically observed with those three swatches,
and Graph Editor restored. No scene/preferences save occurred. Native dirtyFalse
before/after this Python color edit does not establish a saved file; latest
distinct palette is RAM-only. `artist_finish181_01.json` is the final receipt.
