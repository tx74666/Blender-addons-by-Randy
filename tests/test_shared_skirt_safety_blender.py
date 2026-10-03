"""Reject unsafe shared-Dress changes before artist references are modified.

Run serially in an isolated Blender, never in the artist's open scene:
--background --factory-startup --python-exit-code 1 --python this file
"""
import json
from pathlib import Path
import sys
import unittest

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import skirt_rig as service
from test_skirt_shared_rig_blender import (
    fixture, matrix_values, meshes_content, pose_channels, rest_state, rig_fixture,
)


def reference(value):
    return None if value is None else (value.name, value.as_pointer())


def driver_state(item):
    animation = getattr(item, 'animation_data', None)
    if animation is None:
        return ()
    return tuple((curve.as_pointer(), curve.data_path, curve.array_index, curve.mute,
                  curve.extrapolation, curve.driver.type, curve.driver.expression,
                  curve.driver.use_self,
                  tuple((variable.name, variable.type,
                         tuple((reference(target.id), target.data_path,
                                target.bone_target, target.transform_type,
                                target.transform_space) for target in variable.targets))
                        for variable in curve.driver.variables),
                  tuple((tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
                         point.interpolation) for point in curve.keyframe_points),
                  tuple((modifier.type, modifier.mute) for modifier in curve.modifiers))
                 for curve in animation.drivers)


def constraint_state(constraint):
    pointers = tuple((prop.identifier, reference(value))
                     for prop in constraint.bl_rna.properties if prop.type == 'POINTER'
                     and isinstance(value := getattr(constraint, prop.identifier), bpy.types.ID))
    return (constraint.name, constraint.type, constraint.mute, constraint.influence,
            getattr(constraint, 'subtarget', ''), getattr(constraint, 'pole_subtarget', ''),
            getattr(constraint, 'space_subtarget', ''), pointers,
            tuple((reference(target.target), target.subtarget, target.weight)
                  for target in getattr(constraint, 'targets', ())))


def protected_state(source, main, extra_ids=()):
    """Read actual scene content, not the implementation's planned rollback data."""
    objects = tuple(sorted(bpy.data.objects, key=lambda obj: obj.name))
    ids = {item.as_pointer(): item for obj in objects for item in (obj, obj.data) if item is not None}
    ids.update({item.as_pointer(): item for item in extra_ids})
    return {
        'meshes': meshes_content(),
        'objects': tuple((obj.name, obj.as_pointer(), reference(obj.parent), obj.parent_type,
                          obj.parent_bone, matrix_values(obj.matrix_basis),
                          matrix_values(obj.matrix_parent_inverse)) for obj in objects),
        'rest': {obj.name: rest_state(obj) for obj in objects if obj.type == 'ARMATURE'},
        'channels': {obj.name: pose_channels(obj) for obj in objects if obj.type == 'ARMATURE'},
        'modifiers': {obj.name: tuple((mod.name, mod.type, reference(getattr(mod, 'object', None)),
                                      getattr(mod, 'subtarget', '')) for mod in obj.modifiers)
                      for obj in objects},
        'constraints': {obj.name: (
            tuple(constraint_state(con) for con in obj.constraints),
            tuple((pb.name, tuple(constraint_state(con) for con in pb.constraints))
                  for pb in obj.pose.bones) if obj.type == 'ARMATURE' else ()) for obj in objects},
        'drivers': {item.name: driver_state(item) for item in ids.values()},
        'record': source.get(service.RECORD_KEY),
        'rig': reference(source.get(service.RIG_KEY)),
        'registry': tuple(sorted((key, reference(value))
                                 for key, value in main.get(service.SHARED_SOURCES_KEY, {}).items())),
        'frame': (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe),
        'context': (reference(bpy.context.view_layer.objects.active), bpy.context.mode,
                    tuple(obj.name for obj in objects if obj.select_get())),
    }


def artist_object(name='Artist Object'):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    return obj


def add_driver(item, path, rig, *, bone='', channel_path='', index=None):
    curve = item.driver_add(path) if index is None else item.driver_add(path, index)
    curve.driver.type = 'SCRIPTED'
    curve.driver.expression = 'artist_input'
    variable = curve.driver.variables.new()
    variable.name = 'artist_input'
    variable.type = 'TRANSFORMS' if bone else 'SINGLE_PROP'
    target = variable.targets[0]
    if variable.type == 'SINGLE_PROP':
        target.id_type = 'ARMATURE' if isinstance(rig, bpy.types.Armature) else 'OBJECT'
    target.id = rig
    if bone:
        target.bone_target = bone
        target.transform_type = 'LOC_X'
        target.transform_space = 'LOCAL_SPACE'
    else:
        target.data_path = channel_path or '["physics_influence"]'
    return curve


class SharedSkirtSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    @classmethod
    def tearDownClass(cls):
        character_designer.unregister()

    def assert_refused_unchanged(self, source, main, action, extra_ids=()):
        bpy.context.view_layer.update()
        before = protected_state(source, main, extra_ids)
        with self.assertRaises(service.SkirtRigError):
            action()
        self.assertEqual(protected_state(source, main, extra_ids), before)

    def shared_fixture(self):
        source, _old, main, _record = fixture(posed=False)
        record = service.unify_skirt(bpy.context, source, main)
        return source, main, record

    def test_migration_rejects_all_external_constraint_target_slots(self):
        for kind in ('POLE', 'SPACE', 'ARMATURE_TARGETS'):
            with self.subTest(kind=kind):
                source, old, main, record = fixture(posed=False)
                if kind == 'SPACE':
                    external = artist_object()
                    constraint = external.constraints.new('COPY_TRANSFORMS')
                    constraint.target = main
                    constraint.owner_space = 'CUSTOM'
                    constraint.space_object = old
                else:
                    external = rig_fixture()
                    owner = external.pose.bones['Body']
                    constraint = owner.constraints.new('IK' if kind == 'POLE' else 'ARMATURE')
                    if kind == 'POLE':
                        constraint.pole_target = old
                        constraint.pole_subtarget = record['controls']['waist']
                    else:
                        target = constraint.targets.new()
                        target.target = old
                        target.subtarget = record['controls']['waist']
                self.assert_refused_unchanged(source, main,
                    lambda: service.unify_skirt(bpy.context, source, main))
                self.assertIn(old.name, bpy.data.objects)

    def test_migration_rejects_material_node_and_data_driver_ids(self):
        for kind in ('MATERIAL', 'NODE_TREE', 'OBJECT_DATA'):
            with self.subTest(kind=kind):
                source, old, main, _record = fixture(posed=False)
                if kind == 'MATERIAL':
                    item = bpy.data.materials.new('Artist Material')
                    item['artist_value'] = 0.5
                    add_driver(item, '["artist_value"]', old)
                elif kind == 'NODE_TREE':
                    material = bpy.data.materials.new('Artist Node Material')
                    material.use_nodes = True
                    item = material.node_tree
                    node = item.nodes.new('ShaderNodeValue')
                    add_driver(node.outputs[0], 'default_value', old)
                else:
                    item = source.data
                    item['artist_value'] = 0.5
                    add_driver(item, '["artist_value"]', old)
                signature = driver_state(item)
                self.assert_refused_unchanged(source, main,
                    lambda: service.unify_skirt(bpy.context, source, main), (item,))
                self.assertEqual(driver_state(item), signature)

    def test_migration_rejects_existing_main_driver_without_overwriting_curve(self):
        source, old, main, _record = fixture(posed=False)
        path = old.animation_data.drivers[0].data_path
        main['artist_guard'] = 1.25
        # FCurve.data_path is editable even when an old renamed/deleted bone's
        # destination no longer resolves. Create through a real property first.
        curve = add_driver(main, '["artist_guard"]', main, channel_path='["artist_guard"]')
        curve.data_path = path
        curve.mute = True
        curve.extrapolation = 'LINEAR'
        curve.modifiers.new('NOISE')
        pointer, signature = curve.as_pointer(), driver_state(main)
        self.assert_refused_unchanged(source, main,
            lambda: service.unify_skirt(bpy.context, source, main))
        self.assertEqual(main.animation_data.drivers.find(path).as_pointer(), pointer)
        self.assertEqual(driver_state(main), signature)

    def test_incomplete_extra_or_empty_shared_names_block_read_and_remove(self):
        source, main, record = self.shared_fixture()
        valid_raw = source[service.RECORD_KEY]
        original = list(record['shared']['names'])
        for kind, names in (('EMPTY', []), ('MISSING', original[1:]),
                            ('EXTRA', original + ['Hips'])):
            with self.subTest(kind=kind):
                edited = json.loads(valid_raw)
                edited['shared']['names'] = names
                service.write_record(source, edited)
                try:
                    self.assert_refused_unchanged(source, main, lambda: service.read_record(source))
                    self.assert_refused_unchanged(source, main,
                        lambda: service.remove_skirt(bpy.context, source))
                finally:
                    source[service.RECORD_KEY] = valid_raw

    def test_remove_rejects_external_curve_hook(self):
        source, main, record = self.shared_fixture()
        curve = bpy.data.curves.new('Artist Curve Data', 'CURVE')
        curve.dimensions = '3D'
        spline = curve.splines.new('POLY')
        spline.points.add(1)
        spline.points[0].co, spline.points[1].co = (0, 0, 0, 1), (0, 0, 1, 1)
        external = bpy.data.objects.new('Artist Curve', curve)
        bpy.context.collection.objects.link(external)
        hook = external.modifiers.new('Artist Dress Hook', 'HOOK')
        hook.object, hook.subtarget = main, record['controls']['waist']
        hook.vertex_indices_set([0])
        bind = matrix_values(hook.matrix_inverse)
        self.assert_refused_unchanged(source, main,
            lambda: service.remove_skirt(bpy.context, source))
        self.assertEqual(matrix_values(hook.matrix_inverse), bind)
        self.assertEqual(tuple(hook.vertex_indices), (0,))

    def test_remove_rejects_object_and_pose_constraint_slots(self):
        for kind in ('OBJECT_TARGET', 'POSE_TARGET', 'POSE_POLE', 'POSE_SPACE', 'POSE_ARMATURE_TARGETS'):
            with self.subTest(kind=kind):
                source, main, record = self.shared_fixture()
                name = record['controls']['waist']
                if kind == 'OBJECT_TARGET':
                    owner = artist_object()
                else:
                    external = rig_fixture()
                    owner = external.pose.bones['Body']
                constraint = owner.constraints.new(
                    'IK' if kind == 'POSE_POLE' else 'ARMATURE' if kind == 'POSE_ARMATURE_TARGETS'
                    else 'COPY_TRANSFORMS')
                if kind == 'POSE_POLE':
                    constraint.pole_target, constraint.pole_subtarget = main, name
                elif kind == 'POSE_SPACE':
                    constraint.owner_space = 'CUSTOM'
                    constraint.space_object, constraint.space_subtarget = main, name
                elif kind == 'POSE_ARMATURE_TARGETS':
                    target = constraint.targets.new()
                    target.target, target.subtarget = main, name
                else:
                    constraint.target, constraint.subtarget = main, name
                self.assert_refused_unchanged(source, main,
                    lambda: service.remove_skirt(bpy.context, source))

    def test_remove_rejects_external_bone_transform_and_channel_driver(self):
        for kind in ('TRANSFORM', 'CHANNEL', 'NODE_CHANNEL',
                     'DATA_BONE_CHANNEL', 'OBJECT_DATA_BONE_CHANNEL'):
            with self.subTest(kind=kind):
                source, main, record = self.shared_fixture()
                name = record['controls']['waist']
                path = main.pose.bones[name].path_from_id('location') + '[0]'
                if kind == 'NODE_CHANNEL':
                    material = bpy.data.materials.new('Artist Driven Material')
                    material.use_nodes = True
                    item = material.node_tree
                    node = item.nodes.new('ShaderNodeValue')
                    add_driver(node.outputs[0], 'default_value', main, channel_path=path)
                elif kind in {'DATA_BONE_CHANNEL', 'OBJECT_DATA_BONE_CHANNEL'}:
                    item = artist_object('Artist Bone Data Driver Owner')
                    # Both ID target styles can read the same shared Bone:
                    # Armature data -> bones[...], Object -> data.bones[...].
                    bone_path = main.data.bones[name].path_from_id('hide')
                    target = main.data if kind == 'DATA_BONE_CHANNEL' else main
                    add_driver(item, 'location', target,
                               channel_path=bone_path if target == main.data else 'data.' + bone_path,
                               index=0)
                else:
                    item = artist_object('Artist Driver Owner')
                    add_driver(item, 'location', main, bone=name if kind == 'TRANSFORM' else '',
                               channel_path=path, index=0)
                signature = driver_state(item)
                self.assert_refused_unchanged(source, main,
                    lambda: service.remove_skirt(bpy.context, source), (item,))
                self.assertEqual(driver_state(item), signature)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SharedSkirtSafetyTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print(f'PASS Shared Skirt Safety {result.testsRun} tests')
