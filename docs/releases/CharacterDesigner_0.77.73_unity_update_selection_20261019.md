# Character Designer 0.77.73: Unity update selection

Mesh and Rig are separate update-authority groups in the Unity Export panel.
The group title expands its contents; its separate native checkbox selects.
Mesh rows remain in a stable order and show a selected count and Partial state.
Select All and Deselect All affect both groups. Native checkbox brushing works
on the visited mesh rows. Update choices can be changed in Object or Pose Mode;
Edit Mode is read-only to preserve the mesh Undo history. Undo/Redo applies to
individual checkbox changes; a complete drag stroke is not one atomic Undo step.

The saved selection is independent of legacy export exclusions. A full update
collects the complete eligible character through a temporary configuration view
without rewriting those old exclusions. Material discovery uses the same full
context. Body-derived file naming and the accepted 0.77.72 range/Control-click
selection behavior are retained.

Mesh-only updates require a verified compatible Unity receiver. The provider
exports complete FBX context into the existing Unity project's private .codex
inbox, with explicit selected persistent part IDs, a pinned accepted baseline,
and a receiver-state revision. Candidate publication preserves the accepted
FBX, manifest and .meta. Its status is Pending Unity review; Unity review and
application are separate operations. Until the receiver passes its native
verification and publishes its capability file, the mesh-only button is disabled.
Partial Rig updates are refused. Unity still validates the actual imported
skeleton, material slots and retained dependencies; the first receiver slice
refuses Body updates that need Forearm correction support.

An existing partial-update ledger blocks subsequent full replacement until a
supported reconciliation exists. Both direct API admission and publication check
this guard, including nested Assets directories and a ledger appearing while a
worker or temporary payload copy is running. A full export outside a Unity
project retains its existing behavior.

Evidence is under
D:\Blender\Projects\Character\X\validation\unity_update_selection_20261019.
The accepted release projection is recorded in acceptance_summary.json. Testing
uses private inputs, enabled trusted script execution and separate CONFIG
folders. Source-only tests, native provider export, UI routing, lifecycle and
physical/native event checks have separate records and limits; a provider pass
alone does not claim Unity acceptance.

The current artist session is not automatically refreshed by installation.
Local source/build/deployment changes only; no Git commit or push.

Validation cleanup limit: initial GUI01 used the default temporary recovery
location on exit and wrote Temp/quit.blend; its previous contents were not
pinned and cannot be reconstructed from this run. The artist session remained
open. Later native GUI runs isolated TEMP/TMP, CONFIG and recovery output before
startup. Details: gui01_recovery_limit.json.
