# Character Designer 0.77.189 — Weight Original / Controls

**Completed 2026-11-11: live_UI_status = passed.** Source, isolated validation,
packaging, dual-disk deployment, the fresh artist code audit and the current-owner
artist Original → Controls → Original → Finish observations are complete.
Finish restored the current session's frame19, raw pose and Auto Key state while
keeping its declared weights/authors and all Action curve hashes. The Graph
Editor was restored and input released. The evidence limits below remain part
of this acceptance; the formal Rig-page Original history issue remains deferred.

Source/runtime: complete approved188 plus the explicit189 increment; artist
Blender5.2, PID51688, current X.blend. Model/effort: unrecorded.

On the Weight page, **Original** starts painting or resumes the current painting
session. **Controls** temporarily poses the same current rig so the user can
inspect deformation, then Original returns to painting the existing Deform
group. A single **Finish Painting** ends the session. The page identifies the
painting group and whether the current stage is Painting or Pose preview.
Repeated stage requests preserve the session, and a manual mode change can
return through these same buttons. The four existing painting operator IDs
remain available for compatibility; no new RNA class is added by189.

Finish retains edited weights, restores the entry frame and authored raw pose,
and shows the verified current controls/FK bones. It selects only verified
participants or the suitable mapped painting target. If no local posing target
can be verified, Finish still ends safely, restores the previous display and
reports that no verified local controls were found. A successful Finish shows
Controls as the completed display where that display is available. Other display
presets cannot overwrite an in-progress paint/preview session, including calls
through operator search. Native collection-eye controls remain available.

These Weight-page buttons do not enter the historical formal Original workflow.
Formal Original outside Weight retains the strict existing capability, history
and recovery checks and refuses while the same rig belongs to a paint session,
including use through another Scene/ViewLayer. A different rig is not blocked.
Missing Main Rig recovery references are not recreated and Restore Main Rig
permission is not granted. An existing formal Original session must complete
through its normal Rig-page return. Native painting without this RAM session
does not acquire invented recovery authority; its Controls route refuses until
the user explicitly starts the supported painting checkpoint or ends native
painting. Paint recovery remains RAM-only; Finish before closing/reopening.

The complete release extends approved188: 270 files /238 Python files, five
changed files and265 exact inherited files. Changes are limited to
`weight_paint_entry.py`, `body_original_mode.py`, `bone_display.py`, `__init__.py`
and `README.md`. The pose-session helper and all Forearm188 implementation files
are byte-exact188. This is not a Forearm algorithm, global protocol, Main Rig
recovery or playback-performance update.

Projection:
`D:/Blender/Projects/Character/X/validation/weight_original_controls_20261111/stage189_01/projection.json`
SHA256 `481dac4d4a1ef35fec62a42e3abd5f7709c942d86c71d000e0917c510610446b`.
Baseline188 projection SHA256
`a6d6b8608e3e425e34ca68a955981640a016aaf065b551359e8a2f5639ac2923`.

Verification completed:

- **50 focused pure/mock tests** passed for session guards, stage routing,
  cross-Scene ownership, native/legacy boundaries, Finish display/status and
  preset bypass prevention.
- **Six generated native groups** passed: real FK and local IK deformation,
  repeated Original/Controls operator requests, Finish from painting/preview,
  retained edited weights and group identity, managed IK with missing historical
  Main Rig references, unresolved-control refusal/safe Finish, injected Finish
  display failure with rollback/retry, and changed Armature-binding refusal.
  The strict missing-reference error remains present before and after the Weight
  test. These are isolated scenes and native operator/layout-recorder checks,
  not physical artist GUI or full formal Original-entry success.
- The definition-only **188→189 updater passed13 native cases**, including all
  **26 fault boundaries** (25 writes plus the final postcheck), success,
  explicit rollback and ALREADY. It preserves the real active painting-session
  object and recursive recovery-container shape. It adds11 plain functions,
  updates11 existing function code objects, installs only the explicit new
  `_FINISH_STATUS=None` binding and changes the existing version entry. Existing
  function/default/descriptor/class/import/property/handler/signature owners
  remain. Both exact warm-helper and cold-both-slots-absent states pass; partial
  or foreign helper owners and code/import/class/tuple races refuse without
  repair. The cold helper is not imported or resurrected by the updater.
  Guarded execution observed zero updater Scene/context/operator access and zero
  RNA registration/unregistration. Owned test RNA was removed during cleanup.

Official ZIP:
`D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.189.zip`,
SHA256 `08f9e6ed354b6ab874fbfcea83fa352c0dd291ad377685e952e78aa0198dc7f9`.
CRC and all270 payload hashes match. Official build, deployment and matching
`--check` passed for Roaming Blender5.2 and X/addons:270 files /0 differences
at each target. Disk deployment itself does not establish a live refresh.

Artist51688 /Blender5.2 /X.blend was freshly audited **ALREADY189**, with the
three target modules matching the pinned source and a cold unused pose helper.
The transaction performed zero code/version/RNA/Scene writes; its separate
before/after authored witness matched exactly. An earlier fresh188 observation
was separated by a user-input boundary, so refresh timing and initiator remain
unknown and no identity continuity across that boundary is claimed. This is a
fresh local code audit, not whole-runtime protocol or all-attribute certification.

The earlier artist smoke receipt `artist_smoke189_01.json` remains incomplete.
A fresh continuation found its saved Scene, rig Object and Armature native
references removed; `artist_stale189_02.json` records
`STALE_NATIVE_OWNER_REFUSED`. The time, cause and initiator are unknown. The old
frame28 baseline was not restored onto current identities, and no continuity
across that loss is claimed. The original partial receipt and stale-reference
proof remain retained without relabeling.

A separate current-owner baseline was captured in `artist_smoke189_03.json`
using the ordinary physical Weight-page Original route. Its helper was already
`WARM_EXACT`; this new round did not perform a cold-to-warm helper import. The
four recorded observations are paint, preview, resume and finished, all at
frame19. Paint and resume used mesh Weight Paint /rig Pose with the same
`thigh.L` group and93 groups; preview used the current rig in Pose. Finish
cleared the RAM painting session, restored the baseline frame/subframe, raw pose
and Auto Key, kept the active painting group, and verified the available current
Controls/FK display. The declared author and weight witness and all Action curve
hashes matched throughout these observations. Final raw and cached pose hashes
both equal the new baseline.

Before Finish, a new read-only observation `artist_prefinish189_04.json` found
the same native Scene/Mesh/rig owners still valid with the binding/pose guard
passing, unchanged declared authors and all Action curve hashes, but at frame20
in `POSE_PREVIEW` with a different raw-pose hash. The intervening frame/stage/pose
change's time, cause and initiator are unknown. The existing valid session was
kept without restarting, recapturing or replacing its baseline. The physical
Finish then returned it to the verified frame19 baseline. This fresh same-owner
proof does not bridge the earlier stale identities.

The live receipt has `optional_nudge=null`, zero brush strokes and zero key
insertions. This artist round proves the observed routes, existing-weight
preservation and the observed frame20-to-frame19 Finish restoration; it does not
claim an agent-performed preview nudge, a new artist brush edit, or an artist
FK/IK deformation measurement. Actual edited-weight retention and FK/IK mesh
deformation are established by the separate isolated native fixtures above.

The authored witness covers declared mesh coordinates/edges/polygon lengths,
weights/groups, Shape Keys, palettes, Rest, retained opaque strings, raw pose and
all Action curve hashes. Only existing Forearm derived-output key coordinates,
value and mute fields authenticated by both record and primitive lifetime token
are exempted from equality. Cached pose evaluation is reported separately. This
is not all geometry attributes, heap/global lifecycle certification or an
all-ShapeKeys-zero-write claim.

Final native return `artist_return189_01.json` passed: Pose /CoshaRig /frame19 /
subframe0 /AutoKeyOff /dirtyTrue, CoshaRig+Cosha selected, painting session
cleared. The Graph Editor was restored with the native context unchanged across
that editor return. Physical UI showed the completed Controls display and Finish
Painting disabled; input was released. Finish deliberately leaves the selected
active rig in Pose with verified posing targets and filtered bone selection,
while retaining prior selected objects. It does not restore the exact entry
active-object/mode/display/bone-selection context.

Frozen evidence (paths relative to this validation directory):

| Evidence | SHA256 |
| --- | --- |
| `pure189_01.json` | `a0c2ebeace1e7fb5b46ff9d94ae613423ed4860989456c9d059eaad7850ae64d` |
| `native189_01.json` | `4a733790cf3fa24fe790073b797f281ab0689202b2cacf00c965812fb28f6c9d` |
| `updater189_native02/result.json` | `d5ff363ed15761bb2dae654feb6fb98c4bb57b7bee0e4e8274585f6ace83dede` |
| `scoped_weight_routes189_01.py` | `1ccdba5db74d9cd9ea8e00a22132cd28fe9e065762995a45c3c01c49b09e932b` |
| `disk189_01.json` | `720b032aafbe3adff51e6f305fdfceae2335069cd479095269ed5d73ec39f4e9` |
| `artist189_01.json` | `502bebcc7d28e77544ce71b388153234318eff166d36d811f83803877a6c04ed` |
| `artist_smoke189_03.json` | `e6e4570921e45cf6e287530ef8c07ccc17bf3fa2f158e240cb661cf21db34757` |
| `artist_prefinish189_04.json` | `e021afc74a8d6369968a291075b860353eb88320d60d80fe20da1046e39beb55` |
| `artist_return189_01.json` | `7ce00212254c14689bc91443306dd10584cff584d34fb06f4c91514bdd866b93` |
| `artist_smoke189_03_before_finish04.json` | `01453e4aa1f657729fec1a0570ca99305c23769f6fb1b192cd335e0e45563221` |
| `artist_smoke189_03.py` | `aa987719bbbee597db80dd8cf40f8cbd59bbe6ee8176eeefba1050ce06b5de07` |
| `artist_smoke189_01.json` (incomplete, stale owner) | `65e58bc96a1ec46766415fe9b3148710b5f35c5751438f9ed2668421654fd784` |
| `artist_stale189_02.json` | `8adc20bda7cb7ad044a04585797aafe114f450c7c65c017a7e8570ba85e380d8` |
| `release189_draft_before_finish04.md` (original bytes) | `270e86895631d84a534548f6440223741dfac5431d4437ea5e90f57e9cb61d99` |

The first updater proof failed because its harness caught only the updater's
Refusal class and did not recognize the immutable runpy reader's separate
Refusal class during an intentional foreign-code test. Its original harness,
stdout/stderr and failed result remain retained:
`updater189_native01/result.json`, SHA256
`7fd1e5eb82c7fd6236a629ff86bce4ec6b94c4939cb89e628c4d3e2277ceee11`.
The derivative harness accurately recognizes that refusal without changing the
frozen updater or weakening its owner checks. The later pass does not relabel
the first failure or fill inherited historical acceptance gaps.

No agent scene/preferences save, restart/full package reload, Main Rig reference
repair, rig rebuild, reweighting, bake/export, Unity or Git operation was performed
for this delivery. This is a local release and deployment, without commit/push.
Existing Forearm188/painting186 guarantees and their recorded limits remain in
scope. The broad artist protocol alias split and formal Rig-page Original
historical-reference issue are not repaired; Restore Main Rig remains deferred.
No playback-FPS measurement or causal/target claim was added, and that closed
investigation remains closed.

The frozen projection retains its original candidate metadata and SHA256;
completion is recorded in this release note and the separate acceptance receipt,
not by rewriting the projection. The previous draft is preserved byte-for-byte
as `release189_draft_before_finish04.md`. Historical test-reader/mock failures
and all original evidence remain retained; later passes do not fill their gaps.
