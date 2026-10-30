# Character Designer 0.77.89 — control display after playback

Automatic control-display checks could periodically repeat expensive ownership
authentication during Idle playback. Native playback now retains the current
control display and defers automatic visibility synchronization. Stopping
consumes the deferred request once and runs fresh ownership/inventory checks
before updating display. Explicit rig operations keep their original guards.

Actual animation, constraints, mesh deformation and Forearm corrections still
evaluate each frame. No character geometry or display quality is reduced.
No playback timer or positive ownership cache is introduced. Lifecycle state
contains only numeric scene identities; load, Undo/Redo and unregister clear it.

The native GUI test on the latest protected X SaveCopy passed Play/Stop, an
unknown reference, Esc frame restoration and real Undo/Redo. During the three
observed play segments, display ownership/sync calls were zero. Stop ran one
fresh front ownership gate each time; this saved input still refused ownership,
so no display write was authorized. Three witness frames retained all pose,
scene mesh and Shape Key hashes exactly. Pure display/cache tests: 23 passed.
The fixture's native Undo/Redo stopped playback and cleared deferred state.

These are lifecycle and correctness results, not a GUI FPS measurement. Remaining
Forearm cost is still substantial. A separate exact bulk-coordinate experiment
showed no meaningful frame-time benefit and was reverted before release.

The complete 215-file input extends approved .88 by exactly bone_collections.py,
literal version metadata and README. All other 212 files remain unchanged,
including approved body_region and Forearm implementations. Build, deploy and
check must use:

`D:/Blender/Projects/Character/X/validation/idle_followup_20261023/projection089/projection.json`

SHA256: `ea9bf761598bf97c1a3ea224be506dc0df1b0aea244f3c17fd2e70e112a97221`.
The paused canonical body-region candidate remains excluded. Source notes do
not establish delivery: installation rollback, deployed pins, protected live
update and cleanup are recorded in X `validation/idle_followup_20261023/`.
No Git commit or push is included.
