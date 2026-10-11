# Character Designer 0.77.5 — Dress Wiggle / Manual

Released locally on 2026-10-09. The user explicitly restarted only the
Wiggle Bones Automatic / Manual choice and daily Dress display. The earlier
Cloth Goal remains cancelled; held Direct/Cloth development was excluded.

## Artist workflow

Open **Rig → Skirt** for a configured Dress. **Automatic · Wiggle Bones**
uses the verified, already enabled external **Wiggle 2 2.2.4** on the existing
PHYS chains. Play the timeline to simulate. **Manual** stops that owned
simulation and retains its current local PHYS pose and final DEF result;
the waist, hem and manual wire inputs remain editable and unchanged.

**Controls** shows the rings and manual handles. **Original** pauses the
owned Dress simulation and uses the existing pose-preserving native editing
transaction, showing weighted DEF and waist bones. An unattached Dress without
an organized Main Rig has Original disabled with an explanation.
**Advanced Inspection** is collapsed initially and exposes MCH / PHYS separately.
Visibility changes do not stop simulation. Legacy Cloth creation is kept behind
that advanced foldout; existing Cloth records are refused by the Wiggle adapter.

Automatic preference and tuning survive save/reopen. Save temporarily freezes
owned motion, then reseeds it from the current pose. Timeline wraps reseed rather
than disable Automatic. Original, model export and removal suspend only the
relevant owned sources. Removal stops the solver before deleting native bones.
Undo/Redo recovery handles restored native flags with lost Python sessions.

The adapter requires the verified backend's exact normalized source hash and
registered RNA/callback schema. It neither installs nor changes external solver
files. Blender 5.2 dynamic RNA is distinct from similarly named ID properties;
owned setter batches temporarily suppress the external UI propagation callback
and restore its exact identity in `finally`. Solver execution and evaluation
remain outside those batches. No external runtime dependency is bundled.

## Validation

All heavy runs were sequential isolated Blender 5.2.0 LTS processes with
factory startup, disabled autoexec and two threads. They never changed a live
artist window or saved the artist file.

- 8 native UI cases: owned layers, Original/Controls continuity, foreign rigs,
  Solo display recovery, failed-display rollback and disposable save/reopen.
- 13 native motion cases: real multiframe Wiggle, shared/separate rigs,
  freeze/resume, authored inputs/actions/weights/Rest, leases, unsafe graph
  refusal, failed-start rollback, persistence/tuning, restored-ID undo recovery,
  foreign simulation preservation, wraps, Original entry, scoped export barrier,
  and removal/rebuild. Synthetic rig construction suppresses the legacy solver's
  unrelated RNA-copy UI propagation callback before simulations are started.
- Existing Dress Original regression: 17 native cases passed.
- Existing Refresh Add-on regression: normal, stale module, failed enable
  rollback and retry all passed. The injected enable failure is expected.
- 8 lightweight UI cases passed. A native RNA probe confirmed independent ID
  storage, actual RNA setters/unset and exact callback restoration on exception.
- The saved real **X / Dress / CoshaRig** passed with **32 PHYS bones**. Maximum
  combined evaluated vertex/matrix difference on Automatic entry was
  **8.344650268554688e-7**; Manual freeze difference was **0**. Three solver frames
  produced a measurable **0.09663788974285126** combined coordinate/matrix delta.
  This is evidence of motion, not a physical accuracy or collision metric.
  Mesh data, weights, Shape Keys, Rest and Actions remained unchanged.

Raw evidence: `D:\Blender\Projects\Character\X\validation\wiggle_switch_20261008`.
The folder began on October 8; completion and deployment were October 9.
Model/reasoning effort: unrecorded; unavailable values were not inferred.
GUI Undo and a refreshed live artist session were not tested. Collision,
75-degree leg motion, Cloth and Unity physical output are outside this update.

## Release and scene state

The approved projection starts from the exact **148-file 0.77.4 ZIP** and adds
two modules. Its **150 files** change only `__init__.py`, `body_original_mode.py`,
`skirt.py`, `skirt_rig.py`, `skirt_shared_rig.py`, `skirt_wiggle.py`,
`skirt_wiggle_ui.py` and `unity_export.py`. Existing arm-mirror and animation
fixes are retained. Pending Dress files in canonical sources were not included.

Projection SHA256:
`8334311b56765dc353908259cc00319dd67c14a913dbf8fb1ac5940be742d69f`.
`tools/build_releases.py --module character_designer --projection …` built
`dist/character_designer-0.77.5.zip`. `tools/deploy_local.py` deployed this same
projection to Blender **5.2** and **X/addons**, then `--check` reported **0**
different files in both. Previous files are backed up at
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20261009-003454-3402e109`.

X.blend remained SHA256
`b975bdd885fe5d977e70816929cea0ef7badf11db988dbaacc5cfa0488c55496`.
No artist scene edits or saves were performed; disposable tests remain separate.
The final process check confirms the original artist Blender 5.2 process
**27676** remains open; no task-owned background Blender remains. The default
Blender bridge was unreachable, so this task did not refresh that artist session.
Save its current work, then use **Refresh Add-on** to load the installed 0.77.5.
All changes are local, uncommitted and unpushed.

The old chat **X**, ID `01a0f8d5-1398-7482-bbe4-d123cd51098d`, was read and archived
under the user's authorization. Its last three continue/start turns contained
no execution records. Its cancelled Cloth Goal was not resumed.

Next investigation triggers: a reproducible switch jump, lost owned state after
actual GUI Undo, failure to display a selected layer, or a changed Wiggle backend.
Any collision or Unity simulation work requires a separate explicit scope.
