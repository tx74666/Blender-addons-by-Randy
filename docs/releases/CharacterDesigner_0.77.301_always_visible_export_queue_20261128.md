# Character Designer 0.77.301 · Always-visible Animation Export Queue

Under **Miscellaneous → Unity Export → Animations**, the **Export Queue** appears immediately. An empty queue shows **No animations queued**. Queued entries show their count and removal controls. **Pick from Existing Animations** and **Add Starred to Queue** sit below the list. **Export Queue to Unity** becomes available when the queue has entries and the exporter is idle. **Export Current Action as FBX…** remains a separate button.

Blender 5.2 verification exercised the empty queue, adding and removing entries, clearing the list, the local picker and single-action export entry. It used a private fixture and performed no exports. The Blender 5.2 and X installations each matched the 330-file release projection. [Delivery receipt](<D:/Blender/Projects/Character/X/validation/always_open_animation_export_queue_20261128/delivery301.json>)

The layout was refreshed in the open X session and the empty queue was visible with the animation picker folded. Scene and queue state were preserved and X was left unsaved. [Live receipt](<D:/Blender/Projects/Character/X/validation/always_open_animation_export_queue_20261128/live301.json>)
