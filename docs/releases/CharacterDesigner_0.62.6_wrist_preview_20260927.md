# Character Designer 0.62.6 — distinguish wrist input from its candidate

The Arms view displayed the current hand Rest axes beside the arm calibration.
That could be mistaken for the wrist candidate even though Arms Apply changes
only upper/lower arm bones. In the observed character the uncalibrated hand Roll
differs from the Hands A-pose candidate by approximately 48.719 degrees.

Arms now shows the hand's current axes only in Details, explicitly labelled
Current wrist (Hands). Hands rotation axes and arcs pivot at the wrist head;
Palm plane and Up/down path distinguish the palm surface from its flexion path.
The existing A-pose direction calculation is unchanged: with a hand long axis
near character XZ and zero Palm tilt, the flexion axis is near character Y, so
the up/down path lies near XZ. It does not follow the old hand Roll. Arms/Hands
Apply boundaries and all generation behavior are unchanged.

Validation: AST and diff checks pass. The new overlay function was executed
read-only in the user's running Blender 5.2.0 LTS, verifying wrist-pivot path
coordinates, near-XZ flexion, the Arms display boundary, exact Rest/Pose/record
preservation and no added objects or bones. Settings were restored after that
check. Independent read-only code review found no blocking issue.

Added a regression covering ideal XZ hand axes, independence from old Roll,
Apply preserving endpoints, the wrist pivot and Arms diagnostic labels. The
separate full Hands background suite was initially not run because available
memory was below the user's 1 GiB launch threshold; this is distinct from the
completed live Blender drawing checks. See the delivery evidence for final
background-suite status. No new claim is made about complete Fingers acceptance.
