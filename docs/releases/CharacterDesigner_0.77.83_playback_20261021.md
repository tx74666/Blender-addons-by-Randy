# Character Designer 0.77.83 playback validation (2026-10-21)

0.77.83 reduces repeated playback validation and RNA reads while preserving
correction output, failure guards, authored weights, Shape Keys, native Rest and
Actions. The release contains the accepted candidate04 algorithms. Unrelated
canonical `body_region.py` work is excluded through the frozen projection.

- Original mode caches an immutable plan for exact saved Rest JSON; each call
  still checks current native bone inventory, ownership, Rest and tolerances.
- Audited read-only JSON scopes reuse data within one call. Large finite JSON
  records may reuse private process-local marshal bytes, bounded to 16 entries
  and 2 MiB, with fresh mutable trees for new calls. Ordinary writers still use
  fresh standard JSON decoding. Native validation results are never cached.
- Forearm weight/input reads retain local RNA references during the current
  calculation. Foot Controls builds exact bone-path fragments once per side.
  Vector and Matrix finite checks batch native values and retain the original
  component fallback for overflow/non-finite inputs. Normalization order,
  correction arithmetic and error handling remain unchanged.

[77 pure tests](D:/Blender/Projects/Character/X/validation/performance_20261021/profiles/candidate04_unit_tests.json)
passed without skips. The
[native Action report](D:/Blender/Projects/Character/X/validation/performance_20261021/profiles/action_c04_r1.json)
contains the successful 16-test native math preflight; the separate
[finite fixture](D:/Blender/Projects/Character/X/validation/performance_20261021/profiles/finite_math_c04_r1.json)
covers native Vector/Matrix values, NaN/Infinity and large finite overflow.
The historical `candidate04_existing_math_tests.json` failed run is retained and
is not the successful native16 evidence.

[Native correctness acceptance](D:/Blender/Projects/Character/X/validation/performance_20261021/profiles/candidate04_native_action_acceptance.json)
verified author/output records across baseline82, candidate03 and candidate04:
all 60 owned Action payloads and full geometry/pose/managed-output witnesses
every 15 frames, with 20 warm frames. Original bindings, Action inventory, raw
channels and callback order were restored. These witness limits remain explicit.

| Native Action source | Mean wall ms | Mean process CPU ms |
| --- | ---: | ---: |
| 0.77.82 baseline | 96.71699 | 94.53125 |
| candidate03 | 87.85405 | 85.15625 |
| candidate04 / accepted .83 algorithms | 86.67070 | 85.41667 |

The timed operation is `frame_set` plus `view_layer.update` on Blender 5.2.0 LTS
build `fbe6228777e7`. These are single ordered comparisons on a shared host,
not controlled FPS gains. Candidate04 shows no clear incremental whole-Action
CPU benefit over candidate03. The final
[GUI baseline](D:/Blender/Projects/Character/X/validation/performance_20261021/profiles/gui_final/response_baseline_final_material_01.json)
and [GUI candidate](D:/Blender/Projects/Character/X/validation/performance_20261021/profiles/gui_final/response_candidate_final_material_01.json)
observed 8.0379559 to 8.8537584 displayed frame changes/s (+10.15% observed;
137 candidate frames over 15.47 s). Frame evaluation median was 60.53755 to
52.60590 ms, and mean was 62.82643145 to 54.27179708 ms. This is one ordered,
instrumented pair on a shared host; POST_PIXEL does not measure physical monitor
presentation. All 15 cross-version protected components were exact in the
[final acceptance](D:/Blender/Projects/Character/X/validation/performance_20261021/acceptance_summary.json)
(SHA256 `743a3f6b124bc7fe1d514b8d00283ae005ecf72d9f9324b8618c842020e8e8e6`).
[Runtime cleanup](D:/Blender/Projects/Character/X/validation/performance_20261021/profiles/runtime_cleanup.json)
passed: the task SDK stopped normally and owned helper modules/timers were
removed, with artist content preserved.

In a private copy with Forearm frozen, temporarily detaching `ShoesAction`
changed median update time from 14.39075 to 0.30865 ms. This is a non-equivalent
cost diagnostic, not an adopted optimization. The
[artist binding observation](D:/Blender/Projects/Character/X/validation/performance_20261021/profiles/shoes_action_live_read.json)
remains unchanged; intended shoe animation is still a user decision. The
[raw ablation report](D:/Blender/Projects/Character/X/validation/performance_20261021/profiles/scene_ablation_r5.json)
remains **FAIL** because two computed corner-normal outputs did not restore
byte-exactly. A separate
[52-check interpretation](D:/Blender/Projects/Character/X/validation/performance_20261021/profiles/scene_ablation_r5_interpretation.json)
passed for limited cost attribution: authored `custom_normal` attributes and
the other 13,086 witness rows remained exact. It does not relabel the raw failure
or establish equivalent normal-output restoration.

The final package has 213 files; all 181 Python sources compiled successfully.
[Staging](D:/Blender/Projects/Character/X/validation/performance_20261021/release083_final/staging_proof.json)
and [ZIP payload verification](D:/Blender/Projects/Character/X/validation/performance_20261021/release083_final/build_proof.json)
passed. Deployment checks reported 213 files and zero differences at both the
Blender 5.2 user add-ons destination and X's deployment copy.
[AST completeness](D:/Blender/Projects/Character/X/validation/performance_20261021/profiles/candidate04_runtime_patch_completeness.json)
covered 12 source files and 23 definitions. The
[guarded runtime patch](D:/Blender/Projects/Character/X/validation/performance_20261021/profiles/runtime_patch_release083_final.json)
passed with exact protected content and handler identities/order, plus .83
metadata/source-signature acknowledgement. It was a selected code/metadata patch,
not a full add-on reload. No artist file or preferences were saved; no Git commit
or push was performed.

| Frozen artifact | SHA256 |
| --- | --- |
| `character_designer-0.77.83.zip` | `e317a82cc40c2549dfb05261f4293363a16dafbed0c1f59b9f895738aaee2793` |
| `release083_final/manifest.json` | `66245fe092b4627c4d2d8ee10b457f2356b80819a2d935cf500af82a9b75a7b1` |
| `release083_final/projection.json` | `2ff55497e320581885f2195c06495f2ddeb402420eea16891f17917b8ad1763a` |

Raw evidence remains under
`D:\Blender\Projects\Character\X\validation\performance_20261021`.
Model/reasoning settings were not independently recorded. Further investigation
should target new measured bottlenecks or changed scenes/code and preserve the
separate GUI, native Action and non-equivalent ablation scopes above.


## Scene follow-up on 2026-10-21

The user subsequently confirmed ShoesAction was accidental and unwanted. Its sole Shoes binding and unused Action datablock were removed, with current-disk and unsaved-scene backups retained. Native X.blend save returned FINISHED; separate post-event-loop verification confirmed a clean scene and exact scoped geometry, weights, Shape Keys, transforms and rig poses. The immediate synchronous dirty check failed and remains recorded separately; it was not rewritten as a pass. This follow-up did not modify the add-on or remeasure FPS. See [scene operation and saved-file receipt](D:/Blender/Projects/Character/X/validation/shoes_action_remove_20261021/README.md).
