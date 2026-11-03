# Character Designer 0.77.139 — Neutral IK wrist reset

Alt+R on one owned IK hand control, and the existing Reset Target Rotation
entry, return the visible wrist to native/FK rotation-clear orientation under
the current solved forearm. Previously, zeroing an Auto target removed its
additional offset while retaining the authored native FK wrist bend.

The reset solves only the target's active rotation representation. It preserves
native FK inputs and keys, target position/scale, arm and Pole, and the persistent
IK graph. Target angles may remain nonzero because existing animation keeps its
original meaning. The reset does not apply the complete Relax Pose or finger curl.

Auto Key off preserves Actions exactly. Auto Key on edits only target rotation
in the current Custom Action/slot, retaining its row and Current-Rig checkpoint.
Available/Needed options and keyframe type are honored. Available requires
existing rotation channels. Source, Original, FK/blended limbs, drivers, locks,
ambiguous owners, active sessions/physics and unsupported key policies refuse.
Mixed Alt+R selection and pose mirroring refuse; other bones retain native Alt+R.
The existing Foot Reset path is unchanged.

Complete244/212 Python files extend complete138 by3 changed existing files,
3 new files and238 exact inherited files. Projection:
`D:/Blender/Projects/Character/X/validation/wrist_neutral_20261102/stage139_03/projection.json`
SHA256 `5ce57b769adb49a73d4797e47da2f41fdcd7f21f1168fae8f2c675f3f34ef964`.

Validation includes32 focused pure checks,48 private native service checks and
the retained80-case baseline diagnosis. Native tests cover real target/key writes,
rollback, supported rotation modes, Source ownership, default key policies and
genuine Current-Rig ownership. Nonuniform parent scale/shear is a separately
retained quality-refusal/rollback case; unit-scale Current-Rig proof is distinct.
Earlier product and harness failures remain with their original reports.

Official ZIP and both244-file deployments/checks match. Private native-WM6 and scoped updater7 passed; artist25544 adopted only the reviewed code/UI with exact bounded witnesses. Final acceptance:
`D:/Blender/Projects/Character/X/validation/wrist_neutral_20261102/acceptance_summary139.json`.

Private native-WM simulation is not a physical keyboard or artist pose trial.
No artist scene/preferences save, restart/full reload, Inputs close, Unity/Git,
FPS measurement or monitoring is part of this change. The existing playback-FPS
investigation remains closed. Exact model variant/reasoning effort unrecorded.

The separately validated complete-field updater05 reader completed four full artist reads of 23 Actions,22 slots,26,566 curves and3,187,370 keys (zero samples). Buffers are bounded to8MiB. The effective revision grants a cooperative120-second deadline to EACH read, not a total update deadline. Observed per-read times: 87.999s, 89.092s, 85.063s, 73.176s. Reader pure12/native4, installer7 and real right-area3 checks passed.
The pinned adoption-gate02 top-level30s wording is inherited historical text; its artist_read_limit_revision.max_read_seconds=120 is the explicit effective revision. The gate file remains unchanged.
Artist update01 failed before code changes at the old2m cap and its Python-wrapper area restore guard refused. Update02 also remained uncommitted: its30s read stopped after1,369,471 keys/10,004 curves; that partial read was never accepted. Only actual update03 success completes this delivery. Probe01 failed before creating JSON; its source and reported failure limitation remain linked, while probe02 supplies its own bounded observation.
All earlier product/harness/GUI failures remain failed in their original artifacts. The nonuniform-parent quality refusal/rollback and the separate unit-scale Current-Rig positive fixture retain their distinct scopes.
