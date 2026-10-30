# Character Designer 0.77.104 — Pole guide visibility

Blender 5 separates a bone's Edit Mode visibility (`Bone.hide`) from its
Object/Pose visibility (`PoseBone.hide`). The guide reader used both flags,
so it could reject a line while Blender still displayed its Pole arrow.

The reader now uses Pose visibility when available, with the earlier Bone
flag retained for Blender versions that do not expose that property. All
other visibility, ownership, IK/FK, collection and finite-coordinate checks
remain. No rig geometry, control position, roll, animation or author data is
changed by this reader.

The complete release extends approved103 with only this function, the literal
version and the README. Unrelated canonical candidates remain excluded.
X owns actual source deployment, current-model observation and scene saving;
the corresponding receipts are in X's `validation/takeover103_20261027`.

Blender's migration reference:
https://developer.blender.org/docs/release_notes/5.0/python_api/

Local delivery and formal X integration are accepted in
`D:/Blender/Projects/Character/X/validation/takeover103_20261027/acceptance_summary104.json`.
Both220-file checks are exact. The actual runtime was already refreshed to104
when the proposed narrow installer checked its baseline, so that attempt refused
before code writes. Fresh source/code/author/Action and normal-save/event checks
establish the final state; preservation of pre-refresh Python/RNA identities is
not claimed. Positive visibility was verified in the private native fixture;
the final current Walk is FK and correctly hides its Poles.
