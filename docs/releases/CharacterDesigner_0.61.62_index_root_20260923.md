# Character Designer 0.61.62 — real INDEX.L root transition

The uploaded `X.blend` at model commit `534eacbe8203d77ea991901b5d922d68f428eaaa`
contains the LFS object `0d96eb89e8154538728476becf5db330807c848fb160521de1ca626c0be35455`
(31,754,165 bytes). On the saved `Cosha` / `CoshaRig` / `INDEX.L` case, the
operator identified `f_index.01.L`, `f_index.02.L` and `f_index.03.L` correctly.

Before the fix, the first planned segment entered the current finger interior,
but `_regular_root_band()` rejected the palm transition because two proximal
quad strips branch at the root boundary. The failure was therefore a local
proof-shape limitation, not a missing mark or a wrong bone chain. The existing
local-band proof remains first. On an explicit Align Joints action only, when
that band cannot be formed, `_root_volume()` can use the current connected
closed shell; `internal.Volume` still certifies the fixed root and the complete
changed segment before the transactional bone write. No draw callback, timer or
editing-time monitor performs this work.

Real-file acceptance used a clean copy of the same LFS object. The actual
`bpy.ops.character_designer.finger_loop_marks(action='ALIGN')` returned
`{'FINISHED'}`. Only the two shared joints moved:

```text
f_index.01.L.tail = f_index.02.L.head
f_index.02.L.tail = f_index.03.L.head
```

`f_index.01.L.head`, `f_index.03.L.tail`, all Roll values, parent/Connected/
Deform flags, every other bone, mesh/Shape Keys/weights, and saved marks were
unchanged. The saved copy reopened with zero joint gaps, marker-plane errors
below `2.2e-7`, and a repeated Align action produced zero drift.

Validation included the new branching-root regression plus 7 other root
certification cases, 12 loop-mark cases, and 7 automatic-loop-mark cases in
serial Blender 5.2.0 background runs. The original failing copy remains
available as before evidence; it is not overwritten.
