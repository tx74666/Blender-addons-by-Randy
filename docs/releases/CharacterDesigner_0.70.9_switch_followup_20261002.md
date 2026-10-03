# Character Designer 0.70.9 — Follow-up switch performance

The saved Cosha scene had an existing Forearm Twist calibration failure:
`Captured loop positions are stale; capture the arm again.` Two no-edit
Original / Controls roundtrips recalculated that same failed geometric mirror
proof eight times. This release caches deterministic ValueError outcomes under
the existing complete exact input signature, alongside successful proofs.

The signature retains all Basis coordinates, topology bytes, object world
frames, every bone's Rest endpoints/deform flag and complete source/target
records. Any changed input immediately retries the proof. Errors remain visible
to the runtime and invalid corrective keys remain paused. Only the exception
type and arguments are stored; no exception instance or traceback is retained.
Cache capacity remains 32 and existing Undo/load/unregister cleanup applies.
This is a performance fix, not a recapture or repair of that calibration.

Original panel redraws also use a one-entry cache keyed by the exact saved
session text. It stores only immutable role/index/name tuples. Each caller gets
fresh name sets and resolves current Blender Object references, then reads live
visibility. Session edits, replaced/missing references, Undo and reload cannot
reuse stale Blender objects. Older Body-only recovery sessions remain supported.

Within one switch's preflight, ownership records already validated by the full
inventory are reused, and optional extension records are read after that
validation. Rest data is calculated once and reused for pose names and recovery
checks. Unchanged rotation locks are no longer written. No extra handlers or
timers are installed and pose validation/rollback remain intact.

## Evidence

Serial Blender 5.2 background runs used the same saved X.blend, factory startup,
two threads, and profiling overhead. They never saved the loaded file. Timings
are descriptive and affected by system load; removed repeated proofs are the
deterministic performance improvement.

| Two roundtrips | 0.70.8 | 0.70.9 |
| --- | --- | --- |
| Original, first / next cycle | 0.842 / 0.471 s | 0.238 / 0.160 s |
| Controls, first / next cycle | 1.212 / 0.593 s | 0.178 / 0.172 s |
| Repeated failed full mirror proofs during switches | 8 | 0 (initial checked proof reused) |
| Original / Controls pose updates per switch | 3 / 2 | 3 / 2 |
| Runtime forearm calibration error | Same stale-loop message | Same stale-loop message |
| Protected content after each roundtrip | Unchanged | Unchanged |

Real-scene evidence is in X's `validation` directory:
`original_switch_0708_followup_baseline.{json,log}` and
`original_switch_0709_followup_optimized.{json,log}`. The shared profiling harness
hashes mesh coordinates, every Shape Key, weights/groups, edges/faces, native
Rest/parents, pose channels and bone display before and after each roundtrip.

Validation:

- Seven forearm-cache tests pass. New cases cover repeated failure, direct Basis
  repair without notification, changed frames/loop positions/Rest endpoints,
  fresh exception identity, continuous rig validation and Undo/unload clearing.
  Existing weight changes, artist keys and managed output recovery remain live.
- Fourteen Original-mode tests pass, including the new saved-text cache test for
  decode count, fresh name sets, live/missing/replaced references, session edits
  and previous-version recovery.
- Six forearm review regressions pass, including Undo, picker safeguards,
  migration and profile preservation.
- Built and verified `character_designer-0.70.9.zip` (130 files). Blender 5.2's
  installed add-on and the X validation copy report zero differing files in
  deployment `--check`.
- No background Blender validation processes remain. The two artist Blender
  windows remain open. Current X scene refresh/save is pending input coordination;
  this phase has not changed the artist scene.

Local changes only; no commit, push, PR or publication.
