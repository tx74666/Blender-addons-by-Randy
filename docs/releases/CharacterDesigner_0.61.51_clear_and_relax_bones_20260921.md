# Character Designer 0.61.51 — clear stale finger state and relax selected bones

Clearing a captured finger now clears both sides' guides, errors and saved bone
bindings. Both the visible X button and the legacy definition CLEAR entry point
use the same operation. Empty fingers do not display stale census warnings, and
Recheck skips their empty slots. Other captured fingers retain their errors and
guides. Capture still checks the real mesh's symmetry; clearing does not change
geometry or discard that evidence.

Relax Thumb is replaced with Relax Bones. It uses the actual bone chains selected
in Armature Edit Mode, regardless of the active Basic Setup digit or any missing
or invalid capture. Arbitrary bone names and multiple selected unbranched chains
are supported, including non-deform bones. The existing polyline is redistributed
without forcing a straight chain. Root, tip, rolls, connections and selection are
preserved. Repeating the same operation retains the result rather than gradually
straightening it.

With Armature X Mirror off, only the selected chains are changed. With it on,
existing complete counterparts receive the reflected source result. Selecting
both sides does not run the pair twice: the selected active bone chooses the
source, with the named L side preferred if no selected active bone exists.
Missing counterparts are reported and never created. Asymmetric fixed endpoints,
gaps, branches, locks, unsafe poses or unrelated connected children that would
move are rejected before keeping any changes. Failed writes roll back the whole
operation. No mesh or weight changes are made.

Optional Basic bone-binding metadata is refreshed only when its previous
signature was valid. Unrelated malformed or noneditable metadata is ignored.
The generic edit-rig context now restores the active bone before selection flags
so an active-but-deselected bone stays deselected.

Validation: serial Blender 5.2 factory-startup tests, one thread. Relax Bones 13,
Clear 5, Align 2, Roll targets 3, Basic bank 7, Definition 10, reference cache 5,
retirement/lifecycle 4: **49 passed**. No Git commit or push.

AST and diff whitespace checks pass. The release archive was built and both
the Blender user add-on and X validation copy were deployed from canonical
sources: 99 shipped files, zero differences. Previous file backups are in
%LOCALAPPDATA%/CodexBackups/addon-deploy/20260921-185826-5c5c3600.
