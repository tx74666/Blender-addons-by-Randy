# Character Designer 0.77.86 — animation list sync

The Animation panel replaces Import Prepared with Sync List. An existing
X scene can reconcile a changed Unity workspace without rebuilding its
artist model, switching scenes or overwriting a Custom Action.

Sync previews the complete prepared Unity list, preserves authenticated
existing work, and adds missing Actions through the existing serial queue.
Extra or rebuilt work requires an explicit Retire and Sync / Back choice.
Retirement preserves complete row metadata and Source/Custom pointers in
Retired Animations; Advanced exposes Restore. Other project Actions are
outside this membership operation and remain intact.

The confirmation binds current membership, Custom content and physical
workspace/model/Link/packet files. Main Rig bone/control mapping remains
strict. Changes after review, missing files, mismatched targets, unsafe
native state and invalid input fail before accepting a source. Cancellation
and failure preserve completed work and permit retry. Export Changes and
Unity's explicit Collect / Preview / Apply remain separate actions.

Validation and deployment evidence are recorded in the X project:
`validation/worklist_sync_20261022/`. This is a local source/release change;
no Git commit, push or public release is performed.
