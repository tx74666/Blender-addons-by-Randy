# RR Helper 0.2.64 · Enable sidebar reordering

Owner filtering in a workspace previously disabled both Sidebar Tabs arrows
with only a small message below the list. The reason now appears above the
list, with an explicit **Enable Tab Reordering** button. Clicking it shows all
add-on tabs in the current workspace and enables reordering. Other workspaces
and their allowed add-on lists remain unchanged. A failed enable operation
restores the workspace filtering flag.

Select a list row, then use the labeled **Up** / **Down** buttons. Each move
updates the actual sidebar and remembers the order in RR Helper's small CONFIG
JSON. Native or pinned categories remain outside the movable list. This uses
the existing owned positioning panels and preserves original add-on panels.

## Validation and local release

Evidence: `D:\Blender\Projects\Character\X\validation\rr_sidebar_order_help_20261019`.
Blender 5.2.0 LTS, build `fbe6228777e7`; generated validations use isolated CONFIG
directories and trusted script auto-execution. No real user preferences were
saved by the tests.

- Frozen native regression: **20 passed** (14 existing and 6 new). Includes
  current-workspace scope, registration/configuration failure recovery,
  successful native Enable and Move operators, EditMesh selection, locked
  weights, Shape Keys, UVs, colors and original panel identities.
- Full-package generated GUI fixture: the filtering notice and Enable button
  were visible; actual clicks unlocked the arrows. Moving Create above and then
  below RandomRealm changed both the list and the visible native tab strip.
  Built-in Item appeared below the add-on tabs in this fixture; no fixed-prefix
  visual guarantee is made for that built-in category.
- Projection contains 30 files, based on accepted 0.2.63 with only `__init__.py`
  and `rr_sidebar_tabs.py` replaced. Manifest SHA256:
  `c21df2404a292821c11a3fc908e095ac5c6f04a3fbe8f9aa44eab6b13804e86e`.
- Official build and local deployment completed for Blender 5.2 user addons and
  `D:\Blender5.2\5.2\scripts\addons_core`; both checks show zero differences.
  Character Designer is excluded. Local changes only; no commit or push.

Live-session adoption and save state are recorded separately in the evidence
acceptance summary. An installed source version does not identify every code
object already loaded by the artist session.
