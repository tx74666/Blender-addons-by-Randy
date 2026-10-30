# Character Designer 0.77.87 — approved animation sync package

This correction retains the 0.77.86 Sync List functionality while restoring
the approved 0.77.85 body-region implementation in the local release input.
The separate paused regional-ownership source candidate remains untouched
in canonical development sources and is excluded from deployment.

Build and deploy this version with the complete hash-locked projection
at `D:/Blender/Projects/Character/X/validation/body_region_approved_restore_20261022/projection087/projection.json`
(SHA-256 `70e0291992dd1bfe83fc99d04e762581a959c9c4268884c97ae48ca9694e5f1a`). A plain canonical deployment
would include that paused candidate and is not the approved release input.
The prior 0.77.86 archive and validation attempts remain historical evidence.

The current native flow has successfully synchronized Walk_N and Idle,
retaining the old Walk_N in Retired Animations. Source/Custom and the artist
model are retained. Native correctness checks do not establish acceptable
latency for the long Idle clip; that reported delay is tracked separately
with the X conversation.
