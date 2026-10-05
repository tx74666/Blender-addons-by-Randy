# Character Designer 0.76.3: animation collection candidate

Local deployment completed on 2026-10-06. Animation Worklist can add missing
Ready entries, scan Custom changes and serially sync the selected changed
motions through their existing Links. Existing Custom edits and linked Source
baselines are retained. Unity Apply remains explicit.

Full fingerprints cover supported Action data and rig dependencies. Unsupported
constraints, drivers or NLA report Unknown and require explicit inclusion;
there is no nearest-match or name-only provenance shortcut. Sync validates its
current proof once, validates publication receipts and keeps a completed prefix
when cancelled. Workers and transient scans stop on unload/load boundaries.

Scan displays early feedback and blocks repeated clicks. Its dense callback
still runs synchronously; cancellation cannot interrupt that callback. The
private fixture took 89.479 s / 62.004 s for two scans, and 77.943 s / 14.687 s
for Idle / Walk Sync. These are single-run work timings, not comparative CPU,
FPS or click-latency gains.

Blender 5.1 isolated two-motion tests passed independent edits, save/reopen,
serial export and repeated Sync without a new worker/output. R8 activation,
UI and rollback fixtures passed. Official deployment/check found zero
differences across 147 files in Blender 5.1 and X. This frozen candidate
excludes unrelated unpublished Dress changes; Blender 5.2 was not deployed.

The live X already had 0.76.3 loaded. Ten key functions, file inventory,
registration and inspected audit state matched; no live Refresh or activation
was performed. The initial combined report remains failed after an immediate
post-save assertion; its differing fields were not captured. A later exact
baseline comparison differed only in the dirty flag. Final native Ctrl+S after
editor restoration displayed `Saved "X.blend"`, with no title star. The final
artist was not reopened or given another complete post-save audit.

The Character-owned private Unity Return report passes Walk/Idle Sync, render,
Apply and native Undo/Redo. An earlier WriteAtomic failure and its successful
private recovery are retained separately. Separate Visible acceptance also
passed: both saved private Controller slots match their candidates, and
edited-forearm changes measure 5.6655° / 5.8917° maximum for Walk / Idle.
Humanoid import still warns about discarded shin/foot translations; this is
not a lossless translation roundtrip. This locally deployed package remains
a candidate until the owner completes its overall handoff.

- ZIP SHA256: `9df21c111412725b80e9b2ec885bc0d1c5fc78d60a010398a501b4a6faab6a35`.
- Final X: 2026-10-06 00:53:13.945 +08; 32,254,503 bytes; SHA256 `2b36fb936082cbe7a81dd29e1dba22b9f9fbefa935015bfb29cc37b5b496ca9a`.
- [X evidence index](D:/Blender/Projects/Character/X/Validation/animation_collection_20261005/README.md) contains the raw pass/failure reports, deployment receipts and save limits.
- [Worklist usage](../animation_worklist.md) describes the workflow.

Canonical runtime changes remain in this repository. Model/effort were
unrecorded. No Commit, Push, PR or external publication was performed.
