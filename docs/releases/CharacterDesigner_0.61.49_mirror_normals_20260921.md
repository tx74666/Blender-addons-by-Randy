# Character Designer 0.61.49 — Preserve custom normals during region mirror

After a one-sided loop cut, Mirror Selected Region correctly found the opposite
hand region but refused the temporary result with "Custom normal preservation
verification failed." The legacy normal setter re-encoded every corner into
packed smooth-fan coordinates. Eight reflected corners at two vertices acquired
up to 0.001428757 direction error (about 0.082 degrees), exceeding the 0.001 guard.
The live model was not changed by the failed operation or the diagnostic.

On Blender 4.5+, staging now writes free FLOAT_VECTOR/CORNER normals. Retained
corners keep their decoded direction; reflected corners use the inverse transpose
and normalize. This avoids quantization, smooth-fan averaging and incidental sharp
edge creation. Direction verification is stricter at 2e-6, and missing normal data
is explicitly rejected. Older Blender retains its existing packed-normal path;
it was not runtime-tested in this release.

Blender's free-normal representation is documented in its
[4.5 implementation](https://raw.githubusercontent.com/blender/blender/blender-v4.5-release/source/blender/blenkernel/intern/mesh_normals.cc)
and [5.2 implementation](https://raw.githubusercontent.com/blender/blender/blender-v5.2-release/source/blender/blenkernel/intern/mesh_normals.cc).

Finger ring staging also writes normalized interpolated vectors directly when
the source already has free corner normals. The legacy setter cannot update
that storage. Explicit all-false sharp-edge layers survive BMesh's sparse-layer
omission, and unrelated normal directions retain their original values.

## Validation

47 cases pass in six serial Blender 5.2 suites: mesh mirror (12), region matching
(6), new normal preservation (5), new finger free-normal interpolation (3), ring
reuse (14), and finger layout (7). New cases cover a local patch with an extra
source row, packed and free source normals, nonuniform transforms, explicit
sharp-edge preservation, Edit/Object round trips, repeat mirroring, deliberate
normal corruption and transactional rollback, plus moved-center and asymmetric
support interpolation with unchanged external normals.

The unsaved current Cosha selection was exported to a separate local library
for isolated replay. The selected 370 faces replace 364 opposite faces across
eight seam vertices. All ordinary attribute, weight and Shape Key checks pass
before commit and after returning through Edit Mode. The output changes from
3,424 vertices / 3,350 faces to 3,430 / 3,356, retains ten Shape Keys and zero sharp
edges, and has maximum normal direction error 0. A second mirror produces an
identical full fingerprint. No original blend file is saved by this replay.

Reports in X/outputs: mirror_normals_inspect_20260921.json and
mirror_normals_verified_20260921.json. Replay tool:
tests/inspect_real_mesh_mirror_normals.py.

Changes are local; no commit or push was performed.

## Deployment and live application

Built immutable character_designer-0.61.49.zip (108 files). Blender's installed
copy and X's validation copy both pass deployment --check with zero differing
files. Reloaded using Refresh Add-on, then invoked Mirror Selected Region on
the unchanged 375-vertex / 370-face source selection in the live X session.
The operation completed: Mesh.002.Mirror has 3,430 vertices, 6,772 edges and
3,356 faces, with the original source selection retained. No error report was
shown. The operation is registered with Blender Undo; no blend save was issued.
