# Character Designer 0.77.262 — Original Bones and Neutral

Original Bones can now open and return to Controls on CoshaRig when Blender has retained the foot control shapes and removed their display collections. Pose transfers still validate the active control bindings. Structural and rest-edit operations continue to require their asset inventory.

Opening the Neutral clip displays the original toes, wrists and fingers as native Blender bones. The Rig panel highlights Original Bones. Fingers can be keyed directly on the single-frame Neutral action. Click Original Bones to pose constrained joints, then return to Controls before keying. Switching to a controller clip restores its controls.

The private Blender 5.2 test exercised both toe bones, the native hand display, a keyed Neutral finger, and a pose transfer after simultaneous toe and finger edits. The working Walk_N Feet IK action kept its original keys. Two isolated refreshes preserved all 28 measured scene properties. The focused regression suite passed 95 tests.

Both installed copies matched the 320-file release. In the artist window the native refresh and Original Bones entry completed on Walk_N · Feet IK at frame 1. The right toe was visibly selected. The window remains in Original Bones for editing, with its existing unsaved state.

[Functional checks](<D:/Blender/Projects/Character/X/validation/original_bones_display_20261127/native01.json>) · [Delivery and hashes](<D:/Blender/Projects/Character/X/validation/original_bones_display_20261127/delivery262.json>) · [Approved projection](<D:/Blender/Projects/Character/X/validation/original_bones_display_20261127/stage262_03/projection.json>)
