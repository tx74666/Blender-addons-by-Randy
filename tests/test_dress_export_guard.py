"""Standard-library admission tests; no Blender import or native evaluation."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'
spec = importlib.util.spec_from_file_location('direct_guard_under_test', ROOT / 'dress_export_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ID(dict):
    def __init__(self, name, type='MESH', **attributes):
        super().__init__()
        self.name, self.type = name, type
        self.modifiers = []
        self.__dict__.update(attributes)

    def __eq__(self, other):
        return self is other

    __hash__ = object.__hash__


class Objects(list):
    def get(self, name):
        return next((obj for obj in self if obj.name == name), None)

    def __contains__(self, item):
        return self.get(item) is not None if type(item) is str else super().__contains__(item)


def fixture(backend=None):
    rig = ID('Rig', 'ARMATURE', data=NS(name='RigData', bones=[]), mode='POSE', animation_data=None)
    action = NS(name='Action', library=None, slots=[NS(handle=7, target_id_type='OBJECT')])
    objects = Objects([rig])
    if backend is not None:
        source = ID('Dress')
        source[guard.RECORD_KEY] = json.dumps({'version': 1, 'owner': 'owner', 'rig': rig.name,
                                              'physics': {'backend': backend}})
        source[guard.RIG_KEY] = rig
        source[guard.OWNER_KEY] = 'owner'
        rig[guard.SHARED_KEY] = {'owner': source}
        bone = ID('DressDEF', 'BONE')
        bone[guard.OWNER_KEY], bone[guard.SOURCE_KEY] = 'owner', source
        rig.data.bones.append(bone)
        objects.append(source)
    return objects, rig, action


def function(filename, name, namespace):
    tree = ast.parse((ROOT / filename).read_text(encoding='utf-8-sig'))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), namespace)
    return namespace[name]


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.snapshot = Path(self.temp.name) / 'snapshot.blend'
        self.snapshot.write_bytes(b'private snapshot fixture, not a native blend')

    def tearDown(self):
        self.temp.cleanup()

    def packet(self, backend=None):
        objects, rig, action = fixture(backend)
        admission = guard.animation_host_admission(objects, rig, action, 7)
        packet = {'rig': rig.name, 'action': action.name, 'action_slot': 7,
                  guard.JOB_KEY: guard.bind_animation_snapshot(admission, self.snapshot)}
        return objects, rig, action, packet

    def test_plain_legacy_delta_json_roundtrip(self):
        for backend in (None, 'LEGACY_CAGE', 'ACTUAL_SURFACE_DELTA_V1'):
            with self.subTest(backend=backend):
                objects, rig, action, packet = self.packet(backend)
                guard.reject_model_sources(objects)
                guard.verify_animation_snapshot(json.loads(json.dumps(packet)), objects, rig, action, self.snapshot)

    def test_non_source_legacy_delta_helpers_are_not_reverse_sources(self):
        for backend in ('LEGACY_CAGE', 'ACTUAL_SURFACE_DELTA_V1'):
            objects, rig, action = fixture(backend)
            helper = ID('ClothProxy')
            helper[guard.RIG_KEY], helper[guard.OWNER_KEY] = rig, 'owner'
            helper[guard.ROLE_KEY] = 'CLOTH_PROXY'
            objects.append(helper)
            admission = guard.animation_host_admission(objects, rig, action, 7)
            self.assertEqual(admission['source_count'], 1)
            packet = {'rig': rig.name, 'action': action.name, 'action_slot': 7,
                      guard.JOB_KEY: guard.bind_animation_snapshot(admission, self.snapshot)}
            guard.verify_animation_snapshot(packet, objects, rig, action, self.snapshot)

    def test_direct_never_waived_by_mode_bake_or_surface_state(self):
        objects, rig, action = fixture(guard.DIRECT)
        for mode in ('MANUAL', 'AUTOMATIC'):
            raw = json.loads(objects[-1][guard.RECORD_KEY])
            raw['physics'].update({'baked_range': [1, 60], 'surface': {'mode': mode, 'editing': False}})
            objects[-1][guard.RECORD_KEY] = json.dumps(raw)
            with self.assertRaises(ValueError):
                guard.reject_model_sources(objects)
            with self.assertRaises(ValueError):
                guard.animation_host_admission(objects, rig, action, 7)

    def test_state_and_owned_gn_cannot_hide_without_record(self):
        source = ID('Dress')
        for indicator in ('state', 'input', 'output', 'body'):
            source.clear(); source.modifiers = []
            if indicator == 'state': source[guard.STATE_KEY] = '{}'
            elif indicator == 'input': source[guard.ROLE_KEY] = 'INPUT_SURFACE'
            else: source.modifiers = [NS(node_group={guard.ROLE_KEY: 'DIRECT_NODE_GROUP' if indicator == 'output' else 'BODY_NODE_GROUP'})]
            with self.assertRaises(ValueError): guard.reject_model_sources([source])

    def test_bad_records(self):
        source = fixture('LEGACY_CAGE')[0][-1]
        for raw in (None, [], '{}', '{', '{"version":1,"version":1}',
                    '{"version":true,"owner":"owner"}', '{"version":1,"owner":"owner","x":NaN}',
                    '{"version":1,"owner":"owner","physics":{"backend":"UNKNOWN"}}'):
            source[guard.RECORD_KEY] = raw
            with self.subTest(raw=raw), self.assertRaises(ValueError): guard.reject_model_sources([source])

    def test_unknown_and_changed_receipts(self):
        objects, rig, action, packet = self.packet('LEGACY_CAGE')
        edits = [lambda p:p.pop(guard.JOB_KEY),
                 lambda p:p.update({guard.JOB_KEY: True}),
                 lambda p:p[guard.JOB_KEY].update(version=True),
                 lambda p:p[guard.JOB_KEY].update(complete_host_reverse_audit=False),
                 lambda p:p[guard.JOB_KEY].update(source_count=0),
                 lambda p:p[guard.JOB_KEY].update(sources=[]),
                 lambda p:p[guard.JOB_KEY]['sources'][0].update(record_sha256='0'*64),
                 lambda p:p[guard.JOB_KEY]['sources'].append(p[guard.JOB_KEY]['sources'][0]),
                 lambda p:p[guard.JOB_KEY]['snapshot'].update(sha256='0'*64),
                 lambda p:p[guard.JOB_KEY].update(physics_export_authorized=True),
                 lambda p:p.update(action_slot=True), lambda p:p.update(action='Other')]
        for edit in edits:
            changed = copy.deepcopy(packet); edit(changed)
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                guard.verify_animation_snapshot(changed, objects, rig, action, self.snapshot)

    def test_missing_source_broken_registry_and_bone(self):
        for failure in ('omitted_mesh', 'registry_none', 'registry_wrong_owner', 'bone_none', 'bone_missing_owner', 'missing_rig'):
            objects, rig, action, packet = self.packet('LEGACY_CAGE')
            if failure == 'omitted_mesh': objects.pop()
            elif failure == 'registry_none': rig[guard.SHARED_KEY]['owner'] = None
            elif failure == 'registry_wrong_owner': rig[guard.SHARED_KEY] = {'wrong': objects[-1]}
            elif failure == 'bone_none': rig.data.bones[0][guard.SOURCE_KEY] = None
            elif failure == 'bone_missing_owner': rig.data.bones[0].pop(guard.OWNER_KEY)
            else: objects[-1].pop(guard.RIG_KEY)
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                guard.verify_animation_snapshot(packet, objects, rig, action, self.snapshot)

    def test_snapshot_bytes_changed(self):
        objects, rig, action, packet = self.packet()
        self.snapshot.write_bytes(b'changed')
        with self.assertRaises(ValueError): guard.verify_animation_snapshot(packet, objects, rig, action, self.snapshot)

    def test_empty_receipt_does_not_allow_native_direct(self):
        _, _, _, packet = self.packet()
        objects, rig, action = fixture(guard.DIRECT)
        with self.assertRaises(ValueError): guard.verify_animation_snapshot(packet, objects, rig, action, self.snapshot)

    def test_host_scans_other_scene_sources_and_native_arm_links(self):
        objects, rig, action = fixture(guard.DIRECT)
        rig.clear(); rig.data.bones.clear()
        source = objects[-1]
        source.pop(guard.RIG_KEY)
        raw = json.loads(source[guard.RECORD_KEY]); raw.pop('rig'); source[guard.RECORD_KEY] = json.dumps(raw)
        source.modifiers = [NS(type='ARMATURE', object=rig)]
        with self.assertRaises(ValueError): guard.animation_host_admission(objects, rig, action, 7)

    def test_workers_refuse_before_any_side_effect(self):
        objects, rig, action = fixture(guard.DIRECT)
        for filename, error_name in [('unity_export_worker.py', 'ExportError'), ('animation_export_worker.py', 'AnimationExportError')]:
            events = []
            namespace = {'_direct_export_guard': lambda:guard,
                         'bpy': NS(data=NS(objects=objects, actions=Objects([action]), filepath=str(self.snapshot))),
                         error_name: type(error_name, (ValueError,), {}),
                         'Path': lambda *args:events.append('filesystem')}
            call = function(filename, 'export_job', namespace)
            packet = {'objects': ['Dress'], 'rig': rig.name, 'action': action.name, 'action_slot': 7}
            with self.assertRaises(namespace[error_name]): call(packet)
            self.assertEqual(events, [])

    def test_hosts_refuse_before_preview_or_snapshot(self):
        package = ModuleType('_direct_guard_pure_package'); package.__path__ = []
        sys.modules[package.__name__] = package
        sys.modules[package.__name__+'.dress_export_guard'] = guard
        events = []
        hair = NS(status=lambda context:events.append('hair') or {'active': False})
        sys.modules[package.__name__+'.hair_wiggle_adapter'] = hair
        sys.modules[package.__name__+'.unity_export'] = NS(_ACTIVE_JOB=None)
        objects, rig, action = fixture(guard.DIRECT)
        context = NS(mode='POSE', scene=NS(objects=objects))
        for filename, error_name in [('unity_export.py', 'ExportError'), ('animation_export.py', 'AnimationExportError')]:
            namespace = {'__package__': package.__name__, '_ACTIVE_JOB': None, 'export_running': lambda:False,
                         'collect_character': lambda *args:{'objects': objects}, '_slot': lambda *args:7,
                         'bpy': NS(data=NS(objects=objects)), error_name: type(error_name,(ValueError,),{})}
            call = function(filename, 'begin_export', namespace)
            with self.assertRaises(namespace[error_name]):
                if filename.startswith('unity'): call(context, rig, NS())
                else: call(context, rig, action, '/unused.fbx', frame_start=1, frame_end=60)
            self.assertEqual(events, [])

    def test_actual_worker_loader_is_registration_free(self):
        namespace = {'sys': sys, 'importlib': __import__('importlib'), 'Path': Path,
                     '__file__': str(ROOT/'unity_export_worker.py')}
        loader = function('unity_export_worker.py', '_direct_export_guard', namespace)
        module = loader()
        self.assertEqual(module.SCHEMA, guard.SCHEMA)
        self.assertNotIn('bpy', module.__dict__)


if __name__ == '__main__': unittest.main()
