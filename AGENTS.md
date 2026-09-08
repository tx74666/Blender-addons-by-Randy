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
