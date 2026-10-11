"""Local topology proof regressions; these require no Blender process."""
import importlib.util
import pathlib
import unittest
from types import SimpleNamespace


SOURCE = pathlib.Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


topology = load('forearm_twist_topology')
cache = load('forearm_twist_cache')


class Items(list):
    def foreach_get(self, name, output):
        values = []
        for item in self:
            value = getattr(item, name)
            values.extend(value if isinstance(value, tuple) else (value,))
        output[:] = type(output)(output.typecode, values)


def mesh():
    edges = [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)]
    faces = [(0, 1, 2), (3, 4, 5)]
    result = SimpleNamespace(vertices=list(range(6)),
                             edges=Items(SimpleNamespace(vertices=e) for e in edges),
                             polygons=Items(SimpleNamespace(vertices=f, loop_start=3 * i, loop_total=3)
                                            for i, f in enumerate(faces)),
                             loops=Items(SimpleNamespace(vertex_index=v) for f in faces for v in f),
                             as_pointer=lambda: 1)
    return result


class SupportTopologyTests(unittest.TestCase):
    def tearDown(self):
        cache.clear()

    def test_unrelated_geometry_counts_and_inventory_order_do_not_change_proof(self):
        data = mesh()
        before = topology.support_topology_digest(data, (0, 1))
        data.vertices.append(6)
        data.edges.append(SimpleNamespace(vertices=(5, 6)))
        data.polygons.append(SimpleNamespace(vertices=(3, 5, 6)))
        data.edges.reverse()
        data.polygons.reverse()
        self.assertEqual(topology.support_topology_digest(data, (0, 1)), before)

    def test_incident_boundary_edge_change_fails_even_outside_support(self):
        data = mesh()
        before = topology.support_topology_digest(data, (0,))
        data.edges[2].vertices = (3, 0)
        self.assertNotEqual(topology.support_topology_digest(data, (0,)), before)

    def test_incident_face_winding_and_non_support_member_change_fail(self):
        data = mesh()
        before = topology.support_topology_digest(data, (0,))
        data.polygons[0].vertices = (0, 2, 1)
        self.assertNotEqual(topology.support_topology_digest(data, (0,)), before)
        data.polygons[0].vertices = (0, 1, 3)
        self.assertNotEqual(topology.support_topology_digest(data, (0,)), before)

    def test_exact_local_endpoint_order_is_preserved(self):
        data = mesh()
        before = topology.support_topology_digest(data, (0,))
        data.edges[0].vertices = (1, 0)
        self.assertNotEqual(topology.support_topology_digest(data, (0,)), before)

    def test_invalid_or_reindexed_support_is_not_remapped(self):
        data = mesh()
        before = topology.support_topology_digest(data, (0, 1))
        self.assertNotEqual(topology.support_topology_digest(data, (3, 4)), before)
        for ids in ((), (1, 0), (0, 0), (-1, 0), (0, 6), (True, 2), (0., 1)):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                topology.support_topology_digest(data, ids)

    def test_cache_rechecks_direct_same_count_connectivity_edits(self):
        data = mesh()
        calls = []

        def calculate(*args):
            calls.append(True)
            return topology.support_topology_digest(*args)

        before = cache.support_topology_digest(data, (0, 1), calculate)
        self.assertEqual(cache.support_topology_digest(data, (0, 1), calculate), before)
        self.assertEqual(len(calls), 1)
        data.edges[0].vertices = (1, 0)
        self.assertNotEqual(cache.support_topology_digest(data, (0, 1), calculate), before)
        self.assertEqual(len(calls), 2)
        with self.assertRaises(ValueError):
            cache.support_topology_digest(data, (True, 2), calculate)


if __name__ == '__main__':
    unittest.main()
