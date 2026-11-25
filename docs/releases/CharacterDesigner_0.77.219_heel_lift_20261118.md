# Character Designer 0.77.219 · Foot Roll heel lift

2026-11-18

In IK, rotate the circular arrow on `CTRL_foot_roll.L` or `CTRL_foot_roll.R` to raise the heel around the ball of that foot. The arrow turns around its own center on local X. Returning to zero restores the starting pose, and zero stops travel in the opposite direction. Translation, scale and the other two rotation axes are locked. Toe Bend remains a separate control.

New Foot Controls include the heel lift. For an existing rig, open **Body Setup & Recovery**, choose **Left Leg** or **Right Leg**, and click **Set Up Heel Lift** for each foot. The upgrade preserves the existing bone names and animation channels. If a retained clip uses the former bank, heel-down or toe-tip behavior, setup identifies the conflict and keeps the legacy rig intact.

Auto Align uses the current unrolled foot pose to locate the pivot when the leg is planted or raised. The ball continues to drive the lift past the old 45° toe-tip transition. Numeric and keyed angles can exceed half a turn. Blender's interactive Euler rotation can wrap when a drag crosses 180°.

In a Blender 5.2 copy of X at frame 26, a 20° standing lift raised the sampled left and right shoe heels by 39.5 mm and 41.9 mm. Across both feet, standing and raised, the toe joint moved less than 0.09 mm at the tested 20° and 60° lifts. Both setups preserved the rest matrices and pose inputs of all 392 existing bones. A native rotate-and-return test also preserved the held forearm and thumb poses. [Native measurements](D:/Blender/Projects/Character/X/validation/foot_roll_hinge_20261118/native/saved_x_acceptance09.json)

The delivery record tracks the official package, installation checks and the active X session. [Final acceptance and installation status](D:/Blender/Projects/Character/X/validation/foot_roll_hinge_20261118/acceptance219.json) · [Complete release projection](D:/Blender/Projects/Character/X/validation/foot_roll_hinge_20261118/stage219_09/projection.json)
