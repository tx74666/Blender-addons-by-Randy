# Character Designer 0.77.46: regional controls and character placement

2026-10-17. Canonical source: `addons/character_designer`. Local work; the owner
handles Git commit and push.

**Body > Edit One Part** releases generated controls for one leg/arm or both
legs/arms, retaining original deform bones, mesh binding and calibration.
After adjusting original bones and the corresponding model, **Rebuild Controls**
builds only the released part around its edited Rest. The status changes between
Controls Ready and Released. Finished hands and unrelated controls are protected.
Current support is Direct schema 5 limbs; head/neck and torso/spine do not yet
have independent regional teardown. See [usage and limits](../body_region_controls.md).

**Body > Character Placement** adds a saved **Vertical Distance** field and
**Move Character**. Positive moves up and negative moves down along world Z.
**Ground Character** places the lowest evaluated vertex of visible character
models, including footwear, at world Z=0 in the displayed pose. Skeletons,
controllers, bound models and recognized attachments translate as one unit;
native Rest, local pose, hierarchy, weights and Shape Keys are retained. A failed
verification restores exact local object transform channels. Both buttons use
native Blender Undo and do not insert animation keys.

Evaluated mesh output uses the explicit native numerical limit
`2e-5 + world_bbox_diagonal * 1e-4` per world-coordinate component; floor height
uses the largest visible-mesh limit. This covers measured float32 Hook/Spline IK
amplification during otherwise unchanged whole-character translation, rather
than promising bit-for-bit evaluated vertices. Receipts expose the per-mesh
limits and measured maximum errors. Original Rest and authored local channel
checks remain exact, and object matrix checks remain unchanged. Transform keys
on internal child objects are retained; outermost placement objects still refuse
their own animated/driven transforms.

Known recovery Action references can increase Armature data users without a
second Object binding. Regional admission now distinguishes those authenticated
references from actual shared or foreign owners. Its revision proof pins current
Rest and provenance; old motion proofs and Actions remain unchanged. Clips whose
Rest no longer matches edited proportions remain subject to the normal strict
compatibility checks. The panel uses cached small status summaries rather than
performing graph/file audits on every redraw.

Transaction rollback now restores authored pose channels directly. Reassigning
matrix_basis after restoring those channels decomposed and rounded otherwise
unchanged artist values. Selected recreated helper Rest matrices have bounded
native float reconstruction tolerances; unrelated and original authored state
remains exact.

Consecutive disposable fixtures exposed a real deleted-Shape-Key cache hazard:
the old cache could dereference a freed KeyBlock before Python could reject it.
It now stores primitive Object/Key session identities and pointers, resolving
only current live KeyBlocks. Ordinary named hits remain indexed; rename repair
scans only live members. Rest-resync rollback uses the same proven live lookup
and no longer stores KeyBlock wrappers in its corrective checkpoint.
This cache-lifetime crash has a different native stack from the previously
reported GUI Undo crash; that original GUI failure is not certified eliminated.

Validation uses Blender 5.2.0 LTS, build `fbe6228777e7`, isolated factory scenes
and the saved X input copy SHA256
`4ba194afc9bf0d178391a137e366799433deb1685b0fa44bd4fc00ff8a640982`.
The X copy passed unchanged release/rebuild and a second round with legs
shortened to 85%, with eight native leg/foot/toe bones edited. It retained all
59 protected scene meshes, 310 other bones, 13 Actions and the body calibration
record. Ten selected derived widget objects are explicitly outside that mesh
equality check. The largest release/rebuild surface deviation was about
`2.12e-5` world units. These are recovery/roundtrip checks, not a recommendation
for the artist's proportions and not a full mesh proportion edit.

Feature suites passed: regional controls 18, placement 24, real registration and
Undo/Redo lifecycle 6, deleted-key lifetime 7, Rest resync 10, transactions 10,
workflow 13; pure UI 9+4 and pose-range 13. The native lifecycle includes Scene
settings persistence across save/reopen while the add-on is disabled. Failing
early runs and their crash reports remain alongside the final runs.
Two additional native checks passed release/rebuild and placement/ground while
the armature remained in Rest display, preserving original bones and hand inputs.

The X copy also passed +0.37, -0.37 and Ground as three separate operations.
The independent check covered 90 scene objects (18 moving, 72 unrelated), six
visible models / 74,591 evaluated vertices, all 69 raw scene meshes and both
Armature datablocks. Ground moved the bottom from `0.0735873431` to
`-1.11758709e-8`. Maximum object-matrix error was `1.20e-7`; the identified Dress
Hook/Spline IK had `5.43e-5` maximum vertex component error during signed movement
under its separate fixed `3e-4` independent cap. Other model vertices retained
the `3e-5` independent cap. Native authored inputs, weights, Shape Keys, Rest,
local pose and Action data matched exactly. The earlier fixed-`3e-5` Dress
failure report is retained as `actual_actor_placement_report_02.json`.

A disposable registered 0.77.45 runtime passed targeted function updates and
registration of the five new RNA classes / two Scene properties. Immediate raw
scene and existing-settings hashes matched; disable/re-enable after the update
also passed. Live delivery is indexed separately in the X acceptance receipt.
Live artist PID 23256 received the targeted update with matching immediate
native-input/settings hashes. Its original frame 8 and unsaved content remained;
only UI selection/sidebar navigation then opened Rig > Body to show Both Legs:
Controls Ready and the placement controls. No placement, proportion edit or
artist save was applied. Both deployment destinations had zero file differences;
the original artist was the only remaining Blender process.

Raw evidence, failed runs and reproducible scripts remain in
`D:/Blender/Projects/Character/X/validation/body_region_20261017/`.
The artist file and pinned input hashes were unchanged; no artist scene operation
or save was performed by this feature implementation. Test scene saves stay in
isolated temporary directories. Existing Body Setup suite failures are compared
with the unchanged 0.77.45 baseline in
`existing_suite_baseline_comparison.json` and `additional_baseline_comparison.json`.
The existing Body Setup, daily UI, Arms/Wrist UI and Original display failures
reproduce on that baseline; they are not claimed as passing.

Playback optimization remains separate. No viewport FPS or memory improvement
is claimed. The model and reasoning effort of this run are unrecorded.
