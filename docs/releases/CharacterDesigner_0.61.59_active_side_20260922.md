# Character Designer 0.61.59 — independent current-side finger work

Editing one hand no longer requires the other hand to match. Capture, Start/End,
Recheck, Clear, reverse bend, Align Finger, Mark, Align Joints and Roll calibration
use only the current side. Existing opposite references, marks and bones stay
unchanged, even when Armature X Mirror is enabled. Calibrate Selected ignores
selected bones on the other side. Relax Bones retains its independent selected
chain behavior and existing X Mirror support.

The header uses the current side's readiness and error state. Previously detected
left/right differences appear as one small non-red `L/R differ` badge, without
pairing warnings or blocked current-side actions. The badge reads saved detection
metadata, never scans geometry during editing, and is not a live comparison.
Users can mirror their finished mesh separately through Mirror Selected Region.

Bone Chain now has two equal-width rows: Align Finger / Mark, then Align Joints /
Relax Bones. Mark keeps its small clear icon. Both saved loops remain bright
yellow and share one retained GPU batch; no synthetic opposite guides or borrowed
marks are created. Master-eye toggles and editing retain the existing cached
polylines, with no dependency-graph mesh monitor.

Actual current-side bone writes retain geometry validation and rollback.
Updating the add-on does not save or modify the artist's mesh or bone transforms.

Validation: 91 checks passed in serial Blender 5.2.0 disposable-fixture runs,
covering current-side capture/clear/alignment/calibration, opposite-data retention,
automatic mark ordering, source validation and rollback, Relax and Edit Mode
preservation, frozen reference/bend/marker caches, and actual enable/hot reload.
Cache tests forbid geometry reads during display, eye/side toggles and repeated
mesh updates; the marker renderer retains one batch across 2,001 draws. This is
validation of the add-on work avoided, not a claim about the model's total FPS.
