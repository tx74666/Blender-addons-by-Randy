"""Actual Worklist/swap Source with fake RNA; no bpy/add-on/native imports.

The coordinate/control validator is mocked at its admission boundary. These
tests prove routing, copied structural flags and actual Add cleanup, not an
FBX Rest fit, limb solver or native Blender operator.
"""
import ast
from copy import deepcopy
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


def function(path, name, namespace):
    tree = ast.parse(path.read_bytes(), filename=str(path))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(nodes) != 1:
        raise RuntimeError('Expected one actual function: ' + name)
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace[name]


class ID(dict):
    __hash__ = object.__hash__
    __eq__ = object.__eq__

    def __bool__(self):
        return True

    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)
        self.is_embedded_data = False


class Bone:
    def __init__(self, name, parent=None):
        self.name, self.parent = name, parent
        self.use_connect = True
        self.use_inherit_rotation, self.inherit_scale = True, 'FULL'
        self.use_deform = not name.startswith('Opaque')
        self.matrix_local = tuple(range(16))


class Data(ID):
    def __init__(self, env, name='Original data'):
        super().__init__(name=name, use_fake_user=False)
        self.env = env
        self.bones = {name: Bone(name) for name in
                      ('Hips', 'Spine', 'DressFollow', 'Opaque 71', 'Opaque 92')}
        self.bones['Spine'].parent = self.bones['Hips']
        self.bones['DressFollow'].parent = self.bones['Spine']
        self.bones['Opaque 92'].parent = self.bones['Opaque 71']
        self.edit_bones = list(self.bones.values())

    def copy(self):
        self.env.events.append('copy')
        result = Data(self.env, self.name)
        for name, bone in self.bones.items():
            clone = result.bones[name]
            for field in ('use_connect', 'use_inherit_rotation', 'inherit_scale', 'use_deform', 'matrix_local'):
                setattr(clone, field, deepcopy(getattr(bone, field)))
            clone.parent = result.bones[bone.parent.name] if bone.parent else None
        self.env.ids.add(result)
        self.env.copies.append(result)
        return result


class Object(ID):
    def __init__(self, name, data=None, *, kind='ARMATURE'):
        super().__init__(name=name, data=data, type=kind, mode='POSE', selected=True)

    def select_set(self, value):
        self.selected = value


class Context:
    def __init__(self, env, rig):
        self.env = env
        self.scene = ID(name='Original Scene', unit_settings=SimpleNamespace(scale_length=.01))
        self.window = SimpleNamespace(scene=self.scene)
        self.view_layer = SimpleNamespace(objects=SimpleNamespace(active=rig, get=self.get_object), update=self.update)
        self.window_manager = SimpleNamespace(character_designer_animation=SimpleNamespace(target=rig))

    def get_object(self, name):
        return next((obj for obj in self.env.objects if obj.name == name), None)

    @property
    def selected_objects(self):
        return tuple(obj for obj in self.env.objects if obj.selected)

    def update(self):
        self.env.events.append('update')


class Environment:
    def __init__(self):
        self.events, self.ids, self.copies, self.objects = [], set(), [], []
        self.original_data = Data(self)
        self.rig = Object('Actual Rig', self.original_data)
        self.objects.append(self.rig)
        self.context = Context(self, self.rig)
        self.ids.update((self.rig, self.original_data, self.context.scene))
        self.preview, self.mapping_error, self.controls_missing, self.fail_edit_once = False, None, False, False
        self.packet = {'rigging': {'authenticated_capture': True}, 'bones': ['actual source packet']}
        self.plan = SimpleNamespace(mapped_names=frozenset(('Hips', 'Spine', 'DressFollow')),
                                    delegated_names=frozenset(('DressFollow',)))
        self.bpy = SimpleNamespace(ops=SimpleNamespace(object=SimpleNamespace(mode_set=self.mode_set)),
                                  data=SimpleNamespace(user_map=lambda: {value: () for value in self.ids},
                                                       batch_remove=self.remove_ids))
        namespace = {'bpy': self.bpy, 'UnityAnimationError': RuntimeError}
        swap = function(SOURCE / 'unity_animation.py', '_swap_armature_data', namespace)
        self.ua = SimpleNamespace(active_preview=lambda _rig: self.preview, restore_preview=self.restore,
                                  _mapping=self.mapping, _swap_armature_data=swap)
        self.namespace = {'ua': self.ua}
        self.editing = function(SOURCE / 'animation_worklist.py', '_editing_data', self.namespace)
        self.removed = set()

    def restore(self, _context, rig):
        self.events.append('restore_preview')
        rig.data, self.preview = self.original_data, False

    def mapping(self, rig, packet, scale):
        self.events.append('mapping')
        if packet is not self.packet or scale != .01 or rig.data is not self.original_data:
            raise AssertionError('Mapping must authenticate the actual packet/Rest before copying.')
        if self.mapping_error:
            raise self.mapping_error
        if not rig.data.bones['Opaque 92'].use_connect:
            raise ValueError('Actual generated hierarchy was edited.')
        return SimpleNamespace(indices={name: index for index, name in enumerate(self.plan.mapped_names)},
                               controls=None if self.controls_missing else self.plan)

    def mode_set(self, *, mode):
        self.events.append('mode:' + mode)
        self.context.view_layer.objects.active.mode = mode
        if mode == 'EDIT' and self.fail_edit_once:
            self.fail_edit_once = False
            raise RuntimeError('Injected native edit-entry failure')
        return {'FINISHED'}

    def remove_ids(self, *, ids):
        self.removed.update(ids)
        self.ids.difference_update(ids)


def structure(data):
    return {name: (bone.parent.name if bone.parent else None, bone.use_connect,
                   bone.use_inherit_rotation, bone.inherit_scale, bone.use_deform, bone.matrix_local)
            for name, bone in data.bones.items()}


class RiggingEditingDataTests(unittest.TestCase):
    def test_rigging_relaxes_only_authenticated_authored_source_bones(self):
        env = Environment()
        original = structure(env.original_data)
        env.editing(env.context, env.rig, packet=env.packet)
        self.assertLess(env.events.index('mapping'), env.events.index('copy'))
        self.assertIsNot(env.rig.data, env.original_data)
        expected = deepcopy(original)
        for name in ('Hips', 'Spine'):
            row = list(expected[name]); row[1] = False; expected[name] = tuple(row)
        self.assertEqual(structure(env.rig.data), expected)
        self.assertEqual(structure(env.original_data), original)
        self.assertTrue(env.rig.data.bones['Opaque 92'].use_connect)
        self.assertTrue(env.rig.data.bones['DressFollow'].use_connect)
        self.assertEqual(env.rig.mode, 'POSE')
        self.assertEqual(env.context.selected_objects, (env.rig,))

    def test_mapping_rejects_edited_generated_hierarchy_before_any_copy(self):
        env = Environment(); env.original_data.bones['Opaque 92'].use_connect = False
        before = structure(env.original_data)
        with self.assertRaisesRegex(ValueError, 'generated hierarchy'):
            env.editing(env.context, env.rig, packet=env.packet)
        self.assertEqual(env.events, ['mapping'])
        self.assertEqual(structure(env.original_data), before)
        self.assertIs(env.rig.data, env.original_data)

    def test_rigging_without_a_validated_control_plan_rejects_before_copy(self):
        env = Environment(); env.controls_missing = True
        with self.assertRaisesRegex(ValueError, 'validated native control plan'):
            env.editing(env.context, env.rig, packet=env.packet)
        self.assertEqual(env.events, ['mapping'])
        self.assertFalse(env.copies)

    def test_ordinary_legacy_default_still_relaxes_every_copied_bone(self):
        env = Environment(); before = structure(env.original_data)
        env.editing(env.context, env.rig)
        self.assertNotIn('mapping', env.events)
        self.assertTrue(all(not bone.use_connect for bone in env.rig.data.bones.values()))
        self.assertEqual(structure(env.original_data), before)

    def test_non_rigging_packet_keeps_the_ordinary_default(self):
        env = Environment()
        env.editing(env.context, env.rig, packet={'bones': []})
        self.assertNotIn('mapping', env.events)
        self.assertTrue(all(not bone.use_connect for bone in env.rig.data.bones.values()))

    def test_restore_preview_precedes_authentication_of_original_rest(self):
        env = Environment(); env.preview = True
        env.rig.data = Data(env, 'Temporary preview')
        env.editing(env.context, env.rig, packet=env.packet)
        self.assertEqual(env.events[:3], ['restore_preview', 'mapping', 'copy'])
        self.assertTrue(env.original_data.bones['Opaque 92'].use_connect)

    def test_actual_add_failure_cleans_only_new_ids_and_restores_original_context(self):
        env = Environment(); ctx = env.context
        original_ids, old_scene, old_active = set(env.ids), ctx.scene, ctx.view_layer.objects.active
        original_rest, original_selected = structure(env.original_data), ctx.selected_objects
        saved = SimpleNamespace(items=[], rig=None, active_index=-1, model_sha256='f' * 64,
                                target_name='Prepared Cosha')
        new_saved = SimpleNamespace(items=[], rig=None, active_index=-1, model_sha256='f' * 64,
                                    target_name='Prepared Cosha')
        new_data = env.original_data
        # The test's protected original Rig is a separate native ID. The new
        # model uses a fresh data/rig/scene, as the real first Add import does.
        env.original_data = new_data = Data(env, 'Actual newly imported model')
        new_rig, new_scene, baseline = Object('New Model', new_data), ID(name='New imported Scene'), ID(name='New base')
        new_scene.unit_settings = SimpleNamespace(scale_length=.01)
        ctx.scene.saved, new_scene.saved = saved, new_saved
        def import_prepared(context, *_args, **kwargs):
            self.assertIs(kwargs.get('native_timebase'), True)
            env.ids.update((new_rig, new_data, new_scene, baseline))
            env.objects.append(new_rig)
            context.scene, context.window.scene = new_scene, new_scene
            for obj in context.selected_objects: obj.select_set(False)
            new_rig.select_set(True); context.view_layer.objects.active = new_rig
            new_rig.mode = 'OBJECT'
            return SimpleNamespace(rig=new_rig, action=baseline)
        def restore_context(context, before, *, playing):
            self.assertIs(playing, True)
            context.scene, context.window.scene = before['scene'], before['scene']
            for obj in context.selected_objects: obj.select_set(False)
            for obj in before['selected']: obj.select_set(True)
            context.view_layer.objects.active = before['active']
        package = ModuleType('_editing_data_boundary'); package.__path__ = []
        composition = ModuleType(package.__name__ + '.animation_composition'); composition.active = lambda _rig: False
        target = ModuleType(package.__name__ + '.animation_worklist_target')
        target.is_adopted = target.managed = lambda _rig: False
        source = SimpleNamespace(_context_state=lambda context: {'scene': context.scene,
            'active': context.view_layer.objects.active, 'selected': context.selected_objects},
            _restore_context=restore_context, _import_prepared_source=import_prepared)
        native = SimpleNamespace(restore_owned=lambda *_args: env.events.append('restore_owned'))
        env.namespace.update(__package__=package.__name__, bpy=env.bpy, source=source,
            state=lambda context: context.scene.saved, _idle=lambda *_args, **_kwargs: None,
            _composition_add_route=lambda *_args, **_kwargs: (False, None),
            _prepared=lambda *_args, **_kwargs: (None, {'clipName': 'Walk'}, {'_manifest_path': 'actual prepared'},
                                                env.packet, 'Cosha', 'actual model'),
            _rigging_module=lambda *_args, **_kwargs: native, _find_owned_rig=lambda *_args: None,
            _copy_workspace=lambda *_args: None)
        add = function(SOURCE / 'animation_worklist.py', 'add', env.namespace)
        env.fail_edit_once = True
        with patch.dict(sys.modules, {package.__name__: package,
                composition.__name__: composition, target.__name__: target}):
            with self.assertRaisesRegex(RuntimeError, 'Injected native edit-entry failure'):
                add(ctx, 'actual prepared row', activate_new=False)
        self.assertEqual(env.ids, original_ids)
        self.assertTrue(env.copies and env.copies[0] in env.removed)
        self.assertFalse(env.removed.intersection(original_ids))
        self.assertIs(ctx.scene, old_scene); self.assertIs(ctx.window.scene, old_scene)
        self.assertIs(ctx.view_layer.objects.active, old_active)
        self.assertEqual(ctx.selected_objects, original_selected)
        self.assertEqual(structure(old_active.data), original_rest)
        self.assertEqual(saved.items, [])
        self.assertIn('mapping', env.events)
        self.assertLess(env.events.index('mapping'), env.events.index('copy'))


if __name__ == '__main__':
    unittest.main()
