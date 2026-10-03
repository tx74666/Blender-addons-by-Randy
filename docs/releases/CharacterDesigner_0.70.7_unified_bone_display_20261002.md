# Character Designer 0.70.7 — Unified Original / Controls

Bone Display now has two text-only mode choices: Original and Controls.
Original shows native Body, Hair and attached Dress bones together. Controls
restores the saved display and control configuration while preserving the body
pose. Body / Hair / Dress share one text-only row; blue means visible. Clicking
an already-active mode does not replace the recovery snapshot.

Body Controls is renamed Bone Setup. The mode switch is centralized in Bone
Display. The duplicate eye icons, Hair/Dress bone icons, Show All Controls and
Weight-page Original display button are removed. Edit Weights and Back to
Original remain reachable during the native session.

Attached dress views are captured independently and restored using persistent
Blender object references. Rename, save/reopen and failed return preserve the
correct association and all current views. Captured skirt setup cannot be
rebuilt, removed, reattached or reorganized until Controls is restored. Other
characters and unattached skirts are excluded.

Native Body posing retains the existing validated constraint suspension and
pose-preserving return. Hair and Dress use their existing native weighting-bone
display semantics; their constraints and pose channels are not modified by the
display switch.

Validation on 2026-10-02:

- 11 Original-mode Blender checks passed, including unified group isolation,
  explicit mode idempotence, foreign-character isolation, persistent renamed
  dress references, structural guards and complete display rollback.
- 6 UI-page Blender checks passed.
- Release archive verified: 130 shipped files.
- Local Blender 5.2 installation and X validation copy match canonical sources
  with zero different files in deployment --check.
- Refreshed the running Cosha scene, confirmed Original displays all three
  groups and Controls returns successfully with the same visible pose.
- Saved D:\Blender\Projects\Character\X\X.blend in Controls mode; Blender
  displayed Saved "X.blend" and cleared the unsaved title indicator.

Local source and deployment only; no Git commit, push or publication.
