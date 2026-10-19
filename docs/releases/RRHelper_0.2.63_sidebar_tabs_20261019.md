# RR Helper 0.2.63 · Miscellaneous and Sidebar Tabs

The former Layout page now reads **Miscellaneous**. Its stored `LAYOUT` enum
identifier and ordinal are unchanged, and the existing Layout Snapshot actions
are retained. **Sidebar Tabs** contains a selection list, up/down arrows and a
refresh button. Each move is applied and remembered in a dedicated small CONFIG
JSON; it does not save or replace `userpref.blend` or write scene data.

The implementation is independent of Simple Tabs. Its list workflow was
reviewed as a reference, but no third-party source or package is included.
Only RR Helper's own empty headerless positioning Panels are registered or
removed. Original Panel class identities, order values, callbacks, owner
information and registration hooks remain untouched. Original context/poll
conditions control whether a category's positioning panel appears.

Item, Tool and View retain a fixed prefix. Categories already led by a native
headerless order-zero Panel retain their native position and are omitted from
the movable list. Owner-filtered workspaces retain Blender's original order;
their move buttons are disabled. This is list sorting, not native tab dragging.

## Validation

Evidence: `D:\Blender\Projects\Character\X\validation\rr_sidebar_tabs_20261019`.
Blender 5.2.0 LTS, build `fbe6228777e7`; every new validation process used a
dedicated isolated CONFIG with trusted auto-execution enabled. The artist file
was not opened by those generated-scene checks.

- Final frozen native suite: **14 passed**, including exact original Panel and
  EditMesh/weight/selection preservation, saved-order restoration, owner filters,
  context visibility, dynamic replacement/unload, corrupt configuration and
  failures while registering/removing owned Panels or writing the order file.
- Full RR Helper registration/disable/re-enable/refresh regression: **276 checks
  passed**. The final subsequent rollback hardening is covered by the frozen
  native suite; it does not change the normal empty-order lifecycle path.
- The prepared bounded adoption into an old 0.2.61 session passed in a generated
  EditMesh fixture. Four function identities, authored mesh state, original Panel
  metadata, RR globals and isolated preference file remained exact; the updated
  lifecycle also cleaned the added UI module correctly. This is preparation for
  the current session, not a claim that the artist session was updated.
- Earlier original-Panel re-registration was **rejected**, despite its 11 core
  tests passing: GUI inspection showed a headerless category ignored the desired
  order. Its source, tests and reports are retained as rejected evidence.
- GUI interaction and live adoption are recorded separately; native `bl_order`
  values alone are not treated as visual tab-order verification.

The final frozen GUI interaction and adoption into the current artist session
are pending. Input protection detected concurrent desktop use, and the user
answered “过一会儿” to the requested short input window. All owned test windows
were then closed normally. This release has not changed or saved the current
artist `.blend`; installed-source verification is distinct from live adoption.

## Local release

The frozen projection contains 30 files. Manifest SHA256:
`c0875b28653cd73c52d00df838d9411cd9ad4b099b10232cd9c32d36e3fd0374`.
ZIP SHA256:
`d7729d98e26450e5b8a0788be3d3e279c92f835facc0e525552c8e0becf33520`.

Official build and deployment completed for both Blender 5.2 user addons and
`D:\Blender5.2\5.2\scripts\addons_core`; both `--check` reports show zero
differences across all 30 files. The Character Designer package is excluded.
Local changes only; no commit, push or publishing was performed.
