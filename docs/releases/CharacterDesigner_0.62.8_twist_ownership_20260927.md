# Character Designer 0.62.8 — forearm preview ownership recovery

Forearm Twist Setup can now start when unrelated or orphaned Shape Keys already
use `CD Forearm Twist.L` / `.R`. Fresh calibration allocates an unused output name
and persists that exact name. Existing unrecorded keys are preserved and are never
adopted, renamed, muted, or deleted based on their names alone.

Preview creation, confirmation, and cancellation keep their transaction guards
through restoration. Failed initialization cleans its new key and newly created
Basis. Interrupted paired confirmation records every added output for cancellation
and serialized recovery. Cancellation validates ownership, name conflicts, and
relative-key dependencies before modifying data; failures retain the session and
recovery marker for retry. Saved outputs restore by managed identity.

The reported Cosha state had two nonempty old outputs but no calibration record.
That mismatch caused the ownership refusal; it was not failed arm identification.
The historical cause of the missing record is unknown. Review reproduced a former
mode-change reentry path that could leave an opposite output without a record; the
transaction guard now also blocks that path.

Validation in Blender 5.2.0 LTS:

- `test_forearm_twist_ownership_blender.py`: all 11 tests passed, including
  collisions on either side, cancellation, re-edit/removal, serialized recovery,
  initialization failure, late paired-confirm failure, and preservation of artist
  keys with naming conflicts or relative references.
- Existing `test_forearm_twist_blender.py` regression passed, including Direct
  Pre-Roll and legacy Roll-Decoupled rigs, save/reload, current-frame animation,
  FK hand rotation modes, and complete pose restoration.
- The current unsaved Cosha mesh and CoshaRig were copied into a temporary scene
  with independent mesh, Shape Keys, and armature data. The actual Setup operator
  succeeded; UI angle properties exercised 0, +45 and -90 degrees. Cancel and
  Confirm/Remove restored the copy. Both original orphan outputs were preserved.
  The original mesh, all keys, weights, native Rest and pose matched snapshots;
  the original .blend file hash was unchanged and temporary objects were removed.
- Independent review rechecked all three identified rollback issues after repair;
  no remaining blocker was found within this change's scope.

This release changes neither calibration axes nor IK generation. Full artistic
motion acceptance and Fingers acceptance are not claimed by these tests. Changes
remain local and uncommitted; no push was performed.
