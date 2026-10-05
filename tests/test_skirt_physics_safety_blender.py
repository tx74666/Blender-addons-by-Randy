"""Owned Dress graph, posed collider construction and native Reset safety.

Run only in a disposable factory Blender with --disable-autoexec. No artist
scene is opened; the save/reopen test writes a TemporaryDirectory only.
"""
from pathlib import Path
import sys
import tempfile
import unittest

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
from character_designer import skirt_physics as physics, skirt_rig as skirt
from test_skirt_physics_blender import character, activate, action_state, evaluated_vertices, maximum_distance
from test_skirt_topology_blender import frustum
from test_skirt_shared_rig_blender import mesh_content, pose_channels, rest_state


def fixture(*, shared=True, create_physics=True):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 6
    scene.frame_set(1)
    main = character()
    source = frustum('Safety Dress', rows=10, sides=32)
    source.shape_key_add(name='Basis')
    source.shape_key_add(name='Independent_L').data[1].co.x += .003
    source.data.uv_layers.new(name='Protected UV')
    activate(source)
    record = skirt.build_skirt(bpy.context, source, chain_count=4, segment_count=3,
                               armature=main, parent_bone='Hips', shared=shared)
    rig = source[skirt.RIG_KEY]
    if create_physics:
        record = physics.add_physics(bpy.context, source)
    return source, rig, main, record


def _rna_values(owner):
    result = []
    for prop in owner.bl_rna.properties:
        if prop.identifier == 'rna_type' or prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'}:
            continue
        value = getattr(owner, prop.identifier)
        result.append((prop.identifier, tuple(value) if getattr(prop, 'is_array', False) else value))
    return tuple(result)


def snapshot(source, rig, main):
    """Capture artist content and all native structural inputs after any tamper."""
    meshes = {obj.name: mesh_content(obj) for obj in bpy.data.objects if obj.type == 'MESH'}
    objects = []
    for obj in bpy.data.objects:
        modifiers = []
        for mod in obj.modifiers:
            modifiers.append((mod.name, mod.type, _rna_values(mod),
                              getattr(getattr(mod, 'object', None), 'name', None)))
            if mod.type == 'CLOTH':
                modifiers.append(('settings', _rna_values(mod.settings), _rna_values(mod.collision_settings),
                                  _rna_values(mod.point_cache)))
        objects.append((obj.name, obj.parent.name if obj.parent else None, obj.parent_type, obj.parent_bone,
                        tuple(tuple(row) for row in obj.matrix_basis),
                        tuple(tuple(row) for row in obj.matrix_parent_inverse), tuple(modifiers),
                        tuple((constraint.name, constraint.type, _rna_values(constraint))
                              for constraint in obj.constraints),
                        obj.get(skirt.OWNER_KEY), getattr(obj.get(skirt.SOURCE_KEY), 'name', None),
                        obj.data.get(skirt.OWNER_KEY) if obj.data else None))
    constraints = tuple((bone.name, tuple((item.name, item.type, _rna_values(item),
                        getattr(getattr(item, 'target', None), 'name', None)) for item in bone.constraints))
                        for bone in rig.pose.bones)
    collections = tuple((group.name, group.get(skirt.OWNER_KEY), tuple(group.objects.keys()),
                         tuple(group.children.keys())) for group in bpy.data.collections)
    return (source[skirt.RECORD_KEY], meshes, tuple(objects), collections, constraints,
            pose_channels(rig), rest_state(rig), pose_channels(main), rest_state(main),
            action_state(rig), action_state(main), action_state(source),
            bpy.context.scene.frame_current, bpy.context.scene.frame_subframe,
            bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
            tuple(obj.name for obj in bpy.context.selected_objects), bpy.context.mode)


class PhysicsSafetyTests(unittest.TestCase):
    def _posed_creation(self, shared):
        source, rig, main, record = fixture(shared=shared)
        reference = [tuple(tuple(vertex.co) for vertex in bpy.data.objects[name].data.vertices)
                     for name in record['physics']['colliders']]
        source, rig, main, record = fixture(shared=shared, create_physics=False)
        hips = main.pose.bones['Hips']
        hips.rotation_mode = 'XYZ'
        hips.location = (.23, -.05, .13)
        hips.rotation_euler = (.22, -.17, .08)
        bpy.context.view_layer.update()
        before_mesh, before_rest, before_pose = mesh_content(source), rest_state(main), pose_channels(main)
        record = physics.add_physics(bpy.context, source)
        physics.validate_physics(source)
        for name, raw in zip(record['physics']['colliders'], reference):
            collider = bpy.data.objects[name]
            self.assertLessEqual(maximum_distance([Vector(point) for point in raw],
                                                 [vertex.co.copy() for vertex in collider.data.vertices]), 1e-5)
            bone_name = collider.vertex_groups[0].name
            deform = main.matrix_world @ main.pose.bones[bone_name].matrix @ main.data.bones[bone_name].matrix_local.inverted()
            expected = [deform @ Vector(point) for point in raw]
            self.assertLessEqual(maximum_distance(expected, evaluated_vertices(collider)), 1e-5)
        self.assertEqual(mesh_content(source), before_mesh)
        self.assertEqual(rest_state(main), before_rest)
        self.assertEqual(pose_channels(main), before_pose)

    def test_posed_shared_hips_does_not_apply_pose_twice_to_colliders(self):
        self._posed_creation(True)

    def test_posed_legacy_hips_does_not_apply_pose_twice_to_colliders(self):
        self._posed_creation(False)

    def test_old_record_without_pin_expectation_is_readonly_and_proven(self):
        source, rig, main, record = fixture()
        record['physics'].pop('pin_weights')
        skirt.write_record(source, record)
        before = snapshot(source, rig, main)
        physics.validate_physics(source)
        self.assertEqual(snapshot(source, rig, main), before)

    def test_saved_pin_tuning_and_closed_collider_fitting_are_allowed(self):
        source, rig, main, record = fixture()
        proxy, _cloth = physics._cloth(record)
        sides = record['physics']['columns']
        pin = proxy.vertex_groups['CD Waist Pin']
        pin.add(list(range(sides, sides * 3)), .25, 'REPLACE')
        record['physics']['pin_weights'][sides:sides * 3] = [.25] * (sides * 2)
        skirt.write_record(source, record)
        collider = bpy.data.objects[record['physics']['colliders'][0]]
        for vertex in collider.data.vertices:
            vertex.co.x *= 1.03
        before = snapshot(source, rig, main)
        physics.validate_physics(source)
        self.assertEqual(snapshot(source, rig, main), before)

    def _rejects_without_mutation(self, source, rig, main):
        before = snapshot(source, rig, main)
        for operation in (physics.validate_physics,
                          lambda value: physics.clear_cache(bpy.context, value),
                          lambda value: physics.reset_simulation(bpy.context, value),
                          lambda value: next(physics.bake_steps(bpy.context, value, 1, 2))):
            with self.assertRaises(ValueError):
                operation(source)
            self.assertEqual(snapshot(source, rig, main), before)

    def test_graph_tampering_refuses_tuning_clear_reset_and_bake_before_mutation(self):
        def tamper_owner(source, rig, main, record, proxy, cloth):
            proxy.data[skirt.OWNER_KEY] = 'Foreign Owner'

        def tamper_source(source, rig, main, record, proxy, cloth):
            proxy[skirt.SOURCE_KEY] = main

        def tamper_data_source(source, rig, main, record, proxy, cloth):
            proxy.data[skirt.SOURCE_KEY] = main

        def tamper_bind(source, rig, main, record, proxy, cloth):
            proxy.modifiers[0].object = None

        def tamper_pin(source, rig, main, record, proxy, cloth):
            proxy.vertex_groups['CD Waist Pin'].add([record['physics']['columns']], .7, 'REPLACE')

        def tamper_pin_record(source, rig, main, record, proxy, cloth):
            record['physics']['pin_weights'][0] = float('nan')
            skirt.write_record(source, record)

        def tamper_sample(source, rig, main, record, proxy, cloth):
            proxy.vertex_groups['CD Sample 00.00'].add([1], 1.0, 'REPLACE')

        def tamper_vertex(source, rig, main, record, proxy, cloth):
            proxy.data.vertices[3].co.x += .01

        def tamper_frame(source, rig, main, record, proxy, cloth):
            proxy.location.x += .02

        def tamper_collection(source, rig, main, record, proxy, cloth):
            bpy.data.collections[record['physics']['collection']].objects.link(source)

        def tamper_collision_filter(source, rig, main, record, proxy, cloth):
            cloth.collision_settings.collection = None

        def tamper_collider_binding(source, rig, main, record, proxy, cloth):
            obj = bpy.data.objects[record['physics']['colliders'][0]]
            obj.vertex_groups[0].add([0], .7, 'REPLACE')

        def tamper_collider_topology(source, rig, main, record, proxy, cloth):
            obj = bpy.data.objects[record['physics']['colliders'][0]]
            import bmesh
            mesh = bmesh.new()
            mesh.from_mesh(obj.data)
            mesh.faces.ensure_lookup_table()
            bmesh.ops.delete(mesh, geom=[mesh.faces[0]], context='FACES_ONLY')
            mesh.to_mesh(obj.data)
            mesh.free()

        def tamper_aim(source, rig, main, record, proxy, cloth):
            rig.pose.bones[record['chains'][0]['phys'][0]].constraints['CD Physics Aim'].target = source

        def tamper_extra_constraint(source, rig, main, record, proxy, cloth):
            rig.pose.bones[record['chains'][0]['phys'][0]].constraints.new('LIMIT_ROTATION')

        def tamper_influence_driver(source, rig, main, record, proxy, cloth):
            bone = rig.pose.bones[record['chains'][0]['def'][0]]
            path = bone.constraints['Skirt physics delta'].path_from_id() + '.influence'
            curve = next(curve for curve in rig.animation_data.drivers if curve.data_path == path)
            curve.driver.variables[0].targets[0].data_path = '["Foreign Property"]'

        for tamper in (tamper_owner, tamper_source, tamper_data_source, tamper_bind, tamper_pin, tamper_pin_record,
                       tamper_sample, tamper_vertex, tamper_frame,
                       tamper_collection, tamper_collision_filter, tamper_collider_binding,
                       tamper_collider_topology, tamper_aim, tamper_extra_constraint, tamper_influence_driver):
            with self.subTest(tamper=tamper.__name__):
                source, rig, main, record = fixture()
                proxy, cloth = physics._cloth(record)
                tamper(source, rig, main, record, proxy, cloth)
                self._rejects_without_mutation(source, rig, main)

    def test_physics_animation_and_custom_proxy_modifiers_are_refused(self):
        source, rig, main, record = fixture()
        bone = rig.pose.bones[record['chains'][0]['phys'][0]]
        bone.keyframe_insert('rotation_quaternion', frame=1)
        self._rejects_without_mutation(source, rig, main)
        source, rig, main, record = fixture()
        proxy, _cloth = physics._cloth(record)
        proxy.modifiers.new('Artist modifier', 'SMOOTH')
        self._rejects_without_mutation(source, rig, main)

    def _reset(self, *, baked):
        source, rig, main, record = fixture()
        hips = main.pose.bones['Hips']
        waist = rig.pose.bones[record['controls']['waist']]
        for frame, x in ((1, 0.0), (3, .16), (6, -.07)):
            hips.location.x = x
            hips.keyframe_insert('location', frame=frame)
            waist.rotation_mode = 'XYZ'
            waist.rotation_euler.z = x * .2
            waist.keyframe_insert('rotation_euler', frame=frame)
        bpy.context.scene.frame_set(1)
        actions = action_state(main), action_state(rig), action_state(source)
        content, rest = mesh_content(source), rest_state(rig)
        proxy, cloth = physics._cloth(record)
        initial = evaluated_vertices(proxy)
        trajectory = []
        for frame in range(1, 7):
            bpy.context.scene.frame_set(frame)
            trajectory.append(evaluated_vertices(proxy))
        self.assertGreater(maximum_distance(initial, trajectory[-1]), .005)
        if baked:
            physics.bake_simulation(bpy.context, source, 1, 6)
            self.assertTrue(cloth.point_cache.is_baked)
        step = cloth.point_cache.frame_step
        result = physics.reset_simulation(bpy.context, source)
        self.assertEqual(result, {'start': 1, 'is_baked': False})
        self.assertEqual(bpy.context.scene.frame_current, 1)
        self.assertEqual(bpy.context.scene.frame_subframe, 0.0)
        self.assertEqual(cloth.point_cache.frame_step, step)
        self.assertFalse(cloth.point_cache.is_baked)
        self.assertIsNone(skirt.read_record(source)['physics']['baked_range'])
        self.assertLessEqual(maximum_distance(initial, evaluated_vertices(proxy)), 1e-5)
        for frame, expected in enumerate(trajectory, 1):
            bpy.context.scene.frame_set(frame)
            self.assertLessEqual(maximum_distance(expected, evaluated_vertices(proxy)), 3e-5)
        self.assertEqual((action_state(main), action_state(rig), action_state(source)), actions)
        self.assertEqual(mesh_content(source), content)
        self.assertEqual(rest_state(rig), rest)

    def test_unbaked_reset_discards_state_and_replays_without_action_edits(self):
        self._reset(baked=False)

    def test_baked_reset_discards_state_and_replays_without_action_edits(self):
        self._reset(baked=True)

    def test_graph_proof_after_save_reopen_preserves_native_and_author_data(self):
        source, rig, main, record = fixture()
        names = source.name, rig.name, main.name
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dress-physics-proof.blend'
            bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=str(path))
            source, rig, main = (bpy.data.objects[name] for name in names)
            before = snapshot(source, rig, main)
            physics.validate_physics(source)
            self.assertEqual(snapshot(source, rig, main), before)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PhysicsSafetyTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print(f'PASS Dress physics safety {result.testsRun} tests', flush=True)
