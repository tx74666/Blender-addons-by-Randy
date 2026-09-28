# Character Designer 0.62.9 — wrist calibration in Arms and continuous forearm twist

The normal Setup checklist is Arms, Legs and Fingers. Wrist Roll is part of the
same Arms candidate: project the proposed forearm Z axis perpendicular to the
authored hand's longitudinal axis. Preview, Apply and confirmation share this
candidate. Hand head/tail are retained; the character's natural 3.17-degree
hand/forearm longitudinal difference is not forced to zero. Wrist edits invalidate
Arms confirmation. Generate retains the existing no-Rest-write contract.

A-pose Plane, Mesh Reference, Palm tilt and palm-side controls are removed from the
normal UI. Their saved metadata and historical HANDS identifier remain readable.
Existing generated rigs are not migrated or removed. Forearm Twist Setup is in
Arms; the compact wrist axes, cyan forearm arrow and continuous arc remain.

Forearm Twist Setup starts at the current measured angle without altering pose
channels. Only explicit UI angle changes enter the temporary test. Numerical
orientation probes suppress mesh correction until the final pose; unconstrained
FK hands use a verified direct quaternion step. Profile validation/sorting is done
once per record, overlay transforms once per bone, and topology expansion reuses
one connectivity snapshot within each call. No geometry cache persists across
topology capture calls.

Fresh UI captures use WRIST_CONTINUOUS distribution. Adjacent quad loops extend
the detected sleeve; the selected seed becomes its actual start. Start/end anchors
are 0/100%, and the final twist carries into connected lower/hand-weighted wrist
vertices while other bones' contributions remain unchanged. Existing weights and
artist Shape Keys are never modified. Existing bounded captures retain their
behavior on re-entry; explicit Recapture preserves interior shares but resets the
new continuous endpoints. Cancel/Undo and owned-key safeguards remain intact.

Actual Blender 5.2.0 LTS tests passed: new Arms/wrist integration (8), calibration
(28), historical hands APIs (10), display cache (13), new Setup/continuity (11),
ownership (11), and existing forearm lifecycle, explicit range, topology, cache,
recovery and review regression suites. Pure profile tests (12) also passed.

The current unsaved Cosha/CoshaRig were tested only through independent data copies.
Combined Arms Preview/Apply/Confirm changed the two hand Rolls on the copy while
preserving hand endpoints, mesh/keys, weights and pose. Original scene snapshots
and the original file hash were unchanged; no test objects remain.

On that same character copy, at a 90-degree test, old captured-ring angles were
approximately 0, 39.87, 59.67, 0 degrees: the final forearm-weighted ring was forced
back to the original skinning. New angles are 0, 15.00, 40.11, 63.87, 81.37, 90.00
degrees along six adjacent loops. The partly upper-arm-weighted ring retains that
other influence, so this is not a claim of universal exact volume preservation.

Measured Python operator time in this session: Setup 919 ms -> 381 ms; explicit
45/-90/+90-degree UI adjustments 432/793/598 ms -> 56/55/54 ms. Intermediate mesh
calculation calls dropped from 12–20 to 2. These are single-session measurements,
not a guaranteed viewport FPS or performance result on every character.

Independent review found and verified fixes for mixed-weight start-loop correction,
explicit old-profile recapture endpoints, and metadata preservation when adding
loops. No remaining blocker was found in scope. Full artistic animation acceptance
and original Fingers acceptance were not repeated and remain unverified here.
All changes are local; no commit or push was performed.
