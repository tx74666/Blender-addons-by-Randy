# Character Designer 0.62.1 — visual calibration preview

Incremental update to 0.62.0. No change to calibration math, allowed Rest writes,
weights, Shape Keys, generation schema or legacy rig policy.

- Preview now enables Show Directions itself. Setup remains the existing entry.
- The normal panel contains part states, relevant controls and actions. Direction
  metrics, zero-noise diagnostics, explanatory text and Undo help move to Details.
- Default viewport labels are shorter. Detailed bone names and metrics follow the
  same Details switch; solid current / dashed candidate geometry stays visible.
- A readonly preflight checks only the selected part's current rig before Apply.
  It distinguishes local Pose offsets, evaluated posture, constraints, animation,
  existing controls and editability. It does not scan all scene dependencies.
- Full Apply still validates external dependencies, flushes Edit data and rechecks
  signatures inside the original transaction. Pose is never cleared automatically.
- Select Posed Bones selects local Pose inputs only. Hidden/locked bones are refused
  before context changes. The operator does not change pose transforms or Rest.
- Failed actions reveal Details; a selection failure remains visible alongside the
  general blocker, so a hidden bone warning is not lost.

Live readonly inspection of the user's f_index.01.L found local translation X
0.0041360096 with skin-matrix deviation 0.00407486. Its parent arm/hand transforms
were within 2e-5 Rest tolerance. This is a real pose offset, not numerical noise.
No live pose, mesh data or Rest was changed by this inspection.

23 Blender 5.2.0 integration tests passed (10.548 s), including Preview enabling
visuals without Rest writes, pose/constraint/animation classification, selective
bone location and pose/rest preservation, hidden-selection refusal, compact panel
labels, Details-signature independence and uncommitted Edit preflight handling.
Independent readonly review found no release-blocking issue. Existing 0.62.0
real-character full rig/motion/export acceptance limitations still apply.

Final GUI probe passed Undo, Redo, reload, multiple viewports, file-reload handlers,
visible pose blockers and selective UI reload preservation, with no reported errors.
Both local deployments match all 108 packaged files. The user's live Blender was
updated after saving its unsaved state to a separate backup. Rest, Pose, calibration
records, mode, selection and the original on-disk file were preserved. Clicking
Preview in the installed live panel visibly enabled the direction overlay.

Final GUI, deployment and source hashes are recorded in the task artifact folder:
D:\Blender\Projects\Character\X\task_artifacts\body_calibration_20260927.
No commit or push. Preserve concurrent RR Helper work.
