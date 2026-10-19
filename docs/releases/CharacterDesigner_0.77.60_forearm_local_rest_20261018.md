# Character Designer 0.77.60 · Forearm Rest scope

Released locally on 2026-10-18. The `.60` ZIP is built and deployed to Blender
5.2 and X, with all 204 installed files independently verified at each target.
The running artist session refreshed to `.60` with 65 preservation checks.
The separately proven right-sleeve metadata repair passed in that session,
Ctrl+S saved `X.blend`, and an independent readback of the saved copy passed.

Forearm Correction in Original can now continue after an unrelated native Rest
geometry edit, such as changing a leg length. The explicit Forearm validator
protects the selected upper-arm, forearm and hand chain, its native ancestors,
and positive-weight deform bones affecting the captured sleeve support. A
paired calibration protects both arms. An unweighted finger edit is unrelated;
a finger weighted into the sleeve is a real dependency and remains protected.

The ordinary global inventory validator is unchanged. Original session schema,
bone inventory, parent/inheritance relations, owned constraints, influence
drivers and saved mute ownership still require proof. An actual arm/dependency
Rest change still refuses; this release does not accept a new arm calibration
baseline, rewrite the saved Original session, resync controls or restore bones.
Original-to-Controls and Rest resync retain their existing global contract.

Existing model export already validates the saved Forearm arm-chain Rest and
local topology independently. No Forearm calibration is required for ordinary
FBX export. This release leaves exporter/sidecar code, all topology guards,
mesh geometry, painted weights, group names/locks and artist Shape Keys intact.
Deleting vertices can reindex the captured sleeve and invalidate its local
proof even when the deletion is elsewhere. Automatic topology remapping is
not included. The actual current model's right-side topology mismatch received
the separate bounded metadata repair described below.

## Frozen source

- Accepted base: `.59` ZIP SHA256
  `7702b3063c5cd0ae87f31e1ff9db16f9c12456278a0835527d537db71dbeddd4`.
- Candidate02 manifest SHA256
  `3270a944881e7a58e557a1f809179f9304715b254e6074e0a40786e74167c278`.
- 204 files; only the initializer version, `forearm_original_inventory.py`
  and `forearm_twist.py` differ. The other 201 files retain exact base bytes.
- The function allowlist adds `validate_forearm` and `_forearm_inventory`,
  changes only `_resolve_rig` and `_prepare_runtime_records`, and keeps default
  `validate` exactly unchanged. Paused .48 watcher/provenance/display/ownership
  optimizations remain excluded.
- Evidence root:
  `D:/Blender/Projects/Character/X/validation/forearm_local_rest_20261018`.
  `source204_verification.json` independently verifies the complete source,
  allowlist and unchanged guard modules. Its SHA256 is
  `ff8b3039cfc1f62b4c2927da4a8b8b9fd571874b08564dba8e7a062dff7a260d`.

## Local delivery

The built ZIP is `dist/character_designer-0.77.60.zip`, 1,563,149 bytes,
SHA256 `3a1d231a0e3cf1b770db610bd1c984d3dfc1c4ebcb7036ba37a9dcc538e41452`.
`deployment_receipt.json` independently verifies ZIP integrity and its exact
204-member content, the frozen source and both deployment directories against
candidate02. Both targets have zero missing, extra or changed shipped files.
Its SHA256 is
`193bfba8e5637a314e41df2bf427fb0aba0dae19b8815ac04717a684503a963d`.

Deployment changed three files at each target. All six backed-up files match
the accepted `.59` base. Backup:
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20261018-191509-cabd387a`.
This is local delivery; no Git operation, preference write, artist refresh or
scene repair was performed by the independent delivery audit. Disk verification
does not establish the version already loaded in an artist Python session.

## Actual model and saved readback

The saved right-sleeve support still used vertex IDs from the historical
3,658-vertex body; the current body has 3,634 vertices. Independent, exact and
unique raw/Basis coordinate matches identify all 191 support vertices and 54
incident boundary vertices. The full incident graph, face winding, raw/normed
weights, participating locks and non-ID artistic profile passed their stated
proofs before a right-side metadata-only proposal was admitted. This mapping
is scene-specific evidence and is not part of the add-on runtime.

Historical `.46` `forearm.R`/`hand.R` connected flags differ from the current
artist setting. That false full historical-Rest comparison remains a diagnostic;
the V3 policy requires only those exact known differences, exact matrices,
lengths/parents and endpoint relations, all current arm Rest fields matching the
current Original entry, and real bilateral Forearm inventory with weighted
dependencies. All 21 mandatory V3 checks passed. Old connected flags, geometry
or weights were not restored.

The artist repair preserves current raw mesh/UV, every current weight and group
lock, ordinary artist Shape Keys, native/helper Rest, local pose channels,
listed constraint inputs, Action key data, rig drivers, Original session and
context. Only right-side vertex-ID/topology metadata is initially replaced.
The left artistic profile remains unchanged; normal runtime also refreshes its
whole-mesh topology digest. Managed corrective outputs are recomputed normally.
The isolated stage passed application, save/reopen, runtime preparation and
read-only export capture of both sides with 64 samples. No full
all-RNA/attributes/material/NLA claim is made by this scene-specific proof.

`artist_right_forearm_repair.json` passed on the actual `.60` session, SHA256
`d0fa236d41715dfb832b0496a6329143353cf6ce2386ee5c3375a66d3a69a345`.
Normal Ctrl+S then displayed **Saved X.blend** and removed the title's unsaved
marker. The saved file and immutable `artist_saved_forearm.blend` copy share
SHA256 `35953d9231ffa041e8dfbbbb1bf83e6c370dd3db26a0794e6599fa3d79964a37`,
34,523,845 bytes. The copy operation did not save the artist scene.

`artist_saved_readback.json` passed 35 explicit authored-state checks, both
record/output hashes, runtime preparation, both-side/64-sample export capture
and all 204 source hashes. The ordinary global validator still rejects the
intentionally edited `shin.L`; that contract has not been weakened. The reader
did not repair, update correction outputs, change frame or save. Its SHA256 is
`be8c4ef9a8fb71024da42662584654fc132232937b8c11698dcbd6f9de683270`.
Reader PID 11400 exited normally; only the original artist process 27952 remains.

## Validation and retained failures

The corrected seven-case native suite passed in isolated Blender 5.2. It covers
unrelated leg/finger edits, arm/ancestor and weighted-finger refusal, paired-arm
scope, strict structure/inventory, constraint/driver/mute tamper, preview cancel
and read-only export capture. `native7_v2_result.json` SHA256:
`ddc7ec070dbe7eaa6d0c6098bfe863eff570f076bbff337432e44bb22df8e992`.

The first 40-case candidate01 run remains failed: 37 passed, two failed and one
errored. The new influence-tamper test initially wrote a switch-driver output;
the corrected test now tampers an authenticated, undriven fixed-influence
helper instead. Runtime bytes did not change between candidate01 and candidate02.
The original failure report and frozen test remain intact.

Two older cases also fail unchanged on the accepted `.59` source in an isolated
process. One expects optional Spine IK after whole-body Generate, which now
deliberately skips that optional branch; Torso, Eyes and Root records are present.
The other manually grows its saved support from 96 to 108 vertices but retains
the 96-vertex proof, so the existing local topology guard rejects it. The tests
and their assertions have not been changed or counted as passing.
`baseline059_result.json` retains one failure and one error, SHA256
`877a04b87475cbbedb1be6c16dd746ac6fd393b200710c595fedcf36d51a5db3`.
There is no claim that all 40 cases passed. Full FBX conversion/publication and
Unity import were not run. No performance or Unity-runtime result is claimed.
