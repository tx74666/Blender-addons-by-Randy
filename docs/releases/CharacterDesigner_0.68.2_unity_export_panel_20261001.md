# Character Designer 0.68.2: Unity Export panel

Date: 2026-10-01. Local source, package and deployment update only; no Git
commit, push, pull request or published release.

The default Unity Export panel keeps Main Rig, Folder, Name and the export
button visible. The native Folder picker is the only folder icon on that row.
Open Folder is a labeled secondary action inside Warnings. Object totals appear
once, in the Objects disclosure heading. Objects, material choices and Warnings
start collapsed; explicitly saved foldout choices remain saved per rig.

Use Simplified Materials opens a compact checklist of materials used by included
meshes, plus retained export choices. Turning a material off does not make it
disappear from the list while it is used. Use Original changes only that
material's export preference. Existing validation, Undo and temporary-snapshot
conversion remain unchanged; the original shader is never rewritten.

Success uses one brief notification. Failures, cancellation and active progress
remain visible. Actionable warnings, the single Open Export Report button and
Open Folder share the Warnings disclosure. Expected binding skips remain report
notices. Forearm-correction details remain in the report; runtime-prefab guidance
is added there instead of occupying the main panel.

The production Unity importer currently returns no validation receipt to
Blender. The report still states that Unity import has not been verified.
The existing Unity developer validation menu runs fixtures and fault-injection
checks; it is not substituted for ordinary export verification.

This package includes the previously prepared 0.68.1 packet-reuse and streamed
publication changes. A compatibility path preserves Link import when the older
animation module is still cached during a file-only update; a newly loaded
module retains the single-parse path and all existing identity/hash checks.

Validation:

- Nine lightweight panel/report checks pass against the actual Python source.
- Eight packet-reuse/publication/compatibility checks pass.
- Fourteen existing Animation Link checks pass.
- Python syntax and Git whitespace checks pass.
- An independent source review confirmed the dynamic Operator tooltip API
  against Blender 5.2's bundled operator sources.

Local deployment:

- `dist/character_designer-0.68.2.zip`: 121 shipped files, verified by the build
  tool against canonical sources.
- Blender 5.2 user installation and X's `addons/character_designer` copy both
  match canonical sources: 121 files checked, zero differences per destination.
- Deployment backup:
  `C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20261001-155903-c3cbdbad`.

The native Blender UI/RNA test has been updated but was not run in this pass.
No new Blender process or Unity operation was started while the shared heavy
work slot was reserved for Building/Unity. Local file deployment does not reload
the two open Blender sessions. Save work and reopen Blender normally to activate
the updated classes and new material-disclosure property. The live Builder6
scene with unsaved changes is preserved.
