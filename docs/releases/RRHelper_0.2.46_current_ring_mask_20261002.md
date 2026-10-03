# RR Helper 0.2.46: current multifunction Ring Mask

Existing Builder6 files could still offer the historical three-control Ring
Mask through their linked Group entries, even after the standalone asset had
been retired. Node Library **0.3.1** now exposes **Ring Mask 0.2.1** as the one
current circular-mask asset. The intended Shader Editor path is
**Shift+A > Textures > Ring Mask**. Blender's native **Texture** color tag gives
it the normal brown header.

The five controls are **Inner Radius**, **Ring Width**, **Edge Softness**,
**Start Angle** and **Sweep Angle**. Start defaults to 0 and Sweep to 360, so a
new node is a full ring. Reduce Sweep to make an arc, and change Start to
rotate it. The outputs are **Mask** and native **Ring Data**; connect Ring Data
to **Extend Mask > Source** to reuse the same boundaries and angles.

RR Helper's Add Ring and Add Arc shortcuts both use this current asset. Add
Ring starts at Sweep 360; Add Arc starts at 180. Runtime lookup skips cached
legacy Ring/Arc groups instead of trusting the display name alone. Saved
nodes still evaluate with RR Helper disabled. Ring Group continues to combine
several masks for one shared shader, and Mix Shaders keeps its expandable
Mask / Shader pairs.

## Preservation and migration

The new public asset retains Arc Mask 0.2.0's native calculation and socket
identifiers, with a full-ring default. The original radial Ring Mask 0.1.1
binary and its evidence remain historical dependencies. Arc Mask 0.2.0 is
preserved under `node_library/validation/fixtures/`, outside the visible asset
directory; the standalone Arc asset is removed from the current inventory.
The library contains six assets in five bundles.

Current-file migration is explicit and follows a backup. Independently loaded
historical references must prove the complete source calculation before a
node is replaced. Existing values, external socket connections and node
presentation survive. Three-control rings receive Start 0 / Sweep 360, while
five-control arcs retain their angles. Customized calculations, animation and
read-only owners are refused; staged failures roll back. No load, update, save
or render handler migrates user scenes.

Ring Group's six factory functions and seven contract constants remain
AST-equivalent to the accepted RR Helper 0.2.44 package. Ring Group and Extend
Mask native binaries are unchanged. Historical render reports are kept
separate from the fresh current-release verification.

## Validation

- Ring Mask passed **96 saved-asset checks**: 46 independently calculated real
  shader samples in Cycles, the same 46 in EEVEE, and four structure,
  persistence and source/asset-preservation checks. These cover full rings,
  controlled arcs, seam handling, linked controls and Ring Data / Extend Mask
  connections. Native save/reopen works without the add-on.
- The isolated migration fixture passed **ten checks**, including old ring
  and arc values, editable nested owners, exact retained links and
  presentation, customized-source refusal and injected-failure rollback.
- Current runtime tests passed **37 Ring/Arc workflow checks** (including
  seven actual shader samples), **nine legacy-retirement checks** and
  **13 Ring Group mask-slot checks**.
- Fresh Ring Group compatibility verification passed **24 checks**, including
  17 actual shader samples and expansion to 24 inputs. Fresh Extend Mask
  verification passed **131 checks**: 64 samples in each of Cycles and EEVEE,
  plus three native structure/persistence/preservation checks.
- Five publication tests and two deployment tests passed. The lightweight
  library verifier passes for all six assets/five bundles, and the current
  native asset's read-only deployment check passes.

Current saved-asset evidence is
[ring_mask_current.json](../../node_library/validation/ring_mask_current.json).
The migration and additional runtime evidence is under
`D:\Blender\Projects\Build\Recovery\RingMaskCurrent_20261002`.

## Deployment

The 20-file `dist/rr_helper-0.2.46.zip` matches canonical runtime source.
SHA256:
`6db0b902fa6f9d08304d0ef83c05aac2d94739f1d3e7a613ec843966962c8ce0`.
All three local add-on destinations match the package: the Blender 5.2 user
add-ons directory, `D:\Blender5.2\5.2\scripts\addons_core`, and the RandomRealm2
asset-pipeline add-ons directory. Deployment backups remain under
`%LOCALAPPDATA%\CodexBackups\addon-deploy`.

The native asset is installed at
`D:\Blender\Helper\Asset-Libraries\Costom\Nodes\Randy_Ring_Mask.blend`.
SHA256:
`485274c248c90b1529a584756810bce0c7f935aea7c1077fd0d6c687562a7de6`.
The old standalone Arc file is absent from that directory. Native asset and
publication backups are outside the visible library in the recovery
directory. Existing catalog identity and unrelated library files are retained.

## Live Builder6 verification

The authorized live session was backed up before migration. Its one actual
legacy Ring Mask instance in **Hub_Base** was replaced with local Ring Mask
0.2.1, keeping the three radial values, node presentation and every existing
material connection. Start 0 / Sweep 360 preserves its full-ring behavior.
The new group has all five controls, Ring Data and `color_tag = TEXTURE`.

Five retired linked mask groups were separately preserved as recursive local
copies before cleanup. A stale material reference to another independently
verified radial group was remapped to the current group; only the verified
superseded groups and this task's temporary proof/snapshot groups were
removed. User object transforms and geometry were not changed. The running
add-on was refreshed and inspected as version 0.2.46, and runtime lookup
returned the current Ring Mask. The original selection and Shader Editor
were restored.

The migration helper itself does not save. Builder6 was subsequently saved
through the live UI, which confirmed **Saved "Builder6.blend"**. The helper's
original `saved_file: false` field therefore describes the migration step,
before that separate UI save.

Recovery files:

- `Builder6_before_current_Ring_Mask.blend`: complete pre-migration backup.
- `Builder6_legacy_linked_masks.blend`: independent native copies of the old
  linked calculations; SHA256
  `75d34a92da725a7a0326632fdc80f27f854645b321f2030021e4bb56480cdf6c`.
- `legacy_mask_snapshot.json`: original identities and instance bindings.
- `live_migration_result.json`: migration, cleanup and live runtime evidence.

The Asset Browser refresh returned `FINISHED`, but the Add menu still showed
a cached **Arc Mask** entry during the follow-up inspection. Final menu-cache
refresh and verification of a newly added current Ring Mask remain pending;
background tests and successful current-file migration do not establish that
UI result.

A subsequent read-only live inventory confirmed that the registered Nodes
library points to the deployed directory, whose asset list contains only the
current Ring Mask and no Arc Mask. `Nodes1.blend` contains six unrelated assets
and was left untouched. The live file contains the current public Ring Mask
0.2.1 and its hidden radial dependency; no retired public mask group remains.
Evidence: `live_asset_inventory.json` in the recovery directory. Follow-up
Windows input repeatedly timed out or reported another active Computer Use
request while the Main chat operated Unity. The prepared all-library refresh
helper was not executed, and no fresh menu insertion is claimed.

Background validation processes exited normally. Changes are local; no Git
commit, push or remote release publication was performed.
