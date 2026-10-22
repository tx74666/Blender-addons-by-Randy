# Character Designer 0.77.85 — stable Move to Floor

The current X character refused Move to Floor because native Dress Spline IK
evaluation changed after a rigid world translation. The first failing component
was vertex 286 X; the full observed maximum was 8.5428357e-5, above its original
7.0014721e-5 shape limit. A no-motion graph rebuild had zero drift. This does not
establish an authoring error or justify raising the shape tolerance.

Grounding first performs the existing transaction. Only a shape rejection on an
authenticated generated Dress Spline IK dependency can try up to eight adjacent
float32 root-Z representations in each direction. Each attempt starts from the
original state and retains the original complete geometry, world-transform,
bone/pose, storage and floor-outline protections. A successful alternative must
survive another full rebuild and put the visible bottom within 1e-6 of world
Z=0. Failed recovery stops retries. Repeated grounding within that precision
does not oscillate the root. Manual vertical movement keeps its existing path.

No mesh, weights, Shape Keys, Rest bones or controller regeneration is part of
this fix. Foot/Master outlines use their existing live display fitting.

On the immutable latest unsaved artist capture (SHA256
`2ef8a9f7f561d2b496897079b74b9262ab750624fa62664da3e641cf55602bf6`),
four lower float32 steps changed the requested displacement by only
7.4505806e-9 world units. Dress maximum error was 4.4949353e-5 and the visible
bottom was 8.9406967e-8. Three rebuilds, repeated grounding and private save/reopen
passed with original author inputs preserved. This is an endpoint proof for the
observed character, not a general Spline IK solver replacement.

Validation includes 32 new pure checks, all 54 existing native ground/scope/
floor/orientation/storage checks, and four native fault-injection scenarios
covering all-candidate refusal, recovery failure, unrelated failure and second
rebuild rejection. The explicit injected failures are not naturally occurring
deformations.

The isolated GUI-process V3 proof also passed the real Ground operator
(`EXEC_DEFAULT`, undo enabled), native Python Undo and Redo. Each history step
reacquired current RNA by name and compared normalized author content, object
channels, custom displays and native mesh outputs under the original limits.
Undo restored the initial root Z exactly; Redo restored the accepted root Z and
all three floor outlines, with bottom 8.9406967e-8. This is native graphical
operator/history coverage, not physical keyboard testing or full artist-session
add-on integration. The earlier background V1 crashed in native HUD creation
while finishing an operator; its exact calling stage is unknown, so it does not
prove Undo itself failed. GUI V2 stopped safely on a timer-context selection
lookup after private file loading, before any Ground/history call. Both failures
and their logs remain unchanged; V3 is a separately identified success.

The approved release is the complete accepted .84 package plus only
`character_ground.py`, initializer version metadata and package README changes.
It contains 214 files, with 182 Python sources syntax checked. Official ZIP SHA256:
`33217f98c76194f965fc8e1b0a4fd01a1b9c1899d29cabc0f3d62b825bb1c23f`.
Official deployment and checks passed for Blender 5.2 and X's validation copy;
all six replaced files have verified .84 backups. Unrelated canonical work is
excluded. Changes are local; no Git staging, commit or push was performed.

Artist runtime adoption is accepted separately in
`profiles/artist_runtime85_v2.json`: the .84 to .85 update retained all 15 complete
protected components, handlers and registered classes exactly. It performed no
full add-on reload, simulation start, manual frame/graph update or scene save.
The initial runtime V1's context-guard refusal occurred before any update and is
retained.

Actual current-artist placement, save and cleanup acceptance is still pending
the final
`D:/Blender/Projects/Character/X/validation/ground_dress_fix_20261021/acceptance_summary.json`.
Runtime adoption and isolated saved-copy tests do not substitute for that final
scene receipt. Raw reports and reproducible scripts remain in that directory.
Actual AI model and reasoning effort were not recorded. This functional fix
makes no FPS claim.

Investigate again for a newly rejected character, changed Dress dependencies,
nonvertical parent transforms, actual deformation, floor-outline or history
failure. A refusal remains the safe result when no bounded candidate satisfies
the existing guards.


Current-artist acceptance is complete. The real operator ran once; source .85
and the whole visible character were verified, normally saved, then checked
after the event loop with dirty=false. Final X SHA256 is
`1112a894b706dbc05d3fc41aa9bc0f5f6cef28d1ad96f413ad92367902b664c7`
(34,442,990 bytes). Root Z is 0.030086785554885864 and lowest visible world Z
8.940696716308594e-8. The three floor displays pass; original authored geometry,
weights, all Keys, Rest and Actions remain protected. Current scene save was not
reopened; private save/reopen and graphical native Undo/Redo are separate proofs.

An extra outer author guard first refused to save because unscened `Cosha.001`
has one parent-derived `matrix_local[2][3]` getter change. Replacing only that
primitive in all 626 live Object rows reconstructs the exact original hash,
retaining all native pointers/properties/order. Its basis/channels/world cache
are unchanged; Blender computes the getter from inverse(parent world) times
cached world. No normalization or native limit was widened. The failed receipt
is preserved, and separate save-only/postsave-v2 full16 proofs passed without
repeating Ground, Undo or author repair. See the X README and single-leaf proof.

The private SDK closed normally; listener/client/thread/module and kernel TCP
inventory checks passed. A supplemental connect probe timed out, so connection
refusal was not inferred. Only the artist Blender remained. No preference,
Unity, Git staging/commit/push or FPS measurement was performed.
