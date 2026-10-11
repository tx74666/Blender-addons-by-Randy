# Character Designer 0.77.15 — single animation list

Imported animations appear once. Each row has two buttons with the same motion
name: a linked icon plays the original, and an edit icon activates the editable
version. The primary panel no longer shows the catalog, search, Link availability,
Source/Custom labels, selection checkboxes, scan columns, deletion or reorder controls.

Import Prepared uses the latest Unity preparation and retains existing drafts.
Export Changes performs a fresh scan and automatically includes every confirmed
change, independent of old checkbox state. Unchanged motions are skipped;
incomplete or blocked comparisons remain local with an explanation. The launch
callback rechecks complete comparison proof before starting a worker. New Action
remains available under Advanced. Saved identities, actions and legacy operator
registration remain compatible.

Validation: 43 collection tests and 60 UI contract tests passed, including
production sync with queue-time changes becoming unknown, losing their comparison
receipt or reverting to unchanged. Independent review passed. This was a local
Blender add-on change; no Unity editor operations or artist-scene edits were made.
The live Blender interface was not reloaded or visually validated by this task.

Release input is a complete 152-file projection of verified 0.77.14, replacing
only the two worklist modules and version declaration. The exact manifest and
deployment receipts are under
`D:\Blender\Projects\Character\X\validation\animation_single_table_20261010`.
Save the artist `.blend` before reopening Blender or reloading the add-on to load
the new interface.
