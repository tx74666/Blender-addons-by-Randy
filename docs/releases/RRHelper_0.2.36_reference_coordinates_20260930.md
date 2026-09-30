# RR Helper 0.2.36 — Reference layout uses the FBX/Unity coordinate basis

The elevator's Reference Layout offset was exported as Blender (0, 31, 0),
then consumed as Unity (0, 31, 0). The FBX itself already used Y-up coordinates:
its elevator root is (20000, 0, -3100) cm and its Core root is (20000, 0, 0).
Unity reflects the FBX X axis, giving the required relative offset (0, 0, -31).
This placed the assembly 31 meters upward even though the mesh hierarchy and
its two door levels, separated by 8.1 meters, were exported correctly.

New manifests retain referenceLayout version 1 and explicitly declare
`coordinateSpace: "UNITY"`. The complete relative matrix is conjugated by the
Blender-to-Unity basis, including the unit conversion used by FBX_SCALE_NONE.
This converts rotations as well as translations. A NONE unit system uses one
meter per unit; Metric/Imperial follow scale_length, matching Blender's exporter.

The coordinated Unity fix normalizes legacy untagged .blend-source manifests at
their import/read boundaries. Explicit UNITY data and legacy Unity-authored data
are not converted again. Unknown coordinate tags are rejected. Existing FBX and
manifest files do not need to be rewritten to use the corrected placement.

The source materials, mesh hierarchy, constraints, and Empty origin remain
unchanged. This repair does not redefine the user's automatic elevator settings.

Validation completed on Blender 5.2.0 LTS and Unity 6000.5.9f1:

- Six isolated coordinate tests passed, including independently parsed real FBX
  transforms and every exported fixture vertex at two unit scales.
- Ten Core tests and 55 canonical exporter tests passed.
- The required Unity AssetPipeline exporter runner passed all eight serial suites
  (exporter, bookmarks, registration, preview, icon lighting, icon integration,
  Standard transactions and editable Surface Text).
- Unity compiled the runtime, Editor and test assemblies with zero errors.
  Existing package/deprecation/serialized-field warnings remain.
- Eleven isolated Unity verification groups passed, including optional absent
  layouts, legacy migration, tagged idempotence, rotations, both authoring frames,
  the Standard and Modular readers, and a real Core prefab in a PreviewScene.
  NUnit cases were compiled; the TestRunner was not run because it would save
  the user's dirty scene. The isolated menu is the executed runtime evidence.
- Real Core world position (-47, 0, 80) resolves the elevator to (-47, 0, 49).
  The live elevator root was corrected with Undo. Its six part transforms and
  four authored elevator marker transforms were checked unchanged locally.
  Adventure remained unsaved; its on-disk SHA-256 was unchanged.

The legacy format has no scene-unit metadata. Untagged Blender layouts use the
historical one-meter conversion; re-export older non-meter scenes to write the
explicitly converted matrix. This does not affect the inspected Builder6 file.
Automatic elevator stop/entrance settings were preserved and were not Play-tested
by this coordinate repair.

Package `dist/rr_helper-0.2.36.zip` contains 15 files. The Blender core install,
Blender user install and Unity AssetPipeline copy were deployed and checked with
zero differing files. Builder6's live RR Helper was refreshed successfully.
Evidence logs are under
`D:/Blender/Projects/Build/Recovery/ReferenceLayout_20260930/`.

Local changes only; no Git commit, push or release publication.
