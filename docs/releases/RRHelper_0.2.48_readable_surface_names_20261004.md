# RR Helper 0.2.48: readable Surface Text sampling names

Surface Text previously exposed the region hash in persistent sampling object
and mesh names. New samples use `RR_SurfaceSample_<target>_<side>_01` and a
matching mesh name, allocating the next free sequence for repeated regions.
Names preserve Unicode target labels and retain the side/sequence suffix within
63 UTF-8 bytes. Region identities and persistent FBX export IDs remain separate
from these display names.

An explicit migration updates exact legacy generated names, including native
copy suffixes and older truncation. It resolves name-only Font references before
renaming and synchronizes saved object/mesh name strings and verified transient
alias ownership. User-chosen names, unmanaged objects, linked/read-only data and
shared mesh names are preserved. Migration is not automatically run on file load.

## Validation

- Four focused checks pass in isolated Blender 5.2.0 LTS: Unicode and length
  bounds, repeated sample numbering, old name-only references, manifest and alias
  pairing, unchanged IDs/geometry, idempotence, native copies, and preservation
  of custom/unmanaged/shared data. The disposable process exited normally.
- Live Builder6 audit found 335 objects and exactly two managed sampling objects
  with hash suffixes, plus their two matching mesh datablocks. No replacement or
  control characters were found in audited object, mesh, curve, material,
  collection, node group, image, action or Text datablock names.
- Evidence is retained under
  `D:/Blender/Projects/Build/WIP/Validation/readable_surface_names_20261004/`.

The 0.2.48 package was built locally. Blender 5.2 user addons and the verified
active `D:/Blender5.2/5.2/scripts/addons_core/` installation both passed deployment
checks with 20 files and zero differences. The active Builder6 module remains
0.2.48 after its official deferred reload completed without error.

Live migration changed exactly two sampling objects and their two mesh names
to `RR_SurfaceSample_Hub_EntranceFrame_Rounded_A_Original_01` and `_02`
(with the matching `RR_SurfaceSampleMesh_` prefix for meshes). Both Font
references were synchronized. Geometry, parent/data pointers, persistent IDs,
export aliases, selection and mode were unchanged; a second migration was empty.
The original viewport and shader editor were restored.

Builder6 was saved with Blender's native `FINISHED` result at
2026-10-04 05:40:44 +08:00. The saved file's object/mesh name lists were checked
through a byte-for-byte copy because Blender disallows library-loading the
currently open file. New names exist on disk and old names are absent. The live
file is clean, still has 335 objects, and has no remaining audited name candidates.
`saved_result.json` records 134,919,213 bytes and SHA-256
`6d27c36ea3b9aa8b8c3dca9aec79a2f15aef3c2021d2338d3a87a8285695074c`.

Pre-migration disk and live-state backups, with verified hashes, are retained in
`Validation/readable_surface_names_20261004/recovery_20261004_053200/`.
The earlier current-file verification restriction is retained in
`verification_error.json`; the copy-based verification completed successfully.
These changes remain local; no Git commit, push or remote release is included.
