# Explicit export base pose for ordinary sparse Actions

Ordinary skeletal Action export now retains explicitly captured unkeyed inputs
on the editable Action. After posing the intended baseline, choose **Capture
Export Base Pose**. This stores export inputs without adding keys, changing the
current pose, selecting another animation, or saving the blend. Save normally
when you want to retain that authored baseline.

Later exports apply the captured values only in their disposable worker
snapshot. Unkeyed artist posing no longer silently changes the exported clip.
Capture again deliberately to include a different unkeyed pose. The reference
object transform is evaluated at the explicit export Start Frame, rather than
depending on the timeline frame from which the export was started. NLA remains
excluded from this one-Action export; the artist's NLA state is retained.

The saved record is checked against the exact Object slot, native Rest and
bone inheritance, enabled channel schema, internal constraints/simple drivers,
and export-input schema. Editing key values keeps the baseline usable. Adding
or removing keyed channels or changing the supported rig structure requires a
new deliberate capture. A copied Action can reuse a record only when these
compatibility checks still pass. Missing, damaged or incompatible records fail
before export. Fully keyed plain FK inputs with neutral object deltas can
continue without a retained baseline.

Capture is available for the active local ordinary Custom, including a new
ordinary Custom. Source, another inactive Action, Base/Rest trials, Original,
Composition and separate Rigging inputs are protected. Playback and physics
must be stopped before capture. Export does not automatically write a baseline.

This first contract supports a bounded local same-rig graph. External input
objects, object parenting/constraints/modifiers, armature-data animation,
weighted/multiple Action layers, oversized mapped driver curves, non-simple expressions
and nested constraint/driver collections require a separately baked contract.
They fail closed. It is not a general bake of arbitrary Blender dependencies.

The established exporter worker is unchanged. A new ordinary-export wrapper
validates and seeds the private snapshot before calling that worker. Composition
and its fixed-byte worker pins remain separate; the existing authenticated
Rigging export DTO continues through its original exporter path. Publication
records the applied baseline hash and fixed reference frame, and refuses a
worker result whose input receipt differs from the sealed job.

Verification records are kept under X
`validation/animation_export_base_20261102`. Early failures are retained there;
the final acceptance entry, when produced, identifies the passing input hashes
and remaining scope limits. This note alone does not claim artist refresh,
scene/preferences save, Unity acceptance, deployment or upload.
