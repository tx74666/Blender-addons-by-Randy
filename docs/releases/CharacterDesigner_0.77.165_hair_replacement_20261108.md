# Character Designer 0.77.165 — selected Hair replacement, directly adopted

Updated 2026-11-08. The user explicitly stopped additional tests and requested direct adoption. The official package and both disk deployments completed, artist replacement completed on November 3, and a fresh November 8 observation confirms the result in the reopened saved X.blend. This is direct adoption with the limits below, not full feature-QA acceptance.

## Intended behavior

Bones Apply replaces the selected Hair chains in the current character. For the authorized integration, CoshaRig receives Front 3 / Back 8; the user's existing 3- and 8-bone versions remain available. The old selected Front/Back 4-bone chains are replaced, while Side 4 remains unchanged. Apply no longer presents an additional retained copy as its result.

The existing curve sampling, bone-count conversion, weight correspondence/remapping method, sample grid, and approximation bounds remain unchanged. This does not claim continuous-time or exact deformation equivalence. The replacement transaction is designed to preserve root object/data identities, current paint, Keys, unselected Hair, Body data, original Actions, Main SESSION/REFS and retained versions. The direct artist receipt verifies the same root data identities, object membership, Main SESSION/REFS and target counts; it does not replace full author-array QA. Current topology receives its own checkpoint, Custom and linked Source baseline; historical animation is not rewritten.

Per the user's scope correction, the new authority runtime no longer scans unrelated Idle/history Action curve arrays. It retains Action identity/slot/property witnesses and the approved CurrentRig/Body/Source validators. The focused private harness was stopped before its final author-array acceptance; no pass is inferred.

## Frozen source

- Complete baseline: `controller_authoring_design_20261102/stage164_01/projection.json`, SHA256 `1d9381986e6c3c2d23d0ee334924ccae9de231c1b770f3e87ea149f3a4c52830`.
- Adopted projection: [stage165_06/projection.json](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/stage165_06/projection.json), SHA256 `c0f23487d7a7635edc0f59afe3c5d13b0db34d873fb56736e83d53bf37a009ea`.
- Verified manifest: **264 files / 232 Python files**, 8 changed + 2 new / 254 exact versus complete164. Preparation receipt SHA256 `7b33f0be9e6dfdac6095a223a0b318439c3f300528b2f3db81bf0ee1f0e9baa8`.
- Fresh authority inputs: observer05 SHA256 `8020290f4815e4685bd771ee41bc7d07ad1e1f62c9071105b6246f11e17d5a47`; input04 Blend SHA256 `6a4e0f19b70a0b40a1a903752358b49f8aa0c65fdbadf9335ec8fe2c5dc6ca7d`; snapshot04 receipt SHA256 `368cd94a0e64793c3c799f56aa08372292d133f80d6e8944696ec021721fb102`.
- Official ZIP SHA256 `e644fe301f95f63ff49f024497067f49cee4c93d93a61742c4178ba18139dc68`; direct_apply01 receipt SHA256 `0b3c32441cff88dcf7bc0ed3306b36986f2e2f1300cfaa1ed4ddc86d323a31e1`; November 8 observer SHA256 `5a2ff2fc8f8c7e0902b2c95d8d0bceb48154b974787df3489c2812ea07ab96f2`.

## Evidence and retained limits

[WORKING_STATUS.md](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/WORKING_STATUS.md), SHA256 `4880e47e488d94a97648b4ef7f2c7e59c3f7f78fd20fc782b34de2ce292031cf`, preserves the earlier preparation failures, snapshot limitations and interrupted native02. Its stage04 runtime-scan description is historical; the user-directed scan removal above supersedes that candidate behavior.

Later evidence remains separate:

- [native03/result.json](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/native03/result.json): failed on an unavailable Body helper and owned Library extra-user cleanup. The candidate now uses complete164's actual Body schema and receipt-scoped Library cleanup. Tiny [lib02/result.json](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/lib02/result.json) passed the cleanup guards; this is not feature acceptance.
- [native04/result.json](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/native04/result.json): failed at the legacy Main mapped-bone presence gate after authorized Hair removal; recovery was clean. Stage06 adds only the authenticated missing-Hair bridge, retaining the ordinary non-Hair refusal and historical SESSION/REFS.
- [native05/result.json](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/native05/result.json): replacement returned REPLACED for 24 chains, but final selected-version equality failed. This failed run is retained without relabeling.
- [native06/result.json](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/native06/result.json): **incomplete, stopped at the user's request**, recorded in [user_stop01.json](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/native06/user_stop01.json). Replacement returned REPLACED for 24 chains in 126,356 ms; final author/CurrentRig proof had not completed. This timing is the integration operation, not a one-strand latency or FPS result.
- Private code01 and code02 passed their respective 10 cases for earlier stage03/stage04. [code03](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/code03/result.json) and [code04](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/code04/result.json) failed updater admission checks. Those failures remain; later actual artist adoption has its separate receipt below and does not relabel these tests.
- Private GUI03 ([undo03/result.json](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/undo03/result.json)) failed exact Undo restoration for one temporary selected pose value over a key (`0.171` returned as `0.13`). Keep this failure and its [diff](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/undo03/result.undo_vs_before.diff.json). No GUI Undo/Redo success is claimed.

No successful Original return, Source/Custom switching, future single-strand Apply or GUI Undo acceptance is claimed. No preferences save or new tests were performed for the November 8 closure.

## Actual adoption and current saved result

- Official 0.77.165 package was built from stage165_06 and deployed to Blender 5.2 and X; both corresponding disk checks reported 264 files, 0 differences. Disk deployment alone is not live-runtime proof.
- [direct_code_live03.json](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/direct_code_live03.json): `CODE_ADOPTED`, passed. Existing candidate function bodies were already loaded, so this call changed 0 bodies and added 0 functions; it imported two new helper modules. No prior refresh initiator or time is inferred.
- [direct_apply01.json](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/direct_apply01.json): actual artist PID36008 replacement passed on November 3. Front **10 chains × 3 bones**, Side **8 × 4**, Back **14 × 8**. The two existing 3-/8-segment versions remain. Same root native data identities were retained. This receipt explicitly says `scene_saved=false`.
- Fresh Animation2 read-only [artist_resume_observe_20261108_01.json](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/artist_resume_observe_20261108_01.json), passed: reopened `X.blend`, PID10664, `dirty=false`, CoshaRig392 bones, with the corresponding 10×3 and 14×8 chains selected (142 bones). The file's local modification time is November 4, 06:27:44. This confirms the replacement persisted into the subsequently saved/reopened scene; the save initiator is unknown.

No duplicate replacement, scene save, foreground takeover or additional test was needed on November 8. Animation2's priority remains intact. [RESUME_20261108.md](D:/Blender/Projects/Character/X/validation/hair_replace_20261103/RESUME_20261108.md) records the resume decision.
