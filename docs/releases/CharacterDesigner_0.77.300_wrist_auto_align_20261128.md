# Character Designer 0.77.300 — Wrist Auto Align

Select the generated wrist IK control in the Animation panel to use **Auto Align** and **Align Wrist**. With the rig's existing forearm follow on, **Auto Align** settles an unkeyed wrist to neutral when a `G` move is confirmed. This editing option starts enabled for each Blender session. Existing rotation curves on the active slot, including interpolation between keys, take precedence. A confirmed move and its alignment undo together.

**Align Wrist** and `Alt+R` explicitly match the current forearm pose. Use a Rotation key to keep that pose at the current frame. Native Auto Key settings still determine which channels are recorded. When Grab creates a wrist rotation key in an unanimated slot, it records the aligned value at the current frame.

Blender 5.2 UI event tests verified Grab, cancel, undo, authored interpolation, Only Insert Available, and an empty Action with Auto Key. The saved X file was not modified by these tests. The current artist window was left running with its existing in-memory add-on and scene.

[Delivery receipt](<D:/Blender/Projects/Character/X/validation/wrist_auto_align_20261128/delivery300.json>)
