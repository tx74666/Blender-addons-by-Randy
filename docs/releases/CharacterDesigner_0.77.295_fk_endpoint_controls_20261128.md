# Character Designer 0.77.295 — FK wrist and foot controls

FK action editing keeps the wrist silhouettes and fitted sole outlines on the existing hand and foot bones. The upper and lower segments still display as bones. The ankle outline reuses the fitted IK geometry in the native ankle's rest frame. Rotation uses the existing FK joint pivot. IK continues to use its own targets and pose channels.

Existing clips can use **Animation → Set Up FK Controls** in FK or Mixed mode when a wrist or foot shape is missing. The operation uses the current clip's display ownership proof. It can recreate detached, missing wrist widgets from their saved records and leaves existing custom shapes and animated display data under their current ownership.

The release extends 0.77.294 by five files. Both Blender 5.2 and X have 329 files with zero differences from the complete projection. The current X session was observed in Object Mode with Blaster selected and unsaved changes. The package was deployed to disk. The live session and X.blend were left in their current state.

On an isolated load of saved X, the existing clip, slot, and action hash were preserved. Both wrists and ankles passed FK rotation and all four targets passed IK translation. Repopulating a detached legacy display, switching IK → FK → IK, running setup twice, and an injected widget creation rollback passed. The FK visual unit suite passed 22 tests, the native FK visual suite passed 8, the native limb display suite passed 10, and the current-frame controller pose test passed. The broader regional style fixture reports the same 17 errors on 0.77.294 and 0.77.295, including its preexisting heel-lift fixture errors.

[Delivery receipt](<D:/Blender/Projects/Character/X/validation/fk_endpoint_controls_20261128/delivery295.json>)
