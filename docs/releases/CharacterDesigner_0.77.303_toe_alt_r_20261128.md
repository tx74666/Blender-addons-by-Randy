# Character Designer 0.77.303 — Toe rotation reset

**Alt+R** on either toe bend control clears its bend in the calibrated Toe Space. The posed FK or IK foot continues to determine the toe's neutral direction. The explicit Edit Pose command retains its rest-pose reference.

Mixed selections now pass the posed reference through to the wrist as well. This covers a wrist selected with foot controls and a wrist selected with ordinary pose controls. The ankle still resets relative to the shin, and the heel-roll control still clears its own rotation.

Native Blender 5.2 tests on a private opening of X's **Slide Start** measured a right-toe reference error of about 68° in FK with the previous build and under 0.01° with this release. Both toe controls passed in FK and IK. The tests also checked the registered Alt+R binding, Auto Key replay, exact Action preservation when Auto Key is off, rollback after a key write, and the narrow runtime activation against 0.77.302.

[Delivery receipt](<D:/Blender/Projects/Character/X/validation/toe_alt_r_20261128/delivery303.json>)
