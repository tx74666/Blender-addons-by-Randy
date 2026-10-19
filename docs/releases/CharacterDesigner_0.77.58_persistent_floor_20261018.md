# Character Designer 0.77.58 · Persistent floor placement

2026-10-18. **Validated and deployed locally.** Frozen candidate03 passes 54
native tests, the live-like X placement/save/reopen test, and independent
readback of the artist's saved result. Blender 5.2 and X deployment checks
both report zero differences. Changes remain local; no commit or push was made.

Move to Floor is the single placement button in Rig > Body, separate from
Rebuild Controls; manual distance/move controls and extra help were removed
in .57. It moves the scoped character together and fits the planar Foot/Master
displays to Z=0. Rebuild does not invoke it automatically. Scene-less orphan
branches remain excluded, while cross-scene and bridging hierarchies remain
refused.
The added correction addresses a saved-file problem: a child mesh with a
constant Object Action can lose an unkeyed location adjustment when Blender
reevaluates that Action on reopen. In the preserved .57 artist readback,
Shoes reached approximately `-0.0670112` world Z even though the live operation
had placed the character on the floor. The .57 native suite and isolated saved
baseline passed; that did not establish persistence for this artist override.
The failed readback remains failed and preserved. Raw saved Object data retains
Shoes Z `0.24263450503349304`, while trusted native loading evaluates its Action
to `0.1756228804588318`. The `0.06701162457466125` reset accounts for the observed
sink within `4.10e-7`. Earlier fresh-load validation began after that sampling
and therefore missed the live unkeyed override.

For a scoped, nonroot mesh with one admitted constant single-key location
Action, .58 stores the keyed location and moves the equivalent unkeyed
adjustment into `delta_location`. The two channels together retain the current
local and world transform before the character moves. It does not delete or
rekey animation, change Action bindings, create a parent, or edit mesh data,
weights, original bones or local pose inputs. Root objects are not normalized.
Only Move to Floor uses this storage correction; existing manual move APIs
retain their behavior.

Admission is conservative: ambiguous Action slots/layers, NLA or blended
animation, multiple location keys, location modifiers or samples, animated
delta location, transform drivers/constraints, another unkeyed animated
transform, and property drivers reading the changed storage channels are
refused. The constant Action and all protected inputs are checked after the
correction. Storage correction, root movement and display fitting share the
placement recovery boundary; a later failure must restore both original
location fields as well as roots and displays.

The frozen source is `validation/move_to_floor_20261018/candidate03`,
based on .57 ZIP SHA256
`f2a9897b226ce3c9baecb493fb2e75b277659655f2df412c2be7e0e7aa296f3b`.
Its allowlist is initializer version only, Ground's `_translate` and `ground`,
and new `placement_offsets.py`. The package must contain 204 files; the other
201 accepted .57 files, including the existing floor display service, limb
builder and UI, must retain identical bytes. Paused .48 watcher/provenance/
display optimizations remain excluded. The new `prepare_release_v3.py` leaves
both .57 candidates and their projectors unchanged. Candidate03's manifest
SHA256 is
`a680789cea6fa151616a0ee4cb2f8de88b970dc5cd925b87eee2811de1b02021`;
all 204 frozen file hashes have been read back successfully. The projector
SHA256 is
`35ec2494f73211558e15b03b8bfb1657700b6d9b541f0afd48813c842bb77d84`.

`native_v3_report.json` records all 54 checks passing: 10 focused storage cases,
24 existing placement cases, five scope cases and 15 floor-widget/orientation
cases. Seventeen pure UI checks are reused from .57: the .58 draw function's
AST is unchanged.

The isolated real-model V3 probe recreates the pinned raw unkeyed shoe location
before its baseline, rather than testing only the already sampled Action value.
`saved_floor_v3b_report.json` passes Ground, repeated Ground and save/reopen,
with all 19 mandatory authored-state and bounded-placement checks at each
stage. Its 18 scoped objects are CoshaRig, six meshes and 11 Dress curves;
CoshaRig is the sole translated root. The repeated-operation displacement is
`-2.253800630569458e-7`; the reopened lowest point is `-9.685754776000977e-8`.
The three saved planar Foot/Master displays independently meet the `1e-5`
world-floor tolerance. The first probe invocation's incorrect source-folder
argument remains recorded in `saved_floor_v3_report.json`.

The declared integration policy is
`AUTHORED_INPUTS_BOUNDED_PLACEMENT_FLOOR_TRANSFORMS_AND_DECLARED_STORAGE_V3`.
It permits only the named Shoes location/delta storage correction and authorized
Foot/Master display fitting while retaining the other authored-state and bounded
world/surface checks. Thirteen Actions retain the declared eight-key-field
fingerprints and supplemental metadata; this is not a full per-key RNA audit.
Evaluated IK outputs remain diagnostic, and neither exact IK output nor FPS
gains are claimed. Only the validated planar displays receive the floor-plane
guarantee; arbitrary nonplanar custom shapes are not flattened.

`deployment_check_v3.json` records the 204-file ZIP SHA256
`975658c6ae46760716493ac631febb68abef4496a0c21d8cb3dd5dcf77147d4e`,
zero differing files at both destinations, and the predeployment backup.
The artist UI completed Refresh, showed the single Move to Floor button,
accepted one placement click, and reported `Saved X.blend` after Ctrl+S with
the dirty title cleared. Rebuild was not invoked. The saved copy is
34,548,016 bytes, SHA256
`281c8c582898b28dda869adad83785c78ddb5b52f4359304fa935022883ce891`.
`artist_saved_readback_v3b.json` independently passes without calling Ground,
changing frames, saving or opening the artist file: the shoe and character
lowest point is `-9.685754776000977e-8`, all three floor displays pass,
normalized storage requires no further correction, and the protected raw
geometry, Rest, local Pose and 13 Actions match the live-like baseline.
The first artist reader's missing pin `source` field remains recorded as a
harness failure; its corrected pinned-copy retry passed.

[Final acceptance and evidence index](D:/Blender/Projects/Character/X/validation/move_to_floor_20261018/acceptance_summary.json)
links the source, delivery, artist-save and independent-readback receipts.
No GUI Undo/Redo or new playback/FPS measurement is claimed. The actual AI
model and reasoning effort were not recorded. No Git publication was performed.

Preserved .57 evidence includes `native_v2_report.json`,
`saved_floor_v2_report.json`, `artist_saved_readback_v2.json` and
`artist_saved_pin.json` under
`D:\Blender\Projects\Character\X\validation\move_to_floor_20261018`.
The failed artist readback SHA256 is
`e015e0c10cf2d3a471d93f307be0355df7ffdde383f0f3332b33d39254d95e4b`.
The earlier candidate01 planar-display failure is also retained unchanged.
