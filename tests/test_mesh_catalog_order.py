"""Catalog sort semantics without requiring a Blender process."""

import ast
import importlib.util
import itertools
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'
spec = importlib.util.spec_from_file_location('mesh_catalog_order', SOURCE / 'mesh_catalog_order.py')
order = importlib.util.module_from_spec(spec)
spec.loader.exec_module(order)


def apply_plan(names, swaps):
    result = list(names)
    for first, second in swaps:
        a, b = result.index(first), result.index(second)
        result[a], result[b] = result[b], result[a]
    return result


def cycle_count(names, desired):
    destination = {name: index for index, name in enumerate(desired)}
    seen, cycles = set(), 0
    for start in range(len(names)):
        if start in seen:
            continue
        cycles += 1
        current = start
        while current not in seen:
            seen.add(current)
            current = destination[names[current]]
    return cycles


class SortPlanTests(unittest.TestCase):
    def test_every_six_name_permutation_reaches_order_with_minimum_swaps(self):
        desired = ['A', 'a', 'b', 'ß', 'ss', '手指']
        desired.sort(key=lambda name: (name.casefold(), name))
        for names in itertools.permutations(desired):
            swaps = order.name_sort_swaps(names)
            self.assertEqual(apply_plan(names, swaps), desired)
            self.assertEqual(len(swaps), len(names) - cycle_count(names, desired))

    def test_empty_sorted_and_singleton_need_no_writes(self):
        for names in ([], ['A'], ['A', 'a', 'b']):
            self.assertEqual(order.name_sort_swaps(names), ())

    def test_plan_does_not_mutate_input_and_accepts_iterables(self):
        names = ['z', 'b', 'A', 'a']
        before = names.copy()
        self.assertEqual(order.name_sort_swaps(iter(names)), order.name_sort_swaps(names))
        self.assertEqual(names, before)

    def test_duplicate_names_are_rejected_before_returning_a_write_plan(self):
        with self.assertRaises(ValueError):
            order.name_sort_swaps(['A', 'A'])

    def test_reversed_hundred_uses_fifty_payload_swaps_instead_of_4950(self):
        desired = [f'Group_{index:03d}' for index in range(100)]
        self.assertEqual(len(order.name_sort_swaps(reversed(desired))), 50)


class Attribute:
    domain = 'POINT'
    data_type = 'INT'

    def __init__(self, name, values):
        self.name, self.values, self.data = name, list(values), self

    def foreach_get(self, field, target):
        assert field == 'value'
        for index, value in enumerate(self.values):
            target[index] = value

    def foreach_set(self, field, values):
        assert field == 'value'
        self.values[:] = values


class Attributes(list):
    active_index = 0

    def get(self, name):
        return next((item for item in self if item.name == name), None)

    def __getitem__(self, key):
        return self.get(key) if isinstance(key, str) else super().__getitem__(key)

    def __contains__(self, key):
        return self.get(key) is not None if isinstance(key, str) else super().__contains__(key)

    def find(self, name):
        return next((index for index, item in enumerate(self) if item.name == name), -1)

    @property
    def active(self):
        return self[self.active_index]


class Vertex:
    def __init__(self, index):
        self.index = index

    def __getitem__(self, layer):
        return layer.values[self.index]

    def __setitem__(self, layer, value):
        layer.values[self.index] = value


class Vertices(list):
    pass


def catalog_functions(kind, mesh):
    group = kind == 'groups'
    filename = f'custom_vertex_{kind}.py'
    predicate = '_is_group' if group else 'is_part'
    swap = '_swap_groups' if group else '_swap_parts'
    action = 'group_action' if group else 'part_action'
    catalog = 'group_attributes' if group else 'part_attributes'
    names = {predicate, '_layer', '_values', '_write_values', '_activate', swap, action, catalog}
    path = SOURCE / filename
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    vertices = Vertices(Vertex(index) for index in range(len(mesh.vertices)))
    vertices.layers = NS(int=NS(get=mesh.attributes.get))
    bm = NS(verts=vertices)
    update = Mock()
    from array import array
    namespace = dict(PREFIX='Custom_' if group else 'Part_', array=array,
        _mesh=lambda context, **kwargs: mesh, name_sort_swaps=order.name_sort_swaps,
        bmesh=NS(from_edit_mesh=lambda value: bm, update_edit_mesh=update))
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace, action, swap, update


class NativeCatalogModelTests(unittest.TestCase):
    """Execute the product action and unchanged native swap in a layer model.

    The model proves named membership and catalog scope, not Blender's Undo/RNA.
    """

    def fixture(self, prefix, edit, active_foreign):
        names = [prefix + name for name in ('z', 'ß', 'a', '手指', 'A', 'ss')]
        attributes = Attributes()
        for index, name in enumerate(names):
            attributes.append(Attribute(name, (index, -index, index % 2, 127)))
            attributes.append(Attribute('Foreign_' + str(index), (17, 18, 19, 20)))
        for name in ('.cdvg_move', '.cdpart_move', '.cdvg_lock_keep', '.cdpart_lock_keep'):
            attributes.append(Attribute(name, (4, 3, 2, 1)))
        active = 'Foreign_2' if active_foreign else prefix + 'ß'
        attributes.active_index = attributes.find(active)
        return NS(attributes=attributes, vertices=range(4), is_editmode=edit), names, active

    def test_sort_preserves_named_payloads_foreign_positions_and_active_in_both_modes(self):
        for kind, prefix in (('groups', 'Custom_'), ('parts', 'Part_')):
            for edit, active_foreign in itertools.product((False, True), repeat=2):
                with self.subTest(kind=kind, edit=edit, active_foreign=active_foreign):
                    mesh, names, active = self.fixture(prefix, edit, active_foreign)
                    before = {attribute.name: tuple(attribute.values) for attribute in mesh.attributes}
                    foreign = [(index, attribute, attribute.name) for index, attribute in enumerate(mesh.attributes)
                               if attribute.name not in names]
                    namespace, action, swap, update = catalog_functions(kind, mesh)
                    original_swap = namespace[swap]
                    namespace[swap] = Mock(wraps=original_swap)
                    namespace[action](None, 'SORT_NAME')
                    self.assertEqual([item.name for item in mesh.attributes if item.name.startswith(prefix)],
                                     sorted(names, key=lambda name: (name.casefold(), name)))
                    self.assertEqual({item.name: tuple(item.values) for item in mesh.attributes}, before)
                    self.assertEqual(mesh.attributes.active.name, active)
                    for index, attribute, name in foreign:
                        self.assertIs(mesh.attributes[index], attribute)
                        self.assertEqual(attribute.name, name)
                    self.assertEqual(namespace[swap].call_count, len(order.name_sort_swaps(names)))
                    self.assertEqual(update.call_count, int(edit))
                    namespace[swap].reset_mock()
                    namespace[action](None, 'SORT_NAME')
                    namespace[swap].assert_not_called()

    def test_failure_before_first_swap_propagates_without_changing_payloads(self):
        # This injects before the first native write. Partial-write recovery
        # and the operator's native Undo behavior are outside this model.
        for kind, prefix in (('groups', 'Custom_'), ('parts', 'Part_')):
            mesh, names, active = self.fixture(prefix, False, False)
            before = {item.name: tuple(item.values) for item in mesh.attributes}
            namespace, action, swap, update = catalog_functions(kind, mesh)
            namespace[swap] = Mock(side_effect=RuntimeError('native swap failure'))
            with self.assertRaisesRegex(RuntimeError, 'native swap failure'):
                namespace[action](None, 'SORT_NAME')
            self.assertEqual({item.name: tuple(item.values) for item in mesh.attributes}, before)
            self.assertEqual(mesh.attributes.active.name, active)
            update.assert_not_called()


if __name__ == '__main__':
    unittest.main()
