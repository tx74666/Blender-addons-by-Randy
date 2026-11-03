# Character Designer 0.77.154 — compact Hair and Physics

The Hair page now puts Previous, Next and Selected directly above the strand
list. Selected imports the viewport's selected Hair bones into the list while
keeping the native single-bone selection. Previous/Next follow visible logical
entries, including whole proven L/R pairs. Clicking a row loads the matching
Physics settings; importing a right-side bone retains that physical member's
asymmetric settings instead of substituting its left partner.

The complete logical list displays consecutive Hair 1…N labels. Original
generated names such as Hair Hair 12.01 no longer appear as the list title.
Numbers stay stable across category filters. Custom names retain their text.
Native names, strand identities, registry order and physical member counts are
unchanged; labels never determine ownership or rebuild scope. Existing proven
L/R grouping remains one row. Distinct native strands are never deleted merely
because their names resemble one another.

Bones and Apply are visible below the list. Setting Bones to 6 and clicking
Apply executes the existing authenticated selected-entry rebuild, retaining the
original version and every unselected chain. The duplicate top Change Selected
Strands entry, keyboard-shortcut hint, repeated counters and routine help are
removed. Source/binding/version-maintenance tools are under Advanced, with
existing destructive-action guards intact.

The lower panel is Physics. Navigation and raw bone-name/counter lines are
removed there. Group/preset share a row; irrelevant mirror information is hidden.
Reconcile and pairing controls are under Advanced. Physical/depth controls,
preview, real errors and recovery remain available. Solver algorithms and
physical defaults are unchanged.

The complete package extends approved153 with five changed files (including
version metadata), one new display helper and 250 inherited files exact:
256 files / 224 Python. All153 wrist modules and earlier151 row-selection
protections remain. Pending Animation2/private150 work is excluded.

Nine pure label tests and ten private native feature/layout/preservation tests
pass. The scoped updater additionally passes thirteen cases from149, including
seven rollback boundaries, and six cases from153. A real isolated GUI screenshot
was inspected. Earlier harness mismatches and GUI02's private native crash remain
retained; its confirmed owned crashed worker was terminated separately from the
normally exited successful GUI03. No artist crash is inferred.

Official ZIP and Blender5.2/X disk checks match all256 files. In artist36008,
fresh preflight, scoped UI adoption and a separate read-only postcheck passed.
The component update changed fifteen callables, loaded the display helper,
added two transient UI fields and retitled the same two Python Panel classes.
State/operator RNA identities remained; only Panel RNA was re-registered for
the requested titles. Context, native IDs, pending WM settings, selection,
frame14 and dirty state matched the bounded witnesses exactly. Metadata remains
153; this is UI revision154 adoption, not a whole-runtime154 refresh claim.

No artist strand rebuild, physical mouse/Undo feature trial, scene/preferences
save, full reload, Unity/Git operation, new monitoring or FPS work was performed.
Private author checks do not prove deep artist author arrays or exact old/new
deformation equivalence after re-segmentation.

Evidence: X `validation/hair_compact_20261103/acceptance154_01.json`.
