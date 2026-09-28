# Character Designer 0.62.14 — restore the Fingers status column

Restore a read-only checkbox indicator beside Fingers. All three navigation rows
use the same 50/50 split, so Fingers no longer fills the complete row.
Captured means at least one saved Finger capture exists on a mesh in the current
scene bound to this rig. It does not certify validity or confirm Body Calibration.
The query checks saved metadata only; no JSON parsing, geometry validation,
Apply/Confirm workflow or Generate gate has been reintroduced.

Actual validation: existing Arms/wrist Blender suite 9 tests passed, including
no duplicate Finger audit/overlay; both local deployment targets match all 110
files. Live UI functions refreshed; current character has capture records. Native
Rest, pose and calibration records remained unchanged; no .blend save or push.
Full motion acceptance was not repeated for this layout-only change (未验证).

0.62.13 was the intermediate status-column package; 0.62.14 includes explicit
equal widths. Previous packaged archives were retained unchanged.

Evidence: `D:\Blender\Projects\Character\X\task_artifacts\body_calibration_20260927\finger_row_0_62_13`
