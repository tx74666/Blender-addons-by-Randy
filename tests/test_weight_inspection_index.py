"""Inspection index regression and paired benchmark, without importing Blender.

Optional --baseline is a pinned weight_cleanup_core.py file or add-on directory.
Full result dictionaries are compared, including warnings and diagnostic order.
"""

import argparse
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
import time
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
SUBJECT = BASELINE = None


def load_subject(path, name):
    path = Path(path).resolve()
    if path.is_dir():
        path = path / "weight_cleanup_core.py"
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def snapshot(module, name, positions, edges, weights, triangles=()):
    return module.MeshSnapshot(name, positions, edges, weights, triangles)


SUBJECT = load_subject(ROOT / "addons/character_designer/weight_cleanup_core.py", "inspection_index_subject")


class InspectionIndexTests(unittest.TestCase):
    def test_index_uses_only_positive_deform_memberships(self):
        source = snapshot(SUBJECT, "Mesh", ((0, 0, 0),) * 4, (), (
            {"A": 1e-30, "B": 0.0, "Mask": 1.0},
            {"B": 0.125}, {"A": 0.0}, {"A": 0.75, "B": 0.25},
        ))
        mesh = SUBJECT._mesh(source)
        self.assertEqual(SUBJECT._positive_weight_indices(mesh, {"A", "B"}),
                         {"A": (0, 3), "B": (1, 3)})

    def test_indexed_and_scanned_island_traversal_match_exactly(self):
        source = snapshot(SUBJECT, "Mesh", ((0, 0, 0),) * 6,
                          ((0, 1), (1, 2), (3, 4), (4, 5)),
                          ({"A": 1e-25}, {"A": 0.5}, {},
                           {"A": 0.25}, {"A": 0.0}, {"A": 1.0}))
        mesh = SUBJECT._mesh(source)
        indices = SUBJECT._positive_weight_indices(mesh, {"A"})["A"]
        segment = ((0, 0, 0), (0, 0, 0))
        scanned = SUBJECT._bone_islands(mesh, "A", segment)
        indexed = SUBJECT._bone_islands(mesh, "A", segment, positive_indices=indices)
        self.assertEqual(scanned, indexed)
        self.assertEqual([island.vertex_indices for island in indexed], [(0, 1), (3,), (5,)])

    def test_each_mesh_indexed_once_and_reused_for_each_bone(self):
        module = SUBJECT
        bones = tuple("B" + str(i) for i in range(5))
        sources = tuple(snapshot(module, name, ((0, 0, 0), (1, 0, 0)), ((0, 1),),
                                 tuple({bone: 0.2 for bone in bones} for _ in range(2)))
                        for name in ("Target", "Reference"))
        index_calls, island_calls = [], []
        original_index, original_islands = module._positive_weight_indices, module._bone_islands

        def index_spy(mesh, deform):
            index_calls.append(mesh.name)
            return original_index(mesh, deform)

        def islands_spy(mesh, bone, segment, **options):
            self.assertIsNotNone(options.get("positive_indices"))
            island_calls.append((mesh.name, bone))
            return original_islands(mesh, bone, segment, **options)

        try:
            module._positive_weight_indices, module._bone_islands = index_spy, islands_spy
            result = module.inspect_islands(sources, ("Target",), bones,
                                            {bone: ((i, 0, 0), (i, 1, 0))
                                             for i, bone in enumerate(bones)})
        finally:
            module._positive_weight_indices, module._bone_islands = original_index, original_islands
        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(index_calls, ["Target", "Reference"])
        self.assertEqual(len(island_calls), 10)

    def test_reference_index_only_collects_bones_used_by_target(self):
        module = SUBJECT
        bones = tuple("A" + str(i) for i in range(5))
        source = snapshot(module, "Target", ((0, 0, 0),), (), ({bone: 0.2 for bone in bones},))
        reference = snapshot(module, "Reference", ((0, 1, 0),), (), ({"B": 1.0},))
        original, calls = module._positive_weight_indices, []

        def index_spy(mesh, deform):
            calls.append((mesh.name, frozenset(deform)))
            return original(mesh, deform)

        try:
            module._positive_weight_indices = index_spy
            result = module.inspect_islands((source, reference), ("Target",), bones + ("B",),
                                            {bone: ((0, 0, 0), (0, 1, 0)) for bone in bones})
        finally:
            module._positive_weight_indices = original
        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(calls, [("Target", frozenset(bones + ("B",))),
                                 ("Reference", frozenset(bones))])

    def test_small_targets_keep_original_reference_scans_at_dispatch_boundary(self):
        module = SUBJECT
        original_index, original_islands = module._positive_weight_indices, module._bone_islands
        for count in (1, 3, 4, 5):
            bones = tuple("B" + str(i) for i in range(count))
            data = tuple((name, ((0, 0, 0),), (), ({bone: 1.0 / count for bone in bones},))
                         for name in ("Target", "Reference"))
            segments = {bone: ((0, 0, 0), (0, 1, 0)) for bone in bones}
            index_calls, island_calls = [], []

            def index_spy(mesh, deform):
                index_calls.append(mesh.name)
                return original_index(mesh, deform)

            def islands_spy(mesh, bone, segment, **options):
                island_calls.append((mesh.name, options.get("positive_indices")))
                return original_islands(mesh, bone, segment, **options)

            try:
                module._positive_weight_indices, module._bone_islands = index_spy, islands_spy
                result = module.inspect_islands(tuple(snapshot(module, *item) for item in data),
                                                ("Target",), bones, segments)
            finally:
                module._positive_weight_indices, module._bone_islands = original_index, original_islands
            self.assertTrue(result.ok, result.to_dict())
            self.assertEqual(index_calls, ["Target", "Reference"] if count > 4 else ["Target"])
            self.assertTrue(all((indices is not None) == (count > 4) for _, indices in island_calls))
            if BASELINE is not None:
                expected = BASELINE.inspect_islands(tuple(snapshot(BASELINE, *item) for item in data),
                                                    ("Target",), bones, segments)
                self.assertEqual(result.to_dict(), expected.to_dict())

    def test_full_inspection_parity_for_randomized_meshes(self):
        if BASELINE is None:
            self.skipTest("Pass --baseline for complete old/new result parity")
        rng = random.Random(34713)
        for case in range(150):
            names = tuple("B" + str(i) for i in range(rng.randrange(1, 9)))
            data, deform = [], {}
            for mesh_index in range(rng.randrange(1, 4)):
                name = "Mesh" + str(mesh_index)
                count = rng.randrange(0, 23)
                positions = tuple(tuple(rng.randrange(-3, 4) for _ in range(3)) for _ in range(count))
                edges = tuple((i, j) for i in range(count) for j in range(i + 1, count)
                              if rng.random() < 0.12)
                triangles = tuple(tuple(rng.sample(range(count), 3)) for _ in range(3)) if count >= 3 else ()
                weights = tuple({bone: rng.choice((0.0, 1e-25, 0.25, 0.5, 1.0))
                                 for bone in names + ("Mask",) if rng.random() < 0.25}
                                for _ in range(count))
                deform[name] = tuple(bone for bone in names if rng.random() < 0.8)
                data.append((name, positions, edges, weights, triangles))
            segments = {bone: ((0, 0, 0), (rng.randrange(-2, 3), rng.randrange(0, 2), 0))
                        for bone in names if rng.random() < 0.9}
            targets = tuple(item[0] for item in data if rng.random() < 0.75)
            for scope in ("CURRENT", "CHARACTER"):
                with self.subTest(case=case, scope=scope):
                    old = BASELINE.inspect_islands(
                        tuple(snapshot(BASELINE, *item) for item in data), targets, deform, segments, scope=scope,
                    )
                    new = SUBJECT.inspect_islands(
                        tuple(snapshot(SUBJECT, *item) for item in data), targets, deform, segments, scope=scope,
                    )
                    self.assertEqual(new.to_dict(), old.to_dict())

    def test_invalid_unused_reference_is_still_rejected_before_indexing(self):
        source = snapshot(SUBJECT, "Target", ((0, 0, 0),), (), ({"A": 1.0},))
        invalid = snapshot(SUBJECT, "Unused", ((0, 0, 0),), (), ({"Mask": -0.1},))
        result = SUBJECT.inspect_islands((source, invalid), ("Target",), ("A",),
                                         {"A": ((0, 0, 0), (0, 1, 0))})
        self.assertFalse(result.ok)
        self.assertEqual(result.records, ())
        if BASELINE is not None:
            expected = BASELINE.inspect_islands(
                (snapshot(BASELINE, "Target", source.world_positions, (), source.weights),
                 snapshot(BASELINE, "Unused", invalid.world_positions, (), invalid.weights)),
                ("Target",), ("A",), {"A": ((0, 0, 0), (0, 1, 0))},
            )
            self.assertEqual(result.to_dict(), expected.to_dict())


def benchmark(vertices, bones, samples):
    names = tuple("B" + str(i) for i in range(bones))
    positions = tuple((i * 0.01, 0.0, 0.0) for i in range(vertices))
    edges = tuple((i, i + 1) for i in range(vertices - 1))
    weights = tuple({names[((i // 100) + j) % bones]: 0.25 for j in range(min(4, bones))}
                    for i in range(vertices))
    segments = {bone: ((i * 5.0, 0, 0), (i * 5.0, 1, 0)) for i, bone in enumerate(names)}
    calls = {}
    for key, module in (("baseline", BASELINE), ("candidate", SUBJECT)):
        source = snapshot(module, "Mesh", positions, edges, weights)
        calls[key] = lambda module=module, source=source: module.inspect_islands(
            (source,), ("Mesh",), names, segments,
        )
    expected = calls["baseline"]().to_dict()
    assert calls["candidate"]().to_dict() == expected
    times = {key: [] for key in calls}
    for turn in range(samples):
        order = tuple(calls) if turn % 2 == 0 else tuple(reversed(calls))
        for key in order:
            start = time.perf_counter_ns()
            result = calls[key]()
            times[key].append((time.perf_counter_ns() - start) / 1e6)
            assert result.to_dict() == expected
    medians = {key: statistics.median(values) for key, values in times.items()}
    return {"vertices": vertices, "deform_bones": bones,
            "positive_memberships": sum(map(len, weights)), "edges": len(edges), "triangles": 0,
            "warmups_per_version": 1, "alternating_samples_ms": times, "medians_ms": medians,
            "median_reduction_percent": (1.0 - medians["candidate"] / medians["baseline"]) * 100,
            "complete_result_parity": True,
            "limits": "Synthetic full inspect_islands timing; shared host load, no GUI/FPS or artist-scene claim."}


def main():
    global SUBJECT, BASELINE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--subject", type=Path, default=ROOT / "addons/character_designer/weight_cleanup_core.py")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--vertices", type=int, default=20000)
    parser.add_argument("--bones", type=int, default=80)
    parser.add_argument("--samples", type=int, default=6)
    args = parser.parse_args()
    SUBJECT = load_subject(args.subject, "inspection_index_subject")
    if args.baseline:
        BASELINE = load_subject(args.baseline, "inspection_index_baseline")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(InspectionIndexTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {"schema": "character-designer.weight-inspection-index/1", "python": sys.version,
              "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "subject": SUBJECT.__file__,
              "subject_sha256": hashlib.sha256(Path(SUBJECT.__file__).read_bytes()).hexdigest()}
    if BASELINE is not None:
        report.update(baseline=BASELINE.__file__,
                      baseline_sha256=hashlib.sha256(Path(BASELINE.__file__).read_bytes()).hexdigest())
    if args.benchmark and result.wasSuccessful():
        if BASELINE is None:
            parser.error("--benchmark requires --baseline")
        report["benchmark"] = benchmark(args.vertices, args.bones, args.samples)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
