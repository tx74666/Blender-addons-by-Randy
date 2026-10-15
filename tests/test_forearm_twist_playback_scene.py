"""Pure regressions for copied playback graph ownership and remapping.

Execute the production helpers through AST with small native-ID/RNA doubles.
These tests cover known protection failures; they do not certify Blender copy
semantics, actual Action slot setters, scene equality or playback performance.
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
import unittest


PATH = (Path(__file__).resolve().parents[1]
        / 'addons/character_designer/forearm_twist_playback_scene.py')


class Pointer:
    def __init__(self, pointer):
        self.pointer = pointer

    def as_pointer(self):
        return self.pointer


class CustomProperties(Pointer):
    def __init__(self, pointer, values=None):
        super().__init__(pointer)
        self.values = dict(values or {})
        self.property_writes = []
        self.key_reads = 0

    def keys(self):
        self.key_reads += 1
        return self.values.keys()

    def __getitem__(self, name):
        return self.values[name]

    def __setitem__(self, name, value):
        self.property_writes.append((name, value))
        self.values[name] = value


class ID(CustomProperties):
    def __init__(self, pointer, name='ID', uid=None, values=None):
        super().__init__(pointer, values)
        self.name = name
        self.session_uid = pointer + 1000 if uid is None else uid
        self.id_data = self
        self.use_fake_user = True
        self.bl_rna = SimpleNamespace(identifier='ID', properties=[])
        self.native_writes = []
        self._native_values = {}

    def set_pointer_fields(self, **values):
        self._native_values = dict(values)
        self.bl_rna.properties = [SimpleNamespace(identifier=name, type='POINTER', is_readonly=False)
                                  for name in values]

    def __getattr__(self, name):
        values = self.__dict__.get('_native_values', {})
        if name in values:
            return values[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        values = self.__dict__.get('_native_values', {})
        if name in values:
            self.native_writes.append((name, value))
            values[name] = value
        else:
            object.__setattr__(self, name, value)


class Object(ID):
    pass


class MatrixRows(tuple):
    def copy(self):
        return MatrixRows(tuple(tuple(row) for row in self))


class ParentResetObject(Object):
    """Native parent/type/bone assignment resets inverse before restoration."""
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name in {'parent', 'parent_type', 'parent_bone'} and self.__dict__.get('reset_enabled'):
            super().__setattr__('matrix_parent_inverse', MatrixRows(
                tuple(tuple(float(i == j) for j in range(4)) for i in range(4))))


class RNA(CustomProperties):
    """A non-ID struct with an explicit ID owner and observable writes."""
    def __init__(self, pointer, owner, values=None):
        super().__init__(pointer, values)
        self.id_data = owner
        self.bl_rna = SimpleNamespace(identifier='RNA', properties=[])


class Collection(list):
    def __init__(self, items=()):
        super().__init__(items)
        self.removed = []

    def remove(self, item, *, do_unlink=False):
        self.removed.append((item, do_unlink))
        super().remove(item)


class Animation(Pointer):
    """Assignment intentionally auto-selects a slot to expose None loss."""
    def __init__(self, pointer, action=None, slot=None, tracks=(), drivers=()):
        super().__init__(pointer)
        self._action = action
        self.action_slot = slot
        self.nla_tracks = list(tracks)
        self.drivers = list(drivers)
        self.action_writes = []

    @property
    def action(self):
        return self._action

    @action.setter
    def action(self, value):
        self.action_writes.append(value)
        self._action = value
        self.action_slot = value.slots[0] if value is not None and value.slots else None

    @property
    def action_slot_handle(self):
        return self.action_slot.handle if self.action_slot is not None else 0


def namespace():
    tree = ast.parse(PATH.read_text(encoding='utf-8'))
    definitions = {'PlaybackSceneError', '_require', '_pointer', '_copy_id_properties',
                   '_remap_rna', '_remap_animation', '_actions_for_id', '_action_curves',
                   '_detach_unused_asset_references', '_copy_view_layer_state', 'PlaybackSceneSession'}
    constants = {'_DATA_COLLECTIONS', '_GEOMETRY_COLLECTIONS', '_READONLY_OBJECT_POINTERS'}
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in definitions:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants
                                                  for target in node.targets):
            nodes.append(node)
    bpy = SimpleNamespace(types=SimpleNamespace(ID=ID, Object=Object),
                          data=SimpleNamespace(),
                          context=SimpleNamespace(window_manager=SimpleNamespace(windows=[])))
    scope = {'bpy': bpy, 'dataclass': dataclass, 'field': field, '__name__': __name__}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(PATH), 'exec'), scope)
    return scope


class SceneRemapTests(unittest.TestCase):
    def setUp(self):
        self.scope = namespace()

    def base_fixture(self):
        class Members(dict):
            active = None
            def __iter__(self):
                return iter(self.values())
        class Layer:
            def __init__(self, name, objects, active):
                self.name, self.objects = name, Members((obj.name_full, obj) for obj in objects)
                self.objects.active = active
            def update(self):
                for obj in self.objects:
                    if obj.hidden[self]:
                        obj.selected[self] = False
        class Actor(Object):
            def __init__(self, pointer, name):
                super().__init__(pointer, name)
                self.name_full, self.hidden, self.selected, self.writes = name, {}, {}, []
            def hide_get(self, *, view_layer):
                return self.hidden[view_layer]
            def select_get(self, *, view_layer):
                return self.selected[view_layer]
            def hide_set(self, value, *, view_layer):
                self.writes.append(('hide', view_layer.name, value))
                self.hidden[view_layer] = value
            def select_set(self, value, *, view_layer):
                self.writes.append(('select', view_layer.name, value))
                self.selected[view_layer] = value
        class Collection(ID):
            def __init__(self, pointer, name, hide):
                super().__init__(pointer, name)
                self.name_full, self.hide_select = name, hide
        old, new = [Actor(1, 'Rig'), Actor(2, 'Backup')], [Actor(3, 'Rig Playback'), Actor(4, 'Backup Playback')]
        left = [Layer('Main', old, old[0]), Layer('Other', [old[0]], None)]
        right = [Layer('Main', new, None), Layer('Other', [new[0]], new[0])]
        for index, layers in enumerate((left, right)):
            for layer in layers:
                for obj in layer.objects:
                    obj.hidden[layer], obj.selected[layer] = False, False
        old[1].hidden[left[0]], old[0].selected[left[0]] = True, True
        old[0].hidden[left[1]] = True
        source, target = ID(10, 'Source'), ID(11, 'Target')
        source.view_layers, source.objects = left, old
        target.view_layers, target.objects = right, new
        a, b = Collection(12, 'Locked', True), Collection(13, 'Locked Playback', False)
        session = self.scope['PlaybackSceneSession'](source, target, object_map=dict(zip(old, new)),
            id_map={source: target, a: b, **dict(zip(old, new))})
        self.scope['bpy'].types.Collection = Collection
        self.scope['bpy'].app = SimpleNamespace(background=True)
        self.scope['bpy'].context.scene, self.scope['bpy'].context.view_layer = source, left[0]
        return session, old, new, left, right, a, b

    def test_per_layer_base_copy_restores_hidden_selected_active_and_excluded_membership(self):
        session, old, new, left, right, a, b = self.base_fixture()
        result = self.scope['_copy_view_layer_state'](session)
        self.assertEqual(result['context_view_layer'], 'Main')
        self.assertEqual([row['active'] for row in result['layers']], ['Rig Playback', None])
        self.assertTrue(new[1].hide_get(view_layer=right[0]))
        self.assertTrue(new[0].select_get(view_layer=right[0]))
        self.assertTrue(new[0].hide_get(view_layer=right[1]))
        self.assertTrue(b.hide_select)
        self.assertEqual(old[0].writes + old[1].writes, [])
        self.assertFalse(any(layer == 'Other' for kind, layer, value in new[1].writes))
        self.assertEqual(result, self.scope['_copy_view_layer_state'](session))

    def test_base_copy_rejects_membership_before_any_target_writes(self):
        session, old, new, left, right, a, b = self.base_fixture()
        right[1].objects[new[1].name_full] = new[1]
        with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'membership'):
            self.scope['_copy_view_layer_state'](session)
        self.assertFalse(b.hide_select)
        self.assertEqual(new[0].writes + new[1].writes, [])

    def test_base_copy_does_not_silently_accept_native_setter_readback_failure(self):
        session, old, new, left, right, a, b = self.base_fixture()
        new[1].hide_set = lambda *args, **kwargs: None
        with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'state differs'):
            self.scope['_copy_view_layer_state'](session)
        self.assertEqual(old[0].writes + old[1].writes, [])

    def test_second_custom_property_remap_keeps_the_owned_clone(self):
        source, clone = Object(1, 'Source'), Object(2, 'Clone')
        target = ID(3, values={'nested': {'rig': source}})
        remap = self.scope['_copy_id_properties']
        remap(None, target, {source: clone})
        remap(None, target, {source: clone})
        self.assertIs(target['nested']['rig'], clone)
        self.assertEqual(source.property_writes, [])
        self.assertEqual(clone.property_writes, [])

    def test_second_native_pointer_remap_does_not_report_owned_as_external(self):
        source, clone = Object(1, 'Source'), Object(2, 'Clone')
        source_root, target_root = ID(3), ID(4)
        target_root.set_pointer_fields(dependency=source)
        id_map = {source: clone, source_root: target_root}
        external = []
        self.scope['_remap_rna'](target_root, id_map, set(), external)
        self.scope['_remap_rna'](target_root, id_map, set(), external)
        self.assertIs(target_root.dependency, clone)
        self.assertEqual(target_root.native_writes, [('dependency', clone)])
        self.assertEqual(external, [])

    def test_unset_registered_pg_pointer_is_not_read_or_materialized(self):
        class LazyOwner(ID):
            def is_property_set(self, name):
                return False
            @property
            def cycles(self):
                self.materialized = True
                raise AssertionError('Unset registered PG getter ran')
        source, target = ID(1), LazyOwner(2)
        target.materialized = False
        group_base = SimpleNamespace(identifier='PropertyGroup', base=None)
        target.bl_rna.properties = [SimpleNamespace(identifier='cycles', type='POINTER', is_runtime=True,
            fixed_type=SimpleNamespace(identifier='CyclesWorldSettings', base=group_base))]
        self.scope['_remap_rna'](target, {source: target}, set(), [])
        self.assertFalse(target.materialized)
        self.assertEqual(target.values, {})

    def test_stored_registered_pg_raw_and_native_object_references_still_remap(self):
        source, target, old_obj, new_obj = ID(1), ID(2), Object(3), Object(4)
        nested = RNA(5, target, values={'raw_object': old_obj})
        nested.reference = old_obj
        nested.bl_rna.properties = [SimpleNamespace(identifier='reference', type='POINTER',
            is_runtime=False, is_readonly=False)]
        target.group = nested
        target.is_property_set = lambda name: True
        target.bl_rna.properties = [SimpleNamespace(identifier='group', type='POINTER', is_runtime=True,
            fixed_type=SimpleNamespace(identifier='StoredGroup', base=SimpleNamespace(identifier='PropertyGroup', base=None)))]
        external = []
        self.scope['_remap_rna'](target, {source: target, old_obj: new_obj}, set(), external)
        self.assertIs(nested['raw_object'], new_obj)
        self.assertIs(nested.reference, new_obj)
        self.assertEqual(external, [])

    def test_native_pg_runtime_non_pg_pointer_and_collections_are_not_skipped(self):
        for kind, runtime, fixed_name, collection in (
                ('POINTER', False, 'PropertyGroup', False),
                ('POINTER', True, 'Object', False),
                ('COLLECTION', True, 'PropertyGroup', True)):
            with self.subTest(kind=kind, runtime=runtime, fixed_name=fixed_name):
                source, target, old_obj, new_obj = ID(1), ID(2), Object(3), Object(4)
                nested = RNA(5, target, values={'object': old_obj})
                target.value = [nested] if collection else (old_obj if fixed_name == 'Object' else nested)
                target.is_property_set = lambda name: False
                target.bl_rna.properties = [SimpleNamespace(identifier='value', type=kind, is_runtime=runtime,
                    is_readonly=False, fixed_type=SimpleNamespace(identifier=fixed_name, base=None))]
                self.scope['_remap_rna'](target, {source: target, old_obj: new_obj}, set(), [])
                if fixed_name == 'Object':
                    self.assertIs(target.value, new_obj)
                else:
                    self.assertIs(nested['object'], new_obj)

    def test_parent_pointer_remap_preserves_inverse_and_binding_without_world_assignment(self):
        for parent_type, bone, vertices in (('OBJECT', '', (0, 0, 0)),
                                            ('BONE', 'Hand.L', (0, 0, 0)),
                                            ('VERTEX_3', '', (2, 4, 7))):
            with self.subTest(parent_type=parent_type):
                source_parent, clone_parent = Object(1), Object(2)
                source, target = Object(3), ParentResetObject(4)
                target.set_pointer_fields(parent=source_parent)
                target.parent_type, target.parent_bone = parent_type, bone
                target.parent_vertices = vertices
                target.matrix_parent_inverse = MatrixRows(((2., -0., 0., 3.), (0., .5, 0., -2.),
                                                          (0., 0., 1., 4.), (0., 0., 0., 1.)))
                before = tuple(tuple(float(value).hex() for value in row) for row in target.matrix_parent_inverse)
                target.reset_enabled = True
                id_map = {source_parent: clone_parent, source: target}
                self.scope['_remap_rna'](target, id_map, set(), [])
                self.assertIs(target.parent, clone_parent)
                self.assertEqual((target.parent_type, target.parent_bone, tuple(target.parent_vertices)),
                                 (parent_type, bone, vertices))
                self.assertEqual(tuple(tuple(float(value).hex() for value in row) for row in target.matrix_parent_inverse), before)
                self.assertEqual(target.native_writes, [('parent', clone_parent)])
                self.scope['_remap_rna'](target, id_map, set(), [])
                self.assertEqual(target.native_writes, [('parent', clone_parent)])
                self.assertNotIn('matrix_world', target.__dict__)
                self.assertEqual(source.native_writes, [])

    def test_parent_inverse_readback_rejects_lost_signed_zero(self):
        class WrongInverse(ParentResetObject):
            def __setattr__(self, name, value):
                if name == 'matrix_parent_inverse' and self.__dict__.get('corrupt_restore'):
                    value = MatrixRows(tuple(tuple(0. if item == 0. else item for item in row) for row in value))
                super().__setattr__(name, value)
        old, new, source, target = Object(1), Object(2), Object(3), WrongInverse(4)
        target.set_pointer_fields(parent=old)
        target.parent_type, target.parent_bone, target.parent_vertices = 'OBJECT', '', (0, 0, 0)
        target.matrix_parent_inverse = MatrixRows(((1., -0., 0., 0.), (0., 1., 0., 0.),
                                                  (0., 0., 1., 0.), (0., 0., 0., 1.)))
        target.reset_enabled = target.corrupt_restore = True
        with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'parent inverse changed'):
            self.scope['_remap_rna'](target, {old: new, source: target}, set(), [])

    def test_source_owned_subtree_is_rejected_before_reading_or_writing_properties(self):
        source_owner, target_owner = ID(1, 'Source'), ID(2, 'Clone')
        source_reference, clone_reference = Object(3), Object(4)
        shared_subtree = RNA(5, source_owner, {'ref': source_reference})
        target_owner.set_pointer_fields(child=shared_subtree)
        with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'not owned by a copied ID'):
            self.scope['_remap_rna'](target_owner,
                                      {source_owner: target_owner, source_reference: clone_reference}, set(), [])
        self.assertEqual(shared_subtree.key_reads, 0)
        self.assertEqual(shared_subtree.property_writes, [])
        self.assertIs(shared_subtree.values['ref'], source_reference)
        self.assertEqual(source_owner.property_writes, [])

    def test_external_custom_object_reference_is_rejected_and_retained(self):
        source_root, target_root = ID(1), ID(2)
        external = Object(3, 'External')
        target_root.values['unsafe'] = external
        with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'Unmapped external Object custom property'):
            self.scope['_copy_id_properties'](None, target_root, {source_root: target_root})
        self.assertIs(target_root['unsafe'], external)
        self.assertEqual(target_root.property_writes, [])
        self.assertEqual(external.property_writes, [])

    def asset_fixture(self):
        source, target = ID(100, 'Source Scene'), ID(200, 'Clone Scene')
        orphan, member = Object(300, 'Unlinked Hair'), Object(400, 'Scene Mesh')
        orphan.users_scene, member.users_scene = [], [source]
        source.objects = [member]
        def row(pointer, owner, obj):
            result = RNA(pointer, owner)
            result.bl_rna.identifier = 'CharacterDesignerAsset'
            result.object, result.role = obj, 'HAIR'
            return result
        old = [row(1, source, orphan), row(2, source, member)]
        new = [row(3, target, orphan), row(4, target, member)]
        source.character_designer_setup = SimpleNamespace(assets=old)
        target.character_designer_setup = SimpleNamespace(assets=new)
        for scene in (source, target):
            scene.bl_rna.properties = {'character_designer_setup': SimpleNamespace(type='POINTER')}
            scene.is_property_set = lambda name: name == 'character_designer_setup'
        source.animation_data = None
        session = self.scope['PlaybackSceneSession'](source, target)
        session.id_map = {source: target, member: Object(500)}
        return session, orphan, old, new

    def setup_getter_trap(self, *, declared=True, stored=False):
        class Scene:
            def __init__(self):
                self.bl_rna = SimpleNamespace(properties={
                    'character_designer_setup': SimpleNamespace(type='POINTER')} if declared else {})
                self.getter_reads = 0
                self.set_status_reads = []

            def is_property_set(self, name):
                self.set_status_reads.append(name)
                return stored

            @property
            def character_designer_setup(self):
                self.getter_reads += 1
                raise AssertionError('Setup getter must not materialize source or clone storage')

        return Scene()

    def test_unset_source_catalog_skips_both_setup_getters(self):
        source, target = self.setup_getter_trap(), self.setup_getter_trap()
        session = self.scope['PlaybackSceneSession'](source, target)
        self.scope['_detach_unused_asset_references'](session)
        self.assertEqual(source.set_status_reads, ['character_designer_setup'])
        self.assertEqual(target.set_status_reads, [])
        self.assertEqual((source.getter_reads, target.getter_reads), (0, 0))

    def test_missing_source_catalog_declaration_skips_set_status_and_getters(self):
        source = self.setup_getter_trap(declared=False)
        target = self.setup_getter_trap()
        session = self.scope['PlaybackSceneSession'](source, target)
        self.scope['_detach_unused_asset_references'](session)
        self.assertEqual((source.set_status_reads, target.set_status_reads), ([], []))
        self.assertEqual((source.getter_reads, target.getter_reads), (0, 0))

    def test_stored_source_catalog_rejects_missing_clone_storage_before_getters(self):
        for declared in (True, False):
            with self.subTest(clone_declared=declared):
                source = self.setup_getter_trap(stored=True)
                target = self.setup_getter_trap(declared=declared)
                session = self.scope['PlaybackSceneSession'](source, target)
                with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'lost its stored'):
                    self.scope['_detach_unused_asset_references'](session)
                self.assertEqual((source.getter_reads, target.getter_reads), (0, 0))

    def test_detach_only_orphan_catalog_pointer_preserves_source_and_rows(self):
        session, orphan, old, new = self.asset_fixture()
        self.scope['_detach_unused_asset_references'](session)
        self.assertIsNone(new[0].object)
        self.assertIs(old[0].object, orphan)
        self.assertIs(new[1].object, old[1].object)
        self.assertEqual([r.role for r in new], ['HAIR', 'HAIR'])
        self.assertEqual(len(new), 2)
        self.assertEqual(session.detached_asset_references[0]['source_session_uid'], orphan.session_uid)

    def test_catalog_object_in_another_scene_is_rejected_without_detachment(self):
        session, orphan, old, new = self.asset_fixture()
        orphan.users_scene = [ID(600)]
        with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'another Scene'):
            self.scope['_detach_unused_asset_references'](session)
        self.assertIs(new[0].object, orphan)

    def test_shared_catalog_row_is_rejected_before_source_mutation(self):
        session, orphan, old, new = self.asset_fixture()
        session.scene.character_designer_setup.assets[0] = old[0]
        with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'independently copied'):
            self.scope['_detach_unused_asset_references'](session)
        self.assertIs(old[0].object, orphan)

    def test_layered_and_legacy_catalog_animation_reject_before_detachment(self):
        for layered in (True, False):
            with self.subTest(layered=layered):
                session, orphan, old, new = self.asset_fixture()
                curve = SimpleNamespace(data_path='character_designer_setup.assets[0].object.location')
                action = SimpleNamespace(layers=[], fcurves=[])
                if layered:
                    action.layers = [SimpleNamespace(strips=[SimpleNamespace(channelbags=[
                        SimpleNamespace(fcurves=[curve])])])]
                else:
                    action.fcurves = [curve]
                session.source_scene.animation_data = SimpleNamespace(action=action, nla_tracks=[], drivers=[])
                with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'Animated asset catalog'):
                    self.scope['_detach_unused_asset_references'](session)
                self.assertIs(new[0].object, orphan)

    def test_driver_catalog_variable_rejects_before_detachment(self):
        session, orphan, old, new = self.asset_fixture()
        reference = SimpleNamespace(id=session.source_scene,
                                    data_path='character_designer_setup.assets[0].object.location.x')
        driver = SimpleNamespace(expression='var', variables=[SimpleNamespace(targets=[reference])])
        item = next(item for item in session.id_map if item != session.source_scene)
        item.animation_data = SimpleNamespace(drivers=[SimpleNamespace(data_path='location', driver=driver)])
        with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'Driver target reads'):
            self.scope['_detach_unused_asset_references'](session)
        self.assertIs(new[0].object, orphan)

    def test_nested_scene_nla_rejects_before_catalog_detachment(self):
        session, orphan, old, new = self.asset_fixture()
        meta_strip = SimpleNamespace(action=None, strips=[SimpleNamespace(action=None)])
        session.source_scene.animation_data = SimpleNamespace(
            action=None, drivers=[], nla_tracks=[SimpleNamespace(strips=[meta_strip])])
        with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'Nested Scene NLA'):
            self.scope['_detach_unused_asset_references'](session)
        self.assertIs(new[0].object, orphan)

    def test_indirect_python_driver_rejects_before_catalog_detachment(self):
        session, orphan, old, new = self.asset_fixture()
        driver = SimpleNamespace(type='SCRIPTED', is_simple_expression=False,
                                 expression='custom_function()', variables=[])
        item = next(item for item in session.id_map if item != session.source_scene)
        item.animation_data = SimpleNamespace(drivers=[SimpleNamespace(data_path='location', driver=driver)])
        with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'Non-simple scripted driver'):
            self.scope['_detach_unused_asset_references'](session)
        self.assertIs(new[0].object, orphan)

    def animation_ids(self, old, new):
        source, target = ID(100, 'Source animated ID'), ID(200, 'Copy animated ID')
        source.animation_data, target.animation_data = old, new
        return source, target

    def actions(self):
        old_action, new_action = ID(10, 'Source Action'), ID(20, 'Copied Action')
        old_action.slots = [SimpleNamespace(handle=7), SimpleNamespace(handle=9)]
        new_action.slots = [SimpleNamespace(handle=7), SimpleNamespace(handle=9)]
        return old_action, new_action

    def test_unbound_active_action_slot_survives_action_auto_selection(self):
        old_action, new_action = self.actions()
        old, new = Animation(1, old_action, None), Animation(2, old_action, None)
        source, target = self.animation_ids(old, new)
        self.scope['_remap_animation'](source, target, {old_action: new_action})
        self.assertIs(new.action, new_action)
        self.assertIsNone(new.action_slot)
        self.assertIs(old.action, old_action)
        self.assertIsNone(old.action_slot)
        self.assertEqual(old.action_writes, [])

    def test_unbound_nla_slot_handle_zero_remains_unbound(self):
        old_action, new_action = self.actions()
        old_strip, new_strip = Animation(3, old_action, None), Animation(4, old_action, None)
        old_track, new_track = Pointer(5), Pointer(6)
        old_track.strips, new_track.strips = [old_strip], [new_strip]
        old, new = Animation(1, tracks=[old_track]), Animation(2, tracks=[new_track])
        source, target = self.animation_ids(old, new)
        self.assertEqual(old_strip.action_slot_handle, 0)
        self.scope['_remap_animation'](source, target, {old_action: new_action})
        self.assertIs(new_strip.action, new_action)
        self.assertIsNone(new_strip.action_slot)
        self.assertIs(old_strip.action, old_action)
        self.assertEqual(old_strip.action_writes, [])

    def test_bound_active_and_nla_slots_use_copied_slot_with_same_handle(self):
        old_action, new_action = self.actions()
        old_strip, new_strip = Animation(3, old_action, old_action.slots[1]), Animation(4, old_action, old_action.slots[1])
        old_track, new_track = Pointer(5), Pointer(6)
        old_track.strips, new_track.strips = [old_strip], [new_strip]
        old = Animation(1, old_action, old_action.slots[1], [old_track])
        new = Animation(2, old_action, old_action.slots[1], [new_track])
        source, target = self.animation_ids(old, new)
        self.scope['_remap_animation'](source, target, {old_action: new_action})
        self.assertIs(new.action_slot, new_action.slots[1])
        self.assertIs(new_strip.action_slot, new_action.slots[1])
        self.assertIs(old.action_slot, old_action.slots[1])
        self.assertIs(old_strip.action_slot, old_action.slots[1])

    def test_missing_nla_track_rejects_before_assigning_an_action(self):
        old_action, new_action = self.actions()
        track = Pointer(5)
        track.strips = []
        old = Animation(1, old_action, old_action.slots[0], [track])
        new = Animation(2, old_action, old_action.slots[0], [])
        source, target = self.animation_ids(old, new)
        with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'NLA track count'):
            self.scope['_remap_animation'](source, target, {old_action: new_action})
        self.assertEqual(new.action_writes, [])

    def test_shared_nla_strip_is_rejected_before_writing_source_strip(self):
        old_action, new_action = self.actions()
        shared = Animation(3, old_action, None)
        old_track, new_track = Pointer(5), Pointer(6)
        old_track.strips = new_track.strips = [shared]
        source, target = self.animation_ids(Animation(1, tracks=[old_track]), Animation(2, tracks=[new_track]))
        with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'shares an author NLA strip'):
            self.scope['_remap_animation'](source, target, {old_action: new_action})
        self.assertEqual(shared.action_writes, [])

    def cleanup_fixture(self):
        source_scene, clone_scene = ID(10, 'Source Scene'), ID(20, 'Playback Scene')
        session = self.scope['PlaybackSceneSession'](source_scene, clone_scene)
        self.scope['bpy'].data.scenes = Collection([source_scene, clone_scene])
        self.scope['bpy'].data.objects = Collection()
        return session, source_scene, clone_scene

    def test_cleanup_reused_pointer_retains_foreign_uid_and_fake_user(self):
        session, source_scene, clone_scene = self.cleanup_fixture()
        foreign = Object(50, 'Foreign replacement', uid=999)
        self.scope['bpy'].data.objects.append(foreign)
        session.created = [('scenes', clone_scene.as_pointer(), clone_scene.session_uid),
                           ('objects', 50, 123)]
        with self.assertRaisesRegex(self.scope['PlaybackSceneError'], 'pointer was reused'):
            session.close()
        self.assertIn(foreign, self.scope['bpy'].data.objects)
        self.assertTrue(foreign.use_fake_user)
        self.assertEqual(self.scope['bpy'].data.objects.removed, [])
        self.assertIn(source_scene, self.scope['bpy'].data.scenes)
        self.assertFalse(session.closed)

    def test_cleanup_only_owned_uids_then_close_is_idempotent(self):
        session, source_scene, clone_scene = self.cleanup_fixture()
        source, clone = Object(30, 'Source Object'), Object(40, 'Copied Object')
        self.scope['bpy'].data.objects.extend([source, clone])
        session.created = [('scenes', clone_scene.as_pointer(), clone_scene.session_uid),
                           ('objects', clone.as_pointer(), clone.session_uid)]
        session.object_map[source] = clone
        session.id_map[source] = clone
        window = SimpleNamespace(scene=clone_scene)
        self.scope['bpy'].context.window_manager.windows = [window]
        session.close()
        session.close()
        self.assertTrue(session.closed)
        self.assertIs(window.scene, source_scene)
        self.assertEqual(self.scope['bpy'].data.scenes, [source_scene])
        self.assertEqual(self.scope['bpy'].data.objects, [source])
        self.assertTrue(source.use_fake_user)
        self.assertEqual(self.scope['bpy'].data.objects.removed, [(clone, True)])


if __name__ == '__main__':
    unittest.main()
