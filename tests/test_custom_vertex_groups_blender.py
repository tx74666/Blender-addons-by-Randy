"""Independent selection groups preserve authored deformation and mesh data.

Run only in a disposable process through run_trusted_blender_validation.py.
The GUI history class is separate so the root harness can launch it with a
native event loop; none of these fixtures open the artist's X scene.
"""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import bmesh
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import custom_vertex_groups as groups


# Blender 5.2 stores UV selection as separate corner attributes, populated by
# ordinary Edit Mode entry. These are native selection output, just like mesh
# selection; UV coordinates, pins and every unrelated attribute remain exact.
SELECTION_ATTRIBUTES = {'.select_vert', '.select_edge', '.select_poly',
                        '.uv_select_vert', '.uv_select_edge', '.uv_select_face'}


def frozen(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if hasattr(value, 'items'):
        return tuple(sorted((str(key), frozen(item)) for key, item in value.items()))
    return tuple(frozen(item) for item in value)


def rna_values(value):
    result = []
    for prop in value.bl_rna.properties:
        if prop.identifier == 'rna_type' or prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'}:
            continue
        result.append((prop.identifier, frozen(getattr(value, prop.identifier))))
    return tuple(result)


def registry(obj):
    # The Custom_ POINT/INT namespace is the saved selection catalog itself.
    # This wrapper only keeps test assertions readable; it is not RNA state.
    return [SimpleNamespace(name=attr.name[len(groups.PREFIX):], attribute_name=attr.name)
            for attr in groups.group_attributes(obj.data)]


def active_name(obj):
    active = obj.data.attributes.active
    return active.name if active is not None else None


def activate_group(obj, index):
    name = registry(obj)[index].attribute_name
    obj.data.attributes.active_index = next(i for i, attr in enumerate(obj.data.attributes)
                                             if attr.name == name)


def try_invalid_label_write(attribute, label):
    # Exercise the guarded native rename backend directly. The real transient
    # row RNA setter and its native Undo are tested separately in the GUI suite.
    try:
        groups._set_label(attribute, label)
    except (ValueError, RuntimeError, TypeError):
        pass


def group_names(obj):
    return {item.attribute_name for item in registry(obj)}


def owned_names(obj):
    names = group_names(obj)
    # Exempt only authenticated native lock markers for current group names.
    # Wrong-typed collisions or orphan markers remain protected foreign data.
    for name in tuple(names):
        marker = groups._lock_name(name)
        attribute = obj.data.attributes.get(marker)
        if attribute is not None and attribute.domain == 'POINT' and attribute.data_type == 'INT':
            names.add(marker)
    return names


def attribute_state(mesh, *, exclude=()):
    return tuple(sorted((attr.name, attr.data_type, attr.domain,
                         tuple(rna_values(item) for item in attr.data))
                        for attr in mesh.attributes if attr.name not in set(exclude)))


def protected(obj):
    """Exact authored inputs; selection and the new feature's data may change."""
    mesh = obj.data
    keys = mesh.shape_keys
    return {
        'vertices': tuple(tuple(vertex.co) for vertex in mesh.vertices),
        'edges': tuple(tuple(edge.vertices) for edge in mesh.edges),
        'polygons': tuple((tuple(face.vertices), face.material_index, face.use_smooth)
                          for face in mesh.polygons),
        'loops': tuple((loop.vertex_index, loop.edge_index) for loop in mesh.loops),
        'deform_groups': tuple((group.name, group.index, group.lock_weight) for group in obj.vertex_groups),
        'deform_weights': tuple(tuple((item.group, item.weight) for item in vertex.groups)
                                for vertex in mesh.vertices),
        'active_deform_group': obj.vertex_groups.active_index,
        'shape_keys': tuple((key.name, key.relative_key.name, key.value, key.mute,
                             key.vertex_group, tuple(tuple(point.co) for point in key.data))
                            for key in keys.key_blocks) if keys else (),
        'shape_key_inputs': (keys.use_relative, obj.active_shape_key_index) if keys else None,
        'attributes': attribute_state(mesh, exclude=owned_names(obj) | SELECTION_ATTRIBUTES),
        'object_matrix': frozen(obj.matrix_world),
        'modifiers': tuple((modifier.name, modifier.type, modifier.show_viewport, modifier.show_render)
                           for modifier in obj.modifiers),
        'artist_object_property': obj.get('Artist note'),
        'artist_mesh_property': mesh.get('Artist note'),
    }


def feature_state(obj):
    return {'groups': tuple((item.name, item.attribute_name) for item in registry(obj)),
            'attributes': attribute_state(obj.data), 'protected': protected(obj)}


def bmesh_protected(obj):
    """Read live Edit Mode geometry, deform and all supported custom-data layers."""
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    excluded = owned_names(obj) | SELECTION_ATTRIBUTES
    result = {'coordinates': tuple(tuple(vertex.co) for vertex in bm.verts),
              'edges': tuple(tuple(vertex.index for vertex in edge.verts) for edge in bm.edges),
              'faces': tuple(tuple(vertex.index for vertex in face.verts) for face in bm.faces)}
    for domain, elements in (('VERT', bm.verts), ('EDGE', bm.edges),
                             ('FACE', bm.faces), ('LOOP', bm.loops)):
        rows = []
        sequence = ([loop for face in bm.faces for loop in face.loops]
                    if domain == 'LOOP' else list(elements))
        for kind in ('float', 'int', 'float_vector', 'float_color', 'color',
                     'deform', 'shape', 'string', 'uv'):
            layer_access = getattr(elements.layers, kind, None)
            if layer_access is None:
                continue
            for name in layer_access.keys():
                if name in excluded:
                    continue
                layer = layer_access[name]
                values = []
                for element in sequence:
                    value = element[layer]
                    if kind == 'uv':
                        # 5.2 removed select/select_edge from BMLoopUV. Keep
                        # authored UV and pin data; selection is allowed above.
                        value = (tuple(value.uv), value.pin_uv)
                    values.append(frozen(value))
                rows.append((kind, name, tuple(values)))
        result[domain] = tuple(rows)
    return result


def edit_group_state(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    return {'catalog': tuple(item.attribute_name for item in registry(obj)),
            'integer_layers': tuple(sorted((name, tuple(vertex[bm.verts.layers.int[name]]
                for vertex in bm.verts)) for name in bm.verts.layers.int.keys())),
            'protected': bmesh_protected(obj)}


def semantic_members(obj):
    return {item.attribute_name: tuple(sorted(members(obj, index)))
            for index, item in enumerate(registry(obj))}


def activate(obj):
    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def select_vertices(obj, indices, *, hidden=()):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vertex in bm.verts:
        vertex.hide_set(False)
        vertex.select_set(False)
    for index in indices:
        bm.verts[index].select_set(True)
    for index in hidden:
        bm.verts[index].hide_set(True)
    bm.select_history.clear()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def selected(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    return {vertex.index for vertex in bm.verts if vertex.select}


def members(obj, index=None):
    name = active_name(obj) if index is None else registry(obj)[index].attribute_name
    if name not in group_names(obj):
        raise AssertionError('No active custom selection attribute: ' + str(name))
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        layer = bm.verts.layers.int.get(name)
        if layer is None:
            raise AssertionError('Registered custom group has no live BMesh layer: ' + name)
        return {vertex.index for vertex in bm.verts if vertex[layer] == 1}
    return {index for index, item in enumerate(obj.data.attributes[name].data) if item.value == 1}


def fixture(name='Selection group artist mesh'):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(column * .17, row * .23, (row + column) * .013)
                      for row in range(3) for column in range(3)], [],
                     [(row * 3 + column, row * 3 + column + 1,
                       (row + 1) * 3 + column + 1, (row + 1) * 3 + column)
                      for row in range(2) for column in range(2)])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    activate(obj)
    for number, group_name in enumerate(('thigh.L', 'foot.L', 'Artist Loops')):
        group = obj.vertex_groups.new(name=group_name)
        for vertex in mesh.vertices:
            group.add([vertex.index], (vertex.index + number + 1) / 17., 'REPLACE')
        group.lock_weight = number != 1
    obj.vertex_groups.active_index = 1
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='Artist shoe correction')
    key.data[4].co.z += .021
    key.value, key.vertex_group = .37, 'Artist Loops'
    obj.active_shape_key_index = 0
    for attr_name, attr_type, domain in (('Artist Point', 'FLOAT', 'POINT'),
                                         ('Artist Edge', 'INT', 'EDGE'),
                                         ('Artist Face', 'BOOLEAN', 'FACE'),
                                         ('Artist Vector', 'FLOAT_VECTOR', 'POINT')):
        attr = mesh.attributes.new(name=attr_name, type=attr_type, domain=domain)
        for index, item in enumerate(attr.data):
            if attr_type == 'FLOAT_VECTOR':
                item.vector = (.03 * index, .02, -.11)
            else:
                item.value = bool(index % 2) if attr_type == 'BOOLEAN' else index / 13. if attr_type == 'FLOAT' else index * 3
    uv = mesh.uv_layers.new(name='Artist UV')
    for index, item in enumerate(uv.data):
        item.uv = ((index % 5) / 5., (index % 7) / 7.)
    color = mesh.color_attributes.new(name='Artist Color', type='FLOAT_COLOR', domain='CORNER')
    for index, item in enumerate(color.data):
        item.color = (.1, index / 31., .3, 1.)
    obj.location = (.3, -.1, .7)
    obj['Artist note'], mesh['Artist note'] = 'Keep paint', 'Keep topology and attributes'
    bpy.context.view_layer.update()
    return obj


class _CustomVertexGroupFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for item in groups.CLASSES:
            if not getattr(item, 'is_registered', False):
                bpy.utils.register_class(item)
        groups.register_properties()

    def setUp(self):
        if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in tuple(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.context.scene.tool_settings.mesh_select_mode = (True, False, False)
        self.obj = fixture()

    def tearDown(self):
        if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

    def create(self, name='Shoe loops'):
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_new(name=name), {'FINISHED'})
        name = active_name(self.obj)
        return next(item for item in registry(self.obj) if item.attribute_name == name)

    def edit(self):
        self.assertEqual(bpy.ops.object.mode_set(mode='EDIT'), {'FINISHED'})

    def object(self):
        self.assertEqual(bpy.ops.object.mode_set(mode='OBJECT'), {'FINISHED'})

    def assert_refused(self, operator, **kwargs):
        if operator.poll():
            self.assertEqual(operator(**kwargs), {'CANCELLED'})
        else:
            with self.assertRaises(RuntimeError):
                operator(**kwargs)

class CustomVertexGroupTests(_CustomVertexGroupFixture):
    def test_new_is_empty_and_separate_from_same_named_locked_deform_group(self):
        before = protected(self.obj)
        item = self.create('thigh.L')
        self.assertEqual(item.name, 'thigh.L')
        self.assertEqual(members(self.obj), set())
        self.assertEqual(self.obj.data.attributes[item.attribute_name].data_type, 'INT')
        self.assertEqual(self.obj.data.attributes[item.attribute_name].domain, 'POINT')
        self.assertEqual(protected(self.obj), before)

    def test_assign_remove_and_replace_select_in_edit_mode_preserve_live_inputs(self):
        before = protected(self.obj)
        self.create()
        self.edit()
        select_vertices(self.obj, {0, 1, 4})
        live = bmesh_protected(self.obj)
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_assign(), {'FINISHED'})
        self.assertEqual(members(self.obj), {0, 1, 4})
        self.assertEqual(bmesh_protected(self.obj), live)
        select_vertices(self.obj, {1, 7})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_remove(), {'FINISHED'})
        self.assertEqual(members(self.obj), {0, 4})
        select_vertices(self.obj, {7, 8})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_select(), {'FINISHED'})
        self.assertEqual(selected(self.obj), {0, 4})
        self.assertEqual(bmesh_protected(self.obj), live)
        self.object()
        self.assertEqual(protected(self.obj), before)

    def test_two_overlapping_groups_remain_independent_and_delete_only_active(self):
        before = protected(self.obj)
        first = self.create('Toe loops').attribute_name
        self.edit()
        select_vertices(self.obj, {0, 1, 2})
        bpy.ops.character_designer.custom_vertex_group_assign()
        second = self.create('Heel loops').attribute_name
        self.assertEqual(members(self.obj), set(), 'New creates empty even with a live selection.')
        select_vertices(self.obj, {1, 4})
        bpy.ops.character_designer.custom_vertex_group_assign()
        self.assertEqual(members(self.obj, 0), {0, 1, 2})
        self.assertEqual(members(self.obj, 1), {1, 4})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_delete(), {'FINISHED'})
        self.assertEqual(len(registry(self.obj)), 1)
        self.assertEqual(registry(self.obj)[0].attribute_name, first)
        self.assertEqual(members(self.obj), {0, 1, 2})
        self.object()
        self.assertNotIn(second, self.obj.data.attributes)
        self.assertEqual(protected(self.obj), before)
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_delete(), {'FINISHED'})
        self.assertEqual(len(registry(self.obj)), 0)
        self.assertNotIn(first, self.obj.data.attributes)
        self.assertEqual(protected(self.obj), before)

    def test_unicode_duplicate_names_and_foreign_attribute_collision_are_readable(self):
        first = self.create('鞋底选区').attribute_name
        self.create('鞋底选区')
        labels = [item.name for item in registry(self.obj)]
        self.assertEqual(len(set(labels)), 2)
        self.assertTrue(all(label.startswith('鞋底选区') for label in labels))
        self.assertEqual(len({item.attribute_name for item in registry(self.obj)}), 2)
        self.assertTrue(all(item.attribute_name.startswith('Custom_') for item in registry(self.obj)))
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_delete(), {'FINISHED'})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_delete(), {'FINISHED'})
        # Matching Custom_ POINT/INT attributes are groups by convention. A
        # same-prefix FLOAT attribute remains unrelated authored mesh data.
        foreign = self.obj.data.attributes.new(name=first, type='FLOAT', domain='POINT')
        foreign.data[3].value = 73
        before = protected(self.obj)
        item = self.create('鞋底选区')
        self.assertNotEqual(item.attribute_name, first)
        self.assertEqual(self.obj.data.attributes[first].data[3].value, 73)
        self.assertEqual(protected(self.obj), before)
        # Pre-existing native Custom_ POINT/INT attributes are intentionally
        # selection groups, not a second hidden registry or foreign data claim.
        existing = self.obj.data.attributes.new(name='Custom_Existing selection', type='INT', domain='POINT')
        for index in (2, 5):
            existing.data[index].value = 1
        existing_name = existing.name
        index = next(i for i, item in enumerate(registry(self.obj)) if item.attribute_name == existing_name)
        activate_group(self.obj, index)
        self.edit()
        select_vertices(self.obj, {0, 8})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_select(), {'FINISHED'})
        self.assertEqual(selected(self.obj), {2, 5})
        self.object()
        self.assertEqual(protected(self.obj), before)

    def test_select_ignores_hidden_members_and_does_not_unhide_artist_vertices(self):
        self.create()
        self.edit()
        select_vertices(self.obj, {1, 2, 4})
        bpy.ops.character_designer.custom_vertex_group_assign()
        select_vertices(self.obj, {7, 8}, hidden={4})
        before = bmesh_protected(self.obj)
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_select(), {'FINISHED'})
        self.assertEqual(selected(self.obj), {1, 2})
        self.assertEqual(members(self.obj), {1, 2, 4})
        self.assertTrue(bmesh.from_edit_mesh(self.obj.data).verts[4].hide)
        self.assertEqual(bmesh_protected(self.obj), before)

    def test_deselect_subtracts_only_visible_members_and_keeps_other_selection(self):
        before = protected(self.obj)
        self.create()
        self.edit()
        select_vertices(self.obj, {0, 1, 4})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_assign(), {'FINISHED'})
        select_vertices(self.obj, {0, 1, 2, 5}, hidden={4})
        live = bmesh_protected(self.obj)
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_deselect(), {'FINISHED'})
        self.assertEqual(selected(self.obj), {2, 5})
        self.assertEqual(members(self.obj), {0, 1, 4})
        self.assertTrue(bmesh.from_edit_mesh(self.obj.data).verts[4].hide)
        self.assertEqual(bmesh_protected(self.obj), live)
        # Restore only the test's own hidden flag before comparing artist data.
        bmesh.from_edit_mesh(self.obj.data).verts[4].hide_set(False)
        self.object()
        self.assertEqual(protected(self.obj), before)

    def test_friendly_rename_preserves_native_prefix_unicode_membership_and_collisions(self):
        before = protected(self.obj)
        first = self.create('鞋底选区').attribute_name
        attribute = self.obj.data.attributes[first]
        self.assertIs(attribute.id_data, self.obj.data)
        self.assertEqual(groups._get_label(attribute), '鞋底选区')
        self.edit()
        select_vertices(self.obj, {0, 4, 8})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_assign(), {'FINISHED'})
        self.object()
        second = self.create('Heel loops').attribute_name
        # A row need not be the active row to be renamed on its own active mesh.
        groups._set_label(self.obj.data.attributes[first], 'Heel loops')
        renamed = next(name for name in group_names(self.obj) if name != second)
        self.assertNotEqual(renamed, second)
        self.assertTrue(renamed.startswith(groups.PREFIX + 'Heel loops'))
        self.assertNotIn(first, self.obj.data.attributes)
        index = next(i for i, item in enumerate(registry(self.obj)) if item.attribute_name == renamed)
        self.assertEqual(members(self.obj, index), {0, 4, 8})
        self.assertEqual(members(self.obj, 1 - index), set())
        self.assertEqual(protected(self.obj), before)

        label = '鞋袜精修区域' * 20
        groups._set_label(self.obj.data.attributes[renamed], label)
        renamed = next(name for name in group_names(self.obj) if name != second)
        self.assertLessEqual(len(renamed.encode('utf-8')), 63)
        self.assertTrue(renamed.startswith(groups.PREFIX))
        self.assertTrue(label.startswith(groups._get_label(self.obj.data.attributes[renamed])))
        index = next(i for i, item in enumerate(registry(self.obj)) if item.attribute_name == renamed)
        activate_group(self.obj, index)
        self.edit()
        live = bmesh_protected(self.obj)
        groups._set_label(self.obj.data.attributes[renamed], '编辑 鞋底')
        renamed = groups.PREFIX + '编辑 鞋底'
        self.assertIn(renamed, group_names(self.obj))
        index = next(i for i, item in enumerate(registry(self.obj)) if item.attribute_name == renamed)
        self.assertEqual(members(self.obj, index), {0, 4, 8})
        self.assertEqual(bmesh_protected(self.obj), live)
        self.object()
        same = feature_state(self.obj)
        try_invalid_label_write(self.obj.data.attributes[renamed], '   ')
        self.assertEqual(feature_state(self.obj), same)
        self.assertEqual(protected(self.obj), before)

    def test_lock_blocks_membership_edits_but_allows_recall_rename_move_and_explicit_delete(self):
        paint = protected(self.obj)
        name = self.create('Protected selection').attribute_name
        self.edit()
        select_vertices(self.obj, {0, 1, 4})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_assign(), {'FINISHED'})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_lock(group_name=name), {'FINISHED'})
        self.assertTrue(groups.is_locked(self.obj.data.attributes[name]))
        marker = groups._lock_name(name)
        self.assertIn(marker, self.obj.data.attributes)
        self.assertEqual(self.obj.data.attributes[marker].domain, 'POINT')
        self.assertEqual(self.obj.data.attributes[marker].data_type, 'INT')
        select_vertices(self.obj, {2, 5})
        locked = edit_group_state(self.obj)
        self.assert_refused(bpy.ops.character_designer.custom_vertex_group_assign)
        self.assert_refused(bpy.ops.character_designer.custom_vertex_group_remove)
        self.assert_refused(bpy.ops.character_designer.custom_vertex_group_action, action='CLEAR')
        self.assertEqual(edit_group_state(self.obj), locked)
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_select(), {'FINISHED'})
        self.assertEqual(selected(self.obj), {0, 1, 4})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_deselect(), {'FINISHED'})
        self.assertEqual(selected(self.obj), set())
        groups._set_label(self.obj.data.attributes[name], 'Renamed protected')
        renamed = groups.PREFIX + 'Renamed protected'
        self.assertNotIn(marker, self.obj.data.attributes)
        self.assertIn(groups._lock_name(renamed), self.obj.data.attributes)
        self.assertTrue(groups.is_locked(self.obj.data.attributes[renamed]))
        self.assertEqual(members(self.obj, 0), {0, 1, 4})
        self.create('Other selection')
        activate_group(self.obj, 0)
        before_move = semantic_members(self.obj)
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_move(direction='DOWN'), {'FINISHED'})
        self.assertEqual([item.name for item in registry(self.obj)], ['Other selection', 'Renamed protected'])
        self.assertEqual(semantic_members(self.obj), before_move)
        self.assertEqual(active_name(self.obj), renamed)
        self.assertTrue(groups.is_locked(self.obj.data.attributes[renamed]))
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_delete(), {'FINISHED'})
        self.assertNotIn(renamed, self.obj.data.attributes)
        self.assertNotIn(groups._lock_name(renamed), self.obj.data.attributes)
        self.object()
        self.assertEqual(protected(self.obj), paint)

    def test_native_style_filter_sort_and_menu_actions_keep_membership_and_foreign_data(self):
        paint = protected(self.obj)
        self.create('Zulu')
        self.edit()
        select_vertices(self.obj, {0, 1})
        bpy.ops.character_designer.custom_vertex_group_assign()
        alpha = self.create('alpha').attribute_name
        select_vertices(self.obj, {1, 4})
        bpy.ops.character_designer.custom_vertex_group_assign()
        self.create('Empty')
        activate_group(self.obj, 1)
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_action(action='DUPLICATE'), {'FINISHED'})
        duplicate = active_name(self.obj)
        self.assertNotEqual(duplicate, alpha)
        self.assertTrue(duplicate.startswith(groups.PREFIX + 'alpha'))
        self.assertEqual(members(self.obj), {1, 4})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_action(action='CLEAR'), {'FINISHED'})
        self.assertEqual(members(self.obj), set())
        self.assertEqual(members(self.obj, 1), {1, 4})
        meaning = semantic_members(self.obj)
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_action(action='SORT_NAME'), {'FINISHED'})
        labels = [item.name for item in registry(self.obj)]
        self.assertEqual(labels, sorted(labels, key=str.casefold))
        self.assertEqual(semantic_members(self.obj), meaning)

        # The throwaway UI rows must be derived strictly from the native group
        # catalog, never from another persistent membership/name registry.
        # Native UIList applies its reverse-display toggle after this callback.
        ui = SimpleNamespace(filter_name='al', use_filter_invert=False,
            use_filter_sort_alpha=False, use_filter_sort_reverse=False,
            bitflag_filter_item=1 << 30, bitflag_item_never_show=1 << 31)
        unchanged = edit_group_state(self.obj)
        groups.sync_view(bpy.context)
        wm = bpy.context.window_manager
        rows = getattr(wm, groups.VIEW_PROPERTY)
        self.assertEqual({row.native_name for row in rows}, group_names(self.obj))

        def visible(flags, order):
            shown = [(index, row.native_name) for index, row in enumerate(rows)
                     if flags[index] & ui.bitflag_filter_item
                     and not flags[index] & ui.bitflag_item_never_show]
            return [name for _index, name in sorted(shown, key=lambda row: order[row[0]] if order else row[0])]

        filter_items = groups.CHARACTERDESIGNER_UL_custom_vertex_groups.filter_items
        flags, order = filter_items(ui, bpy.context, wm, groups.VIEW_PROPERTY)
        self.assertEqual(set(visible(flags, order)), {alpha, duplicate})
        ui.use_filter_invert = True
        flags, order = filter_items(ui, bpy.context, wm, groups.VIEW_PROPERTY)
        self.assertEqual(set(visible(flags, order)), group_names(self.obj) - {alpha, duplicate})
        ui.filter_name, ui.use_filter_invert, ui.use_filter_sort_alpha = '', False, True
        flags, order = filter_items(ui, bpy.context, wm, groups.VIEW_PROPERTY)
        self.assertEqual(len(order), len(rows))
        self.assertEqual(visible(flags, order), sorted(group_names(self.obj), key=str.casefold))
        ui.use_filter_sort_reverse = True
        filter_items(ui, bpy.context, wm, groups.VIEW_PROPERTY)
        groups.sync_view(bpy.context)
        self.assertEqual(edit_group_state(self.obj), unchanged)

        for action, expected in (('LOCK_ALL', True), ('INVERT_LOCKS', False),
                                  ('LOCK_ALL', True), ('UNLOCK_ALL', False)):
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_action(action=action), {'FINISHED'})
            self.assertTrue(all(groups.is_locked(self.obj.data.attributes[item.attribute_name]) == expected
                                for item in registry(self.obj)))
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_lock(group_name=alpha), {'FINISHED'})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_action(action='DELETE_EMPTY_UNLOCKED'), {'FINISHED'})
        self.assertEqual(group_names(self.obj), {groups.PREFIX + 'Zulu', alpha})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_action(action='DELETE_UNLOCKED'), {'FINISHED'})
        self.assertEqual(group_names(self.obj), {alpha})
        self.assertEqual(members(self.obj, 0), {1, 4})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_action(action='DELETE_ALL'), {'FINISHED'})
        self.assertEqual(registry(self.obj), [])
        self.object()
        self.assertFalse(any(attr.name.startswith(groups.LOCK_PREFIX) for attr in self.obj.data.attributes))
        self.assertEqual(protected(self.obj), paint)

    def test_recall_from_face_mode_switches_to_exact_vertex_selection(self):
        before = protected(self.obj)
        self.create()
        self.edit()
        select_vertices(self.obj, {0, 4})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_assign(), {'FINISHED'})
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.faces.ensure_lookup_table()
        select_vertices(self.obj, set())
        bpy.context.scene.tool_settings.mesh_select_mode = (False, False, True)
        bm.select_mode = {'FACE'}
        bm.faces[3].select_set(True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_select(), {'FINISHED'})
        self.assertEqual(tuple(bpy.context.scene.tool_settings.mesh_select_mode), (True, False, False))
        self.assertEqual(selected(self.obj), {0, 4})
        self.assertEqual(members(self.obj), {0, 4})
        self.object()
        self.assertEqual(protected(self.obj), before)

    def test_membership_operators_require_edit_mode_without_touching_data(self):
        self.create()
        before = feature_state(self.obj)
        for operator in (bpy.ops.character_designer.custom_vertex_group_assign,
                         bpy.ops.character_designer.custom_vertex_group_remove,
                         bpy.ops.character_designer.custom_vertex_group_select,
                         bpy.ops.character_designer.custom_vertex_group_deselect):
            self.assert_refused(operator)
            self.assertEqual(feature_state(self.obj), before)

    def test_multimesh_edit_and_shared_mesh_refuse_without_partial_changes(self):
        self.create()
        other = fixture('Other independently authored mesh')
        # The first mesh's attribute cannot be renamed through another active
        # mesh, even though both attribute wrappers are valid and local.
        before_other_context = feature_state(self.obj), feature_state(other)
        try_invalid_label_write(self.obj.data.attributes[registry(self.obj)[0].attribute_name], 'Wrong owner')
        self.assertEqual((feature_state(self.obj), feature_state(other)), before_other_context)
        activate(self.obj)
        other.select_set(True)
        self.edit()
        self.assertEqual(len(bpy.context.objects_in_mode_unique_data), 2)
        before = bmesh_protected(self.obj), bmesh_protected(other)
        self.assert_refused(bpy.ops.character_designer.custom_vertex_group_assign)
        self.assert_refused(bpy.ops.character_designer.custom_vertex_group_deselect)
        self.assert_refused(bpy.ops.character_designer.custom_vertex_group_new, name='Must not create')
        self.assertEqual((bmesh_protected(self.obj), bmesh_protected(other)), before)
        self.object()
        activate(self.obj)
        shared = bpy.data.objects.new('Shared mesh user', self.obj.data)
        bpy.context.scene.collection.objects.link(shared)
        before = feature_state(self.obj)
        self.assert_refused(bpy.ops.character_designer.custom_vertex_group_new, name='Must not create')
        self.assert_refused(bpy.ops.character_designer.custom_vertex_group_delete)
        try_invalid_label_write(self.obj.data.attributes[registry(self.obj)[0].attribute_name], 'Shared data')
        self.assertEqual(feature_state(self.obj), before)

    def test_linked_mesh_refuses_to_create_or_claim_attributes(self):
        self.create('Linked saved group')
        with tempfile.TemporaryDirectory(prefix='cd-custom-groups-linked-') as folder:
            path = str(Path(folder) / 'linked_mesh.blend')
            source_name = self.obj.data.name
            bpy.data.libraries.write(path, {self.obj.data})
            with bpy.data.libraries.load(path, link=True) as (available, loaded):
                self.assertIn(source_name, available.meshes)
                loaded.meshes = [source_name]
            linked = bpy.data.objects.new('Local object with linked mesh', loaded.meshes[0])
            bpy.context.scene.collection.objects.link(linked)
            activate(linked)
            self.assertIsNotNone(linked.data.library)
            before = feature_state(linked)
            self.assert_refused(bpy.ops.character_designer.custom_vertex_group_new, name='Must not create')
            try_invalid_label_write(linked.data.attributes[registry(linked)[0].attribute_name], 'Linked data')
            self.assertEqual(feature_state(linked), before)

    def test_non_group_active_attributes_refuse_membership_and_delete(self):
        self.create()
        for attr_name, attr_type, domain in (('Custom_Artist float', 'FLOAT', 'POINT'),
                                             ('Custom_Artist edge', 'INT', 'EDGE'),
                                             ('Artist integer', 'INT', 'POINT')):
            attr = self.obj.data.attributes.new(name=attr_name, type=attr_type, domain=domain)
            self.obj.data.attributes.active_index = next(i for i, item in enumerate(self.obj.data.attributes)
                                                         if item.name == attr.name)
            before = feature_state(self.obj)
            self.assert_refused(bpy.ops.character_designer.custom_vertex_group_delete)
            self.assertEqual(feature_state(self.obj), before)
            self.edit()
            select_vertices(self.obj, {2, 3})
            live = bmesh_protected(self.obj)
            self.assert_refused(bpy.ops.character_designer.custom_vertex_group_assign)
            self.assert_refused(bpy.ops.character_designer.custom_vertex_group_remove)
            self.assert_refused(bpy.ops.character_designer.custom_vertex_group_select)
            self.assert_refused(bpy.ops.character_designer.custom_vertex_group_deselect)
            self.assertEqual(bmesh_protected(self.obj), live)
            self.object()
            self.assertEqual(self.obj.data.attributes[attr_name].data_type, attr_type)
            self.assertEqual(self.obj.data.attributes[attr_name].domain, domain)
        self.assertEqual(len(registry(self.obj)), 1)
        before = feature_state(self.obj)
        try_invalid_label_write(self.obj.data.attributes['Custom_Artist float'], 'Must stay foreign')
        self.assertEqual(feature_state(self.obj), before)

    def test_native_save_reopen_keeps_independent_membership_and_author_inputs(self):
        before = protected(self.obj)
        name = self.obj.name
        self.create('Saved ankle loops')
        self.edit()
        select_vertices(self.obj, {0, 3, 6})
        bpy.ops.character_designer.custom_vertex_group_assign()
        self.object()
        groups._set_label(self.obj.data.attributes[registry(self.obj)[0].attribute_name], 'Saved heel 选区')
        self.assertEqual(registry(self.obj)[0].name, 'Saved heel 选区')
        expected = feature_state(self.obj)
        with tempfile.TemporaryDirectory(prefix='cd-custom-groups-save-') as folder:
            path = str(Path(folder) / 'custom_groups.blend')
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=path), {'FINISHED'})
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path, use_scripts=True), {'FINISHED'})
            self.obj = bpy.data.objects[name]
            self.assertEqual(feature_state(self.obj), expected)
            self.assertEqual(protected(self.obj), before)
            self.assertEqual(members(self.obj, 0), {0, 3, 6})
            activate(self.obj)
            activate_group(self.obj, 0)
            self.edit()
            select_vertices(self.obj, {2, 8})
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_select(), {'FINISHED'})
            self.assertEqual(selected(self.obj), {0, 3, 6})


class CustomVertexGroupHistoryTests(_CustomVertexGroupFixture):
    """Root launches these history checks in a disposable native GUI process."""

    @unittest.skipIf(bpy.app.background, 'Native GUI history is a separately launched root-owned check.')
    def test_actual_view_row_rna_rename_callback_pushes_native_undo(self):
        original_name = self.create('Proxy rename').attribute_name
        obj_name = self.obj.name
        paint = protected(self.obj)
        previous = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = True

        def native_state():
            self.obj = bpy.data.objects[obj_name]
            self.assertEqual(self.obj.mode, 'EDIT')
            return edit_group_state(self.obj)

        def view_row(name):
            groups.sync_view(bpy.context)
            rows = getattr(bpy.context.window_manager, groups.VIEW_PROPERTY)
            self.assertEqual({row.native_name for row in rows}, group_names(self.obj))
            return next(row for row in rows if row.native_name == name)

        try:
            self.edit()
            select_vertices(self.obj, {0, 4, 8})
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_assign(), {'FINISHED'})
            self.assertEqual(bpy.ops.ed.undo_push(message='Before actual group row RNA edit'), {'FINISHED'})
            before = native_state()
            row = view_row(original_name)
            self.assertEqual(row.bl_rna.properties['name'].type, 'STRING')
            self.assertEqual(row.name, 'Proxy rename')
            # This is the actual inline-field RNA callback. Do not add an
            # after-marker: the callback's own normal UNDO operator must push
            # the native history step for a single Undo to restore membership.
            row.name = 'Through UI 鞋底'
            renamed_name = groups.PREFIX + 'Through UI 鞋底'
            self.assertEqual(group_names(self.obj), {renamed_name})
            self.assertEqual(members(self.obj, 0), {0, 4, 8})
            renamed = native_state()
            self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
            self.assertEqual(native_state(), before)
            self.assertEqual(view_row(original_name).name, 'Proxy rename')
            self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
            self.assertEqual(native_state(), renamed)
            row = view_row(renamed_name)
            self.assertEqual(row.name, 'Through UI 鞋底')
            self.object()
            self.assertEqual(protected(self.obj), paint)

            # A stale row cannot rename a different mesh even when that mesh
            # deliberately has the same native group key.
            other = fixture('Other mesh with matching group key')
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_new(
                name='Through UI 鞋底'), {'FINISHED'})
            both = feature_state(self.obj), feature_state(other)
            try:
                row.name = 'Must not cross mesh ownership'
            except (ValueError, RuntimeError, TypeError, ReferenceError):
                pass
            self.assertEqual((feature_state(self.obj), feature_state(other)), both)
        finally:
            bpy.context.preferences.edit.use_global_undo = previous

    @unittest.skipIf(bpy.app.background, 'Native GUI history is a separately launched root-owned check.')
    def test_friendly_edit_rename_undo_redo_keeps_native_catalog_and_members(self):
        self.create('Before rename')
        obj_name = self.obj.name
        before_paint = protected(self.obj)
        previous = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = True

        def current():
            self.obj = bpy.data.objects[obj_name]
            self.assertEqual(self.obj.mode, 'EDIT')
            bm = bmesh.from_edit_mesh(self.obj.data)
            bm.verts.ensure_lookup_table()
            return {
                'catalog': tuple(item.attribute_name for item in registry(self.obj)),
                'point_integer_layers': tuple(sorted((name, tuple(vertex[bm.verts.layers.int[name]]
                    for vertex in bm.verts)) for name in bm.verts.layers.int.keys())),
                'protected': bmesh_protected(self.obj),
            }

        try:
            self.edit()
            select_vertices(self.obj, {0, 4, 8})
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_assign(), {'FINISHED'})
            group_name = registry(self.obj)[0].attribute_name
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_lock(group_name=group_name), {'FINISHED'})
            self.assertEqual(bpy.ops.ed.undo_push(message='Before friendly Edit group rename'), {'FINISHED'})
            before = current()
            old_name = before['catalog'][0]
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_rename(
                'EXEC_DEFAULT', True, group_name=old_name, name='Renamed 鞋底'), {'FINISHED'})
            self.assertEqual(group_names(self.obj), {groups.PREFIX + 'Renamed 鞋底'})
            self.assertEqual(members(self.obj, 0), {0, 4, 8})
            renamed = current()
            self.assertNotEqual(renamed['catalog'], before['catalog'])
            self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
            self.assertEqual(current(), before)
            self.assertEqual(members(self.obj, 0), {0, 4, 8})
            self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
            self.assertEqual(current(), renamed)
            self.assertEqual(members(self.obj, 0), {0, 4, 8})
            self.assertTrue(groups.is_locked(self.obj.data.attributes[groups.PREFIX + 'Renamed 鞋底']))
            self.object()
            self.assertEqual(protected(self.obj), before_paint)
        finally:
            bpy.context.preferences.edit.use_global_undo = previous

    @unittest.skipIf(bpy.app.background, 'Native GUI history is a separately launched root-owned check.')
    def test_lock_and_manual_reorder_undo_redo_keep_names_members_and_marker_together(self):
        initial_paint = protected(self.obj)
        # Exercise the same native invoke path as the + button. A dialog would
        # return RUNNING_MODAL instead of completing these two named groups.
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_new(
            'INVOKE_DEFAULT', name='Group'), {'FINISHED'})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_new(
            'INVOKE_DEFAULT', name='Group'), {'FINISHED'})
        self.assertEqual([item.name for item in registry(self.obj)], ['Group', 'Group.001'])
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_delete(), {'FINISHED'})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_delete(), {'FINISHED'})
        self.assertEqual(protected(self.obj), initial_paint)
        first = self.create('First region').attribute_name
        second = self.create('Second region').attribute_name
        obj_name = self.obj.name
        paint = protected(self.obj)
        previous = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = True

        def current():
            self.obj = bpy.data.objects[obj_name]
            self.assertEqual(self.obj.mode, 'EDIT')
            return edit_group_state(self.obj)

        try:
            self.edit()
            activate_group(self.obj, 0)
            select_vertices(self.obj, {0, 4})
            bpy.ops.character_designer.custom_vertex_group_assign()
            activate_group(self.obj, 1)
            select_vertices(self.obj, {1, 8})
            bpy.ops.character_designer.custom_vertex_group_assign()
            activate_group(self.obj, 0)
            self.assertEqual(bpy.ops.ed.undo_push(message='Before native custom group lock'), {'FINISHED'})
            unlocked = current()
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_lock(group_name=first), {'FINISHED'})
            self.assertTrue(groups.is_locked(self.obj.data.attributes[first]))
            self.assertEqual(bpy.ops.ed.undo_push(message='After native custom group lock'), {'FINISHED'})
            locked = current()
            self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
            self.assertEqual(current(), unlocked)
            self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
            self.assertEqual(current(), locked)

            activate_group(self.obj, 0)
            self.assertEqual(bpy.ops.ed.undo_push(message='Before native custom group move'), {'FINISHED'})
            before_move = current()
            meaning = semantic_members(self.obj)
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_move(direction='DOWN'), {'FINISHED'})
            self.assertEqual([item.attribute_name for item in registry(self.obj)], [second, first])
            self.assertEqual(semantic_members(self.obj), meaning)
            self.assertTrue(groups.is_locked(self.obj.data.attributes[first]))
            self.assertEqual(bpy.ops.ed.undo_push(message='After native custom group move'), {'FINISHED'})
            moved = current()
            self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
            self.assertEqual(current(), before_move)
            self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
            self.assertEqual(current(), moved)
            self.assertEqual(semantic_members(self.obj), {first: (0, 4), second: (1, 8)})
            self.object()
            self.assertEqual(protected(self.obj), paint)
        finally:
            bpy.context.preferences.edit.use_global_undo = previous

    @unittest.skipIf(bpy.app.background, 'Native GUI history is a separately launched root-owned check.')
    def test_edit_new_and_delete_undo_redo_restore_native_catalog_and_membership(self):
        obj_name = self.obj.name
        before = protected(self.obj)
        previous = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = True

        def current():
            # Native Edit Undo may replace Object/Mesh/attribute wrappers.
            # Resolve current IDs after every history transition.
            self.obj = bpy.data.objects[obj_name]
            self.assertEqual(self.obj.mode, 'EDIT')
            bm = bmesh.from_edit_mesh(self.obj.data)
            bm.verts.ensure_lookup_table()
            return {
                'catalog': tuple((item.name, item.attribute_name) for item in registry(self.obj)),
                # Include every integer layer, including non-group layers,
                # rather than trusting a second catalog or saved vertex IDs.
                'point_integer_layers': tuple(sorted((name, tuple(vertex[bm.verts.layers.int[name]]
                    for vertex in bm.verts)) for name in bm.verts.layers.int.keys())),
                'protected': bmesh_protected(self.obj),
            }

        try:
            self.edit()
            select_vertices(self.obj, {0, 3, 6})
            self.assertEqual(bpy.ops.ed.undo_push(message='Before Edit Mode custom group New'), {'FINISHED'})
            no_group = current()
            self.assertEqual(no_group['catalog'], ())
            self.create('Edit history ankle loops')
            self.assertEqual(members(self.obj), set())
            self.assertEqual(bpy.ops.ed.undo_push(message='After Edit Mode custom group New'), {'FINISHED'})
            created = current()
            self.assertEqual(len(created['catalog']), 1)
            name = created['catalog'][0][1]

            self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
            self.assertEqual(current(), no_group, 'New Undo must remove the native catalog entry and membership layer.')
            self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
            self.assertEqual(current(), created, 'New Redo must recreate the same empty owned attribute.')
            self.assertEqual(members(self.obj, 0), set())

            # Active attribute is native UI focus, not saved membership proof.
            # Choose the restored row explicitly before the next operation.
            activate_group(self.obj, 0)

            # Delete a populated group, so restoration proves membership as
            # well as matching names and actual native attribute allocation.
            select_vertices(self.obj, {1, 4, 7})
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_assign(), {'FINISHED'})
            self.assertEqual(members(self.obj), {1, 4, 7})
            self.assertEqual(bpy.ops.ed.undo_push(message='Before Edit Mode custom group Delete'), {'FINISHED'})
            populated = current()
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_delete(), {'FINISHED'})
            self.assertEqual(bpy.ops.ed.undo_push(message='After Edit Mode custom group Delete'), {'FINISHED'})
            deleted = current()
            self.assertEqual(deleted['catalog'], ())
            self.assertNotIn(name, dict(deleted['point_integer_layers']))

            self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
            self.assertEqual(current(), populated, 'Delete Undo must restore the native catalog and members together.')
            self.assertEqual(members(self.obj, 0), {1, 4, 7})
            self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
            self.assertEqual(current(), deleted, 'Delete Redo must remove the native catalog and membership layer.')
            self.object()
            self.assertNotIn(name, self.obj.data.attributes)
            self.assertEqual(protected(self.obj), before)
        finally:
            bpy.context.preferences.edit.use_global_undo = previous

    @unittest.skipIf(bpy.app.background, 'Native GUI history is a separately launched root-owned check.')
    def test_public_assign_undo_redo_preserves_current_paint_and_shape_keys(self):
        self.create('Undo ankle loops')
        obj_name = self.obj.name
        before = protected(self.obj)
        previous = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = True
        try:
            self.edit()
            select_vertices(self.obj, {0, 4, 8})
            self.assertEqual(bpy.ops.ed.undo_push(message='Before custom selection membership'), {'FINISHED'})
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_assign(), {'FINISHED'})
            self.assertEqual(members(self.obj), {0, 4, 8})
            self.assertEqual(bpy.ops.ed.undo_push(message='After custom selection membership'), {'FINISHED'})
            self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
            self.obj = bpy.data.objects[obj_name]
            self.assertEqual(members(self.obj, 0), set())
            self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
            self.obj = bpy.data.objects[obj_name]
            self.assertEqual(members(self.obj, 0), {0, 4, 8})
            self.object()
            self.assertEqual(protected(self.obj), before)
        finally:
            bpy.context.preferences.edit.use_global_undo = previous


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CustomVertexGroupTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
