# Character Designer 0.61.55 — explicit loop marks

Mark two existing internal edge loops with red/blue cached guides, then align
the two junctions of an existing three-bone chain. Four fingers keep their
root-to-tip straight axis; Thumb follows its original curved bone path. Loop
section planes determine longitudinal placement, not raw vertex averages.
Root, tip and Roll remain fixed, and full interior checks precede atomic writes.

X Mirror moves complete existing opposite chains; both sides are validated
before either changes. Empty-side views can use the opposite complete marks
and show reflected target guides without creating fictitious marked edges.
An active partial mark set never mixes with opposite marks. Incompatible paired
surfaces warn `Sync both hands with Mirror Selected Region.`; pair-only problems
no longer make tabs red. Real invalid source references remain distinguishable.

The Capture Detection master eye also controls these guides. Coordinates and
GPU batches are retained across hide/show and side switching. No mesh monitor,
geometry timer, topology generator or weight operation was added. Modified or
deleted marked loops require an explicit replacement mark; harmless index
renumbering is recovered only when Align is clicked. Clearing Basic Setup clears
both sides' marks. Unload, undo/redo and file load manage only display caches.

Validation on Blender 5.2.0 LTS, single-threaded disposable fixtures:

- 8 loop-mark tests: read-only marking including UV/attributes/Shape Keys,
  four straight fingers with off-center axes, mirrored/single-side writes,
  selection guards, pair mismatch refusal, injected post-commit rollback,
  BMesh index renumbering, curved three-bone Thumb.
- 4 cache tests: 30 eye/side cycles under geometry-read traps; 2,001 mocked GPU
  draw calls reuse exactly two uploads; opposite-view alignment; clear and
  load/undo/unload cache lifecycle. CPU dispatch measured about 0.023 ms per
  call on this fixture; GPU calls were mocked, so this is not viewport FPS.
- 38 retained regressions: Capture 7, retired runtime 4, clear 5, bone targets 3,
  shared reference refresh 6, Relax Bones 13. All passed.
- 4 actual `addon_utils` lifecycle cases: restricted-context enable, disable and
  re-enable retaining data, repeated hot reload, injected registration failure
  with complete cleanup and restoration. Total: 54 passing cases.

Local release/deployment only; artist .blend files are not saved by this change.

The initial 0.61.54 local candidate failed Blender's restricted-context enable
path despite direct-register fixture tests passing. Version 0.61.55 defers
reading saved display metadata until scene access is available and permits
finger preview cleanup under restricted data. Use 0.61.55 for installation.

Live X scene verification confirmed 0.61.55, valid operator registration, no
loop-mark geometry handler, and unchanged artist mesh, Shape Key layers, bone
transforms and edit selection across recovery. The compact buttons appeared in
Bone Chain; the Capture master eye hid and restored the current reference.
No joint alignment was applied to the artist model and the .blend was not saved.
