# Character Designer 0.77.44: Rest Pose and compact animation rows

2026-10-16, local deployment to Blender 5.2 and X's validation copy.

The Animation panel has explicit Rest Pose / Pose display buttons. They preserve
Actions, keyframes, pose channels and bone rest data, including when the artist
is editing a mesh. Choosing a worklist animation resumes a Rest display opened
by these buttons; rejected worklist operations restore that display.

The original and editable animations remain separate left/right buttons. Plain
rows remove the native UIList selection border, and long lists scroll with the
Sidebar. Playback and frame changes use Blender's Space/Timeline controls.

Restore Main Rig is in Advanced. It restores the artist state captured before
Use Main Rig and exits that animation editing session; it is separate from Rest
Pose. Worklist switching and Main Rig adoption still require Object/Pose Mode.

Validation:

- 178 targeted Python tests passed across Rest display, worklist, rigging
  attachment, Main Rig recovery and UI behavior.
- An isolated Blender 5.2 fixture verified actual posed/rest mesh evaluation,
  Action/slot/keyframe and bone rest preservation, Mesh Edit selection, failed
  operation display rollback, successful resume and normal add-on registration.
- Live X PID 24416 passed Rest/Pose/Rest in Shoes Mesh Edit without changes to
  mesh/edit selection, bone rest, pose channels, frame or Source/Custom key data.
  PropertyGroup registrations and the broader add-on runtime were retained.
- Both deployment checks reported zero different files. Native save returned
  FINISHED, followed by a separate event-loop observation of is_saved=true and
  is_dirty=false. The artist was left in Rest display and Shoes Mesh Edit.
- The task's temporary localhost bridge was stopped; no Blender restart or
  Unity scene/export operation was performed.

Local evidence is under
`D:/Blender/Projects/Character/X/validation/animation_rest_pose_20261016/`:
`native_fixture_report.json`, `live_delivery.json`, `post_eventloop_save.json`,
the replay scripts and `artist_before_rest_display.blend`. The saved artist
SHA256 at delivery was
`0b4e22062f5e906acc9b59cdf8ce682b71defbb47fd15febbc1eb3207a5eb18c`.
These scene assets/evidence remain local; they are not embedded in the release
archive. The source/package changes are local and have not been committed or
pushed by this task.
