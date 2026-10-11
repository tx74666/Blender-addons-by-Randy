# Character Designer 0.77.17: preserve terminal bone lengths on source import

FBX reimport reconstructed Cosha's terminal eye bones using the Head bone's
length. Their native Blender lengths became approximately 0.177794 instead of
0.055473, although their heads and Rest directions were retained. All six thumb
joints were present; the thumb tip bone became shorter rather than the mesh
being stretched. This release preserves the missing terminal-tail information
for future CD exports and linked source imports.

## Behavior and use

The final export worker records the cleaned/merged export rig's complete bone
inventory, parent relationships, world-meter heads, axes and lengths in the
existing `.cdesigner.json` manifest. The metadata is bound to the exact exported
FBX SHA256 and source rig. It does not introduce leaf bones or FBX custom props.

When CD imports that model as a fresh Unity animation source, it validates the
metadata and complete imported skeleton before binding the Action. Only eligible
terminal lengths are recovered. Bones with B-Bone segments or bone attachments,
envelope skinned rigs, nonuniform/sheared/reflected rig transforms and mismatched
bind data are excluded or rejected conservatively. The import failure boundary
removes this operation's new scene/data and restores the artist's context.

Legacy exports without the block retain their existing behavior. Existing
worklist Source caches, Custom animations and artist rigs are not retroactively
edited. This is not a Refine, parent-transform reset, mesh repair, or IK/control
import feature.

To use it, reload CD 0.77.17 by reopening Blender after saving the artist's work,
export the original character again through CD, keep its FBX and adjacent
`.cdesigner.json` together, prepare a current Unity link, and import a fresh
source through CD. Reopening an existing cached source does not run this recovery.
A normal manual Blender FBX import also does not invoke this CD hook.

## Validation

- Blender 5.2.0 LTS, build `fbe6228777e7`: real FBX tests pass at scene units 1.0
  and 0.01 with uniform rotated/scaled rigs, five animation frames, unchanged
  weights/Shape Keys/Action curves, repeat recovery and legacy input.
- Wrong hash, rig, head, attachments, envelopes and B-Bones are checked. The
  production source-import boundary restores lengths before binding the Action;
  invalid metadata aborts before binding and removes new IDs while restoring
  the original scene, selection, frame and Pose mode.
- 20 metadata unit tests pass; 6 source-image and 16 animation-link regression
  tests also pass (42 pure Python tests).
- The actual X/Cosha export rig passes an isolated worker-export/FBX-reimport
  test: 217 retained bones, 53 restored terminal lengths and all five meshes
  (27,561 vertices) checked at frames 1, 7 and 15. Heads and pose bases remain
  exactly equal. Eye lengths recover to approximately 0.055473 in rig units;
  thumb.03 lengths recover to approximately 0.038324.
- The largest Rest-matrix component rounding difference is `3.2633543e-6`.
  Blender's float32 length setter can slightly renormalize the terminal axis.
  World-head and pose checks stay at `2e-6`; normalized axes/local Rest 3x3 use
  `2e-5`. Maximum mesh world-coordinate component difference is
  `6.2274839e-7` meters, with weights, Shape Keys and geometry inventories equal.
- Installed 0.77.17 enables and disables normally in a factory background
  Blender session; helper hash and production source-import hook are verified.

Raw evidence is in X's `validation/bone_rest_lengths_20261010`, including the
final `probe_cosha_roundtrip_report_verified.json`, generic Blender log, precision
debug evidence and installed-runtime check. Earlier failed fixture/precision
attempts remain there for audit; the verified report is the final result.

The real-model test captured current Hair configuration and used an isolated
export folder. Texture publication alone was omitted to keep the test small;
Forearm correction shape-key publication was also outside this fixture. No
Unity Editor import, compile, Play-mode or new Unity roundtrip was run. The
test covers the native FBX length loss and CD recovery boundary, not all existing
export features or animations. This is not a performance improvement claim.

## Local delivery

The approved 153-file projection starts from verified 0.77.16 and changes four
files: version-only `__init__.py`, new `bone_rest_lengths.py`, the source-import
hook and final export-worker metadata capture. Unrelated pending changes in
the shared repository are not part of this release.

- Projection SHA256:
  `27eeb6be76d225e7b91bbad780fa1361a6413fa33434f27d70b7bfe96279a4f2`.
- ZIP: `dist/character_designer-0.77.17.zip`, built with `tools/build_releases.py`;
  SHA256 `25cdcd9ee248a7190b329e5b03f5d4fe37f1fb16dc0620e2e5fa898137886cb7`.
- `tools/deploy_local.py` deployed to the verified Blender 5.2 user add-on
  directory and `D:/Blender/Projects/Character/X/addons`; both `--check` results
  are zero differences. Previous files are in
  `C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261010-190552-465068e9`.
- `X.blend`, Unity's `Cosha.fbx` and `Cosha.cdesigner.json` retain their original
  hashes. No artist scene was repaired or saved by this patch. The open artist
  Blender session was not refreshed or restarted; the isolated installed check
  confirms the next load uses 0.77.17.
- No Git commit, push, PR or public release was made. Agent model and reasoning
  effort are unrecorded.

After this deployment, the concurrent Shoes binding task published 0.77.18
from the complete 0.77.17 projection. Final verification preserves that newer
release: only version and `quick_bind.py` differ, while all three bone-length
runtime files remain byte-identical. Both installation targets match the
0.77.18 projection (SHA256
`6ab500acf3fe43528df763a73b4c20c53b9e7598de91f01fbeebd358c1fdbd7b`)
with zero differences. Installed 0.77.18 also passes enable/disable and hook
verification. X's `validation/bone_rest_lengths_20261010/delivery_07717.json`
records both the original patch archive and the final installed version.

Investigate again if a fresh metadata-backed import rejects a valid skeleton,
if a Unity-side export changes the FBX identity, or if deformation is reproduced
with the recovered lengths. Models whose Unity export rewrites the FBX require
a separately verified metadata transfer; this patch never guesses a new mapping.
