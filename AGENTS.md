# Add-on source and deployment

- The only development sources for RR Helper and Character Designer are the
  packages under this repository's `addons/` directory.
- Make runtime code changes here first. Do not develop in the Blender user
  installation, X's validation copy, or an old packaging/staging directory.
- After a runtime change, perform relevant checks, update the add-on version
  when releasing, and run `python tools/build_releases.py`.
- Deploy validated sources with `python tools/deploy_local.py`. When X's
  validation copy is needed, pass `--project-addons D:\Blender\Projects\Character\X\addons`.
  Finish with the same command plus `--check`; report any mismatch.
- Before deploying, confirm the Blender executable/version actually in use.
  The default 5.2 target does not update a separately installed 5.1 add-on.
  Also deploy to the verified active version with --blender-version and
  verify that same target with --check. For apparent regressions after
  reboot, check the .blend opening association before assuming data loss.
- A local deployment is not a GitHub upload. Report whether changes are local,
  committed, or pushed; follow the user's authorization for Git operations.
- Blender installation directories remain deployment copies. Do not describe
  them as junctions or physically shared files unless that has been verified.
- For Finger Joint geometry work, read `docs/finger_joint_tool.md` first.
  Preserve Shape Keys and existing weights, keep topology validation
  conservative, and do not add runtime AI dependencies.

# Trusted X validation (user authorization, 2026-10-18)

For the user's own X files and agent-written local validation scripts, the user
has authorized automatic execution without repeated prompts. The saved Blender
5.2 Auto Run Python Scripts preference was already enabled; explicitly disabled
validation launches were overriding it. Use
`tools/run_trusted_blender_validation.py` (`--enable-autoexec`) and load these
trusted inputs with `use_scripts=True`. Keep historical hash-pinned harnesses
unchanged and identify new trusted derivatives in their evidence. Do not save
factory-startup preferences over the user's preferences. The X project's
`AGENTS.md` and `validation/autoexec_preferences_20261018` record this preference
and the isolated native verification.

Every new agent-owned Blender validation process must have a dedicated, fresh
`BLENDER_USER_CONFIG` directory, including native GUI, background, and
factory-startup runs. Use `tools/run_trusted_blender_validation.py`, which creates
and records this directory inside X evidence. `--factory-startup` alone does not
isolate Save Preferences, and disabling preference autosave does not block an
explicit native preference save. For a saved-profile check, use `--user-startup`
and, when needed, `--user-config-source`: the launcher copies and pins
`userpref.blend` and optional `startup.blend` into its isolated CONFIG instead of
writing to the source profile. Keep the artist's real preferences read-only;
test launches do not authorize copying sandbox preferences back. Preserve the
existing trusted-script authorization above. See
`docs/trusted_blender_validation_preferences.md` and X's
`validation/pole_preferences_recovery_20261018/README.md` for the incident and
verified recovery limits.

Preference hashes identify a snapshot, not a permanent expected value. A normal
artist session or add-on can legitimately autosave updated authentication or
extension state. Before diagnosing another profile loss, pin the current file
and compare saved add-on membership, keymaps/keyconfig and relevant preference
fields with the recovery evidence. Do not overwrite the current profile merely
to regain an older accepted hash; preserve current legitimate settings and
retain a backup before any separately authorized recovery.

# Preserve artist-painted weights (user request, 2026-10-18)

- Character Designer Generate/Regenerate/Rebuild Controls must inherit current
  authored mesh weights, vertex-group names/bindings and group locks. Rebuilding
  controllers is not authorization to run Bind Weights, automatic weighting,
  whole-mesh transfer, normalization or a restore from an older weight backup.
  The artist's latest completed/confirmed paint is authoritative; subsequent
  repainting supersedes an earlier checkpoint. Do not treat a first pre-bind
  backup as the artist's permanent best version or silently restore it.
- Preserve weights on every surviving, identified vertex, including unrelated
  body parts. For an explicitly requested topology/weight repair, interpolate
  only genuinely new vertices from the corresponding local surface or connected
  neighborhood; respect body side, region, mesh boundaries and locked groups.
  A nearby point across a gap, such as the other leg or a shoe, is not a valid
  donor merely because it is spatially close. Do not recompute existing paint.
- Equal vertex counts or indices alone do not prove correspondence after mesh
  editing/reordering. Retain a recoverable current checkpoint before weight
  writes and establish correspondence before restoring; report ambiguity while
  keeping existing data intact. Keep full checkpoints compressed locally and
  concise hashes/counts in reports.
- These are preservation requirements, not a claim that a general new-vertices
  interpolation feature already exists. Current controller rebuild does not
  perform weight transfer. X evidence and live checkpoint are indexed under
  `validation/weight_preservation_20261018` in the X project.

# Generated display names

- Keep generated names that artists see readable. Use a purpose/source label
  and a short sequence number when names collide; store hashes and UUIDs in
  internal metadata instead of persistent object, mesh, or bone display names.
- Preserve valid Unicode without splitting UTF-8 characters. When renaming old
  generated data, keep internal identities stable and synchronize native and
  saved string references. Preserve names chosen by the user.

# Personal node library

- Maintain personal reusable node assets in this repository. Read
  `node_library/README.md` before changing or adding one. Build and verification
  sources live in `tools/randy_node_assets/`; `node_library/assets/` contains the
  current distributable assets, not the live Blender installation.
- Every node addition or change must update its source, versioned entry in
  `node_library/manifest.json`, English documentation, and
  `node_library/CHANGELOG.md`. Refresh the affected asset and verification evidence
  when its behavior, interface, or catalog changes. Keep source hashes current.
- Keep new node names, sockets, descriptions, and catalogs in English. Ring Mask
  uses the top-level `Textures` catalog. Preserve existing geometry catalogs and
  identifiers unless the user explicitly asks to migrate them.
- Run appropriate Blender validation in an isolated background process, never
  on an unsaved user scene. Metadata-only edits can use documented graph equality
  against a previously validated build. Finish with
  `python tools/randy_node_assets/verify_library.py` and relevant deployment
  `--check` when deploying locally.
- Third-party reference packs stay outside the published library; record their
  source/version separately. Do not copy unrelated assets or private scenes into
  a node update.
- The user handles GitHub Desktop Commit and Push. Prepare local changes and
  report them; do not commit, push, create a PR, or publish a release unless the
  user explicitly requests that action later. Local deployment is not an upload.

# Performance records and component ownership

- X owns Character Designer scene integration and performance records;
  Builder6 / Build WIP owns RR Helper. Code for both remains in this repository.
- Before further optimization, read the existing central entry
  `D:\Codex\資料庫\电脑与工作环境\Blender性能優化台帳.md` and the component's
  linked evidence. Reuse verified conclusions and investigate changed code or
  new symptoms; do not repeat a full old investigation by default.
- Record date, source/runtime versions, actual model and reasoning effort when
  verifiable (otherwise unrecorded), comparable measurements, validation limits,
  deployment/refresh/save state and next triggers. Keep raw project evidence and
  release notes in their owning locations; Codex Console indexes them in the
  existing database rather than creating a duplicate database.
