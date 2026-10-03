# Animation Worklist

Available in **Character Designer 0.69.0 > Animation**. The real Walk_N and Idle
pair passed Blender import, independent editing, save/reopen, per-entry Sync,
Unity candidate preview and private-character Apply. See the [local release and
validation record](releases/CharacterDesigner_0.69.0_animation_worklist_20261001.md).
The existing single-Action workflow remains documented in
[Single-Action animation return](animation_roundtrip.md).

A workspace describes one Unity character and its Controller clips. The Clip
Browser lists that metadata; the worklist contains the clips you choose to edit.
Prepared entries share one independent Blender editing rig. Each entry has a
linked **Source** Action for comparison and a local **Custom** Action for editing.
Sync operates on one active Custom Action at a time.

The workspace producer for this integration is **RandomRealm2 > Character
Tuning**. The bundled standalone Unity Animation Exchange window retains its
single-Action workflow; installing that helper alone does not create a character
catalog or the workspace buttons described below.

## Connect and prepare clips

1. In Unity Character Tuning, select the character and use **Link Workspace**
   or **Refresh Workspace**. **Show Workspace** opens the folder containing
   `character_animation_workspace.json`; **Copy Path** copies that file's path.
2. In Blender **Character Designer > Animation > Animation Worklist**, click
   **Connect** and choose that workspace file. Check the displayed **Character**.
3. Expand **Clip Browser** and search by clip name or source name. Connecting or
   refreshing lists metadata; it does not import every animation.
4. Select the clip in Unity and choose **Link for Blender**, or **Rebuild Link**
   when its existing Link needs rebuilding. This prepares that clip's Link and
   sampled motion packet. **Show Link** locates the selected clip's Link.
5. Click Blender's refresh icon, whose tooltip is **Refresh Workspace**. A
   **Prepare in Unity** row still lacks a usable advertised Link or needs rebuilding.
   **Link Available** means a Link file is present; full identity and content
   verification happens when adding the clip.
6. Select an available clip and click **Add to Worklist**, or its row's **+**.
   The first new import opens an independent editing scene using the exact Unity
   model. Further entries reuse that scene's shared rig. A row already included
   is marked **Added**.

If a row still says **Prepare in Unity**, prepare that particular Controller clip
in Unity, refresh the Unity workspace if needed, then refresh Blender. Refresh
keeps existing worklist entries and Custom edits; it does not silently replace
their Source baselines.

The catalog covers this character's published Controller clips. It does not
scan the entire Unity project, sample all clips, or perform batch Sync.

## Compare and edit

| Control | Behavior |
| --- | --- |
| **Source** | Views the original Unity-evaluated motion on this character. The Action is linked from a separate baseline `.blend` and is read-only. |
| **Custom** | Activates the local editable Action. A new entry starts from a copy of Source, except when adopting an existing matching edit. |
| **Play / Pause** and **Frame** | Play or scrub the active worklist Action. |
| **Sync Current to Unity** | Exports the selected entry's active Custom Action as a new candidate revision. |

Click **Source** or **Custom** to switch Actions. Clicking elsewhere in a row only
changes the highlighted selection. Switching stops playback, selects the shared
rig, sets the clip's preview range and moves to its first frame. Auto Keying is
turned off; enable it deliberately when editing Custom if needed.

To see the keyframes, open a **Dope Sheet** area and choose **Action Editor** once.
Each subsequent **Source** or **Custom** click selects the shared rig and its
corresponding Action and slot, so that editor follows the selected motion.
Existing Action Editor and Graph Editor areas refresh without changing the
layout. Their channel filters remain in place; clear a search or **Only Show
Selected** filter if it hides the keys you want to inspect.

Source remains a linked baseline even while Custom changes. Making Source local,
replacing its library or changing its cached file causes validation to stop; an
editable substitute is not accepted as the original baseline. Source cannot be
synced through the worklist.

## Arrange the worklist

Use the up/down buttons to move the highlighted entry. To drag, select an entry,
press inside the tall **Drag Selected Entry** handle below the arrows, move
vertically and release inside the same handle. The distance determines how many
positions to move; a click without movement leaves the order unchanged.

Ordering affects only worklist membership order. It does not rearrange Action
keyframes, change playback timing or reorder the Unity Controller.

The row's **X** removes only that worklist entry. Its Source and Custom Actions,
cached baseline, character and Unity assets remain. If the removed entry is
playing, playback stops and its Action is detached from the rig. Other entries
remain available. Adding that clip again creates a fresh entry; it does not
automatically recover the Custom Action retained from a removed entry.

## Return a Custom edit to Unity

1. Click the desired row's **Custom**, make the edit and save the editing `.blend`
   when you want to retain it.
2. Click **Sync Current to Unity**. The exporter uses an isolated snapshot and
   publishes a new FBX/metadata revision through that entry's existing Link.
3. In Unity, use **Sync Preview**, inspect **View Candidate**, then explicitly
   choose **Apply to Character** when the candidate is ready. Blender Sync alone
   does not apply the edit to the character.

Choose **Cancel Action Export** while a Sync is running to stop it. Completion,
failure and cancellation feedback belongs to the worklist scene that started
the operation. The existing Link publication checks protect the previous usable
output when the export fails or is cancelled.

Each entry keeps its own clip identity and Link. Renaming an Action or changing
the worklist order does not redirect another entry's Sync. Selecting a different
Action directly in Blender requires activating the intended row's **Custom**
again before Sync.

The return uses the character's current Unity Humanoid Avatar. Its joint
translation policy still applies; see the [existing Avatar conversion
limits](animation_roundtrip.md). This worklist does not
change Avatar settings or establish a lossless joint-translation conversion.
The linked return covers skeletal animation rather than materials, Shape Keys
or accessory physics.

## Save, provenance and existing edits

Use **Save As** to save a separate editing `.blend`. It retains the worklist,
order, local Custom Actions, Action references and Object slots. Source Actions
remain linked to external read-only libraries under Blender's user
`DATAFILES/character_designer/animation_sources/<workspace>/<modelhash>/<uuid>.blend`.
The worklist records each library's path and content hash. Include these cache
files when moving to another PC; the editing `.blend` alone is not a complete
transfer backup. Keep the Unity workspace, Link, source packet and model files
available for later verification and Sync. A missing or changed Source library
blocks activation and Sync while retaining the local Custom Actions.

A matching independent rig created by the earlier **Link / Import** workflow can
be adopted. For the same verified clip revision, its existing edited Action is
retained as Custom and a separate Source baseline is built from the original
packet. Multiple matching rigs, a changed Link or a different source revision
cause a refusal instead of an automatic choice. An arbitrary authoring rig is
not adopted merely because its name matches.

The legacy single-Action controls remain available in scenes without a connected
workspace. See [the linked Walk workflow](animation_roundtrip.md#linked-walk-workflow)
for that route.

Workspace, character, model, clip and packet identities are checked at the
relevant import and Sync boundaries. Published Avatar and base Controller
provenance also belongs to the saved baseline when supplied by Unity. A changed
model, Avatar or base Controller requires a separate editing scene. The target's
current `targetHash`, which can change after Apply, is informational and does not
by itself invalidate the baseline. A rebuilt clip Link stops Sync while retaining
Custom edits. Prepare the matching inputs in Unity and use a separate editing
scene for a new baseline; refresh is not an automatic migration of existing edits.

## Validation scope

The worklist UI, metadata contract, linked Source lifecycle and scene persistence
have separate test entry points. The real two-clip acceptance supplements these
checks rather than relying on the earlier single-Action result.

- `tests/test_animation_workspace.py`: bounded metadata parsing and identity.
- `tests/test_animation_worklist_ui.py`: drawing, stable-ID selection/reordering,
  pointer-move cancellation and originating-scene export feedback without Blender.
- `tests/test_animation_worklist_blender.py`: isolated native Action, linked
  Source, shared-rig, adoption and save/reopen checks.

The release record contains the actual mouse-drag evidence, real Unity/Blender
pair results, package hash and local deployment comparison. These checks do not
apply either candidate to the production character.
