# Character Designer 0.61.44 — Ring reuse and confirmed bone joints

## Behavior

- Joint centers get first choice of safely movable existing rings. Supports use
  remaining suitable rings; only missing rings are inserted. An ordered global
  allocation prevents crossing, stealing the next joint's ring, or moving root,
  tip and protected rows. Unequal inner/outer spacing also reuses the center.
- Fill Empty Middle adds one ordinary ring only when the space between finished
  triplets has none. Existing one/many middle rings are kept. Turning it off
  leaves this to the artist; older Between Joints = 0 stays off.
- Prepare binds fixed ordinal bone identities. Sync Bone Joints confirms the
  central ring positions and adjacent rest heads/tails together. Destinations
  lie on the verified internal path; Thumb uses its original curved bone path.
  Endpoints and roll are preserved, while segment lengths redistribute.
- Previews distinguish moved/reused/new rings and display orange internal bone
  destinations. They do not write mesh or rest bones. Mesh, weights, rig and
  workflow metadata commit as one transaction, including failure rollback.
- Align Active Finger is an explicit action for Index/Middle/Ring/Pinky. Thumb
  is excluded and has Relax Thumb instead, supporting the actual chain length.
  Relax preserves endpoints, bend direction and already-confirmed junctions.
  Its saved last-result record makes repeat clicks stable, preventing progressive
  flattening; this metadata participates in rollback and persists with the asset.
  Bone Roll remains a separate action. No bones are added or removed.

Organize the bone chain first, then Prepare Joints, adjust markers, and use
Confirm Rings + Bone Joints. Calibrate Roll separately. If every internal point
is already confirmed, Relax is intentionally a no-op. Existing source equality,
rig, neutral-pose, hidden/locked-bone and volume-containment guards remain.

## Upgrade behavior

Older prepared recipes require Prepare again for fixed bone identities. An
inactive finger with an older generated layout blocks regeneration with an
explicit Release/Prepare explanation. Release keeps the present mesh and bones;
the next Prepare uses them as its source. This avoids silently applying the new
allocation algorithm to another finger that was not previewed. Weight-only
updates never move joints or accept unconfirmed changes to ring positions.

The generator also retains an explicit all-false sharp_face attribute when
BMesh omits it, without weakening existing shading/data verification.

## Validation

Validation logs are in
`D:\Blender\Projects\Character\X\outputs\joint_sync_20260921`.
The focused suites cover allocation/protection, asymmetric interpolation,
Shape Keys/weights/UVs/normals, fixed bone identity, endpoint/roll preservation,
transaction rollback, inactive-finger isolation, legacy migration and preview
non-mutation. Existing workflow, layout/range, defaults, eye, combined preview,
idle-monitor and packed-proof/drag checks are also rerun serially in Blender 5.2.

The old eye test for ambiguous nearest-bone weights now explicitly disables
Sync Bone Joints. Fixed-ordinal synchronization has its own positive tests and
must permit a marker to move away from the old junction.

All **86 focused cases passed**: ring reuse 9, bone chain 9, confirmation 8,
workflow 12, preview eye 11, combined preview 6, idle monitor 5, range 7,
layout 7, joint defaults 6 and packed proof/drag 6. These are targeted regression
checks, not a claim to have rerun every test in the repository. Initial logs are
retained; final chain/confirmation runs have `-final.log` filenames, and the
successful ring rerun is `test_finger_ring_reuse_blender.log`.

Saved Cosha geometry staged successfully for all five pairs with its ten Shape
Keys, weights, UVs, normals and attributes checked by the strict generator.
Four straight-finger bone plans passed. Thumb's existing captured Setup excludes
the proximal start of thumb.01.L/R: its projected start is about -0.0330, beyond
the admitted lower bound -0.01747. The radial check passes, so this is a root-range
mismatch, not rejection of its curvature or naming. The same refusal predates
this release (.61.41). The current asset needs its thumb Setup root range reviewed
and extended before syncing or relaxing the full three-bone chain; no threshold
was loosened and no automatic root edit was made. Three/four-segment curved-thumb
behavior is verified by the dedicated synthetic cases.

An additional disposable real-asset run completed Index, Middle and Ring paired
confirmations, exact destination checks and repeat-generation idempotence;
the Index injected post-commit failure also restored mesh and rig exactly.
Pinky's full confirmation was not completed: the background process was stopped
when free system memory fell below 0.4 GB. Its earlier geometry staging and bone
preflight passed. No user Blender process was stopped. These real-asset checks
never saved the file; its SHA-256 still matches the initial inspection:
`35793dafd0b117290ba53984a6222f8f95b39bb4952df72371caedb9667b5d66`.

## Local deployment

Built `dist/character_designer-0.61.44.zip` with 106 files. Blender 5.2's installed
add-on and X's validation copy each received seven runtime files, and the required
`deploy_local.py --check` reported zero differences at both destinations. Prior
files are backed up at
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260921-071605-66fa70b2`.

The existing X Blender window was refreshed using Refresh Add-on. The new Bone
Chain, Fill Empty Middle, Sync Bone Joints and confirmation controls were visible.
Prepare Joints was rerun for the current Middle pair to bind its preview targets;
this updates preparation metadata and marks the session dirty, but does not
submit ring/bone changes. The mesh remains at 3,418 vertices with the same
2-vertex/1-edge selection, in Edit Mode. No Apply/Confirm or blend save was run.

No live model geometry or rest skeleton is automatically modified by deployment.
Changes remain local and uncommitted; no GitHub push was made.
