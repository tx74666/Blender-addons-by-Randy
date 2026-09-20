# Character Designer 0.61.38 — bounded drag performance work

## Measurements and changes

Profiling identified the repeated legacy full fingerprint as roughly 75% of a
synthetic preview rebuild. This captured identical data, converted large arrays
and weight dataclasses into decimal text, hashed them, and repeatedly resolved
the same bone chains. Blender property notifications also invalidated Basic
Setup's expensive frame proofs although slider changes did not edit geometry.

The new transient proof uses native float32/int32 bulk RNA reads and binary
encoding of all deform memberships (read directly from BMesh in Edit Mode).
Cold/changed inputs still run the original full fingerprint; an exact warm match
can reuse that proof. Original persisted signature format and write validation
are untouched. Tests perturb all legacy data categories, including pending-drag
Edit Mode weight and non-active Shape Key layer changes. Unsupported buffer
layouts use the old validator, never a weaker partial proof.

Cached chain lookup is keyed by full source proof, all relevant rig bone facts,
saved reference records, warnings and symmetry frame. Basic Setup cache entries
are restored only after the same complete equality proof and current domain/key
checks; external bend-source dependencies are not restored. Timers still coalesce
to the latest slider value, and invalid draft feedback/Hide/Undo behavior stays.

## Evidence

- Focused headless cases: proof/performance 5, eye lifecycle 8, workflow 12,
  Basic Setup cache 5, multi-selection 5; all passed.
- Isolated native GUI drag test now also renders the production Basic Setup
  consumer and counts its expensive validations: 139 frames, zero blank ring
  frames, zero reference rechecks, 24 rebuilds averaging 8.19 ms (7.32-9.54 ms).
  Previous minimal-panel GUI run averaged 28.3 ms.
- `benchmark_finger_drag_blender.py -- <X.blend> --baseline` preloads the old
  installed add-on; without `--baseline` it uses canonical development sources.
  Both run the same 30 updates, without saving and with embedded scripts disabled.
  On X: 719.62 ms median before, 90.35 ms after; updated range 85.79-112.78 ms.
  These background figures include parameter assignment, dependency evaluation
  and preview work; they are not display-presentation latency.
- Before/after preview geometry hash:
  `1af3e518ecb3f138cbb19037c886975baa3a6ba5339a154b50f44830808a1abf`.
  All existing mesh data and the original X.blend file remained unchanged.

The initial candidate measured 116.59 ms on X; further packing reduced it to
90.35 ms. Neither this result nor the small-fixture GUI figure establishes a
hard real-time bound. Scanning complete real-character data remains the primary
remaining cost; no safety checks were dropped to advertise a lower number.

## Lightweight broader audit

- Main Setup/Bone Display panels: mostly bounded RNA queries; no mesh-generating
  algorithm was moved into draw callbacks.
- Mesh mirror and legacy ring overlays already reuse GPU batches and build
  plans on explicit actions. Their write-time fingerprint checks were retained.
- Bone-display sync scans scene rig candidates on its 0.2-second timer, but
  skips unchanged signatures and only applies changed visibility. No interval
  change or speculative stateful cache was introduced.
- Forearm depsgraph callbacks appeared in the real-X profile (~1.6 ms per drag
  update here), far below fingerprint/preview cost. Their correction logic was
  left intact, rather than broadening this targeted performance change.

The unrelated pre-existing foot-helper display regression recorded for 0.61.37
is not addressed here. No Git commit/push or change to the live user scene.
