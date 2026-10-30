# Character Designer .106 — Physics setup and Animation sections

Animation now has independently collapsible Physics, Original Rigging Inputs
and Animations sections. Skirt and Hair fold separately. Narrow sidebars show
wrapped messages with expandable full details instead of clipped long text.

Physics distinguishes permission, external add-on registration and an active
simulation session. Installed verified backends have an explicit Enable/Check
action. Missing, disabled, incompatible, conflicting and incomplete cleanup
states have separate explanations. A refused action retains its real reason
for the current character/source. No download or preference save is performed.

Dress's Original-mode restriction is retained. A dedicated Return to Controls
button calls the existing checked return transaction only after a user click.
The native single-user guards now use the same validated recovery-reference
proof as Bone Display, while actual shared or unproven armatures remain refused.

The dependency inspector compares opaque PropertyRNA identities when an add-on
is disabled. It never dereferences potentially freed PropertyGroup targets.
Re-enable after an official disable requires the exact previously authenticated
source/module/native-property identity; unknown leftover registrations remain
refused. Both adapters check all expected RNA classes before accessing their
live registered pointer schema.

This release continues to use Hair's verified Wiggle Bones 1.1.2 adapter and
Skirt's Wiggle 2 2.2.4 adapter. It does not claim that both distinct backends can
own the shared native schema simultaneously. Connecting Hair to the installed
Wiggle 2 is a separate pending preference, not a completed part of this release.

Candidate/actual deployment, native receipts, artist refresh and saved-scene
limits are tracked independently in X's
`validation/animation_physics106_20261028/DELIVERY_STATUS.json`.
