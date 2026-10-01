# RR Helper 0.2.38 — Empty origin child channels

Moving an Empty origin while preserving its parts previously left a translation
in each child's parent inverse. The geometry stayed in place, but static child
Location values could retain the old offset, such as -8.1 m after moving the
elevator root from the second floor to the ground floor.

After the existing Empty-origin operation, eligible static children now absorb
the parent-inverse translation into their Location channels. The full linear
parent-inverse matrix is preserved, including shear. The operation verifies
world transforms and rolls back on failure.

Children with constraints, animation, external constraint/modifier references,
read-only data or singular transforms retain the existing compensation path.
Files containing drivers also skip channel normalization because expressions
can read arbitrary object channels. Moving roots and the active target are
protected. Blender Location remains a parent-relative channel; this change does
not turn it into a universal world-coordinate display.

## Builder6 live repair

Before repair, Hub_Elevator_Bottom had Location Z=-8.100000381 and parent-inverse
translation Z=+8.100000381. Its world origin was (200, 31, 0), while the evaluated
top surface was already at world Z=0.001001358. The user's 1 mm surface lift was
present in the mesh; the bottom was not actually 8.1 m below ground.

Only the bottom was normalized in the live scene. Its origin was then placed at
world (200, 31, 0.001), with a counter-transform of its single-user mesh to keep
the geometry fixed. Location now reads (0, 0, 0.001), and the parent-inverse
translation is zero. All evaluated world vertices across the six elevator parts
were checked within 1e-5 m, all four door constraints were retained, and every
other scene object's world transform was unchanged. The current working blend
was not saved by this operation.

The pre-repair live file copy and JSON evidence are under
`D:/Blender/Projects/Build/Recovery/EmptyLocalChannels_20261001/`:

- `Builder6_before_bottom_channel_fix.blend`
- `live_before.json`, `preflight.json`, `live_result.json`

## Validation and deployment

- All 59 Empty-origin regressions passed in isolated Blender 5.2.0 LTS.
- All 63 real addon_utils registration/refresh lifecycle checks passed.
- The required eight-suite Unity AssetPipeline exporter runner passed.
- The running Builder6 session was refreshed and reported version 0.2.38.
- The Blender user add-on, addons_core and Unity AssetPipeline deployment copies
  were checked against the canonical source with zero differing files.

Archive: `dist/rr_helper-0.2.38.zip`, 17 files.
SHA256: `D95CE959B9E93120D196E0EA0800F1B0E9594ABC86FC1C48437054D4B114DED0`.
Test logs are retained beside the live repair evidence. No Unity scene was
modified or elevator asset re-exported during this repair. Local changes only;
no Git commit, push, PR or published release.
