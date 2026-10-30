# Character Designer 0.77.96 — editable Base Pose

The former Rest Pose button displayed Blender's native binding reference, so
rotating controls did not deform the visible character. The main row now offers
**Base Pose**: an isolated editable neutral Body pose, repeatable reset, and
**Return to Animation**. The native **Rest Display** remains under Advanced.

The original Action/slot/NLA and author pose are retained. Keyed tests survive
as separate drafts; sessions survive save/reopen and Undo/Redo. AutoKey keeps
the artist's current setting. Exact IK/FK matching, existing driver ownership,
local pose/display isolation and ordered nested collection restoration guard
the operation. No rig rebuilding, rest geometry change or weight rebinding.

Ordinary Pose Asset calls retain their default single-user admission. Only the
explicit Base Pose path accepts one actual Object binding with additional ID
references; this does not validate or repair historical recovery records.

The complete approved source is X's
`validation/base_pose_test_20261023/stage07/projection.json`, SHA256
`3f56033129374a9b6fea4643c422e5b3fca8cfca38d4c57439712350a462f0db`.
Build/deploy/check must use it: canonical contains unrelated paused work.
It retains 211 approved .95 files byte-identically, changes five files and
adds one module (217 files / 185 Python sources).

113 pure checks, 19 native fixture checks, ten GUI history checks and eleven
current-model checks pass. The original full author guards before/after the
current-model cycle are byte-identical. Nine narrow-installer rollback points,
success and no-op were tested separately. This is not a playback/FPS release.

X's `validation/base_pose_test_20261023/README.md` indexes raw failures,
accepted evidence, protected backup and current-session delivery state.
No commit/push is authorized by local delivery.
