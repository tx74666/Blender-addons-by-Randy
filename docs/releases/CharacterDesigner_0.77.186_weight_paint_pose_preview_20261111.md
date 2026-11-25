# Character Designer 0.77.186 — Weight painting with pose preview

2026-11-11 (Asia/Shanghai). Source/runtime model-effort: unrecorded.

**Weight > Weight Tools** now keeps one RAM-only painting session across **Paint Weights → Preview Pose → Resume Painting**. The row retains two primary buttons; **Finish Painting** is available in both session phases. The painted vertex group is stored separately from the pose selection, so selecting an existing pose control does not redirect painting to a control-bone group. **Painting Group** displays the current valid deform group while painting and the retained group during pose preview, independently of the selected posing control.

Preview exposes verified existing local IK target/pole controls, FK source bones, and an authenticated public Master where applicable. It does not switch IK/FK, add switching drivers, unlock rotation channels, mute constraints, rebuild controls or change Rest. Unsupported driven bones remain unavailable rather than being presented as free FK bones. Auto Key is temporarily disabled for the session. Finish returns to the captured entry frame and restores raw pose components and existing related numeric controls, including numeric arrays in their original storage, while retaining newly painted weights and locks.

Action/slot, driver, constraint, binding, bone/group structure, and captured numeric-type changes refuse automatic old-pose recovery and retain the current author data and session. Animated NLA time/influence values are treated as evaluated values only when the matching strip flags and real active strip curves establish that role; curve definitions and ownership remain checked. Transition failures retain recovery state and attempt phase, display, context and Auto Key rollback. Manual native mode changes do not silently discard the session.

Finish restores the stored bone selection and reselects prior objects. It deliberately makes the rig active and selected in Pose Mode; it does not promise the exact entry active-object/Object-Mode context.

An older display-only RAM session remains finishable without inventing an entry pose. Its first explicit Preview establishes the new temporary-pose baseline and labels that narrower scope. Native Weight Paint without a recognized RAM session instead offers **End Native Painting**: it exits the native mode without claiming to restore missing pose/display history. Valid painting ownership blocks Original changes to the same rig even across a Scene/ViewLayer change; an unrelated rig's native painting is not globally blocked.

## Original boundary

The Original conflict guard now reports that the active painting session must finish first, before attempting Original mutations. The existing missing-Main-recovery-reference refusal is retained after painting ends. No recovery references are reconstructed, no Original permission is forged, and **Restore Main Rig remains deferred**. The synthetic missing-reference native check proves independent painting/preview/finish and unchanged strict refusal; it is not a successful complete Original session.

## Validation and retained failures

- Intermediate `pure185_01.json`: **31 pure tests** across pose sessions and the Original guard passed. The final `pure186_01.json` passed **32 combined pure tests**, including a direct draw call proving the Paint label follows the actual group, Preview retains that group after control selection, and drawing does not mutate session/group state. Overlapping runs are not added together.
- `native185_03.json`: **15 final tiny-native groups** passed, including actual FK transforms, native IK and generated managed ARM controls, mesh deformation, retained real weight edits, operators/polls/row labels, frame/raw-pose/Auto-Key recovery, author-change refusals and injected partial-transition rollback. Animated NLA changed between frames 7 and 12 and returned correctly; a driven numeric array retained its native buffer address, float storage and IDProperty UI metadata. The final 15 include the earlier 13 groups; counts are not added together. Owned native operators/handlers were cleaned and workers exited.
- `updater_native03/result.json`: **14 private updater groups**, including **44 injected mutation boundaries**, passed. Existing function, descriptor, import, class, RNA/property and source-signature owners and exact Python recovery containers were retained. The updater's scene/data/operator access trap recorded zero hits.
- `label_native186_02/result.json`: **11 final label-updater native groups** and **3 fault boundaries** passed. It admits an exact warm helper or both helper slots genuinely absent, freezes that state through commit/ALREADY/rollback, and refuses partial/foreign helpers, lifecycle aliases and post-preflight races. Success changes one existing draw code object and the version only, with zero RNA lifecycle calls. The earlier warm-only native01 groups retain their separate scope and are not added to these11.
- `native_smoke186_cold02.json` passed the ordinary cold-helper import check: the normal product import produced the exact local module/parent identity, repeated access retained that identity and the product session object, and native objects/scenes/dirty/frame and handlers stayed exact. It did not call smoke begin, register classes, mutate scenes or exercise physical UI. Receipt SHA256: `d4faf58255a7a2320710787db950ed93f61a46215303899a8bb1cb8790d1610c`.

Original `native185_01.json` remains failed: real Blender constraints/modifiers rejected generic IDProperty enumeration before transitions; its additional managed-panel parent-registration harness failure is also retained. `updater_native01/result.json` remains a preflight AST class-tuple-reader refusal with zero mutation. `updater_native02/result.json` retains its successful commit/ALREADY cases followed by a false rollback-owner refusal caused by Python-class/native-RNA identifier comparison. Later passing runs establish only their own scopes and do not relabel these failures.

The first actual label attempt, `artist_label186_01.json`, also remains refused with zero mutation and an exact separate author witness. Its updater incorrectly required a warm helper; the freshly observed runtime had both helper slots absent. The earlier185 update had installed those slots, so the later cold state lies beyond that receipt's identity boundary. Any intervening refresh/state change has unrecorded time and initiator; neither the cold-helper fix nor the later success establishes identity across that interval. No foreign/partial helper was repaired or resurrected.

These are focused private checks, not full geometry/weights/key-array, arbitrary external-control rig, solver/dynamics, global lifecycle, GUI pixels or physical-input certification. No handler, timer or continuous scan was added to the product, and no FPS or preserved-performance claim is made. The existing playback-FPS investigation stays economically closed.

## Complete package and code adoption

The final **270-file/238-Python**186 projection extends the retained complete185 stage185_04 by **3 changed files and 267 exact files**: version, README and the painting-row group label. Its frozen path is `validation/weight_paint_pose_20261111/stage186_01/projection.json`, SHA256 `637c3d5663018f895d89d65ca6226bc686d94d20d93e5948c24694c6f465e04f`. The cumulative delta from approved184 remains **4 changed files,1 new file and265 exact files**. The core preview/restoration helper and Original guard are byte-exact185. Official186 ZIP was built and CRC/payload270 matched; Roaming5.2 and X each matched270 files with0 differences in `disk186_01.json`. Final physical acceptance is recorded separately below. The intermediate185 official ZIP and Roaming5.2/X matching checks reported270 files and0 differences; its package and receipts remain fixed. Existing184 Finger,183 Forearm and182 protocol bytes/scopes are inherited; no older installer was replayed.

Evidence is under `D:/Blender/Projects/Character/X/validation/weight_paint_pose_20261111/`. The retained intermediate185 evidence remains fixed; final186 package and checks are listed here as separate artifacts:

| Artifact | SHA256 |
| --- | --- |
| Final `stage186_01/projection.json` | `637c3d5663018f895d89d65ca6226bc686d94d20d93e5948c24694c6f465e04f` |
| Final `character_designer-0.77.186.zip` | `272cc7ac7151e6da767c246a88c70ece8122e726ac8404f3955cda832d4735e7` |
| `pure186_01.json` | `e44e396bf2037e206f3957c8e05e152f5e8e33ec5667b7683c467a342e9587d3` |
| `label_native186_02/result.json` | `b4490d84d187fa078b53f39947bbbb265a031b2317de57823a72b06c85b67e02` |
| `stage185_04/projection.json` | `bd84e3958f86f78998fbaa3c2f6ce130364a4a2729017bba9eaa432865ad5c66` |
| Official `character_designer-0.77.185.zip` | `64a6211ef080ec529e1c0f1232f1a6cc648f7c594e07deddfa493980fce54cf6` |
| `disk185_01.json` | `1c6a586bbeaddf659a577f65629eb4bce7db2b6e240b5e44cae451356870cbea` |
| `pure185_01.json` | `81b27056988569c0f3badd380b84cfc9b4e746195659b3141e6f728f81f5a97f` |
| `native185_03.json` | `98a38dd0ddfdf085b2156a37c3bc56ab91bdd0cec6e071e26ff21cdfeab08601` |
| `updater_native03/result.json` | `e9ca680274f511ba44ec67684df5ac1d8e9551323739918770478624cdf08554` |
| `artist_code185_01.json` | `79d344fbdcc0d35e67ed88b68ae669f14e610aa9be98e61a5419a0eb0d8ba58b` |

The intermediate185 actual artist narrow update recorded **COMMITTED**, 44 code/binding/version mutations, two new operator registrations, retained loaded source-signature identity, and an exact separate authored witness. Its updater recorded zero Scene reads/writes; the separate witness is a distinct operation. This certifies the painting/Original component update, not complete live Finger defaults, animation protocol, all-cache or global-handler equivalence.

At the fresh186 boundary, `artist_label186_02.json` recorded **COMMITTED** with one draw code update and one version write, **zero RNA lifecycle**, unchanged source-signature identity, and an exact separate authored witness. Both helper slots were absent and were kept absent by this code transaction; ordinary helper import has its separate tiny proof above. The updater read/wrote no Scene data. This certifies the local label/version update at that boundary, not the unobserved intervening refresh or physical product UI. Receipt SHA256: `44d5b19b7583f4255e595c30d7339cef50c2f23d4deff79adc983fdc9cb8089f`.

## Final live product verification

Actual artist PID51688, `D:/Blender5.2/blender.exe`, current unsaved X.blend, was freshly checked. The final186 label transaction recorded COMMITTED with one existing draw code update and one version-metadata write,0RNA lifecycle, and an exact separate bounded author witness. Its cold helper state was preserved by the updater; the smoke baseline then used the product's ordinary verified lazy import. The preceding185 receipt and this fresh audit do not establish identity across the unobserved intermediate boundary.

Physical UI clicks completed **Paint → Preview → Resume → Finish** on Cosha/CoshaRig at frame19. Painting Group remained `thigh.L`, with93 vertex groups throughout; selecting the existing preview control did not create a control-named group. Original/Controls were visibly disabled with a Finish hint during both phases and became available after Finish. Finish was visibly enabled and polled true in both phases; the live Finish click was from Paint. Both Finish paths and repeated cycles were executed in the private native fixture.

A verified existing local IK pole, `CTRL_elbow_pole.L`, received one bounded3mm X translation through native pose RNA in Preview. Resume retained that test pose. Finish restored the original raw pose, cached-pose hash, frame/subframe, captured display and mesh paint flags, prior group index, stored bone selection and Auto Key. It made CoshaRig active/selected in Pose and reselected Cosha, as required by the Pose return contract. This is not the exact entry active-object/Object-Mode context. The lower editor returned to its retained Graph Editor, Weight Tools remained open, session was cleared, and input was released. Dirty state remained true; no scene or preference save occurred.

`artist_smoke186_01.json` passed with declared author arrays/bindings and all9 Action curve hashes exact. No live brush stroke was made: current artist weights stayed exact; actual new-weight retention is proven by the private native edits. Artist Auto Key began false; initially-true Auto Key and changed-frame recovery are private-native checks. Only the existing Forearm derived coordinates/value/mute outputs authenticated by ownership records and primitive lifetime tokens were exempted from phase equality. Their lifetime identity remained fixed. No blanket all-ShapeKeys-zero-write or all-geometry-attributes claim is made.

A first optional nudge call stopped in its foreground preflight before any pose mutation after the preceding read-only witness. A freshly focused standalone nudge then succeeded once; no gate was relaxed. The earlier stale Console183 command also stopped at its first version assertion before cleanup or UI writes. These retained refusals are recorded in `artist_ui186_01.json` and `stale_console_refusal186_01.json`; they are not hidden as successful checks.

| Final live artifact | SHA256 |
| --- | --- |
| `artist_smoke186_01.json` | `172d0399d717bc5860841f47055c1ad4c23528978715e8173f2669194fc41796` |
| `artist_return186_01.json` | `a62c02641824a81eb53c2f3f7e0773d4d19a4785ba6c8f45f180e6ddb6286b22` |

Final acceptance: `validation/weight_paint_pose_20261111/acceptance186_01.json`. The actual UI observation scope is separately recorded in `artist_ui186_01.json`. No restart/full reload, formal Original recovery, Restore Main Rig, bake/export, Unity operation, Git upload, new product timer/handler or FPS investigation was performed by this task. All owned isolated Blender workers exited; shared applications and services were retained.
