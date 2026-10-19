# Character Designer 0.77.59 · Front leg Poles

2026-10-18. Candidate03 passed **84 native tests** in isolated Blender 5.2
(`fbe6228777e7`), including 12 front-leg policy and four angle-key fixtures.
The isolated real-model repair, repeat, save/reopen and fresh normal-runtime
reopen also passed. The .59 ZIP is locally deployed to Blender 5.2 and X, with
all 204 files matching the frozen manifest in both destinations. The authorized
repair has also passed in the artist's .59 session and was saved normally to
`X.blend`. Independent readback of that artist save passed all 24 strict checks.

Direct leg controls now keep their Pole in front of the leg, on its own side,
while matching the native IK angle to the desired pose. Matching no longer
moves the Pole behind the character to compensate for source-bone roll. The
same policy covers regional rebuild, IK/FK switching and return from Original.
Arms retain their existing policy. Pole repair reuses existing functional bones
and artist custom shapes; it does not rebuild native Rest or mesh data.
A right-leg IK angle near ±pi can still be correct: its value compensates the
original axes while the controller remains in front.

The repair service preserves the current native pose by default. Recovering
an already twisted pose requires explicitly admitted reference matrices; it
does not infer a lost artist pose from Rest. Healthy controls within the
existing `2e-4` matching bound avoid unnecessary rematching. Accepted front
direction metadata is retained exactly; absent or backward metadata can be
corrected. Angle and managed direction metadata participate in failure
recovery for individual/batch switching, Original return and pose operations.

When a switch is keyed, the IK angle is keyed with it. The bookend restoration
also freezes both native Bezier handles before restoring the incoming segment,
preventing AUTO_CLAMPED handle recalculation from changing earlier animation.
This concerns the tested switch paths and their protected curve history;
unchanged animation in every possible workflow is not claimed. No FPS gain is
claimed. Move to Floor remains the separate .58 operation.

## Frozen source and verification

The base is `.58` ZIP SHA256
`975658c6ae46760716493ac631febb68abef4496a0c21d8cb3dd5dcf77147d4e`.
Candidate03 contains 204 files under manifest SHA256
`d5ddc39cc4efe0cebb39c0b9d42a44f2c7551af8aea49dc8b7608242496c452c`.
Six files differ: initializer version, `limb_ik_fk.py`, `limb_ik_fk_batch.py`,
`body_region.py`, `body_original_mode.py` and `control_pose_assets.py`.
The other 198 accepted files retain identical bytes.

The projector enforces the per-function allowlist, including a docstring-only
change to `_match_fixed_pole_angle`. Only `_capture_pole_layout` and
`layout_poles` are overlaid into `body_region.py`; paused ownership changes stay
canonical-only. Paused .48 watcher/provenance/display changes remain excluded.
Candidate03 differs from candidate02 in only `limb_ik_fk.py`. Its 12 + 4 focused
test modules are immutable copies under `candidate03/tests/`.

`native_c3_report.json` records all 84 tests passing with no skips and all 204
source hashes verified. Coverage also includes regional Pole layout, Original
leg edits/return, Original mode, pose weights, and individual/batch IK/FK.
The locally built `.59` ZIP SHA256 is
`7702b3063c5cd0ae87f31e1ff9db16f9c12456278a0835527d537db71dbeddd4`.
`deployment_candidate03_verified.json` independently checks ZIP integrity and
all 204 file hashes in the frozen source, Blender 5.2 installation and X copy;
both installed trees have zero differences. It also verifies all 12 backed-up
files against the .58 base. Deployment backup:
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20261018-154807-59184e47`.
The verification receipt SHA256 is
`d01364e60a83d38d173c13597eeaaf189a81b0899d90dd3ef03e791c545d6195`.

The real-model V2 report passed all five stages: repair, repeated repair,
isolated save, reopen and fresh trusted reopen with normal runtime active.
Both Poles remain in front on their own side. Maximum error across the eight
admitted native leg matrices is `1.93938613e-5`, below the existing `2e-4`
bound; current nonleg native matrix error is zero. The admitted old Shoes
surface maximum error is `5.10873584e-6`, below `1e-4`. Repeated repair is a
no-op, with exact control channels and zero Pole position/angle change.
Raw meshes/keys/weights, all Rest, custom styles, Object inputs, existing
Actions and drivers retain their protected fingerprints. Action comparison
uses the accepted eight-key-field semantic fingerprint plus metadata,
modifiers and sampled points, not every keyframe RNA property.

`real_pole_repair_c3_v2_report.json` SHA256 is
`077eef69acf1f44c7e26fae13f3a6ec9d38d1d16030570afbb8910a56b11f0a6`.
Its isolated result, `real_pole_repair_c3_v2_result.blend`, SHA256 is
`3326b69065fc1cddb4e205f6c8d2107b3b314632f0f8048ddb27d50f92b95238`.
These results concern immutable saved copies; they are not artist application
or GUI history proof. Source and immutable inputs remained unchanged.

Candidate01 and candidate02 source, manifests and failed results remain
preserved. Candidate02's native comparison failures are not passes. Its first
real-model preflight also rejected using the entire older body pose as a
reference because current nonleg work had changed. The passed V2 proof uses
the current nonleg pose with an explicitly admitted eight-bone leg reference;
it preserves current nonleg work and reports its surface scope separately.
No full-body historical-surface restoration is claimed from that reference.

## Artist application and save

The artist session verified .59 and all 204 installed files before applying the
authorized repair. Both Poles are in front on their own side, with front
cosines approximately 1. The desired eight-bone maximum matrix error is
`1.93938613e-5`; nonleg native matrix error is zero. Protected authored hashes
and native data identities remain unchanged. This operation did not edit mesh
data or Rest, call Ground, or exercise Undo history.

`artist_pole_repair.json` passed, SHA256
`7a3c276764b214b136a13d53dd9430bc31a86d336c062f60c1677947d3c442ec`.
Its historical `applied-awaiting-artist-save` status is followed by
`artist_pole_save_receipt.json`, SHA256
`60417bb277b443295f3a68d29b676e49606de66334ebf6abd3ac9267b649c71d`.
The later receipt records Ctrl+S, Blender's native Saved X.blend message and
removal of the title's unsaved marker. The saved artist file and exact isolated
copy `artist_saved_after_poles.blend` are 34,549,786 bytes, SHA256
`cf0476f3657e580869ed05415264cea4db690d1daf3ba79640b35540c26a1f6c`.
The repair script SHA256 is
`ad29886e070ed0f08f8ad2204ab3285de506187c1c892989c9f48ef3243f390e`.
Independent V3 readback of this exact saved copy passed, using a fresh isolated
copy of the actual user profile with all 27 enabled modules registered and
module membership verified. It checked all 204 .59 source files before and
after loading the copy. All 24 strict checks passed: the 184 native bone
matrices exactly match the artist's repaired pose, with maximum error zero;
current nonleg error is zero, and the eight safe-reference leg matrices remain
within `1.93938613e-5`. Pole positions and periodic angles have zero error
against the saved artist result. Protected authored hashes, all 13 Action
fingerprints, raw meshes/keys/weights, Rest, custom styles and drivers match.
Source, input and isolated profile bytes remained unchanged. The reader did
not reapply repair, save, move the character or invoke history.

Final `artist_saved_poles_readback_v3.json` SHA256 is
`72f1bb22897d15446da0cc0775f38cbefce7b752c2658df9598734cfd46c20e5`;
reader SHA256 is
`bb557ed4ecf3ee618bd7696a2428d4a9deef6e9f7bd56fc4ac1c957aa0670b40`.
The earlier V1 reader remains failed: its factory/CD-only environment did not
match the actual 27-module RNA schema, failing two nontransform comparisons.
Its native pose/Rest/raw/Action checks passing did not make that run a pass.
V2 remains failed before opening the saved copy because a diagnostic accessed
`is_array` on a CollectionProperty. V3 corrects the diagnostic and uses the
matching user profile while retaining every strict authored-state check.

## Preferences are a separate repair

The factory-window preference overwrite and restoration are documented in the
X evidence README and [validation preference isolation](../trusted_blender_validation_preferences.md).
The recovered profile, launcher isolation and preference-restoration evidence
are not part of the .59 add-on payload. Their outcome does not accept Pole
changes, and the failed background full-keymap roundtrip remains failed.

Evidence directory:
`D:\Blender\Projects\Character\X\validation\pole_preferences_recovery_20261018`.
The owned V3 reader exited after verification. No Git commit, push or
publication is claimed.
