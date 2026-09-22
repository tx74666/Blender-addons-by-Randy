# Character Designer 0.61.61 — joint alignment at the finger root

Align Joints could identify the correct bones but reject an already straight
chain with “A planned bone segment leaves the finger interior.” The local
finger sleeve uses an artificial cap at its proximal boundary; an existing
bone root in the palm can precede that cap. Redistributing two joints along
the same existing line therefore failed despite introducing no new bone path.

Alignment now verifies the new joint positions and retains spans covered by
the actual current head-to-tail bone segments. Collinear coverage may cross an
old joint, but cannot bridge a real gap or shortcut a bend. Changed paths still
require current interior proof. A changed proximal path can use at most three
complete adjacent quad bands; irregular palm topology is not globally searched.
Failures name the affected bone and distinguish an unverified root from a
surface exit. Fixed endpoints, Roll, current-side scope and atomic writes remain.

This change runs only on an explicit Align action. Frozen guides, cached GPU
batches, visibility toggles and the absence of editing-time geometry listeners
are unchanged.
