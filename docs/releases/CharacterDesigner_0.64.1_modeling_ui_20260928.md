# Character Designer 0.64.1 — Modeling navigation and panel hierarchy

Local delivery, 2026-09-28. No commit or push. Existing unrelated modifications preserved.

## Result

- First navigation row: Modeling / Weight / Rig. Second: Animation / Miscellaneous.
- Modeling contains Curve Tools, Shape Key, Build Symmetry, Reference Views, in that order.
- Unity Export remains in Miscellaneous.
- These five tool panels are children of Character Designer; collapsing the main panel collapses their contents too.
- Modeling is the new default page. Its RNA value is 7; all existing values are unchanged. The legacy Hair route now opens Modeling / Curve Tools / Hair.
- Shape Key's operator poll moves with its panel. No modeling, rigging, weighting, curve or export algorithms changed.
- The temporary Controls / Weights return panel retains its existing independent placement so the active weight-edit workspace can always be exited.

## Scope and validation

Six runtime files differ from immutable 0.64.0: `__init__.py`, `ui_constants.py`, `shape_key_tools.py`, `delta_symmetry.py`, `reference_views.py`, `unity_export_ui.py`.

Actual serial Blender 5.2.0 LTS checks passed:

- UI pages: 6 checks, including parent hierarchy/order, five-page routing, legacy Hair, enum compatibility and scene/context preservation.
- Curve modes: 13 tests.
- Shape Key tools: 10 tests.
- Rig hierarchy: 3 scenarios.
- Unity Export UI regression script, including separate rig destinations across save/reopen.
- Independent old-version fixture hotload, then unregister/register and route checks.
- Independent code review: no blocking findings; registration order and Shape Key operator route verified.

Deployment updated 6 files in each of the Blender user installation and X validation copy. `--check` reported all 113 files matching canonical sources in both destinations.

The current Blender session was already running 0.64.1 when the guarded live updater checked its baseline, so it did not reapply a hot patch. Read-only validation confirmed the current enum, parent panels and routing. Actual GUI clicks verified Modeling's four tools, main-panel collapse/expand, and Unity Export under Miscellaneous. The sidebar was left on Modeling. This task did not save X.blend or invoke modeling, weight, pose or export operations on it.

Not reverified: algorithms unchanged by this UI update, arbitrary third-party layouts, and other Blender versions.

## Snapshot

- ZIP: `D:\MyRepository\Blender-addons-by-Randy\dist\character_designer-0.64.1.zip`
- SHA256: `759cbd7c6b6da6bd107577410f4d8c8dc1cdc93b688c4d02ba4e60faafff2582`
- Evidence: `D:\Blender\Projects\Character\X\task_artifacts\modeling_ui_20260928`
- Exact runtime manifest: `release_snapshot.json`; delta: `runtime_diff_from_0.64.0.patch`; live verification: `installation.json`; fixture-only patch verification: `installation_dryrun.json`.
- Previous installation backup: `C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260928-100313-a1c849ff`.
