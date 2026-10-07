# Dress automatic motion

This is the **unreleased Dress work**, not the installed artist workflow. Cloth
has been selected for Dress instead of the earlier proposed Wiggle automation;
Wiggle remains the Hair backend. Daily Dress Automatic is intended to require
only character motion, with no prerequisite Dress keyframes. Final-surface
collision, same-frame editing and artist integration are not yet accepted.
Formal X has received separate Pose `0.77.2` work. The frozen Dress measurements
predate that deployment and subsequent Artist edits; Direct has not been
installed, refreshed or saved in the artist scene.

The unreleased source routes **Setup Dress / Physics + Manual** to
`DIRECT_MAIN_CLOTH_V1` (Direct). Select the actual Body mesh in Character Setup
first. Source tests and a private native public-operator check cover this route;
artist installation remains pending. Existing Delta records retain
their backend and are not silently migrated.

The earlier Delta setup reuses the
character armature and Dress mesh, retains the fitted wire controls, and adds
a connected native Cloth surface with a fixed waist and closed pelvis/leg
collision proxies. Original skin weights and Shape Keys are preserved after
the existing setup has been built. Internal manual, physics and final DEF
chains share that setup; changing motion modes does not rebuild them.

For Delta, **Automatic** enables the actual-surface Cloth displacement layer.
**Manual** disables that layer and retains the wire controls, their animation, and local
Dress pose corrections. Automatic mode also retains those corrections and the
manual baseline for optional refinement. The Physics generation choice enables the
automatic workflow; Manual creates no Cloth surface; Physics + Manual allows
both motion modes. Internal fit controls remain owned data in every choice.

Direct **Manual** displays the pre-Cloth input: the same Main Rig's manual DEF
skinning followed by the raw waist's Body attachment. **Automatic** displays
native Cloth's absolute positions. The old bone-physics influence stays at zero
in both modes; Cloth output is never copied back into the input bones. Direct
currently refuses Dress Shape Keys before installation and preserves them on
rejection. This does not prevent live Shape Keys on the separate Body input.

## Daily workflow

The intended Direct workflow is:

1. Set the actual Body mesh in Character Setup, select Dress, and use
   **Setup Dress**. Existing setup geometry is
   reused; it is not silently replaced after topology or ownership changes.
2. Use **Automatic**, then **Reset** and play from the simulation start frame.
   Advance Cloth sequentially; no Dress keyframes are required. Work on the
   character animation; leg motion affects the skirt through
   collision rather than binding the whole surface to a thigh.
3. Use **Tuning** for bend, damping, shape recovery, clearance and quality.
   Advanced holds material, waist goal, gravity and self-collision settings.
   Only changed fields are applied. Batch tuning explicitly selects all
   registered physics Dresses on the same Main Rig.
4. Use **Bake** for stable seeking/playback. Reset a sealed cache before
   changing physical material settings, then replay or bake again.
5. Use **Manual** for wire handles or **Original** for local bone corrections.
   Finish the correction, use **Reset**, then explicitly choose **Automatic**
   and replay or rebake. Reset keeps the current mode. Corrections feed the
   next collision solve; they are not added to a previously baked surface.
   Inspect the final visible skirt after replay.

In Delta, those refinement channels are preserved, but their current
displacement is added **after** Cloth's collision solve. A safe simulated surface therefore does
not prove that the final manually refined skirt is safe. Do not treat a sealed
cache as collision validation of later Manual, Original or Shape Key edits.

Direct Original edits the manual input bones. Returning to Controls preserves
the authored correction. An Original editing session marks the Cloth output
pending; returning to Automatic is refused until an explicit Reset and time
advancement. This input-edit preview does not prove that the simulated final
skirt is unchanged or collision-safe. Direct collider refitting and same-frame
recalculation are unsupported.

Direct Manual is an input preview, including after a simulation has been baked.
Entering Manual retains the native cache and marks the preview pending, even
when it is not baked or no edit follows. Returning to Automatic requires Reset and replay
or rebake; input edits are not added to the old baked surface. Direct Bake now
refuses Manual, Original editing, pending or disabled-Cloth states before any
cache clearing or frame seeking. Source tests cover the new unbaked guard;
its native check remains pending. The earlier sealed-cache native component
observed that clean Manual switching changed only native
`is_outdated` from false to true; the other thirteen seal/configuration fields
remained exact. A private flag-write/read/restore window observed the same 800
cached Cloth positions at frame 10. Explicit Reset and a second real 1–10-frame
Bake incorporated a 1.004 mm manual-input change into the final surface, whose
maximum change was 1.650 mm. Rejection and observation steps retained their
complete before/after state guards. Both bakes, author/source/Artist guards and
private disposal passed. This is a fixed-pose component check, not full cache
payload or final collision/naturalness acceptance.

The earlier sealed-cache check is retained as a diagnostic failure: its private
disabled-Cloth probe had changed `is_outdated` before the Manual comparison.
The successor isolated that probe at the end and explicitly recorded native
dirty status. It does not relabel the old failed report as successful.

Native Cloth advances through time. Moving a leg or editing a pose at the same
frame does not perform a fresh static solve; its starting frame initializes the
cloth rather than solving arbitrary existing intersections. Same-frame editing
requires replay and inspection of the final surface; no automatic same-frame
solver is implemented. Earlier experimental contact previews fail the raised-leg
plus Manual refinement case. Increasing quality or changing collision normals alone has not
made that workflow acceptable.

The tuning dialog reads current native settings without changing them on
opening or cancellation. Saved configuration belongs to the Dress source,
with a version and setup identity, and survives reopening the `.blend`.
Settings from a different owner or changed chain structure are rejected.

## Native parameter meanings

- Stretch resistance changes Cloth tension and compression together. Shear
  and bend are separate native springs on the connected circumferential and
  vertical surface.
- Damping changes native tension/compression/shear damping together. Bend
  damping and air damping are independent. Material fields that were not
  changed retain the artist's native values.
- Shape recovery adds weak, depth-decaying waist-relative goals to the Cloth
  pin group. It does not modify thigh following, Rest bones or skin weights.
  Zero recovery/waist depth retains the earlier fixed-waist pin pattern.
- Fixed waist depth expands the fully fixed rings; waist transition controls
  the first soft ring. The moving hem is never fully fixed by recovery.
- Body/self clearance is a fraction of fitted Dress height. Native RNA bounds
  are checked at the actual model scale; an unrepresentable value is rejected
  before writing rather than silently clamped and saved incorrectly.
- Gravity multiplies scene gravity. Motion inertia comes from the native
  moving Cloth surface and its mass/damping; there is no separate artificial
  inertia slider or claim that these are Magica parameter equivalents.

The Delta backend simulates the source's exact raw vertex
indices. Its final output adds the simulated displacement to the current
manually posed Dress, subtracting an independently evaluated neutral surface
to avoid applying the same motion twice. Existing asymmetric Shape Key and
manual deformation deltas remain in the final output. A separate tracker still
drives the existing PHYS/DEF chains; it does not substitute its coarse surface
for the final vertex result. Collision acceptance must inspect the final Dress.

Direct also uses the source's exact raw indices. Its pipeline retains the
original groups, weights and UVs, evaluates Main manual DEF skinning, attaches
the raw waist to the live Body with a fresh Surface Deform bind, and supplies
that input to dynamic-rest Cloth. An owned absolute-position node graph writes
the selected Manual input or Automatic Cloth result before the original
Subsurf. Direct has no neutral-surface subtraction or new Cloth-to-PHYS tracker
feedback. Its current cold component uses the unchanged default gravity
multiplier of 1; earlier private previews used other parameters and are not
validation of these defaults.

The earlier bone-driven Cloth cage remains readable for explicit migration.
The unreleased panel's physics setup now requests Direct; it refuses
to discard a sealed legacy cache or rebuild edited geometry implicitly. This
backend has not yet been installed in the artist scene. Direct is an
explicit installation from the supported legacy setup, not an implicit Delta
migration. It refuses sealed, stored or unsafe nonempty legacy cache data rather
than discarding that data to install. Neither backend is accepted as the new
artist workflow.

## Export boundary

Simulation Bake saves Blender's native Cloth cache. A bone-only animation copy
cannot preserve either surface backend's vertex displacement and is refused.
Delta static Unity export validates the owned setup and excludes its internal
simulation helpers; it does not export a baked Cloth animation. Unity's separate
Magica simulation must be validated against the actual imported Dress. Blender
and Magica settings or results are not treated as interchangeable.

Direct static model and skeletal Action export are currently blocked in both
the host and private worker. Private capture, validation and stripping hooks
have source-level tests, but no native animation export roundtrip has passed and the public
route remains disabled. No Direct automatic vertex physics is
published as bone animation. A future manual-only export must also declare that
plain skinning omits the native Body waist attachment; preserving bone channels
alone cannot guarantee the same Direct Manual final surface.

The unregistered `PLAIN_NATIVE_SKIN_V1` model adapter now captures and validates
Manual inputs with either typed pending state. It explicitly omits Cloth and
Body attachment, reports final-surface equivalence false, and does not authorize
animation, FBX or Unity publication. The private PNSv4 model transport/strip
component passed within the boundaries below. Private FBXv2 hit its 240-second
hard timeout before writing or reimporting an FBX; its owned process was gone.
Private FBXv3 completed but failed its current Manual surface gate. The later
REST model component wrote FBX; its diagnostic isolated edge-number reordering
and omitted zero-weight entries. The revised fixed-input REST roundtrip
component passed. The first private Manual Action attempt stopped on reopening
its temporary snapshot: its all-file Mesh inventory included an unreferenced
WGT Object outside the actual library-write roots. FBX execution had not started;
loaded-input author restoration, source pins and owned/factory cleanup passed.
The [Manual Action V2](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/verify_private_manual_action_fbx_component52_v2.py)
now fixes its snapshot protection scope from native ID references before writing;
the full loaded-input author checks remain intact. This V2 and the private normal
model-worker root/Rest check are source-prepared, not native acceptance. Manual
animation, paired production binding and Unity integration remain pending. The
four public export refusal gates remain active; public FBX and Unity are not accepted.

### RandomRealm2 parameters and output ownership

Character owns Unity profiles, imported bindings and final Magica validation;
X owns Blender Dress and its final surface. Unity's existing Tuning UI writes
the following native fields and persists the complete `ClothSerializeData`
configuration together with component/root/collider references:

| Unity control | Magica field | Blender relationship |
| --- | --- | --- |
| Restore shape | `angleRestorationConstraint.useAngleRestoration` | Blender shape recovery uses waist-relative goals; no boolean/value conversion is implemented. |
| Shape stiffness | `angleRestorationConstraint.stiffness.value` | Cloth tension/compression, shear and bend are separate material settings. |
| Damping | `damping.value` | Cloth spring, bend and air damping are separate settings. |
| Motion inertia | `inertiaConstraint.worldInertia` | Native Cloth has no corresponding independent inertia slider. |
| Gravity | `gravity` | Blender multiplies scene gravity; copying the displayed value would change its meaning. |
| Collision radius (m) | `radius.value` | Blender clearance is fitted-height-relative and converted to native scene units. |

These are Unity-native settings, not an automatic conversion of Blender Tuning.
The existing Character profile supports BoneCloth. Selecting MeshCloth is not a
drop-in migration and still requires preservation of the imported skin data and
verification of the final rendered skirt.

| Animation route | Final Dress writer | Current acceptance |
| --- | --- | --- |
| Delta Body plus Manual/Original skeletal animation, Blender automatic displacement omitted | Unity Magica adds the realtime Dress response to the animation inputs. | Supported declaration route; actual imported bindings, profile and final surface still require validation. |
| Direct Manual/Original skeletal export | No Direct output route is published. | Blender export is blocked. The current Unity declaration reader does not recognize Direct as a supported omission backend. |
| Animation containing Blender's baked vertex physics | Baked playback must own Dress output and Dress Magica must be disabled; Hair remains independent. | Not supported by the current skeletal return. Explicit `simulation_baked=true` is rejected before return/import reuse. |
| Missing or incomplete physics provenance | Undetermined. | Metadata remains Unknown; it does not silently disable Magica or prove that physics was omitted. |

`CharacterAnimationDressMetadataReader` currently recognizes Delta declarations
only and does not switch physics components. Unknown backend declarations remain
Unknown; explicit `simulation_baked=true` is still rejected. A future baked route
requires a format carrying the actual vertex result and an explicit single-writer transition. Exporting
only bones cannot carry this backend's complete surface deformation.

The Delta skeletal Action exporter keeps manual controls and Original author
motion, while omitting Delta's automatic Dress contribution in its private
snapshot. It does not change the artist's physics mode. Author animation or
drivers that write the physical mode or reopen an owned physical constraint are
preserved and rejected before snapshot changes. Original Scene membership is
retained in the temporary library so the worker can verify the same native
ownership proof; it does not add simulation helpers to the exported FBX scope.

## Safety and validation status

Setup, tuning, cache actions and baking validate proxy topology, local ownership,
sample/pin/binding weights, exact PHYS/DEF constraints and collision graph.
For Delta, closed, independent pelvis/leg collider vertex fitting remains
allowed through the fitting action. Its Body attachment shares the artist Body mesh and is
excluded from selection for fitting. Edited or foreign targets,
modifiers, animation or incomplete ownership are rejected before mutation.
Posed attachment bones are converted to Rest coordinates when creating
colliders, avoiding applying an existing pose twice.

Direct instead uses an independent empty-Mesh Body relay. Its node graph reads
the actual evaluated Body, including live Body Shape Keys, without sharing
Body mesh or Key data. The original Body retains one Mesh user. Native same-graph
relay/bind comparisons guard topology and world positions; they do not prove
collision safety. Direct collider refitting remains blocked.

Reset releases a sealed cache, explicitly invalidates unsealed data and
returns to the native start frame. It retains original Actions and cache step.
Material tuning invalidates only an unsealed cache; mode changes keep sealed
cache data. A failed tuning transaction restores parameters, goal weights,
influence and saved records; unsealed frame data may require replay afterward.

Earlier Blender 5.1 fixtures verify the legacy structure/ownership, posed
attachments, cache/reset replay, parameter isolation, batch rollback, native
limits and save/reopen. Those fixture results do not validate Direct. Earlier
private Cosha synthetic motion previews have finite outputs and sampled contact
checks, but raised-leg plus Manual previews visibly fail and tail motion is not
proved settled. The earlier Delta fixed neutral-reference candidate failed its
native default Rest-chart installation gate; author-data rollback passed. These
remain historical results, not acceptance of the current Direct provider.

On 2026-10-07, provider `0facebc081163b01612172d59202a17f4422568c8e88c27cf7608ed083f8be41`
passed the native Blender 5.2 current-saved-Artist **cold component check** in a
disposable session. It checked two early rejections, both late rollback stages,
repeat-install record/ID stability, fresh Body binding, all 12 Cosha Body Key
channels, raw meshes/Rest/Actions, and the Manual Original roundtrip. Original
entry and return errors were below the unchanged 50 micrometre gate; an actual
weighted edit was visible and its correction survived returning to Controls.
Source and Artist disk protection passed, and the private scene was disposed.
No Artist or QA blend was saved; cache payload preservation is Unknown.

After Pose recovery and the artist's new saved file
`e0b30f73fc2f8ecc91ae418602fd3bec279b19071be3529b3d109ed55ec40dd7`,
provider `eb1ad005da9c98082b577c4dca4ab32a111ec684fa2ba27e3612b0c396205e99`
passed the same native cold component check. Native collection took 119.531
seconds; the owned process exited normally after 124.507 seconds. Fresh binding
maximum error was 0.120 micrometres, and the weighted Original edit moved the
visible surface by 19.577 mm before returning within 0.835 micrometres. Raw
meshes, Rest, Actions, Body Keys, source files and the saved Artist remained
protected, and the private scene was disposed. This does not test the new
sealed-cache/Bake guards or accept final motion, exports or deployment.

Four earlier saved-Artist, Blender 5.2 disposable cold-to-motion investigations
completed collection: Walk at world -Y 1.2 m/s, Run at 3 m/s, abrupt stop plus
90-degree turn, and stationary gradual leg/Manual-input refinement. They used
fresh canonical binding and unchanged default gravity 1, with no Dress keys or
imported animation clip. All 60 advancing samples retained their hard-waist
microguard. At the six sampled advancing poses in Walk, Run and gradual Manual,
and eight in Stop (including the actual stop at frame 34 and turn completion at
44), the complete 3040-vertex/5760-triangle final Dress and 28624 Body triangles
had no measured Body crossings or vertex penetration into the three proxies.
These sparse observations do not validate every frame or continuous collision
safety, Body volume containment, complete proxy coverage or cloth self-contact.
Self-collision was disabled. Geometry-quality Unknown values remain Unknown;
comparison with the requested input is not final-shape acceptance.

Same-frame held leg and Manual edits still failed: Cloth and the final Dress
remained unchanged while the input moved. Walk, Run and Stop recorded
233/252/272 Body crossing pairs and maximum thigh-proxy penetration of
36.944/36.580/36.735 mm respectively. Stop also retained one unresolved boundary
pair. Its last ten advancing steps still moved the final surface by maxima of
4.283-24.982 mm per step, so a settled result is not proved. The gradual Manual
experiment used only 29 hold frames; its requested L/T surfaces had 215/229
Body crossing pairs. Its final-response gain compares different physical times
and does not prove causal Manual fidelity or feasible target reproduction.

All four runs passed the final author/raw/Rest/Actions, Body Key channels,
source-file and Artist disk guards and disposed their private scenes. The Stop
successor additionally restored one private Body corrective Key's coordinates:
14 vertices in `CD Forearm Twist.R.001` differed by at most 7.45058e-9 in Body
mesh-local units. Base vertices were unchanged and all 12 Key arrays read back
exactly. This is QA restoration, not a runtime corrective repair; the cause
remains unconfirmed and the earlier failed Stop report is retained.

| Investigation | Recorded `frame_set` median (ms) | P95 (ms) |
| --- | ---: | ---: |
| Walk | 390.196 | 1405.195 |
| Run | 490.923 | 622.850 |
| Gradual leg plus Manual input | 307.080 | 700.134 |
| Stop and 90-degree turn | 439.821 | 739.683 |

Each row uses all 60 recorded native `frame_set` calls, including the start-frame
call after public Reset. Median uses the middle-pair mean; P95 is nearest-rank
`ceil(0.95 * 60)`. This does not include the full cold install/Reset
initialization or subsequent mesh extraction, contact metrics, renders and
report writes. External load was not controlled: Unity compilation and other
setup work occurred during the surrounding test period, and physical memory
use approached 98%. These figures are neither GUI FPS nor isolated solver cost,
and they do not establish a performance improvement between runs.

The newer saved-Artist/provider pair above also completed the unchanged stop60
recipe followed by 74 frames holding its actual pose, Root matrix, input800 and
requested3040 exactly. At frames 74, 104 and 134, the complete final surface had
zero measured Body crossing pairs or vertices inside the three proxies; the
waist readback maximum was 0.267 micrometres. This remains sparse contact
evidence. Three seconds after turn completion, the last ten maximum step
movements ranged from 1.761 to 4.396 mm, with a median of 3.118 mm. The preceding
111–120 window reached 6.221 mm, so settling is not established. The actual
front/side diagnostic images were inspected; they do not certify the artist's
materials or natural appearance. Subsequent same-frame leg/Manual edits again
left the Cloth/final surface unchanged: 226 Body crossings and 34.690 mm maximum
thigh-proxy penetration remained.

This 134-frame run recorded a `frame_set` median of 107.080 ms and nearest-rank
P95 of 266.320 ms; its frozen 74-frame tail recorded 101.154/112.105 ms. The
factory one-thread process completed in 114.585 seconds with a 934,494,208-byte
peak working set and 2,333,876 KiB available before launch. Other chats released
their heavy jobs for this serial window. These are background frame-update
measurements, not interactive preview FPS; changed source/Artist/external load
prevents an improvement claim against the earlier four runs. All author, Body
Key, source/Artist and disposal guards passed. No artist scene was saved.

A separate same-source/saved-Artist A/B run changed only the private public
air-damping setting from 3 to 5. Quality, collision, gravity, the 60-frame motion
and the 74-frame frozen-input tail remained the same. In frames 125–134, the
maximum step movement fell from 4.396 to 0.598 mm and the median from 3.118 to
0.317 mm. Earlier frames 81–90 still reached 6.605 mm; this is bounded tail-decay
evidence, not monotonic settling, long-term stability or all-motion acceptance.
The same three final-surface contact samples and waist guards passed, and the
actual diagnostic images were inspected. The formal default remains 3.

The Air 5 run recorded a `frame_set` median/P95 of 123.563/282.597 ms, versus
107.080/266.320 ms for Air 3. It completed in 124.653 seconds with a
934,592,512-byte peak working set and 1,217,604 KiB available before launch.
There is no preview-performance improvement claim. All author, Body Key,
source/Artist and disposal guards passed; no production setting, deployment or
artist save was changed.

The provider hash above identifies the frozen run evidence, not later source
changes or a published build. Direct final effects, natural appearance,
same-frame recalculation, baked-cache save/reopen, exports, interactive Artist integration
and Unity Magica effects remain unaccepted. No production scene was deployed
or saved by these investigations.

The latest Blender 5.2.0 disposable components retain narrow acceptance:

- **OriginalLegv3** used one public Original correction of 0.08 radians. It
  changed the visible Manual input by at most 19.577 mm and returned to Controls
  within 0.835 micrometres while retaining the correction. After Reset and
  Automatic, a left-leg rotation to -0.55 radians advanced over 30 frames. The
  complete 3040-vertex/5760-triangle final Dress versus 28624 Body triangles had
  zero measured crossings, unresolved pairs and proxy-inside vertices at frames
  1, 16 and 30. Its held Manual observation did not recalculate Cloth. This
  verifies the component and these sampled endpoints, not same-frame solving,
  continuous collision safety, naturalness or artist acceptance.
- **WalkSinglev2** collected 60 sequential Walk frames at world -Y 1.2 m/s with
  quality 8, collision quality 4, air damping 3 and gravity 1. All 60 waist
  microguards passed. Four complete final-surface contact observations at frames
  1, 23, 38 and 60 recorded zero Body crossings, unresolved pairs and
  proxy-inside vertices. The bulk/full readbacks matched at those samples. No
  Dress keyframes or imported clip were created; the run did not check held
  edits, rendered appearance, self-contact or every-frame collision safety.
- **PNSv4** completed private model library transport, reopen and removal of
  only the owned absolute-position output. The authored contract and Manual
  mode remained exact; the visible reopened input and stripped surface matched
  their respective 3040-vertex oracles with zero maximum error. Raw meshes,
  Shape Key assets, Rest and Actions were protected, and owned helpers remained
  exact. This plain native skin snapshot deliberately omits both Cloth and the
  raw80 Body attachment. It does not preserve the Direct final surface or prove
  FBX, animation, Unity import or baked-cache payload equivalence.

All three components passed their source/Artist protection and private disposal
guards. No production setting, release, deployment, artist refresh or artist
save changed. Timings are bounded background measurements; no GUI FPS or causal
speedup is established. These frozen snapshot measurements predate the later
Pose `0.77.2` deployment and subsequent Artist edits; Direct integration remains
pending.

Traceable evidence under `D:\Blender\Projects\Character\X\Validation\dress_automatic_20261005`:

- [OriginalLegv3 component and sparse final contacts](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_direct_original_leg_contact_v3_e0b_52_20261007_150150_739_f37d7377be5243b485f70f08caa29c4e/result/report.json), SHA256 `93e254a1dd85dd7485db66459a37043348804a6b10f83cac725b6dc299ac5d4e`.
- [WalkSinglev2 current-source sequential Walk](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_direct_current_single_v2_walk_e0b_52_20261007_135617_546_ea826b95c9074a7ab1d4850a243872d4/result/report.json), SHA256 `04534edae14f609f4216b6957e6478e7ef5a37fa27cea97758185c16d296cf24`.
- [PNSv4 private model transport/reopen/strip](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_plain_native_skin_model_v4_e0b_52_20261007_123727_962_feab010a295d467eae8019cccff98ca8/result/report.json), SHA256 `803b8ec6297eb51f23e4a664e76e21ff354d3d1a97748a826ec41da3bfd67aa9`.
- [Private FBXv2 hard-timeout process receipt](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_plain_native_skin_fbx_component_v2_e0b_52_20261007_151611_882_c3e632a19b434c3ca1940f416c838cd3/process.json).
- [REST FBX read-only diagnostic](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_diagnose_plain_rest_skin_fbx_component_52_20261007_205631_502_627e29bb41874a44a60bbcec1607ce6f/result/report.json), SHA256 `abfcda559443708304f097dbea8bf730f41ad1000a7291c82fd3e17346f52854`. The native writer and reimport finished, but the original strict component gate remains failed. Vertex count/order, ordered faces/triangles/corner vertices, UV and material indices agree. The undirected edge multiset agrees while raw edge numbers and loop edge indices differ; all named weight values agree exactly when missing zero entries are read as zero (472 vertices omit zero entries). The same-index coordinate maximum is 0.000858079 mm; all 217 retained bone names/parents agree, with Rest translation maximum 0.000596046 mm and linear component error 0.0000119209, below the unchanged component thresholds. Author data/source pins and owned/factory cleanup passed. The 27.696-second diagnostic exited without timeout; it does not load current X, accept public export/Unity, or prove material/texture fidelity, Manual animation or physical-surface equivalence. The follow-up [private REST roundtrip V2](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_plain_rest_skin_fbx_roundtrip_v2_52_20261007_211018_107_3294cae56ed64634942c74d558c72eae/result/report.json), SHA256 `711cf6262f4ec87c5dc4897a6481eb2ebff0fc3ee3a18908fcc0a3f7b3a81e96`, passed in 35.486 seconds with native/launcher exit 0 and its child gone. It proves a unique 5920-edge bijection and every loop edge joining its current/next polygon corners, retaining ordered faces/triangles/corner vertices, UV, material indices, named nonzero weights and the original coordinate/Rest thresholds. The raw numbering and 864 omitted zero entries remain explicitly reported. Original author/input pins, full current 159-source manifest and owned/factory disposal passed. This is fixed-input REST component acceptance only; current Artist/live, Manual animation, material/texture, production binding, public export and Unity remain unverified.
- [Private FBXv3 actual report](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_plain_native_skin_fbx_component_v3_currentdisk_52_20261007_155641_137_02a2eabca4d5451a9c910509541a72d4/result/report.json), SHA256 `eda2e1228d81fb83762fa9d7b4fbf6197c6bf975768bb9dbeb44bf7c3d500c7e`. It completed without timeout in 131.216 seconds but failed before writing FBX: the baked-Subsurf skin clone differed from the current Manual input by 0.471379 mm maximum and 0.040847 mm RMS, exceeding the 0.05 mm maximum gate. Skin weights, UV, material assignment and author/disposal guards passed. The subsequent [isolated modifier-order V3 measurement](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_skin_modifier_order_v3_52_20261007_200551_543_80981b36b8b5400382e533036fc86c6d/result/report.json), SHA256 `f82e7c5a60888ef1d534e9fa8ac1ffb310979fd0d41e12d88e044909b4f4058f`, reproduced 0.471379148 mm: explicit Subsurf-before-Armature and independent manual baking both matched the worker point-for-point, while the input used Armature-before-Subsurf. LBS still differed by 0.477945286 mm, so DQ is not necessary for this discrepancy. REST differed by 0.000238884 mm, within the 0.05 mm gate but not strict numeric equality. The 346-bone pose and rig-world checks at copy and raw800-link sampling points were exact; topology, UV, full named weights, material fields, author/source guards and native disposal passed. This bounded run finished in 29.828 seconds with its child gone. The original Manual gate remains failed; neither measurement wrote FBX or proves Unity/public export acceptance.
- [Earlier saved-Artist Direct cold component report](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_direct_cold_current_pose_v4_52_20261007_034418_213/report.json), SHA256 `4807a237182654dc4fe6977dcd89c59394f84cd34816349363f07616cad24977`.
- [New saved-Artist Direct cold component report](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_direct_cold_e0b_bake_guards_52_20261007_062634_189/report.json), SHA256 `0a016dc10d5cf706cbd529449a330d630d7e87459189b3f2cd068557a050d959`.
- [New saved-Artist stop settling tail](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_direct_cold_stop_tail_e0b_52_20261007_064740_348/result/report.json), SHA256 `5515a976f72ad6f5d43079432500ea1d6cc1af21efd387fcf915e0ce90d8d92c`.
- [Private air-damping 3-to-5 tail comparison](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_direct_cold_stop_tail_air5_e0b_52_20261007_072600_392/result/report.json), SHA256 `a595112339181b5eed962fa92a31fd09f252326616026d95f1844fcc904d981c`.
- [First sealed Manual/Bake diagnostic failure](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_direct_sealed_manual_bake_e0b_52_20261007_065400_531/result/report.json), SHA256 `36a958a32585a75603d3a36b48ba4670b8a2881796dd8c828bfa44bd551b54bf`.
- [Clean sealed Manual/Reset/rebake component](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_direct_sealed_manual_bake_v2_e0b_52_20261007_072128_753/result/report.json), SHA256 `63ae024fe79261d6bd8e0c518e2ccc478cb718059058c409dddbec1a56478917`.
- [Private Manual save/reopen and plain-skin snapshot component](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_direct_save_reopen_snapshot_e0b_52_20261007_080226_267/result/report.json), SHA256 `1751d54e72893d8b865fe9a8cfb39a515ec28831b3317c2e2feacada40a7d74a`.
- [Earlier Direct cold component report](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_direct_cold_install_persisted_return_v4_52_20261007_025806_936/report.json), SHA256 `a251fd889b4f2157565a0e10bbcdb80e0a93bd8b85bdbf798dfe9893966b63ab`.
- [Current canonical Walk investigation](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_current_cold_walk_pose_52_20261007_034910_198/result/report.json), SHA256 `51c3d7f4be0e29350fb511599e6a4fbc12657d9fb0fbdd91478b95f976fa04d4`.
- [Current canonical Run investigation](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_current_cold_run_pose_52_20261007_035244_796/result/report.json), SHA256 `b4dba8c371ba2335835cd5522d822272b126cb884e7d2d96776d81ce894bedf8`.
- [Current canonical gradual Manual investigation](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_current_cold_progressive_manual_52_20261007_040232_441/result/report.json), SHA256 `710b0ef38fbb248ff35bd409981c8731aa352a30369dd700a960765889886cfb`.
- [Current canonical Stop with QA Body-Key restoration](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_current_cold_stop_body_keys_52_20261007_041042_233/result/report.json), SHA256 `449c34230275fbded92dce57dd0f95c7d27ad6a0dd0c77a14afd2bc21c1b75e1`.
- [Earlier current-Artist Stop author-data restoration failure](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_current_cold_stop_pose_52_20261007_035534_526/result/report.json), SHA256 `3bce7768739568015376d23f631e2111d891fb36f2aa6e8776e634d067d7a49b`.
- [Earlier neutral Rest-chart installation failure](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_install_51_20261006_132931_537/result/workflow_install.json).
- [Earlier private forward-leg/Manual failure](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_live_disposable_forward_leg_v5_52_20261006_231439_184/result/input800_reproduction.json).
- [Earlier private stop/turn motion diagnostics](D:/Blender/Projects/Character/X/Validation/dress_automatic_20261005/actual_movement_stress_stop_winding_full_52_20261007_004120_440/result/input800_reproduction.json).
