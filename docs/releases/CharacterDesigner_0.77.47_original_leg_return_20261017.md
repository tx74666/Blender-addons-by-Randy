# Character Designer 0.77.47: return edited Original legs to existing Controls

After editing leg proportions in Original, Controls previously refused changed
native feet/toes and the retained Worklist Action reference to the artist data.
The return now admits the verified native leg/foot/toe geometry, retains all
existing control identities and helper Rest, and matches the current visible
pose without deleting bones or rebinding weights. Ordinary structural and
external-dependency refusals remain in place.

Geometry-only limb edits no longer rematch unrelated arms because of native
bake rounding. This avoids moving an untouched elbow Pole along its bend ray.
An Original session with actual pose changes outside the edited limb still
matches that current pose. This can change control channels from the historical
pre-Original values; it does not restore that earlier pose over the artist's
current work. Current native pose and evaluated surfaces are verified.

## Ownership and rollback

Known local animation recovery references require a unique actual rig binding,
the saved Original geometry, the complete authenticated older artist Rest and
unchanged separate motion data. Later edits use the accepted model revision.
Historical Actions and motion baselines are never rewritten; clips whose old
Rest differs remain stale rather than silently retargeted.

Only verified FK_TOE_REF and AUTO_TOE_REF roles have a native Edit Mode rounding
comparison. Both old and current geometry must independently match the immutable
Foot footprint's canonical head/tail Y and perpendicular Z frame within 1e-6.
Each redundant X-column residue is bounded by 2e-5. Parents, flags, head/tail and
translation retain the ordinary checks; all other roles retain the complete
matrix comparison. This is not a global or pairwise matrix tolerance increase,
and no helper, Foot record or historical Action is altered to satisfy it.

The ownership revision is committed inside Original's existing rollback scope.
Failure restores the edited Original session and exact prior ownership property.
Bone attachments, Hook/Warp targets, authored constraints and object/data-bone
animation or external drivers in the affected region are refused before writes.

## Limits and evidence

This returns the current pose to the existing controls. It does not regenerate
the reverse-foot pivot spacing or accept a full gait/contact cycle. Changed
heel/ball/tip anatomy can require the explicit regional release/rebuild workflow.

Native checks use Blender 5.2.0 LTS, build fbe6228777e7. Project evidence and
disposable runners are under
`D:\Blender\Projects\Character\X\validation\leg_edit_review_20261017`.
The current artist input is pinned by SHA256
`de83f5e7fd57a22bd0d119ec75ab2bdef66485fae20d9954bd1d552c5ca1ec19`.
The accepted summary records final checks, real-model limits, source/deployment
hashes and the live artist refresh/save outcome. Failed intermediate attempts
are retained with their diagnostic reports and do not count as accepted runs.

Changes are local. No Git commit, push or public release is performed.
