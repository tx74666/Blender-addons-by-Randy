# Character Designer 0.61.46 — Finger visibility, topology edits and subdivision

The Capture Detection eye is the master for reference, bend and joint overlays.
The lower joint eye remains an independent child setting. Master-off cancels
unnecessary display work without changing that setting; master-on restores only
the enabled children.

A global geometry notification no longer marks every digit invalid. References
can survive complete loop subdivision or a surface-preserving loop dissolve,
including index changes on other fingers and unequal mirrored loop counts.
The proof retains spatial endpoints, bend direction and the accepted internal
path. Geometry-changing dissolves, broken boundaries and ambiguous references
remain local failures rather than silently fitting a different finger.

The workflow adopts the edited current mesh as its immutable source, retaining
all current artist data. Surviving managed rings retain exact identities and
phase; missing managed rings reset only the affected pair. Other finished fingers
are kept as already-authored geometry and weights. Subsequent updates recognize
only their own previously generated supports; they do not steal nearby authored
rows or accumulate duplicate supports.

Viewport Catmull-Clark levels 1 and 2 use evaluated longitudinal loop positions.
Preview shows the evaluated contour, and write-time verification checks the
complete staged mesh. Adding supports can adjust the same central control row's
compensation while preserving the evaluated center and bone junction. Warm
dragging reuses cached Blender subdivision responses. The target is the loop's
longitudinal centroid, not a claim that every vertex of an irregular loop lies
on one plane. Support insertion remains optional and its width is adjustable.

Unconfirmed centers start at legal old-row cage positions and try bounded
nearby ordered assignments when the nearest cage row cannot reach the final
surface target. The successful mapping and subdivision response are cached;
confirmed center identities are never silently exchanged.

Supported evaluation currently requires one enabled Catmull-Clark modifier,
with locally neutral armature deformation and relative Shape Keys.
Remote nonzero forearm correctives remain enabled. The guard checks the finger
and a conservative subdivision neighbourhood, including relative-key chains,
weighted deform bones and vertex-group masks, and repeats that check on the
staged candidate. Ordinary neutral-rig float32 drift is tolerated; unrelated
posed bones are allowed. Nonzero
creases, unsupported active modifier stacks and other subdivision levels are
explicitly refused. No modifier is disabled or applied automatically.

## Validation and local deployment

Serial Blender 5.2 validation completed 139 cases across 15 targeted suites:
master visibility, modifier invalidation, topology adaptation, workflow rebasing,
subdivision levels 1/2, two-stage confirmation, source preservation, bone sync,
reference detection and idle monitoring. Real native Undo/Redo and dependency
events were covered; unchanged warm drags performed no subdivision evaluation.
The final subdivision suite contains 21 cases, including local/remote Shape
Keys and armature influences, deleted modifier masks, and feasible alternative
center selection on uneven rows. Unchanged warm alternative drags perform no
subdivision evaluation. All targeted suites passed.

The existing X session was reloaded to 0.61.46. Its user-added Index loop was
adopted, all ten reference slots have empty errors, and asymmetric 8/7 Index
ring counts are accepted. The left/right Index level-1 previews are evaluated
and bone preflight passes. Actual master-eye and child-eye clicks verified the
requested visibility relationship. The live mesh remains 3,424 vertices with
10 Shape Keys; mesh identity, full data fingerprint, key values and rig data
remain unchanged. No blend file was saved and no generation step was applied.

Live limitations were retained explicitly: Middle/Ring/Pinky have true UVMap
discontinuities across their old joint rows (roughly 0.10–0.12 UV units); Thumb
has a corner-color boundary and its old captured root range excludes part of
the named chain. Pinky's old bone signature differs from current rest joints
by about 0.00127. These are local generation/rebinding issues, not global
topology invalidation. Re-Prepare changed Index's bone-depth path endpoints,
so its previous Step 1 confirmation must be renewed before Step 2. None of
these guards was bypassed or silently presented as a usable final plan.

The synthetic default support width of 0.025 cannot always hit the final surface
without crossing cage rows. Both levels correctly refused that geometry, kept
the transaction unchanged, and recovered with width 0.07. Width feasibility
depends on the existing rails; unsupported widths are not silently forced.

Changes are local; no commit or GitHub push is authorized by this release.

Built `dist/character_designer-0.61.46.zip` (108 files). Both Blender 5.2's
installed add-on and X's validation copy match the canonical source exactly;
the final `deploy_local.py --check` reported zero differing files at each.
