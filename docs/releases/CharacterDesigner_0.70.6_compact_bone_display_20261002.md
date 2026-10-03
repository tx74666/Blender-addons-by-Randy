# Character Designer 0.70.6 — Compact Bone Display

Body, Hair and Dress now share one horizontal row in Rig / Weight > Bone
Display. Hair Bones and Dress Bones remain available as adjacent bone icons,
with action-specific hover descriptions. Each group keeps its independent
enabled and visibility state. Existing Original mode routing is unchanged.

Validation on 2026-10-02:

- Built and verified character_designer-0.70.6.zip (130 shipped files).
- Deployment syntax checks passed; Blender 5.2 installation and X validation
  copy both report zero different files with --check.
- Refreshed the running X scene successfully and inspected the compact row at
  the existing sidebar width: all three labels and both bone icons fit.
- Saved D:\Blender\Projects\Character\X\X.blend at 18:45:55 Asia/Shanghai;
  Blender displayed Saved "X.blend" and cleared the unsaved title indicator.

This is a UI-only change. No additional background Blender process was needed.
Sources and local deployment are prepared; no Git commit or push was performed.
