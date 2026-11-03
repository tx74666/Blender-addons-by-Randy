# Character Designer 0.77.161 — selected Hair rebuild

Complete161 extends approved159, retaining its wrist/action-owner changes.
Official packaging, dual5.2/X deployment, and scoped live code adoption passed.

Changing a selected Hair entry from four bones to eight previously copied the
Main Rig recovery identity into an independent Hair version. Replacing its old
bone names then failed the inherited Main Rig Rest proof, after sampling had
already finished. Newly owned copies now detach that inherited identity; the
original rig, recovery references and Actions remain intact. The new binding is
verified before sampling and checked again afterward.

Generated, constraint-free Hair bones with NONE scale inheritance use native
local-to-pose conversion with each parent's actual decomposed matrix. Unexpected
settings or existing dependencies retain the previous evaluation order. Sample
times, motion targets, curve writing and numerical limits are unchanged.

Sampling now also checkpoints the pre-existing automatic Body/FK display targets.
Success and rollback restore their current display records, native membership
order, widgets, visibility and two per-rig cache entries against the captured
native identities. This restores the current operation's checkpoint.

Private acceptance covers one-bone and whole-chain inputs, adopted Main Rigs,
early refusal and rollback, display handlers, numerical equivalence and scoped
runtime adoption. The saved X input was pinned at
`22b7a093f3dc4066bdef9bc3c66041dc2aed20794d57aec98e9975b9ad435ab9`.
Its Hair 19 entry (native Hair 23) passed four-to-eight conversion across the
unchanged 64-sample grid, with all original author and context witnesses exact.
The final Controls observation was 32.47 seconds for the operation, including
21.45 seconds of sampling; the old observation failed after 45.78 seconds,
including 41.21 seconds of sampling. These are single private background
observations under varying load, not artist GUI timing or FPS measurements.
The public Original-mode preparation and the same 64-sample conversion also
passed all original author and context witnesses; its operation observation was
17.19 seconds, including 10.23 seconds of sampling.

Evidence and all earlier failures are retained under
`D:/Blender/Projects/Character/X/validation/hair_rebuild_20261103` and
`D:/Blender/Projects/Character/X/validation/hair_resegment_mainrig_20261103`.
The artist received three existing function-code updates and five explicit
helpers. Existing Python/RNA identities and native state were retained; runtime
metadata156 remains, so this is component adoption rather than a full reload.
The Hair task performed no scene or preferences save. The final receipt is
`validation/hair_rebuild_20261103/acceptance161_01.json`.
