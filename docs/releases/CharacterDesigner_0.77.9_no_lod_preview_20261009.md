# Character Designer 0.77.9 — complete preview copy for no-LOD meshes

Source changes are local, uncommitted and unpushed. This release inherits all
152 approved 0.77.8 files and changes only the release version and
`unity_runtime/Editor/CharacterAnimationReadableMesh.cs`.

Character display names follow the current Unity workspace: Cosha / Carmen.
No character GUID, model, Avatar, original resource or existing draft is renamed.

The real Carmen preview exposed a Unity API boundary: meshes with no internal
LODs can report a default zero selection curve, while assigning that curve to
the new writable mesh rejects its zero slope. The pinned Unity API counts the
base LOD0 as one layer and activates internal LOD selection only when the count
exceeds one. The provider copies the exact count, retaining a single layer
through its complete submeshes. It assigns the source selection curve and
additional LOD ranges only for genuine multiple layers. Multi-LOD validation
and complete vertex/index/submesh/skin/blend-shape copying retain their strict
checks. No source mesh or importer is modified.

The root owner verifies the actual Unity/Carmen preview. Blender A/B/C export
and private idle-refresh evidence from 0.77.8 remains applicable because all
Python workflow/export modules inherit the exact approved bytes. This release
does not repeat that heavy Blender run. Existing held exporter changes remain
excluded. Deployment/projection hashes and the root's final native result are
recorded in the completed deployment receipt below.

Validated and deployed locally on 2026-10-09:

- Actual Unity cross-character preview passed in
  `D:/Unity Projects/RandomRealm2/Logs/CharacterTuning/PendingPreview/verification.json`.
  The single-layer Carmen Ch15 mesh has inactive internal LOD selection and a
  zero default slope; its complete geometry, skin and blend-shape copy passed.
  Scene, production assets, artist queue and selection preservation passed.
- The source-first frozen projection contains 152 files. Only `__init__.py`
  and `unity_runtime/Editor/CharacterAnimationReadableMesh.cs` differ from
  0.77.8; the other 150 files retain their exact approved bytes.
- Official build, local deploy and subsequent `--check` passed. The Blender
  5.2 user installation and X project add-on copy each match all 152 files,
  with zero differences. Each installation updated only the two named files.
- The previous four files were backed up under
  `C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261009-041552-271a6826`;
  their bytes were verified against the approved 0.77.8 projection.
- Projection SHA256:
  `332a8573d51216a68a9605c7a66d50bc517f7b2db8489e06f93c8d94e1448cac`.
  Archive SHA256:
  `de4d6cea8360e1b9d329cb7e875fd1cd9a20e024281c481b3a857f1a7d1ed2d7`.
  Helper SHA256:
  `a759f52f2d8e803f2ddfc57cd988bb9bc885a224dafb8ef75cd507a388e1bc53`.

The exact receipt is
`D:/Unity Projects/RandomRealm2/.codex/task-staging/character-pending-crosspreview-20261009/blender-candidate/evidence/deployment-receipt-0779.json`.
Installed bytes alone do not establish the running Blender module version;
the root owner performs the artist-session Refresh and registered-panel check
separately, without saving or reopening X.blend. This release did not invoke
another heavy Blender run or any Unity operation.
