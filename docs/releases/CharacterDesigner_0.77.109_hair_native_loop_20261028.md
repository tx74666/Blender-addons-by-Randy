# Character Designer 0.77.109

Hair preview now accepts the verified inactive Auto-Rig Pro layer callback and stays active when native playback wraps from its exact last frame to its first frame. It reseeds only its own Hair solver; the existing callback, timeline and ownership protections remain in effect. Hair remains a Blender preview, with existing Skirt Automatic/Manual modes preserved.

The complete approved source is X's `validation/animation_physics109_20261028/stage109_01/projection.json`, SHA256 `fccb53b8624494bc511e2e62f49297e616bdf853de62b4ff8f8e34de17659043`: 225 files / 193 Python files, three changed and 222 unchanged from108. Build and both official Blender5.2/X deployment checks passed with zero differences. Continue from that projection, excluding paused canonical candidates.

Targeted ARP native checks, installer rollback cases and GUI loop cases passed. The current artist preview survived two natural31→1 wraps with its Hair source geometry/weights unchanged. A separate read-only observation confirmed meaningful owned-bone basis changes across four consecutive forward frames while that same preview remained active. The loop-end basis reset is recorded separately and is not used as strong motion evidence.

The artist runtime was already109 before the attempted update; refresh timing and initiator are unknown. That attempt refused before mutation. A fresh read-only audit verified current Hair2 code and the loaded signature. No identity-preservation claim is made across the unknown refresh. This workflow did not save the formal scene or preferences, operate Unity, commit or push. No new FPS, collision quality or export guarantee is claimed.

Final acceptance: [X acceptance summary](D:/Blender/Projects/Character/X/validation/animation_physics109_20261028/acceptance_summary109.json), SHA256 `acea3ea0b9eb39e8ff6962311d6f9d6dbaf046ae52c02da3f52767679b741101`. Raw failures and scope limits remain in X's `README109.md`.
