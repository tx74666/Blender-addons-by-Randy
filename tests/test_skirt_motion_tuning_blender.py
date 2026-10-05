"""Native owned Dress tuning, cache retention and all-or-none recovery.

Run only with Blender --background --factory-startup --disable-autoexec.
These tests create small shared native fixtures and disposable saved files;
they never open, modify or save the artist's scene.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
from character_designer import (
    limb_ik, skirt_motion_profiles as profiles, skirt_motion_tuning as tuning,
    skirt_physics as physics, skirt_rig as skirt,
)
from test_skirt_physics_blender import activate, character
from test_skirt_shared_rig_blender import mesh_content, pose_channels, rest_state
from test_skirt_topology_blender import frustum


# Independently list the native material surface. Comparisons therefore detect
# accidental changes to coefficients which were not part of an artist request.
MATERIAL = ('quality', 'mass', 'tension_stiffness', 'compression_stiffness',
            'shear_stiffness', 'bending_stiffness', 'tension_damping',
            'compression_damping', 'shear_damping', 'bending_damping',
            'air_damping', 'pin_stiffness', 'vertex_group_mass', 'use_dynamic_mesh')
COLLISION = ('use_collision', 'use_self_collision', 'collision_quality',
             'distance_min', 'self_distance_min', 'self_friction')


def native(cloth):
    return {'material': {name: getattr(cloth.settings, name) for name in MATERIAL},
            'collision': {name: getattr(cloth.collision_settings, name) for name in COLLISION},
            'gravity': cloth.settings.effector_weights.gravity,
            'collection': cloth.collision_settings.collection}


def cache_state(cloth, *, include_outdated=True):
    cache = cloth.point_cache
    state = (cache.as_pointer(), cache.is_baked, cache.is_baking, cache.use_external,
             cache.use_disk_cache, cache.frame_start, cache.frame_end, cache.frame_step)
    return state + (cache.is_outdated,) if include_outdated else state


def groups(obj):
    return {group.name: {vertex.index: entry.weight for vertex in obj.data.vertices
                        for entry in vertex.groups if entry.group == group.index}
            for group in obj.vertex_groups}


def actions():
    result = {}
    for action in bpy.data.actions:
        curves = limb_ik._fcurves_for_action(action)
        result[action.as_pointer()] = (
            action.name, action.use_fake_user,
            tuple((slot.identifier, slot.target_id_type) for slot in action.slots),
            tuple((curve.data_path, curve.array_index, curve.lock, curve.mute,
                   curve.extrapolation,
                   tuple((tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
                          point.interpolation, point.handle_left_type, point.handle_right_type)
                         for point in curve.keyframe_points)) for curve in curves),
        )
    return result


def influences(rig, record):
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return tuple(evaluated.pose.bones[name].constraints['Skirt physics delta'].influence
                 for chain in record['chains'] for name in chain['def'])


def protected(sources, rig):
    return {'sources': {source.name: mesh_content(source) for source in sources},
            'rest': rest_state(rig), 'pose': pose_channels(rig), 'actions': actions(),
            'action': rig.animation_data.action if rig.animation_data else None,
            'slot': rig.animation_data.action_slot if rig.animation_data else None,
            'inventory': tuple(sorted((obj.name, obj.type) for obj in bpy.data.objects))}


def complete(sources, rig, *, include_outdated=True):
    result = protected(sources, rig)
    result['dress'] = {}
    for source in sources:
        record, _rig, proxy, cloth = physics.validate_physics(source)
        holder, _id, _path = skirt.physics_control(source)
        result['dress'][source.name] = (
            source[skirt.RECORD_KEY], source.get(profiles.PROFILE_KEY),
            holder.get('physics_influence'), native(cloth), groups(proxy),
            cache_state(cloth, include_outdated=include_outdated),
        )
    return result


class DressMotionTuningTests(unittest.TestCase):
    def setUp(self):
        if not bpy.app.background:
            raise RuntimeError('Dress tuning tests require an isolated background Blender.')
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 2
        bpy.context.scene.frame_set(1)

    def make(self, count=1):
        rig = character()
        sources = []
        for index in range(count):
            source = frustum('Artist Dress ' + str(index + 1), rows=6, sides=16)
            activate(source)
            skirt.build_skirt(bpy.context, source, chain_count=4, segment_count=3,
                              armature=rig, parent_bone='Hips', shared=True)
            physics.add_physics(bpy.context, source)
            source.shape_key_add(name='Basis')
            source.shape_key_add(name='Artist asymmetry').data[0].co.x += .019
            uv = source.data.uv_layers.new(name='Artist UV')
            for loop in source.data.loops:
                uv.data[loop.index].uv = (loop.vertex_index / len(source.data.vertices), .37)
            material = bpy.data.materials.new('Artist fabric ' + str(index + 1))
            source.data.materials.append(material)
            extra = source.vertex_groups.new(name='Artist mask')
            extra.add([0, 2], .27, 'REPLACE')
            sources.append(source)
        bpy.context.view_layer.update()
        return tuple(sources), rig

    def seal(self, source):
        physics.bake_simulation(bpy.context, source, 1, 2)
        _record, _rig, _proxy, cloth = physics.validate_physics(source)
        self.assertTrue(cloth.point_cache.is_baked)
        return cloth

    def test_initialize_adopts_native_settings_only_on_source(self):
        sources, rig = self.make()
        source = sources[0]
        _record, _rig, proxy, cloth = physics.validate_physics(source)
        cloth.settings.mass = .23
        cloth.settings.shear_stiffness = 17.25
        cloth.settings.compression_stiffness = 31.0
        before, coefficients = protected(sources, rig), native(cloth)
        profile = tuning.initialize(source)
        self.assertAlmostEqual(profile['settings']['mass'], cloth.settings.mass, places=7)
        self.assertEqual(profile['settings']['shear'], 17.25)
        self.assertEqual(native(cloth), coefficients)
        self.assertEqual(protected(sources, rig), before)
        self.assertIn(profiles.PROFILE_KEY, source)
        self.assertNotIn(profiles.PROFILE_KEY, rig)
        self.assertNotIn(profiles.PROFILE_KEY, proxy)
        self.assertTrue(all(profiles.PROFILE_KEY not in bone for bone in rig.pose.bones))

    def test_partial_material_edit_changes_only_requested_coefficient(self):
        sources, rig = self.make()
        source = sources[0]
        _record, _rig, _proxy, cloth = physics.validate_physics(source)
        cloth.settings.compression_stiffness = 43.0
        cloth.settings.compression_damping = 7.25
        cloth.settings.shear_damping = 6.75
        tuning.initialize(source)
        # A material edit must accept ordinary artist animation while keeping
        # both the shared native Action and the independent Shape Key Action.
        hips = rig.pose.bones['Hips']
        hips.keyframe_insert(data_path='location', frame=1)
        hips.location.x = .04
        hips.keyframe_insert(data_path='location', frame=2)
        key = source.data.shape_keys.key_blocks['Artist asymmetry']
        key.keyframe_insert(data_path='value', frame=1)
        key.value = .4
        key.keyframe_insert(data_path='value', frame=2)
        shared_reader = bpy.data.objects.new('Shared artist Action reader', None)
        bpy.context.scene.collection.objects.link(shared_reader)
        shared_reader.animation_data_create().action = rig.animation_data.action
        bpy.context.scene.frame_set(1)
        before, coefficients = protected(sources, rig), native(cloth)
        results = tuning.apply(bpy.context, sources, {'shear': 14.0})
        coefficients['material']['shear_stiffness'] = 14.0
        self.assertEqual(native(cloth), coefficients)
        self.assertEqual(results[0]['settings']['shear'], 14.0)
        self.assertEqual(protected(sources, rig), before)
        self.assertEqual(shared_reader.animation_data.action, rig.animation_data.action)
        self.assertEqual(physics.validate_physics(source)[0]['physics']['baked_range'], None)

    def test_recovery_edits_only_owned_proxy_goal_weights(self):
        sources, rig = self.make()
        source = sources[0]
        record, _rig, proxy, cloth = physics.validate_physics(source)
        tuning.initialize(source)
        before, coefficients, weights = protected(sources, rig), native(cloth), groups(proxy)
        tuning.apply(bpy.context, sources, {'waist_depth': .12, 'transition': .2, 'recovery': .45})
        updated = groups(proxy)
        self.assertEqual(set(updated), set(weights))
        self.assertEqual({name: value for name, value in updated.items() if name != 'CD Waist Pin'},
                         {name: value for name, value in weights.items() if name != 'CD Waist Pin'})
        columns = record['physics']['columns']
        goal = updated['CD Waist Pin']
        self.assertTrue(all(abs(goal[index] - 1.) < 1e-7 for index in range(columns * 2)))
        self.assertTrue(all(abs(goal[index] - .2) < 1e-7 for index in range(columns * 2, columns * 3)))
        self.assertTrue(all(0. < goal[index] < .04 for index in range(columns * 3, len(proxy.data.vertices))))
        self.assertEqual(native(cloth), coefficients)
        self.assertEqual(protected(sources, rig), before)
        physics.validate_physics(source)

    def test_mode_writes_evaluated_shared_driver_and_retains_native_cache(self):
        sources, rig = self.make()
        source = sources[0]
        record, _rig, proxy, cloth = physics.validate_physics(source)
        tuning.initialize(source)
        before, coefficients, weights, cache = protected(sources, rig), native(cloth), groups(proxy), cache_state(cloth)
        for mode, value in (('MANUAL', 0.), ('AUTOMATIC', 1.)):
            tuning.apply(bpy.context, sources, mode=mode)
            self.assertTrue(all(abs(actual - value) < 1e-7 for actual in influences(rig, record)))
            self.assertEqual(native(cloth), coefficients)
            self.assertEqual(groups(proxy), weights)
            self.assertEqual(cache_state(cloth), cache)
            self.assertEqual(protected(sources, rig), before)

    def test_mode_success_and_late_profile_failure_keep_sealed_cache(self):
        sources, rig = self.make()
        source = sources[0]
        tuning.initialize(source)
        cloth = self.seal(source)
        sealed = cache_state(cloth)
        tuning.apply(bpy.context, sources, mode='MANUAL')
        self.assertEqual(cache_state(cloth), sealed)
        self.assertEqual(physics.validate_physics(source)[0]['physics']['baked_range'], [1, 2])
        before = complete(sources, rig)
        with patch.object(profiles, 'write', side_effect=RuntimeError('late mode profile failure')):
            with self.assertRaisesRegex(RuntimeError, 'late mode profile'):
                tuning.apply(bpy.context, sources, mode='AUTOMATIC')
        self.assertEqual(complete(sources, rig), before)

    def test_animated_or_driven_influence_is_refused_before_writes(self):
        for kind in ('ACTION', 'DRIVER'):
            with self.subTest(kind=kind):
                sources, rig = self.make()
                source = sources[0]
                tuning.initialize(source)
                holder, _id, _path = skirt.physics_control(source)
                if kind == 'ACTION':
                    holder.keyframe_insert(data_path='["physics_influence"]', frame=1)
                else:
                    curve = holder.driver_add('["physics_influence"]')
                    curve.driver.type, curve.driver.expression = 'SCRIPTED', '.5'
                bpy.context.view_layer.update()
                before = complete(sources, rig)
                with self.assertRaisesRegex(ValueError, 'animation or a driver'):
                    tuning.apply(bpy.context, sources, {'mass': .24}, mode='MANUAL')
                self.assertEqual(complete(sources, rig), before)

    def test_bad_second_target_causes_no_first_target_mutation(self):
        sources, rig = self.make(count=2)
        for source in sources:
            tuning.initialize(source)
        first_before = complete((sources[0],), rig)
        second_record, _rig, proxy, _cloth = physics.validate_physics(sources[1])
        proxy[skirt.OWNER_KEY] = 'artist-reassigned-owner'
        second_before = mesh_content(sources[1]), sources[1][skirt.RECORD_KEY], sources[1][profiles.PROFILE_KEY]
        with self.assertRaises(ValueError):
            tuning.apply(bpy.context, sources, {'mass': .24})
        self.assertEqual(complete((sources[0],), rig), first_before)
        self.assertEqual((mesh_content(sources[1]), sources[1][skirt.RECORD_KEY], sources[1][profiles.PROFILE_KEY]), second_before)
        self.assertEqual(proxy[skirt.OWNER_KEY], 'artist-reassigned-owner')
        self.assertEqual(second_record['source'], sources[1].name)

    def test_late_native_write_failure_restores_entire_batch(self):
        sources, rig = self.make(count=2)
        for source in sources:
            tuning.initialize(source)
        before = complete(sources, rig, include_outdated=False)
        apply_native, writes = tuning._apply_native, []
        def fail_second(item, *args, **kwargs):
            apply_native(item, *args, **kwargs)
            writes.append(item['source'])
            if len(writes) == 2:
                raise RuntimeError('late native coefficient failure')
        with patch.object(tuning, '_apply_native', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'late native coefficient'):
                tuning.apply(bpy.context, sources, {'mass': .24, 'recovery': .4})
        self.assertEqual(writes, list(sources))
        # Unsealed simulation cache invalidation is explicitly allowed; the
        # artist/native data, cache identity/range and sealed flags are exact.
        self.assertEqual(complete(sources, rig, include_outdated=False), before)

    def test_late_profile_write_failure_restores_material_mode_and_records(self):
        sources, rig = self.make(count=2)
        for source in sources:
            tuning.initialize(source)
        before = complete(sources, rig, include_outdated=False)
        write, writes = profiles.write, []
        def fail_second(source, *args, **kwargs):
            value = write(source, *args, **kwargs)
            writes.append(source)
            if len(writes) == 2:
                raise RuntimeError('late saved profile failure')
            return value
        with patch.object(profiles, 'write', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'late saved profile'):
                tuning.apply(bpy.context, sources, {'mass': .24, 'recovery': .4}, mode='MANUAL')
        self.assertEqual(writes, list(sources))
        self.assertEqual(complete(sources, rig, include_outdated=False), before)

    def test_baked_material_change_is_refused_without_mode_or_record_change(self):
        sources, rig = self.make()
        source = sources[0]
        tuning.initialize(source)
        self.seal(source)
        before = complete(sources, rig)
        with self.assertRaisesRegex(ValueError, 'Reset the baked'):
            tuning.apply(bpy.context, sources, {'mass': .24}, mode='MANUAL')
        self.assertEqual(complete(sources, rig), before)

    def test_normalized_margin_boundary_matches_native_or_refuses_before_write(self):
        for key, attribute in (('collision_margin', 'distance_min'), ('self_margin', 'self_distance_min')):
            for requested in profiles.RANGES[key]:
                with self.subTest(key=key, requested=requested):
                    self.setUp()
                    sources, rig = self.make()
                    source = sources[0]
                    record, _rig, _proxy, cloth = physics.validate_physics(source)
                    tuning.initialize(source)
                    distance = requested * record['fit']['height_world']
                    prop = cloth.collision_settings.bl_rna.properties[attribute]
                    before = complete(sources, rig)
                    if not prop.hard_min <= distance <= prop.hard_max:
                        with self.assertRaises(ValueError):
                            tuning.apply(bpy.context, sources, {key: requested})
                        self.assertEqual(complete(sources, rig), before)
                    else:
                        tuning.apply(bpy.context, sources, {key: requested})
                        self.assertAlmostEqual(getattr(cloth.collision_settings, attribute), distance,
                                               delta=max(1e-9, distance * 1e-6))
                        effective = tuning.effective(source)
                        self.assertAlmostEqual(effective['settings'][key], requested,
                                               delta=max(1e-9, requested * 1e-6))

    def test_native_float_mass_and_effector_boundary_roundtrip(self):
        sources, _rig = self.make()
        source = sources[0]
        tuning.initialize(source)
        for mass, gravity in ((.001, 0.), (5., 2.)):
            with self.subTest(mass=mass, gravity=gravity):
                tuning.apply(bpy.context, sources, {'mass': mass, 'gravity': gravity})
                _record, _rig, _proxy, cloth = physics.validate_physics(source)
                self.assertAlmostEqual(cloth.settings.mass, mass, delta=max(1e-9, mass * 1e-6))
                self.assertEqual(cloth.settings.effector_weights.gravity, gravity)
                self.assertEqual(tuning.effective(source)['settings']['mass'], mass)
                self.assertEqual(tuning.effective(source)['settings']['gravity'], gravity)

    def test_source_profile_survives_save_reopen_with_native_settings_and_artist_content(self):
        sources, rig = self.make()
        source = sources[0]
        tuning.initialize(source)
        tuning.apply(bpy.context, sources, {'mass': .24, 'recovery': .4, 'shear': 14.}, mode='MANUAL')
        names = source.name, rig.name
        raw, mesh, skeleton = source[profiles.PROFILE_KEY], mesh_content(source), rest_state(rig)
        coefficient = native(physics.validate_physics(source)[3])
        collection_name = coefficient.pop('collection').name
        with tempfile.TemporaryDirectory(prefix='cd_dress_tuning_') as directory:
            path = str(Path(directory) / 'owned_dress.blend')
            self.assertIn('FINISHED', bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False))
            self.assertIn('FINISHED', bpy.ops.wm.open_mainfile(filepath=path, load_ui=False))
            source, rig = (bpy.data.objects[name] for name in names)
            self.assertEqual(source[profiles.PROFILE_KEY], raw)
            self.assertEqual(mesh_content(source), mesh)
            self.assertEqual(rest_state(rig), skeleton)
            restored = native(physics.validate_physics(source)[3])
            self.assertEqual(restored.pop('collection').name, collection_name)
            self.assertEqual(restored, coefficient)
            self.assertEqual(tuning.effective(source)['mode'], 'MANUAL')
            self.assertNotIn(profiles.PROFILE_KEY, rig)
            self.assertNotIn(profiles.PROFILE_KEY, physics.validate_physics(source)[2])


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DressMotionTuningTests))
    if not result.wasSuccessful():
        raise RuntimeError('Native Dress motion tuning tests failed')
