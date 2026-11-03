# Character Designer 0.77.146 — arm FK controls

Arm FK now has selectable upper-arm, forearm and wrist rings on the existing
native FK bones. The rings retain the existing transform channels and animation
paths. A short hand bone uses its forearm's ring radius. Existing artist shapes,
custom-shape transforms, display animation and generated styles keep their
ownership protections.

An explicit successful arm IK/FK button press hands selected inputs to the
corresponding usable wrist control. Unrelated selections and unavailable
destinations are preserved. Frame following, pose application and normal keying
do not perform this handoff. Native Rotation keying continues to use the current
editable Action and slot.

New generation and selected-part rebuild include the wrist ring. Body Setup
Update can extend an older valid FK record without rebuilding its limbs. No FK
bone chain, constraint, automatic bake or per-frame handler is added. Leg display
behavior is unchanged.

The release inherits the complete approved 145 projection and overlays eight
function bodies, three helpers, one selection module and release metadata.
Other current canonical candidates, including plugin cleanup, are excluded.
Function spans preserve all other inherited bytes and their AST. The native
compatibility fixtures cover real Rotation keying, Original transitions,
CurrentRig admission, selected-part preservation and failure recovery.

At 1,000 and 110,000 keys, the new steady-state arm display and selection paths
performed no Action/FCurve traversal. These are scoped operation measurements,
not artist playback FPS or an end-to-end IK/FK matching benchmark. Wrist-reset
145 performance and acceptance remain separate inherited evidence.

Source/disk installation, live function adoption and owned widget creation are
recorded separately in X `validation/fk_controls_20261102`. The narrow installer
defers widget changes during Original, saved native display views, Rest/Base/
Natural tests or Edit Mode. It never performs a mode switch, pose trial, scene
save, preferences save or full reload. Old failed harness runs, safe rollback
receipts and candidate exclusions remain pinned in the acceptance chain.
