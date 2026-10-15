# RR Helper 0.2.61 — Surface Text after adding a roof

Adding geometry with Shift+D inside an existing building mesh used to block
export even when every original face remained intact. Surface Text stored a
digest of the entire source polygon list, and the export guard required an
exact whole-mesh match.

The guard now matches that existing digest against ordered prefixes of the
current source polygon list. It accepts appended faces only when the complete
original face topology and winding can be proven unchanged and every bound
face index still belongs to that original prefix. It never guesses a new face
mapping, rewrites the saved digest, or rebinds/moves the Font. Existing source
face edits, deletions, and reorderings still require an explicit rebind.
Modifier, evaluated face-tag, Mirror side, and joined-seam checks are unchanged.

## Validation

- Blender 5.2.0 LTS factory process: 76 tests passed across the new append-only
  suite and existing editable Surface Text, Mirror, shading/rebind and UV suites.
- Twelve new tests include real BMesh duplicate/translate geometry, FBX
  export/import, exclusion of roof faces from the sampling alias, unchanged
  authoring data, and rejection of changed original topology or invalid indices.
- The first test run found a test snapshot helper using `TextCurve.type`, which
  does not exist. The helper now uses `isinstance(..., bpy.types.TextCurve)`;
  initial and final evidence are preserved.
- Read-only live diagnosis: `Hub_Floor_Circular_A` contains 2,662 faces; its
  first 1,642 reproduce the saved original digest exactly. The 10 selected
  `Project Nexus` faces remain within that original prefix.
- Current Builder6 refreshed from 0.2.60 to 0.2.61 through the supported add-on
  refresh function. A 207,836-byte FBX verification export succeeded, retaining
  10 sampling faces. Object references, selection, transforms, custom properties,
  mesh coordinates/topology/material indices, Font settings and queue/output
  configuration matched before/after refresh and verification export.
- FBX SHA256: `9c0405b3bfeef64f45bb163228732ed8e2b7239f941b06ffcd65fe8bfd2c9dfb`.
- Export retained the pre-existing warning that `Hub_Base` has a linked Base
  Color network without an exportable basecolor/diffuse image. This fix does not
  bake that material or validate its Unity appearance.

Evidence: `D:\Blender\Projects\Build\WIP\Validation\surface_text_roof_20261015`.

## Delivery and limits

The 29-file 0.2.61 release was built with `tools/build_releases.py` and deployed
through `tools/deploy_local.py` to the Blender 5.2 user add-ons directory and the
verified active `D:\Blender5.2\5.2\scripts\addons_core` directory. Both final
`--check` runs reported zero differences. The open Character X process was not
refreshed. Factory test processes exited normally.

Builder6 was already dirty and was not saved; verification did not write the
Unity package or run a new Unity import. During initial console access a pending
historical verification command was accidentally executed: it changed selection
then stopped at an assertion, before export/save. The original sole selection
and active object `Hub_Floor_Circular_A` were restored and verified.

Source, tests, release ZIP and deployment are local. No commit, push, PR or remote
release was created. This repair permits proven appended geometry; broader edits
to original faces continue to use the existing explicit rebind workflow.
