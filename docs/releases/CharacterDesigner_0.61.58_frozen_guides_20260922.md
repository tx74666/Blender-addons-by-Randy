# Character Designer 0.61.58 — frozen guides during editing

Sliding an edge loop used to invalidate the Start/End display cache through a
dependency-graph listener. The next redraw rebuilt a validated frame, checked
the mesh stamp, ran finger adaptation and could store a red reference error.
Yellow marks already reused cached buffers, but this separate reference path
still caused repeated work during editing.

Start/End now uses saved path/normal data only, including cold display after
reload, undo/redo, mode changes and Shape Key editing. Finger reference and bend
listeners are no longer registered. Mesh edits do not schedule validation,
adaptation, display rebuilds or error writes. Bright-yellow marks remain fixed
until Mark replaces them, and all guides use the Capture Detection master eye.
Saved legacy Start markers and Show also avoid live geometry reads.

Capture and explicit Recheck own reference updates. Existing Mark/Align and bone
operations retain direct current-geometry checks and rollback; saved display
frames are labeled display-only and never become a write-validation shortcut.
Reload/load clears the known obsolete geometry-monitor warnings, retaining
reference records, confirmation, bone bindings and marker metadata. Pairing and
unrelated explicit-operation warnings are retained.

39 checks passed on Blender 5.2.0 in disposable fixtures: 8 saved-guide checks,
5 shared-cache checks, 8 batch/edit/load checks, 6 yellow-marker cache checks,
8 bone-alignment checks and 4 real registration/reload checks. Both reference
and bend tests perform 25 actual BMesh updates, preserving cached visuals and
saved metadata with all geometry-read/adaptation entry points forbidden and
their call counts checked. Cold restore, hidden/side switches, active Shape Key
edits, lifecycle callbacks and an actual temporary blend save/reopen are covered.
These checks establish zero reference-revalidation work during editing; they do
not claim an overall viewport FPS independent of Blender/modifier costs.
