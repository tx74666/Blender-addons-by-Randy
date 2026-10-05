# Character Designer 0.75.2 — switch performance

2026-10-04 (Asia/Shanghai); X owns scene integration. Actual execution model
variant and reasoning effort are unrecorded. Local changes only; no Commit,
Push, PR or upload. This records the final code work once per task.

FK switches were solving the native graph after every bone write. Supported
Direct chains now compute three native local bases from captured desired parent
frames, write them together, then solve once. Independent Direct IK target/Pole
inputs and the mode property share their first evaluation. The actual Cosha
reduces matcher updates from IK 28→24 and FK 20→14; three FK chains qualify.

Strict ownership, driver and parent dependency proofs limit both optimizations.
Unknown constraints/drivers, parent feedback, object dependencies, Stable IK,
reverse-foot IK and unrepresentable FK bases keep the existing ordered path.
Validation tolerances, precision compensation, Auto Key, FK bone display,
Pole/guide display and complete failure rollback remain unchanged.

Original/Controls reuses Dress resolution only during consecutive reads. The
native update boundary and final Forearm refresh retain fresh ownership proofs.
Unchanged mode/lock/constraint setters avoid writes. Ordinary Original entry
resolution calls drop 7→6, Controls return 8→6. There is no persistent pointer
cache or bypass of validation after native callbacks.

## Comparable measurements

Saved Cosha, Blender5.1.0, one requested thread; same process/native graph and
display policy. Frozen0.75.1 function bodies versus patched source; one warmup
and five alternating pairs per run, followed by a second run with reversed
initial condition order. Full checkpoints are reset outside timing to avoid
accumulating float drift. Timings exclude snapshot/reset/external proof and GUI
click/redraw/Undo. Sample ranges overlap and individual pair results vary.

| Operation | Run1 old→new median | Reversed run old→new median | Evaluation/proof reduction |
| --- | --- | --- | --- |
| IK | 959.67→778.10 ms | 818.53→656.96 ms | 28→24 matcher updates |
| FK | 569.61→435.78 ms | 540.36→420.29 ms | 20→14 matcher updates |
| Original | 345.72→359.30 ms | 342.46→339.15 ms | 7→6 Dress resolutions; no consistent speedup |
| Controls, no edits | 300.61→267.57 ms | 290.70→256.48 ms | 8→6 Dress resolutions |

The two median comparisons support roughly19–20% lower IK,22–23% lower FK and
11–12% lower no-edit Controls function time in this saved model. They do not
promise those gains for each click. Original entry still needs its pose/session
transfer; no instant-switch or viewport FPS/memory benefit is claimed.
Authored Original poses and one-time Rest resync are separate operations and
were covered by correctness regressions, not timed in this comparison.

## Verification and deployment

Blender5.1.0 and5.2.0LTS each passed87 native methods: FK7, IK seed7, Dress read
scope5, all-limb transaction14, existing single-limb11, Original18, Rest resync10,
display10 and animated display follow5 (174 executions, not174 distinct cases).
5.1's first functional suites ran before the metadata-only0.75.2 version bump;
the same runtime function bodies passed the final0.75.2 paired comparison and
display suites.5.2 ran the final version. Focused tests cover inheritance flags,
connected bones, alternate rotation modes, driver/constraint fallback, Auto Key,
late failure rollback and ownership changes after native callbacks.

The first Dress callback test changed a redundant source OWNER tag which the
existing resolver does not use. That fixture failed and is retained; corrected
testing changes the authoritative source→rig Object reference and passes.
Runtime proof was not weakened or extended merely to pass the fixture.

Both Cosha paired runs preserve raw model, native Rest, Hair/Dress, animation,
calibration, baseline and Direct registry exactly after reset. In-switch maximum
pose/skin/surface errors in final reverse run:0.000164259691 /
0.000154096168 /0.0000427403360 rig units, below existing0.0002 /0.0001 gates.
Final reset evaluated pose, skin and surfaces return with zero error. The artist
file hash stays87d9e22bfcc1cb37449f3a76f0a8674959751ffc8c3e25ceb6d0c50ad17b26ca.

0.75.2 archive:138 files,1,029,640 bytes,
SHA256 a19fab0d2f7258f96a4948c0c896950fb2d3d3d8fbdb2e2a893cfce1f4898ec0.
5.1/5.2 user installations and X validation copy deployed; all`--check` results
show0 differences. Each user installation updated5 files. Backups:
20261004-101327-5af4faf3 and20261004-101341-66408993. Builder GUI untouched.

## Current artist integration status

User requested code inspection first and GUI work later. No native GUI input,
scene benchmark, refresh or save has been performed in this task yet. The
coordination question is pending. Last verified artist disk save remains
2026-10-04 07:16:49.4838186+08,32,235,486 bytes, with the hash above. A prepared
native refresh/save script captures the latest artist state, writes an independent
recovery copy, refreshes0.75.2, verifies exact channels/model/Rest/display and
saves only after those checks. It does not apply IK/FK benchmark poses to X.

## Evidence and next trigger

Raw evidence:
`D:/Blender/Projects/Character/X/Validation/switch_performance_20261004`.
`profile_switches.json` supplies the per-function diagnosis;`compare_switches.json`
and`compare_switches_reverse.json` supply controlled measurements. Native logs,
frozen baseline and scripts stay here. The final reverse report includes source
and script hashes.

All background jobs exited; only the two original GUI Blender processes remain.
No Unity operation or application closing occurred. The next investigation should
measure loaded-version GUI click→draw/Undo costs if switching still feels slow;
reuse these function profiles rather than repeating the old full arm-drag study.
