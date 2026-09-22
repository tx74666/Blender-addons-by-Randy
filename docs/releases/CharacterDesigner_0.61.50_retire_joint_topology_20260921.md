# Character Designer 0.61.50 — retire automatic finger joint topology

The Joint Topology & Weights workflow has been removed at the user's request.
Its preparation, loop generation, center/bone synchronization, local weighting,
subdivision planning, previews and recurring monitor are no longer registered
or shipped. Legacy Create 3 Rings and Prepare Rings are also removed.

Basic Setup, independent bone alignment, curved Thumb Relax, Roll calibration,
single-side cached bend previews and Mirror Selected Region remain available.
Read-only finger detection no longer imports topology-generation modules.
The master eye retains reference/bend caches; the compact side arrow remains.

Updating the add-on does not restore a prepared source or remove mesh loops.
Existing topology, Shape Keys, UVs, normals, weights and bones stay as they are.
Old saved workflow properties and source datablocks are inert and are not purged.

Ten obsolete Python modules are removed. Deployment backs up those exact old
files outside the add-on before normal canonical deployment and hash checking.
Old running code must unregister before reloading the new package, so pending
timers, draw handlers and obsolete RNA are removed without editing the model.

Validation on Blender 5.2, factory startup, one thread, serial execution:

| Suite | Passed |
| --- | ---: |
| Retirement/lifecycle/cache controls | 4 |
| Basic bank | 7 |
| Bone alignment and Thumb Relax | 4 |
| Paired Roll targets | 3 |
| Manual topology reference adaptation | 6 |
| Reference cache | 5 |
| Finger selection | 5 |
| Definition | 10 |
| Internal reference | 10 |
| Legacy bone Roll | 5 |
| Mirror custom normals | 5 |
| Total | 64 |

The warm eye/side test rejects any geometry snapshot, frame build or bone-chain
resolution during 20 master-eye cycles and 10 side round trips. This is a cache
regression check, not an end-to-end viewport FPS measurement.

Live X reload from 0.61.49 to 0.61.50 passed. Cosha's existing
Mesh.002.Mirror.Mirror remained at 3436 vertices / 6784 edges / 3362 faces and
10 Shape Keys. Full mesh artist-data and rest/pose-bone digests were identical.
No obsolete modules, timers, event handlers or workflow/layout/joint RNA remained.
The original Object Mode and 3D view were restored; no blend file was saved.
Evidence: X/outputs/retire_finger_topology_live_20260921.json.

Both the Blender user add-on and X validation copy match canonical sources:
99 shipped files, zero differences. Old module backups are under
%LOCALAPPDATA%/CodexBackups/addon-retirement/20260921-182155;
updated-file backups are under addon-deploy/20260921-182159-f2af1d93.
Release archive character_designer-0.61.50.zip was built and verified.
All changes are local; no Git commit or push was made.
