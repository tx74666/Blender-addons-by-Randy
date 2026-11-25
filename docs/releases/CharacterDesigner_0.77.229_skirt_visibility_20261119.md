# Character Designer 0.77.229 — skirt Bone Collections

Dress now contains Deform Bones, Manual Controls, Mechanism Bones and Physics Bones. For X, the daily view shows the 32 weighted skirt segments and their weighted waist. With skirt Control Mode on, three ring handles including that waist and sixteen small handles are available. The display switch works with the physics master off. The artist can open either helper collection directly for inspection. Subsequent refreshes retain manual collection-eye changes.

Children are identified by role and their parent's owner, so collection names can be localized or renamed. Shared rigs retain their exact source and bone membership checks. The existing Original and temporary display systems continue to own their saved views. The solver and simulation permissions follow the existing Animation Physics actions.

The release extends the complete 0.77.228 projection. Eight runtime files changed, one module was added and the other 290 files are byte-identical. The ZIP and the two installations match all 299 files. The paused Weight-page condition in the canonical bone display panel was excluded. [Deployment](<D:/Blender/Projects/Character/X/validation/skirt_visibility_20261119/delivery.json>)

Blender 5.2.0 LTS tests covered actual X with physics off, public mode toggles, manual inspection eyes, rejection and rollback, new and legacy rigs, Original round trips, private save/reopen and owned removal. The exact X check compared the pose, evaluated meshes, animation, weights and unrelated display. Twenty native shared-rig and UI tests, six accessory checks and 125 focused Python tests passed. [X verification](<D:/Blender/Projects/Character/X/validation/skirt_visibility_20261119/x04.json>) · [Test receipts](<D:/Blender/Projects/Character/X/validation/skirt_visibility_20261119/review.json>)

Open X was updated in place on frame 26 in OBJECT mode. The pose, foot matrices, evaluated meshes, weights, Action, selection and physics state matched before and after. The scene remains unsaved. [Live result](<D:/Blender/Projects/Character/X/validation/skirt_visibility_20261119/live01.json>)
