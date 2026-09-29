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
- A local deployment is not a GitHub upload. Report whether changes are local,
  committed, or pushed; follow the user's authorization for Git operations.
- Blender installation directories remain deployment copies. Do not describe
  them as junctions or physically shared files unless that has been verified.
- For Finger Joint geometry work, read `docs/finger_joint_tool.md` first.
  Preserve Shape Keys and existing weights, keep topology validation
  conservative, and do not add runtime AI dependencies.

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
