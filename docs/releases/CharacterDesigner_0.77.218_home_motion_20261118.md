# Character Designer 0.77.218 · Edit imported motions on the character

The Animation sidebar and the Action Editor Motions picker keep an unprepared motion on the current scene. Clicking its name selects the clip and prompts for **Prepare**. After the checkmark appears, clicking its name opens the retained controller Action and slot on the character. **Prepare Missing** handles the remaining eligible motions in sequence.

**Animation → Advanced → Open Source Motion** opens the chosen original-skeleton motion in the separate source scene. **Return to X** returns to the character. Unity source rigs and clips remain available to the converter. Artists edit the mapped animation on their own character controls.

A private Blender 5.2 process opened the current saved X and checked the imported Walk Forward, catalog-only Walk_N, mapping attention, explicit source access and return. The saved X lacked a READY controller edit. A separately verified private file with an edited Walk Forward confirmed that both list entry points reopen its saved controller Action on the home rig. Both runs checked original Action data, geometry, weights, shape keys and rest bones. The UI and service suites passed 111 and 71 tests respectively.

The official archive and both deployed packages contain 291 verified files. Four changed against 0.77.217. A narrow live update retains one cached native tooltip until Blender next starts normally. Live status and the original test scopes are recorded in the [X acceptance record](D:/Blender/Projects/Character/X/validation/controller_home_open_20261118/acceptance218.json).
