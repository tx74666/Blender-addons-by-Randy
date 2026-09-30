# RR Helper 0.2.29 — Material → Shader

The existing RandomRealm sidebar's RR Helper > Texture page now includes a
default-collapsed Material → Shader section between PBR Framework and Texture
Packages. The existing sidebar/tab names are retained.

## Workflow

1. Open the target material at the root of a Shader Editor in the same window.
2. Expand Material → Shader and filter current-file materials by name. Empty
   search shows all materials; typing filters the same list immediately.
3. Select a source, click Import Shader, and confirm the displayed source/target.
4. Connect the new, unconnected `Material <SourceMaterialName>` node manually.

A checkmark means a ShaderNodeTree with the generated name already exists.
Use Existing adds another reference to that group, without copying it, even if
the source Surface is temporarily disconnected. Refresh updates the existing
group for all its users and does not insert another node. Both actions confirm
before modifying data. A source that is also the target generates/reuses the
group without inserting it back into itself.

## Extraction and preservation

- Follow the explicitly active Material Output's Surface dependencies only.
  Ignore unrelated nodes, Volume, Displacement, and material render settings.
- Preserve node types, properties, links, sockets, frames, positions, labels,
  ramps, curve mappings, image-user settings and common shader-node operations.
- Images and nested groups retain their existing datablock references.
- Replace Material Output with one Group Output exposing `Shader`.
- Do not alter source materials or existing target nodes/links. Place new nodes
  in free space to the right of the current graph with normal Blender styling.
- Refresh retains the node group ID and its Shader interface identifier. Build
  the replacement and validate a rollback copy before changing the old graph.
- Reject recursive group dependencies, incompatible name/interface collisions,
  read-only targets and names that cannot fit without Blender adding a suffix.

The importer handles static Surface graphs. OSL Script nodes, animated source
node trees, and unsupported node-owned collections produce an explicit error
instead of an incomplete copy. Animation inside shared nested groups remains
part of those shared groups. Pinned material editors are respected; ambiguous
editors, World editors and nested-group editing are not used as insertion targets.
There is no Asset Library or external-file browsing.

## Validation and deployment

- Blender 5.2.0 LTS: 26 isolated material/shader tests passed, including full
  upstream graphs, shared IDs, source snapshots, interface-preserving refresh,
  injected partial-failure rollback, duplicate handling, self-import, filtering,
  confirmation, target selection and explicit active-output selection.
- Registration, reload, failure recovery and unregister: 63 lifecycle checks
  passed on the final version.
- Builder6 UI: collapsed/expanded section, filtered list, source selection,
  Hub_Base target detection and pre-import confirmation were inspected. The
  confirmation was cancelled; validation did not import into the user's scene.
- Package: `dist/rr_helper-0.2.29.zip`, 15 files; hashes in `dist/SHA256SUMS.txt`.
- Program add-ons, user add-ons and Unity Tools/AssetPipeline/Blender/addons
  copies passed `tools/deploy_local.py --check` with zero differences.

Local source and deployment only; no Git commit or push. Builder6 was not saved.
