# Character Designer 0.77.79 — Clear feedback

The Clear Bone Influences dialog now displays **Keep / Clear bone counts** and
the actual names about to be cleared. Checked still means **KEEP**, unchecked
means **CLEAR**. The column is named **Total Weight**, since it sums weights
over vertices. A small total alone does not establish an incorrect influence.
Active Mirror modifiers show that only source vertices are edited.

Rejected plans now report the failure count and at most two examples, with a
600-character maximum and short UI lines. This replaces the previous unbounded
join of per-vertex errors. The user-supplied failure contained 11,930 unique
failed vertices and 1.39 million characters. The pictured choices kept
`foot.R`/`toe.R` while clearing `foot.L`/`shin.L`/`toe.L`, reversing the stated
intent to remove right-side leftovers. The rejected vertices cannot reach a
retained donor on their connected source piece. The plan refused without
writing weights; the error is not an authorization to borrow across gaps.

For a left-only source Mesh with Vertex Group mirroring, keep the intended
three `.L` groups and clear the two `.R` groups; Mirror generates the other
side. This advice is conditional: the current live Mirror parameters were not
re-observed. No live artist cleanup was performed.

## Validation and deployment

- All **44 pure tests** and **28 native Blender groups** passed, none skipped.
  New cases cover a real 11,930-error plan, bounded rejection, single-error
  detail, failure rollback wording and read-only Keep/Clear/Total Weight draw.
- Full-add-on isolated native GUI rendered the updated table and large-error
  summary. Actual Return confirmation refused the invalid clear, preserving
  every original fixture weight. The screenshot was visually reviewed.
- The complete accepted .78 projection is retained. Only `__init__.py`
  (version), add-on `README.md` and `weight_cleanup.py` change; all 212 files,
  including the pure planner and the accepted flat list rows, are inherited.
- Official build/deploy/check passed. Blender 5.2 user and X copies each have
  212 matching files, zero differences. All owned tests used trusted autoexec
  and fresh CONFIG/TEMP and exited. No desktop input, artist scene/weight
  write, runtime refresh, preference save, Unity operation or export occurred.
  Changes are local; no commit/push. Model and reasoning effort: unrecorded.

Evidence:
`D:\Blender\Projects\Character\X\validation\weight_cleanup_feedback_20261019\acceptance_summary.json`.
Complete projection SHA256:
`8b52cb486cd1844b56611c2b5297a0d85bc5bc4c61e3dc484b433ca948438cd8`.
Archive `dist/character_designer-0.77.79.zip` SHA256:
`16c09098ac79bf70e1da90a812b2a3776cbf96e42787f0a6256d848de3fe9a33`.

The next check is the artist's intended Keep choices. A remaining refusal
requires identifying that particular disconnected piece and its intended bone;
the existing same-surface, lock and current-paint protections remain in force.
