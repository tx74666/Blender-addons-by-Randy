# Character Designer 0.77.45: animation connection and history safety

2026-10-16, local Blender 5.2 / X deployment. No Git commit or push.

After Restore Main Rig, clicking either animation now resumes the retained
editing session before activating that Action. Rejected activation restores
the preceding artist state. The separate entry is named Edit Animations.
Connected/check shows a workspace identity verified by Connect/Refresh;
failed Refresh has its own error state. The check is a file-exchange receipt,
not a live Unity process or continuous filesystem monitor. Drawing performs
no filesystem probes.

The reported X crash was an ANIM_armature_runtime_refresh null read. Its main
thread was in Undo post processing, entering Python and forcing an evaluated
depsgraph. Forearm Undo/Redo now discards stale Python references and defers
preview recovery/evaluation until Blender supplies a subsequent graph. It no
longer requests a graph when there are no managed meshes. Restricted add-on
teardown also retains active Dress recovery until native data is available.

The original crash was not deterministically reproduced: a baseline .44
isolated single-thread run passed too. The .45 artist-copy test passed normal
worker threading, direct Custom resume, restore and Source resume, switching
back to Custom, and four Undo/Redo rounds. Cache, recovery and real restricted
enable/disable native suites passed. The original GUI Undo failure has not
been certified eliminated, and native Undo was not attempted on the artist.

Live X PID 48720 was observed anew. Original display was active, custom shapes
were disabled, and 84 controller shape assignments still existed. The existing
Controls operation restored display and native FK presentation (76 assignments;
eight FK widget bindings removed by the existing policy, assets retained).
Walk_N Custom is active at frame 22 in Pose Mode; Rest/Pose roundtrip passed.

Independent original-backup/current witnesses matched all 69 scene mesh raw
coordinates, topology, weights and author Shape Keys, Source/Custom key data,
and bone rest matrices. This does not claim unchanged evaluated geometry while
switching animations or identical visibility/pose between Original and Controls.
Native save returned FINISHED; later is_saved=true/is_dirty=false and disk SHA256
`a61225d51e5f6432f4eca1359a06a4c3e5bbef54a2260441843011bc73851941` passed.
Both deployment checks had zero differences. No full PropertyGroup teardown,
Blender restart, Unity modification, mesh deletion or performance sampling.

Local raw evidence and scripts:
`D:/Blender/Projects/Character/X/validation/animation_reconnect_crash_20261016/`.
Keep `X_reported_crash.txt`, `input_artist.blend`,
`artist_before_reconnect.blend`, stage reports, protection witnesses,
`live_delivery.json` and `post_eventloop_save.json`. The task bridge was stopped;
only the artist Blender remained. Scene backups are not in the release ZIP.

Playback remains a separate unresolved cost. Reuse the .41 performance evidence:
deleting Cosha also removes the sole Forearm owner; prior complete-role GUI
results were approximately 4.4/5.6 Hz. This release claims no FPS improvement.
The older PC2/twist-bone experiments remain unaccepted for the current model.
