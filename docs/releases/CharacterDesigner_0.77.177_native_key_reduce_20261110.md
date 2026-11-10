# Character Designer 0.77.177 — use native keyframe reduction

**Locally delivered and adopted.** This supersedes the unreleased 0.77.174 preparation.
The standalone **Simplify Keys** and dedicated **Before / After** buttons,
two operators and `animation_key_reduce.py` backend are removed. Existing
Actions, retained versions and reduction metadata remain available.

Manual reduction uses Blender's Graph Editor **Key > Density > Decimate**
(**Allowed Change** or **Ratio**) / **Clean Keyframes** on an Action copy.
Curve-value error is not a whole-rig position or rotation guarantee. Shared
`animation_key_reduce_math.py`, controller conversion and its automatic sample
simplification remain unchanged. Export Base Pose keeps its active-Custom/slot
eligibility under `_active_custom_action`.

## Complete candidate

Base: `validation/color_center_20261110/stage176_01/projection.json`, SHA256
`a170e1bec2c5fc1ec832e19bbc6b624ccee5abf67f1a04859198650a1202135d`.
Candidate: `validation/remove_custom_key_reduce_20261110/stage177_01/projection.json`,
SHA256 `1145f99b89cc7e4d63bd709d149458d45663b6d975a7820d6f4b1aa678f15e1d`.
It contains 267 files / 235 Python files: 3 changed, 1 removed and 264 byte-exact
to 0.77.176. Worklist UI is byte-exact to the reviewed17403 removal. Unrelated held source
changes are excluded; original source, retired tests and documentation remain
backed up in this validation directory.

## Validation

Related pure checks passed 313/316; the three inherited Physics folding/message
failures match the recorded 0.77.171 failures and remain unfixed. Exact-input frozen
Worklist/controller-integration 95/95 checks are reused. Actual 0.77.174 native
transaction fault/refusal checks (nine cases) and public cold/repeated
registration proof retain their 0.77.174 identity; they are not 0.77.177 native proof.
The 0.77.177 helper derivation passed whole-AST equality after version-constant
normalization. Fresh 0.77.177 native verification passed strict176-to177
adoption (two unregisters, no registrations, 15 events), zero-write ALREADY,
public warm registration/integrity and actual177 cold/repeated registration
and integrity, followed by normal cleanup. One inherited case label still says
174; the preserved source assertions and input/output metadata explicitly test
176/177. Earlier refusal and failure records are retained. Owned workers exited.

## Delivery status

- Actual 0.77.177 native verification: **Passed**, `runtime177/native17701/result.json`,
  SHA256 `32380b0481468ae7970cc82c376d6a8196aa24f2612ba10f24c19d3fb07d06e5`.
- Official 0.77.177 ZIP: **Built**, CRC/file-set/payload267 exact; SHA256
  `f6ecf7ed738c1e31f40f93146c0ac449a7cf917a320c6f77e51219d75576ac2a`.
- Dual Roaming Blender 5.2/X deployment and exact checks: **Passed**. The exact
  old backend and three changed files were backed up at both targets before
  deletion/deployment. Official deployment and matching check both returned0;
  all267 files match and the standalone backend is absent at both targets.
- Current artist live adoption: **Passed**, actual176-to177 with two unregisters,
  no registrations and15 events. Frame7, Pose/CoshaRig/CTRL_elbow_pole.R,
  slot929201142,392 cached pose matrices, preview1..31 and dirtytrue stayed exact.
  Live receipt SHA256 `1c57494a5072365fbd829b510ab03990e195281f1856ef0d5b9d92d71c2706fe`.
- GUI finish: bottom Graph Editor restored with two editor-menu clicks; curves
  and frame7 visible, Properties/Colors window retained, input released.

The176 inheritance handoff was independently recovered from the actual source,
native check, official ZIP, exact dual deployment and passed scoped-adoption
receipts after interrupted coordinator turns. This does not add full artist
color-edit workflow certification. The current source working tree also holds
unapproved178 browser work, which is excluded from this complete177 package.

Acceptance: `validation/remove_custom_key_reduce_20261110/acceptance177_01.json`.
Scoped live adoption keeps the current source-signature reference and any
already-loaded reducer module/maps inert; it does not clear authored history.

No Scene or preferences save, restart, full reload, keyframe reduction/edit,
Unity change or Git upload is included. No performance improvement is claimed.
Detailed receipts: `D:/Blender/Projects/Character/X/validation/remove_custom_key_reduce_20261110`.
