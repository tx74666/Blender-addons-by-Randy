# Character Designer 0.77.122 — native Rest/Pose panel

The Animations panel previously exposed the Base Pose authoring test where a Rest
view toggle was expected. Its stricter test admission could disable the button
while native Rest/Pose display was available. The main row now uses the native
Rest Pose/Pose operator. Base Pose remains under Advanced with its existing
explicit return and draft-recovery routes. Repeated Scene, Character and Rig
identity labels are removed; configuration still resolves the existing Main Rig.

Only draw_worklist changes at runtime. Native display/test services, ownership,
recovery, physics, Pose Blend/Offset, Body/Forearm and performance code are inherited.

Validation: 130 focused pure tests; 12 public native Rest/Pose transitions in
Object, Pose and Mesh Edit contexts; two actually reached narrow-update fault
rollbacks and a success, with 335 RNA registrations retained. Native01 failed
before any cases because its harness used obsolete Bone.select; the separately
pinned native02 derivative corrects that API use without weakening preservation.
The final ZIP has 228 exact files, and official dual 5.2/X deployment reports zero
differences.

Artist32760 was already running cold122 when observed, before this agent's planned
update. A fresh read-only audit confirmed all105 UI definitions, all228 package
files/196-Python signature,4598 callable bindings and335 RNA registrations. Native
pose/Rest/bindings/physics and OBJECT/Shoes/selected[]/frame14/dirtyTrue were exact
within the audit. The refresh initiator/time is unknown; no cross-refresh author
identity or full artist-array claim is made. The Action Editor was restored.
No agent display/physics/Pose/recovery action, hot update, reload or save occurred.

X and X2 received user-authorized scope/version/window notices. No acknowledgement
was recorded at completion. Shared release files were double-fingerprinted before
the short window, no in-flight122 package/source write was observed, and their
unapproved physics/hair candidates were excluded. Future work extends the complete
122 projection and allocates a later free release version.

Evidence: `D:/Blender/Projects/Character/X/validation/animation_panel_rest_20261029/acceptance_summary122.json`.
Projection SHA256: `3623d4f8c15146db73fd33cab325dc4a9685926196ee49a4d0a90f335a58e5a6`.
No full artist dynamics/FPS/loop/collision acceptance, Unity action or Git upload.
