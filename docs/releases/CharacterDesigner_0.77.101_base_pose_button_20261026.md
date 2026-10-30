# Character Designer 0.77.101 — Base Pose button

The left Base Pose button now explicitly enters/resets Base Pose even after Return to Animation stored finish=True in native operator last-properties. A return request without an active test reports that no return was performed. The pose engine and Forearm remain unchanged.

Complete frozen projection: `D:/Blender/Projects/Character/X/validation/base_pose_button_20261025/projection101/projection.json` (SHA f6b7d57ce2efa0b7ce78c95ac3b510815eb4872685d2645fd5b66658fa7941e5), 217 files/185py, UI/version/README only. Official build and dual deployment checks passed. Existing78 UI tests and7 native steps are retained; the current artist received the narrow update and one actual left-button click succeeded, followed by native Save and dirty=false verification.

Current artist evidence, exact save/backup pins, independent review and preservation limits: `D:/Blender/Projects/Character/X/validation/base_pose_return_20261026/README.md`. This is a functionality repair; no FPS measurement or performance gain is claimed. No Git commit/push.
