# Character Designer 0.77.182 — character-owned animation slots

Status: source/ZIP/dual disk delivered. Private roundtrip and re-edit readback
passed. X's installed183 inherits all six protocol modules exactly; current
artist live adoption was deferred after the user said not to operate Blender.

Prepared ordinary Work Items can bind to a fixed character-owned semantic slot,
independently of Action names, exported filenames and work-item variant IDs.
The optional `targetSlotId` is retained in saved Link associations, compared
between workspace rows and packets, frozen into export jobs, and emitted at the
return sidecar root. Publication checks the actual sidecar before advancing a
Link revision. Routing metadata does not grant Unity application authority.

The key is case-sensitive lowercase, 1–96 characters, matching
`^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)*$`. Walk Forward uses
`locomotion.walk.forward`. Only absent, null and empty-string values are legacy;
whitespace, coercion and a missing downstream nonempty binding are refused.
Legacy identity tuple shapes and unbound collect/preview/Apply behavior remain.
No watcher, reducer, solver or per-drag synchronization was added.

Actual-model verification exposed an existing ordinary Add copy defect:
disconnecting every copied bone also broke generated Stable arm helpers. Add
now authenticates its actual ordinary packet mapping and permits translations
only on mapped, nondelegated bones. Plain FK uses its mapped bone domain.
Generated helpers and delegated structure keep their native connections;
Rigging without a validated control plan still refuses before copying.

Controller rigs remain conservatively Unknown to the plain-FK change scanner.
Use the explicit current-motion export, available through F3 **Sync Current to
Unity**, and **Capture Export Base Pose** when required. This release does not
certify automatic changed-only filtering for arbitrary constrained rigs, new
semantic Composition/New Action application protocols, or old legacy rig data
switching/recovery. The current artist's deferred Main Rig restoration is not
performed by this work.

Approved Blender projection: `X/validation/owned_animation_slots_20261111/stage182_02/projection.json`,
SHA256 `1147451a58b8cf966c61c097d1c07bca156847d59641dd54c44cd74b82bfada6`.
Complete181 plus eight reviewed files, 268 total / 260 exact baseline. The
existing canonical relocation of `_populate_controller_custom` is AST-identical
to181; only `_editing_data` and `add` change worklist function semantics.

Frozen initial protocol checks:223 passing executions. After the copy fix,
five affected entrypoints:116 passing executions; these overlap and are not
summed as unique coverage. Original failures, fixture corrections, manifests,
SHA/AST reuse proof and independent audit remain under `frozen_pure01/02`.

Native records are retained independently: native01 refused shared private
Armature data; native02 exposed the helper-connection defect; native03 hit a
private Windows cache path limit; n04 completed the real controller conversion
but failed the extra Source pose gate after nonactivating legacy restoration.
The p05 diagnostic preserved that failure and identified the incorrect
connected-data preparation: the same Source on correct motion data reproduces
within the original gate, including switches from raw IK1 back to keyed FK0.
The final run uses an explicitly prepared new private Motion Rig, not a repair
or replacement of artist data. No earlier failed run is relabeled passed.

Native n06 completed the original Source gate, actual controller edit and real
Sync Selected publication to revision1. LeftLowerArm changed18.4729degrees,
loop endpoints were exact, original Action/Source remained independent and
unchanged, and both the main process and worker exited. The latest native-saved
edited scene was synchronized to the private Link's existing blendFile, with
the pre-edit checkpoint backed up. Original reports were not rewritten.

Character2's Unity Update passed13 core native checks, including same-slot
application, other24 slots/Avatar/canonical preservation, two footstep events,
loop/root settings, visible17.938degrees arm change, no-op repeat, Re-Edit zero
sampling and queue-lock receipt recovery. Its final metadata-lock fixture had
an incorrect JSON reader which synthesized composition objects; the guard
correctly refused. That failure remains. With its fixture corrected, the three
remaining real workspace-lock checks also passed: locked publication preserves
motion/receipts/bytes, then explicit Update0 repairs current metadata without
creating another receipt, revision or application Link.
Blender skeletal export does not export animation events. The private target is
a Cosha-derived clone, not the formal artist character.

r07 opened the Link's latest native-saved blendFile with scripts enabled and
proved the edited Custom and immutable Source hashes. Real connect, fresh
workspace and route validation against the pinned healthy Unity publication
retained Action/rig/data/row identity, slots, curves, Rest and frame. All input
bytes remained unchanged. The updated current clip and targetHash do not replace
stable source provenance or semantic slot identity.

Formal Cosha Walk initialization is separate from the private edited return.
After Unity naturally exited Play, it was filled and immediately applied from
an independent working clip, preserving the unapplied Blaster candidate and
other motions. Test arm edits were not applied to the formal target. The new
working asset is `Assets/Prefabs/Characters/Cosha/Cosha.Player.locomotion.walk.forward.Working.anim`,
GUID `e6d0e328d6e708d4c858be7c3b61221e`, with managed Work Item
`cc3eda0c-b7ef-4f0e-85ba-335f62d1bb2f`. The production queue contains its original
seven rows plus this new row. The evidence reconstructing the old queue hash
was performed on an in-memory copy, not by removing the new production row.
Direct Fill does not create an edited-return application receipt; the original
QA expectation to do so was wrong and is retained as a fixture failure.
Character2's final aggregate records33 native checks:28 private plus5 formal.
Formal repeat Fill creates or writes nothing and resolves the same Working,
Work Item and Source Link, with no packet sampling. The final Unity owner
receipt is pinned in `acceptance182_01.json` (SHA256
`3b253633f9134c6428ece14f466c7166b90d0c9c9695818de71af3b67032617b`).

Official ZIP SHA256
`4140bcd827aaef990b9983cd7ac2bacc087d5ef6035bb0ef862f9896ba44c1d6`;
268 payload files/CRC passed. Official5.2 Roaming and X deployment changed8 files
each; matching checks report0 differences. `disk182_01.json` records the exact
projection, backup and destinations. Disk delivery is not artist live adoption;
X prepared the combined183 narrow adoption, then stopped before any artist
input or code loading after the user's explicit refusal. Installed183 has
projection SHA256 `839fd7030e6f19a443fcca2182f7cccd962387719814c96d7aa3117409053d84`
and ZIP SHA256 `e6b8a25e73f146085a7c3f735aebf89805e4183368ed81f046b34cc5e7a8e1f1`.
Its private loader/forearm checks are not artist adoption proof. The last
reported loaded artist version was181. No artist scene/preference
save, restart, full reload, Git upload, whole-library bake or FPS certification
was performed by this release work.
