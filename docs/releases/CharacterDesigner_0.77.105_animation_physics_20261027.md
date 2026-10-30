# Character Designer 0.77.105 — Animation physics controls

Animation now exposes the current character's **Enable Physics** switch,
separate Hair/Skirt permissions, and the existing Dress Automatic/Manual
choices above the animation worklist. Selecting a Dress mode does not create
another rig or change its control method.

The master switch releases only owned Dress Wiggle sessions while retaining
their saved mode, disables the actual owned Cloth endpoints for all supported
Dress backends, and freezes an active Hair preview without rewinding the
timeline. A separate Start Hair Preview action uses the existing supported
adapter. The checkbox is permission; running, paused and refused states are
shown separately. Existing backend compatibility and preview lifecycle rules
remain in force.

Suspended Cloth flags and source ownership are stored as versioned JSON.
Resume proves ownership and native bindings before touching an endpoint;
foreign replacements and edited states are preserved and refused. Existing
Rig activation paths respect the same permission. Source data, authored
animation, Rest, weights and Shape Keys are not replaced by these controls.

The complete 222-file/190-Python release extends approved104 by eight changed
files and two new modules; 212 files remain exact. Canonical paused candidates
are excluded. The approved manifest is
`D:/Blender/Projects/Character/X/validation/animation_physics_20261027/stage105_02/projection.json`
(SHA256 `dddefebc2c4a4128bae75dd15b266408b9fca3bde0a3e614df4571ed133059a4`).

Validation: 77 focused pure tests, nine native Wiggle/Hair tests including
unknown-callback refusal and private Off-state reopen, plus small native Direct
and Actual Surface fixtures exercising Automatic/Manual, Off/On, typed output
and Cloth flags, author inputs, cache configuration and private save/reopen.
The first native run's three Hair callback refusals remain preserved; the
exact, separately reviewed normal-bridge guard is now recognized. Arbitrary
pose callbacks remain refused.

These checks do not establish an FPS gain, collision quality, cached simulation
payload preservation or artist-scene playback performance. Formal scene and
runtime delivery evidence are recorded separately in the X validation folder.
No Git commit/push, Unity operation or original preference save is part of this
release.

Final delivery completed 2026-10-28: both official targets have222files/0diff;
native history2/2 also passed. Artist was directly observed already .105. A
read-only audit verified190source signature rows,47loaded function code checks
and three registered physics class identities. Five functions in lazy modules
were left unloaded and have disk proof only. Earlier installers both refused
before code commit; the v2 refusal retained complete author equality. The prior
refresh was not observed, so preservation across it is not claimed.

The actual Animation Physics panel is visible; original Manual and dirty artist
edits are retained, and the temporary Console is restored to 3D Viewport. No
artist physics action or formal scene save was performed. Exact receipts and
limits are indexed in the X folder's `acceptance_summary105.json` (SHA256
`a16403bef4103578728ff1d47dee858580e51ebf1fd20a308b31216d287865e3`).
