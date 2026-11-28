# Character Designer 0.77.297 — Compare Key Edits

**Compare Key Edits** opens an editable copy of the active Action at the current frame. Use Blender’s Graph Editor tools on **Edited**, switch to **Original** to compare, then choose **Apply Edits** or **Keep Original**. The panel reports the actual change in key counts across affected curves in the active Action slot.

The release extends the complete 0.77.296 projection by three files. Blender 5.2 and X each matched all 329 files after local deployment. The running X session retains its current state.

Blender 5.2 native Decimate reduced a Bézier curve from 61 keys to 31. The test verified the original until Apply, application to the existing Action and slot, preservation of the other slot, and discard from Original. An isolated opening of saved X also verified the previously saved preview and a fresh copy of In Air at frame 11. The legacy explicit-parameter count and object-property tests passed.

[Delivery receipt](<D:/Blender/Projects/Character/X/validation/key_edit_comparison_20261128/delivery297.json>)
