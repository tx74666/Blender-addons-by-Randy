"""Factory-only native differential proof against byte-pinned 0.77.40 sources.

Example (root coordinates the serial Blender run):
  blender --background --factory-startup --disable-autoexec --python-exit-code 1
    --python tests/test_motion_rest_snapshot_blender.py --
    --baseline-dir <X/validation/playback_performance_20261014/round3/baseline_07740>
    --report <private validation report.json>

Imports only the selected source contracts, with separate baseline/candidate
module namespaces. No add-on registration, handlers, saved scene, or artist file.
"""

import argparse
import ast
import copy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
from types import MappingProxyType, ModuleType
import unittest
import uuid

import bpy
from mathutils import Vector


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'
EXPECTED_BASELINE = {
    'unity_animation.py': ('c8db791e3077c439552211d5fef3770a59c9630340528ef451ecdea5b7f697b4', 43840),
    'animation_worklist_target.py': ('55b1dad8c9bfd6986a34b5e4cfc6c120dba80fedb2c76f09d613ad80b17a57fd', 45088),
}
FLAGS = ('use_deform', 'use_inherit_rotation', 'inherit_scale',
         'use_local_location', 'use_relative_parent')
MODULES = {}
CASE_RESULTS = []


def pin(path):
    raw = path.read_bytes()
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def assert_factory():
    if not bpy.app.background or '--factory-startup' not in sys.argv or bpy.data.filepath:
        raise RuntimeError('This test requires --background --factory-startup and no opened .blend.')


def owner_key(path):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    node = next(node for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'OWNER_KEY' for target in node.targets))
    value = ast.literal_eval(node.value)
    if not isinstance(value, str) or not value:
        raise ValueError('Unexpected ownership key.')
    return value


def load_contract(folder, variant):
    """Each frozen function resolves only its own frozen UA globals."""
    package_name = '_native_rest_' + variant + '_' + uuid.uuid4().hex
    package = ModuleType(package_name)
    package.__path__ = []
    sys.modules[package_name] = package
    ua_name = package_name + '.unity_animation'
    ua = ModuleType(ua_name)
    ua.__dict__.update(dataclass=dataclass, MappingProxyType=MappingProxyType,
                       math=math, Vector=Vector)
    sys.modules[ua_name] = ua
    wanted = {'_rest_state', '_same_rest_bone', '_same_current_rest_bone',
              '_SavedRestState', '_compile_saved_rest_state', '_matches_saved_rest_state',
              '_matches_current_rest_state', '_matches_rest_state', '_read_matched_rest_state'}
    constants = {'_SAVED_REST_FIELDS', '_SAVED_REST_VECTOR_LENGTHS'}
    path = folder / 'unity_animation.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    nodes = [node for node in tree.body if
             isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in wanted
             or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants
                                                    for target in node.targets)]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ua.__dict__)
    package.unity_animation = ua
    for component in ('limb_ik', 'skirt_rig'):
        module_name = package_name + '.' + component
        module = ModuleType(module_name)
        module.OWNER_KEY = owner_key(SOURCE / (component + '.py'))
        sys.modules[module_name] = module
        setattr(package, component, module)
    path = folder / 'animation_worklist_target.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    node = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == '_prove_motion_data')
    target = ModuleType(package_name + '.target')
    target.__package__ = package_name
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), target.__dict__)
    MODULES[variant] = {'ua': ua, 'target': target, 'package': package}


def outcome(callback):
    try:
        value = callback()
        if isinstance(value, frozenset):
            value = sorted(value)
        return {'kind': 'return', 'value': value}
    except Exception as error:
        return {'kind': 'raise', 'type': type(error).__module__ + '.' + type(error).__name__,
                'message': str(error)}


def next_float32(value, step):
    bits = struct.unpack('<I', struct.pack('<f', value))[0]
    return struct.unpack('<f', struct.pack('<I', bits + step))[0]


class NativeRestSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.data = bpy.data.armatures.new('Rest proof native fixture')
        self.obj = bpy.data.objects.new('Rest proof native fixture', self.data)
        bpy.context.scene.collection.objects.link(self.obj)
        self.edit(self.data, self.build_bones)
        self.motion = self.data.copy()
        self.motion.name = 'Rest proof native motion copy'
        self.edit(self.motion, lambda bones: setattr(bones['child'], 'use_connect', False))
        baseline = MODULES['baseline']['ua']
        self.original_rest = baseline._rest_state(self.data)
        self.motion_rest = baseline._rest_state(self.motion)
        self.mapped = tuple(sorted(self.data.bones.keys()))

    @staticmethod
    def build_bones(bones):
        root = bones.new('root')
        root.head, root.tail = (0., 0., 0.), (0., 1., 0.)
        child = bones.new('child')
        child.head, child.tail = (0., 1., 0.), (0., 2., 0.)
        child.parent, child.use_connect = root, True
        free = bones.new('free')
        free.head, free.tail = (0., 0., 0.), (0., 1., 0.)

    def edit(self, data, callback):
        previous_data = self.obj.data
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            raise RuntimeError('The factory fixture unexpectedly left Object Mode.')
        self.obj.data = data
        self.obj.select_set(True)
        bpy.context.view_layer.objects.active = self.obj
        try:
            result = bpy.ops.object.mode_set(mode='EDIT')
            if result != {'FINISHED'}:
                raise RuntimeError('Could not enter Edit Mode on the private fixture.')
            callback(data.edit_bones)
        finally:
            if self.obj.mode == 'EDIT':
                result = bpy.ops.object.mode_set(mode='OBJECT')
                if result != {'FINISHED'}:
                    raise RuntimeError('Could not leave Edit Mode on the private fixture.')
            self.obj.data = previous_data

    def tearDown(self):
        if self.obj.mode != 'OBJECT':
            bpy.context.view_layer.objects.active = self.obj
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.data.objects.remove(self.obj, do_unlink=True)
        for data in (self.motion, self.data):
            if data.users != 0:
                raise RuntimeError('The private fixture retained an unexpected Armature user.')
            bpy.data.armatures.remove(data)

    def fingerprint(self):
        ua = MODULES['baseline']['ua']
        state = {'original': ua._rest_state(self.data), 'motion': ua._rest_state(self.motion),
                 'owners': [{bone.name: dict(bone.items()) for bone in data.bones}
                            for data in (self.data, self.motion)]}
        return hashlib.sha256(json.dumps(state, sort_keys=True, allow_nan=False,
                                          separators=(',', ':')).encode()).hexdigest()

    def compare(self, label, *, original_saved=None, motion_saved=None, mapped=None,
                error=None, translated=('child',), details=None):
        original_saved = self.original_rest if original_saved is None else original_saved
        motion_saved = self.motion_rest if motion_saved is None else motion_saved
        mapped = self.mapped if mapped is None else mapped
        before = self.fingerprint()
        results = {}
        for storage in ('ordinary', 'compiled'):
            pair = {}
            for variant in ('baseline', 'candidate'):
                ua, target = MODULES[variant]['ua'], MODULES[variant]['target']
                first, second = copy.deepcopy(original_saved), copy.deepcopy(motion_saved)
                if storage == 'compiled':
                    first, second = ua._compile_saved_rest_state(first), ua._compile_saved_rest_state(second)
                    self.assertIsNotNone(first, label + ': original compiled state')
                    self.assertIsNotNone(second, label + ': motion compiled state')
                pair[variant] = outcome(lambda: target._prove_motion_data(
                    self.data, self.motion, first, second, mapped))
                self.assertEqual(self.fingerprint(), before, label + ': no native changes')
            self.assertEqual(pair['candidate'], pair['baseline'], label + ': differential ' + storage)
            if error is None:
                self.assertEqual(pair['baseline'], {'kind': 'return', 'value': sorted(translated)})
            else:
                self.assertEqual(pair['baseline']['kind'], 'raise')
                self.assertEqual(pair['baseline']['type'], 'builtins.ValueError')
                self.assertIn(error, pair['baseline']['message'])
            results[storage] = pair
        CASE_RESULTS.append({'label': label, 'results': results, 'native_before_after_sha256': before,
                             'details': details or {}})

    def test_legal_connected_translation(self):
        self.compare('legal connected translation')

    def test_unmapped_and_reverse_connect_changes(self):
        self.compare('unmapped connected translation', mapped=('free', 'root'),
                     error='unapproved connected-bone flag')
        self.edit(self.data, lambda bones: setattr(bones['child'], 'use_connect', False))
        self.edit(self.motion, lambda bones: setattr(bones['child'], 'use_connect', True))
        baseline = MODULES['baseline']['ua']
        self.compare('reverse connected transition', original_saved=baseline._rest_state(self.data),
                     motion_saved=baseline._rest_state(self.motion), error='unapproved connected-bone flag')

    def test_owner_checks_stay_live(self):
        self.compare('before owner mutation')
        package = MODULES['candidate']['package']
        for data_label, data in (('original', self.data), ('motion', self.motion)):
            for component in ('limb_ik', 'skirt_rig'):
                key = getattr(package, component).OWNER_KEY
                for value in (0, 'generated owner'):
                    with self.subTest(data=data_label, component=component, value=value):
                        data.bones['child'][key] = value
                        self.compare('owner refusal ' + data_label + '/' + component + '/' + str(value),
                                     error='unapproved connected-bone flag')
                        del data.bones['child'][key]
        self.compare('after owner removal')

    def test_current_inventory_changes(self):
        def add(bones):
            extra = bones.new('extra')
            extra.head, extra.tail = (3., 0., 0.), (3., 1., 0.)
        self.edit(self.motion, add)
        baseline = MODULES['baseline']['ua']
        self.compare('motion inventory added with matching saved Rest',
                     motion_saved=baseline._rest_state(self.motion), error='bone inventory')
        self.edit(self.motion, lambda bones: bones.remove(bones['extra']))
        self.edit(self.motion, lambda bones: bones.remove(bones['free']))
        self.compare('motion inventory removed with matching saved Rest',
                     motion_saved=baseline._rest_state(self.motion), error='bone inventory')

    def test_original_rest_changes_after_success(self):
        self.compare('original before native edit')
        self.edit(self.data, lambda bones: setattr(bones['free'], 'head', (.02, 0., 0.)))
        self.compare('original native head edited after success', error='owned motion-copy Rest changed')

    def test_motion_rest_changes_after_success(self):
        self.compare('motion before native edit')
        self.edit(self.motion, lambda bones: setattr(bones['free'], 'head', (.02, 0., 0.)))
        self.compare('motion native head edited after success', error='owned motion-copy Rest changed')

    def test_structural_flags_with_matching_saved_rest(self):
        baseline = MODULES['baseline']['ua']
        for field in FLAGS:
            with self.subTest(field=field):
                bone = self.motion.bones['free']
                old = getattr(bone, field)
                changed = 'NONE' if field == 'inherit_scale' else not old
                self.edit(self.motion, lambda bones: setattr(bones['free'], field, changed))
                self.compare('cross-copy structural flag ' + field,
                             motion_saved=baseline._rest_state(self.motion),
                             error='geometry, parents or inheritance')
                self.edit(self.motion, lambda bones: setattr(bones['free'], field, old))

    def test_native_head_tail_norm_boundaries_with_matching_saved_rest(self):
        baseline, candidate = MODULES['baseline']['ua'], MODULES['candidate']['ua']
        values = (5e-7, next_float32(1e-6, -1), 1e-6, next_float32(1e-6, 1),
                  7e-7, 8e-7, 2e-6)
        for field in ('head', 'tail'):
            for axes in (1, 3):
                for delta in values:
                    with self.subTest(field=field, axes=axes, delta=delta):
                        def change(bones):
                            bone = bones['free']
                            bone.head, bone.tail = (0., 0., 0.), (0., 1., 0.)
                            offset = Vector((delta, 0., 0.) if axes == 1 else (delta, delta, delta))
                            setattr(bone, field, Vector(getattr(bone, field)) + offset)
                        self.edit(self.motion, change)
                        source_bone, motion_bone = self.data.bones['free'], self.motion.bones['free']
                        old_geometry = baseline._same_rest_bone(source_bone, motion_bone)
                        original_current = baseline._rest_state(self.data)
                        motion_current = baseline._rest_state(self.motion)
                        self.assertEqual(candidate._same_current_rest_bone(
                            original_current['free'], motion_current['free']), old_geometry)
                        native_length = (getattr(source_bone, field + '_local')
                                         - getattr(motion_bone, field + '_local')).length
                        # Records deliberately capture the altered motion Rest:
                        # both saved-Rest matches pass and cross-copy norm runs.
                        for variant in ('baseline', 'candidate'):
                            ua = MODULES[variant]['ua']
                            self.assertTrue(ua._matches_rest_state(self.data, self.original_rest))
                            self.assertTrue(ua._matches_rest_state(self.motion, motion_current))
                        self.compare('native ' + field + '/' + str(axes) + ' axes/' + repr(delta),
                                     motion_saved=motion_current,
                                     error=None if old_geometry else 'geometry, parents or inheritance',
                                     details={'requested_delta': delta, 'native_vector_delta_length': native_length,
                                              'cross_copy_geometry_result': old_geometry,
                                              'saved_rest_matches_proven': True})


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--baseline-dir', type=Path, required=True)
    args.add_argument('--report', type=Path, required=True)
    parsed = args.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    report = {'schema': 'x.native-rest-current-snapshot-differential/1', 'completed': False,
              'blender': bpy.app.version_string, 'no_scene_save': True,
              'no_addon_registration': True, 'artist_file_opened': False}
    try:
        assert_factory()
        pins = []
        for name, (digest, length) in EXPECTED_BASELINE.items():
            item = pin(parsed.baseline_dir / name)
            if (item['sha256'], item['bytes']) != (digest, length):
                raise RuntimeError('Frozen baseline pin mismatch: ' + name)
            pins.append(item)
        report['baseline_pins'] = pins
        report['candidate_pins'] = [pin(SOURCE / name) for name in EXPECTED_BASELINE]
        handler_names = ('frame_change_pre', 'frame_change_post', 'depsgraph_update_pre',
                         'depsgraph_update_post', 'load_pre', 'load_post', 'save_pre', 'save_post')
        before_handlers = {name: tuple(getattr(bpy.app.handlers, name)) for name in handler_names}
        before_inventory = (len(bpy.data.objects), len(bpy.data.armatures))
        load_contract(parsed.baseline_dir, 'baseline')
        load_contract(SOURCE, 'candidate')
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(NativeRestSnapshotTests))
        report['test_count'] = result.testsRun
        report['failures'] = [(str(test), trace) for test, trace in result.failures]
        report['errors'] = [(str(test), trace) for test, trace in result.errors]
        report['cases'] = CASE_RESULTS
        assert_factory()
        after_handlers = {name: tuple(getattr(bpy.app.handlers, name)) for name in handler_names}
        report['handlers_unchanged'] = before_handlers == after_handlers
        report['fixture_inventory_cleaned'] = before_inventory == (len(bpy.data.objects), len(bpy.data.armatures))
        if not report['handlers_unchanged'] or not report['fixture_inventory_cleaned']:
            raise RuntimeError('The factory test left a handler or native fixture behind.')
        report['completed'] = result.wasSuccessful()
    except Exception as error:
        report['fatal_error'] = type(error).__name__ + ': ' + str(error)
    finally:
        parsed.report.parent.mkdir(parents=True, exist_ok=True)
        parsed.report.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n',
                                 encoding='utf-8')
    if not report['completed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
