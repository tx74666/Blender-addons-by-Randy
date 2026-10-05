# Character Designer 0.76.0 — Hair strand motion

Date: 2026-10-04. Ownership: X / Character Designer. Git changes remain local;
the owner handles Commit and Push. Model and reasoning effort: unrecorded.

## Result and integration state

The Blender implementation adds stable strand identities, editable group
defaults, per-strand and normalized-depth settings, reliable whole-strand mirror
proofs, explicit manual pairing and external official Wiggle Bones preview.
Ordinary game export does not bake preview. Active configuration produces a
versioned sidecar bound to the actual FBX hash and manifest identity.

This is a Blender release, not completion of the full Unity integration goal.
Unity native Magica application, comparative motion/collision validation,
current artist GUI refresh and artist save remain separately tracked. Artist
X was not modified by the background checks described below.

## Native verification

Isolated Blender 5.1.0 and 5.2.0 checks passed:

| Suite | Cases per version | Evidence |
| --- | ---: | --- |
| Complete registry / geometry / graph proofs | 18 | registry_51_extended_run2.log / registry_52_extended.log |
| Registered UI, persistent settings, undo and rollback | 16 | ui_51_final16.log / ui_52_final16.log |
| Wiggle adapter / native PG / lifecycle / registration | 12 | wiggle_51_release.log / wiggle_52_full.log |
| Actual public remove/rebind, copy rejection and save/reopen | 10 | lifecycle_51_holder_run2.log / lifecycle_52_holder.log |
| Actual FBX roundtrip and atomic sidecar publication | 6 | export_51_run2.log / export_52_final.log |

Evidence folder: `D:\Blender\Projects\Character\X\Validation\hair_motion_20261004`.
Pure profile tests passed 15 cases; pure sidecar tests passed 23. Existing 5.1
Hair binding, binding guards and bone-generation suites also passed, along with
the native Action export roundtrip.

The public lifecycle test actually removes and rebinds 3 → 5 → 2 bone segments;
it does not patch UUIDs by hand. Strand IDs, mirror pairs, group defaults and
normalized curves survive. An independent native Text ownership anchor prevents
Object.copy from inheriting identity, including after original-owner deletion.
Failed operations remove only their newly created private records and preserve
unrelated artist Texts. Early removal failure does not reconstruct unchanged
native bones.

## Real saved X

Source UID `331afdb7e5154ae1aaa39603a4f61768` contains 32 strands / 128 segments.
Strict proof yields 11 L/R pairs, 2 center strands and 8 unpaired strands; the
last eight receive no nearest/name-based guesses. Editable group suggestions
yield Front 10, Side 8 and Back 14.

`real_hair_preview_validation_51_run2.json` proves a short-front pair (orders
14/16) and long-back pair (2/5) over 96 frames, a separate 96-frame gravity
independence test and all 32 over 60 frames. All endpoints remain finite.
Changing one short strand's gravity changes that strand's response while the
other three focused chains have zero same-frame response difference.

Every stage restores pose channels with maximum observed error zero and exact
native backend PG, numeric custom pose properties, Shape Key values and frame.
Raw/portable fingerprints also preserve mesh coordinates, UVs, weights, Rest,
material bindings, parent relationships and Actions. Only registry/profile
metadata is added to a separate stopped-preview `copy=True` candidate.
Artist disk SHA256 remains
`87d9e22bfcc1cb37449f3a76f0a8674959751ffc8c3e25ceb6d0c50ad17b26ca`.

The first real test uses a proven root-armature world rotation. The subsequent
`real_hair_preview_validation_51_headlocal3.json` also passes all three stages
with direct Head-local rotation. It proves the actual native parent/copy
constraint dependency graph and scalar driver inputs are Hair-disjoint; driver
expressions and native FCurve mappings are observed, not accepted by names.
Colliders, wind and pins
are disabled in these Blender tests. Background timing includes observation and
dependency-graph work, so it does not establish GUI FPS or Unity equivalence.

`hair_preview_cost_51.json` separates 60-frame Head-input measurements:

| Stage | Native frame_set median | Adapter guard median | Endpoint observation median |
| --- | ---: | ---: | ---: |
| No simulation | 16.40 ms | 0 | 0.166 ms |
| Four strands / 16 segments | 49.73 ms | 4.01 ms | 0.053 ms |
| All 32 / 128 segments | 91.66 ms | 3.81 ms | 0.167 ms |

Available memory remained about 1.4 GB during this bounded run. The guard is
included in frame_set; do not add those columns together. Scene evaluation and
the unchanged upstream solver dominate. This is not GPU-rendered viewport FPS.
Every stage restores the raw model and author pose, and the artist disk hash
remains unchanged. Preview start/stop is about 0.8–0.9 seconds in this run because
it strictly snapshots and restores native state.

The Head-local stopped candidate was reopened and exported through the actual
worker and publication transaction, then imported with native FBX animation
import enabled for detection. `character_consumer_ea1a4e078c074b46885222978263d933`
contains the passed `character_consumer_validation.json` and `fbx_roundtrip.json`,
plus the actual FBX, active sidecar and manifest in `published`. The reopened
portable raw fingerprint, stable IDs, pairs and effective profiles match the
passing preview evidence. FBX roundtrip preserves 32 independent roots, 128 Hair
bones, 217 retained skeleton bones, five mesh parts and their skin/Shape Keys,
with zero imported Actions. All ordinary export bake switches are false.

Asset ID is `ea1a4e078c074b46885222978263d933`; actual FBX SHA256 is
`603648483019bc847df8d1e36e21a4a236739a6a3a395425220ddef6051b753c`.
Candidate SHA256 remains
`a7642c321e72437be30f6c6b291eaa858f7b4d45e945852ed7e03c42a5cae7d1`.
The candidate and artist were not saved, and no Unity file was written. This
candidate is a validation input, not an automatic production model replacement.
Existing worker notices about Dress Preserve Volume and Stocking's custom shader
remain in the manifest. Unity native physics equivalence is still pending.

The first export attempt stopped at the serial resource gate because quoted
native Unity import-worker flags were misclassified. Its evidence remains intact.
The corrected Windows argument parser distinguishes persistent import workers
only with explicit caller scheduling proof; it does not infer their idle state
from flags. The passed run used Terrain's released heavy slot and current RAM
above 200 MiB, with no concurrent background task, and exited normally after
about 33 seconds.

## Performance and remaining work

Effective settings for all strands validate the complete profile once, instead
of once per strand. Navigation reuses only the freshly verified registry within
the same action; the next action verifies native ownership again. Draw reads
saved metadata and does not run full native mirror pairing. Idle preview guards
have no recurring mode timer. Official Wiggle solver source remains unchanged.

The immutable local ZIP contains 144 files, SHA256
`ce12cc2c3773f67f95d0df49f153a1819b996e98102e1ee969d7fb1808b85ad8`.
Blender 5.1, Blender 5.2 and X's validation copy were deployed and each check
reports zero different files. Deployment backups are
`20261004-124816-07617511` and `20261004-124823-be33bee8` beneath the existing
CodexBackups addon-deploy folder. Current artist refresh/save evidence will be
added after that phase completes. Unity owns its native implementation and
author-tuned baseline; no Dress physics or Unity asset was overwritten here.

Next investigation triggers: changed topology/ownership, a new backend version,
source/installed mismatches after reboot, observed GUI lag or changed native
Magica conversion behavior. Keep one final task record in the existing Codex
Console database, rather than an entry for each test or click.
