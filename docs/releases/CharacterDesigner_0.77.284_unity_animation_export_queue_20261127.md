# Character Designer 0.77.284 — Unity Animation Export Queue

In **Miscellaneous → Unity Export → Animations**, open the **Animation Library**, choose a motion and click **Add Selected to Queue**. Use **Set FBX…** to assign its existing Unity animation FBX. The destination is saved with the Blender scene when the scene is saved. The queue lasts until the file is reopened. Remove a row with **×** or use **Clear** to empty the queue.

**Export Queue to Unity** bakes each queued Action and Object slot with the existing animation-only FBX exporter. Each successful export replaces the assigned FBX at its existing path and preserves Unity's importer settings and asset GUID in the existing `.meta`. Successful rows leave the queue. Failed rows stay with their errors, and the batch continues. Sparse Actions use the existing explicit export base-pose contract. **Export Current Action as FBX…** remains available for one-off file export.

## Pipeline audit

The Unity workspace catalog contains shared source FBXs and working `.anim` files. Character Tuning publishes immutable review revisions with a separate apply step. Those records do not identify a dedicated replaceable FBX for every motion. The queue therefore uses explicit per-motion destinations in Unity `Assets`. **Linked Revisions** retains the review workflow.

## Verification

- 102 focused Python tests passed.
- Blender 5.2 produced four native FBXs in a disposable Unity-shaped project. The fixture covered a failed item between successes, a repeated same-path update, unchanged `.meta`, an inactive multi-slot Action, a managed sparse Action retry, and preserved Action binding and keys.
- An additional 171-test Worklist and sections run reproduced the same 3 failures and 16 fixture errors on the previous and new projections.
- The fixture did not run the Unity Editor or exercise reimport in a live project. Live `X.blend` was left open and its loaded add-on was not refreshed.
- The official ZIP and the two installed copies match the 328-file projection.

[Native acceptance](<D:/Blender/Projects/Character/X/validation/unity_animation_export_queue_20261127/native284_acceptance.json>) · [Projection](<D:/Blender/Projects/Character/X/validation/unity_animation_export_queue_20261127/stage284_02/projection.json>) · [Delivery](<D:/Blender/Projects/Character/X/validation/unity_animation_export_queue_20261127/delivery284.json>) · [Previous delivery](<D:/Blender/Projects/Character/X/validation/rig_panel_compact_20261127/delivery283.json>)
