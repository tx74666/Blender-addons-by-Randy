"""Pure static-export boundaries; native graph/FBX proof remains separate."""
import ast
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'addons/character_designer'
RECORD = 'character_designer_skirt_v1'
OWNER = 'character_designer_skirt_owner'
SOURCE = 'character_designer_skirt_source'
RIG = 'character_designer_skirt_armature'
ROLE = 'character_designer_skirt_surface_role'
BACKEND = 'ACTUAL_SURFACE_DELTA_V1'


def runtime(filename, names):
    scope = {'json': json, 'Path': Path, '__file__': str(ROOT / filename)}
    tree = ast.parse((ROOT / filename).read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if
             isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
             or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                and target.id.startswith('_DRESS_') for target in node.targets)]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / filename), 'exec'), scope)
    return scope


class Object:
    def __init__(self, name, kind='MESH', **metadata):
        self.name, self.type, self.metadata = name, kind, metadata
        self.parent = None
        self.modifiers, self.users_collection = [], []
        self.data = SimpleNamespace(vertices=[object()])
        self.pose = SimpleNamespace(bones=[])
        self.hide_render = self.hide_viewport = False

    def __contains__(self, key): return key in self.metadata
    def __getitem__(self, key): return self.metadata[key]
    def get(self, key, default=None): return self.metadata.get(key, default)
    def keys(self): return self.metadata.keys()


class Objects(list):
    def get(self, name): return next((obj for obj in self if obj.name == name), None)
    def __getitem__(self, value): return self.get(value) if isinstance(value, str) else super().__getitem__(value)
    def __contains__(self, value):
        return any(obj.name == value for obj in self) if isinstance(value, str) else super().__contains__(value)


class StaticDressExport(unittest.TestCase):
    def setUp(self):
        self.export = runtime('unity_export.py', {
            'ExportError', '_dress_record', '_dress_surface_helper', '_capture_dress_surfaces',
            '_dress_publication', '_model_snapshot_datablocks', 'begin_export',
            '_descends', '_binding_armatures', '_character_armatures',
            '_helpers', '_bound_meshes', '_collection_scope'})
        self.worker = runtime('unity_export_worker.py', {
            'ExportError', '_dress_backend_source', '_capture_dress_snapshot', '_strip_dress_snapshot', 'export_job'})
        self.rig = Object('CharacterRig', 'ARMATURE')
        self.source = Object('Dress', **{OWNER: 'dress-owner', RIG: self.rig})
        self.overlay = SimpleNamespace(name='Dress Surface', type='NODES')
        self.arm = SimpleNamespace(name='Dress Skin', type='ARMATURE', object=self.rig,
            show_viewport=True, use_vertex_groups=True, use_bone_envelopes=False)
        self.generic = SimpleNamespace(name='Artist Attributes', type='NODES')
        self.subsurf = SimpleNamespace(name='Subdivision', type='SUBSURF')
        self.source.modifiers = [self.arm, self.overlay, self.generic, self.subsurf]
        self.original = {'basis': [[0., 1., 2.]], 'keys': {'Artist': [[.1, 1., 2.]]},
                         'weights': [{0: 1.}], 'rest': [[1., 0.], [0., 1.]]}
        self.source.native_inputs = copy.deepcopy(self.original)
        self.source.native_digest = 'graph-1'
        self.record = {'version': 1, 'owner': 'dress-owner', 'physics': {
            'backend': BACKEND, 'proxy': 'Cloth', 'colliders': ['Collider'],
            'surface': {'version': 1, 'roles': {'CLOTH_PROXY': ['Cloth'],
                'NEUTRAL_RIG': ['Neutral Rig'], 'BODY_ATTACHMENT': ['Body Collision']}}}}
        self.save_record()
        self.cloth = self.helper('Cloth', 'CLOTH_PROXY')
        self.collider = self.helper('Collider', None)
        self.body_collision = self.helper('Body Collision', 'BODY_ATTACHMENT')
        self.neutral = self.helper('Neutral Rig', 'NEUTRAL_RIG', 'ARMATURE')
        self.neutral.parent = self.rig
        self.proof = {'version': 1, 'backend': BACKEND, 'source': self.source.name,
            'owner': self.record['owner'], 'rig': self.rig.name, 'graph_digest': 'graph-1',
            'roles': copy.deepcopy(self.record['physics']['surface']['roles'])}
        self.calls = []
        self.service = ModuleType('_dress_test.skirt_surface')
        self.service.BACKEND = BACKEND
        self.service.export_capture = lambda source: self.calls.append(('capture', source.name)) or copy.deepcopy(self.proof)
        self.service.validate_snapshot = self.validate
        self.service.strip_export_snapshot = self.strip
        package = ModuleType('_dress_test'); package.__path__ = []
        package.skirt_surface = self.service
        self.module_patch = patch.dict(sys.modules, {'_dress_test': package,
            '_dress_test.skirt_surface': self.service})
        self.module_patch.start(); self.addCleanup(self.module_patch.stop)
        self.export['__package__'] = '_dress_test'
        self.worker['_dress_services'] = lambda: self.calls.append(('service',)) or self.service

    def save_record(self): self.source.metadata[RECORD] = json.dumps(self.record)

    def test_snapshot_scene_roots_preserve_exact_fbx_inventory(self):
        home = Object('Source Scene', 'SCENE')
        service = ModuleType('_dress_test.dress_export_snapshot')
        captured = []
        service.snapshot_scene_roots = lambda proofs: captured.append(proofs) or {home}
        with patch.dict(sys.modules, {'_dress_test.dress_export_snapshot': service}):
            roots = self.export['_model_snapshot_datablocks']([self.rig, self.source], [self.proof])
            self.assertEqual(roots, {self.rig, self.source, home})
            self.assertEqual(captured, [[self.proof]])
        self.assertEqual(self.export['_model_snapshot_datablocks']([self.rig, self.source], []),
                         {self.rig, self.source})  # Ordinary/legacy snapshots need no service.

    def test_public_snapshot_writes_scene_proof_but_not_fbx_selection(self):
        self._public_snapshot_case()

    def test_public_snapshot_proof_failure_does_not_launch_or_change_asset_id(self):
        self._public_snapshot_case(failure=True)

    def _public_snapshot_case(self, *, failure=False):
        home = Object('Source Scene', 'SCENE')
        service = ModuleType('_dress_test.dress_export_snapshot')
        def scene_roots(proofs):
            self.assertEqual(proofs, [self.proof])
            if failure:
                raise ValueError('Changed native Scene membership')
            return {home}
        service.snapshot_scene_roots = scene_roots
        hair = ModuleType('_dress_test.hair_wiggle_adapter')
        hair.status = lambda _context: {'active': False}
        forearm = ModuleType('_dress_test.unity_forearm')
        package = sys.modules['_dress_test']
        writes, launches = [], []
        def write(path, roots, **options):
            writes.append((set(roots), options))
            Path(path).write_bytes(b'Private pure-test snapshot; not native Blender evidence')
        def launch(command, **_options):
            launches.append(command)
            return SimpleNamespace(pid=0)
        context = SimpleNamespace(mode='OBJECT', scene=SimpleNamespace(
            unit_settings=SimpleNamespace(scale_length=1.0)))
        config = SimpleNamespace(asset_id='', simple_materials=[])
        original = copy.deepcopy(self.source.native_inputs)
        with tempfile.TemporaryDirectory(prefix='cdesigner-snapshot-transport-') as target:
            self.export.update(_ACTIVE_JOB=None, time=time, uuid=uuid, tempfile=tempfile,
                export_running=lambda: False,
                collect_character=lambda *_: {'objects': [self.rig, self.source], 'warnings': []},
                _target=lambda *_: (Path(target), 'Character.fbx'), _previous=lambda *_: None,
                _capture_hair_motion=lambda *_: [], _owned_keys=lambda *_: {},
                _image_buffers=lambda *_: {},
                bpy=SimpleNamespace(app=SimpleNamespace(binary_path='NotLaunchedBlender'),
                    data=SimpleNamespace(libraries=SimpleNamespace(write=write))),
                subprocess=SimpleNamespace(Popen=launch, STDOUT=-2))
            modules = {'_dress_test.dress_export_snapshot': service,
                       '_dress_test.hair_wiggle_adapter': hair, '_dress_test.unity_forearm': forearm}
            with patch.dict(sys.modules, modules), patch.object(package, 'hair_wiggle_adapter', hair, create=True), \
                    patch.object(package, 'unity_forearm', forearm, create=True):
                if failure:
                    with self.assertRaisesRegex(self.export['ExportError'], 'native Scene proof'):
                        self.export['begin_export'](context, self.rig, config)
                    self.assertEqual(writes, [])
                    self.assertEqual(launches, [])
                    self.assertEqual(config.asset_id, '')
                    self.assertIsNone(self.export['_ACTIVE_JOB'])
                else:
                    job = self.export['begin_export'](context, self.rig, config)
                    try:
                        specification = json.loads((job['root'] / 'job.json').read_text(encoding='utf-8'))
                        self.assertEqual(writes[0][0], {self.rig, self.source, home})
                        self.assertEqual(specification['objects'], [self.rig.name, self.source.name])
                        self.assertEqual(specification['dress_surfaces'], [self.proof])
                        self.assertEqual(len(launches), 1)
                    finally:
                        job['log'].close()
                        job['temporary'].cleanup()
                        self.export['_ACTIVE_JOB'] = None
        self.assertEqual(self.source.native_inputs, original)

    def helper(self, name, role, kind='MESH'):
        metadata = {OWNER: 'dress-owner', SOURCE: self.source}
        if role is not None: metadata[ROLE] = role
        obj = Object(name, kind, **metadata)
        if kind == 'MESH': obj.modifiers = [self.arm]
        obj.hide_viewport = True
        return obj

    def validate(self, source, proof):
        self.calls.append(('validate', source.name))
        if proof.get('graph_digest') != source.native_digest:
            raise ValueError('Native graph identity changed')

    def strip(self, source, proof):
        self.calls.append(('strip', source.name))
        self.validate(source, proof)
        source.modifiers.remove(self.overlay)

    def test_visible_or_hidden_helpers_are_excluded_by_exact_owner_inventory(self):
        self.cloth.hide_viewport = False
        self.source.hide_viewport = True
        scene = SimpleNamespace(objects=Objects([self.rig, self.source, self.cloth,
            self.collider, self.body_collision, self.neutral]))
        scope = self.export['_collection_scope'](SimpleNamespace(scene=scene), self.rig)
        self.assertEqual(scope['eligible'], [self.source])
        self.assertNotIn(self.neutral, scope['rigs'])
        self.assertEqual(self.calls, [])  # collect never invokes surface proof.

    def test_helper_marker_without_exact_role_owner_source_or_membership_rejects(self):
        for field, value in ((ROLE, None), (ROLE, 'FUTURE_ROLE'), (OWNER, 'another'),
                             (SOURCE, self.rig)):
            obj = self.helper('Cloth', 'CLOTH_PROXY'); obj.metadata[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(self.export['ExportError']):
                self.export['_dress_surface_helper'](obj)
        with self.assertRaises(self.export['ExportError']):
            self.export['_dress_surface_helper'](self.helper('Not Registered', 'CLOTH_PROXY'))

    def test_normal_and_legacy_paths_never_load_surface_services(self):
        for marker in (None, {}, {'backend': 'LEGACY_CAGE'}):
            self.record['physics'] = marker; self.save_record()
            self.assertEqual(self.export['_capture_dress_surfaces']([self.rig, self.source]), [])
            self.assertEqual(self.worker['_capture_dress_snapshot']({}, [self.rig, self.source]), [])
        self.assertEqual(self.calls, [])

    def test_unknown_backend_malformed_roles_and_duplicate_inventory_reject(self):
        for backend in ('FUTURE', None, True, {}, 'LEGACY_CAGE'):
            self.record['physics']['backend'] = backend; self.save_record()
            with self.subTest(backend=backend), self.assertRaises(self.export['ExportError']):
                self.export['_capture_dress_surfaces']([self.rig, self.source])
            with self.assertRaises(self.worker['ExportError']):
                self.worker['_capture_dress_snapshot']({}, [self.rig, self.source])
        self.record['physics']['backend'] = BACKEND
        self.record['physics']['surface']['roles']['TRACKER'] = ['Cloth']; self.save_record()
        with self.assertRaises(self.export['ExportError']): self.export['_dress_record'](self.source)
        self.record['physics']['surface']['roles'] = {'FUTURE': ['Cloth']}; self.save_record()
        with self.assertRaises(self.export['ExportError']): self.export['_dress_record'](self.source)

    def test_capture_is_detached_and_requires_original_rig_inventory(self):
        capture = self.export['_capture_dress_surfaces']([self.rig, self.source])
        capture[0]['roles']['CLOTH_PROXY'].append('Wrong')
        self.assertEqual(self.proof['roles']['CLOTH_PROXY'], ['Cloth'])
        with self.assertRaises(self.export['ExportError']):
            self.export['_capture_dress_surfaces']([self.source])
        with self.assertRaises(self.export['ExportError']):
            self.export['_capture_dress_surfaces']([self.rig, self.source, self.collider])

    def test_worker_requires_complete_matching_proof_before_any_strip(self):
        for proof in (None, [], [dict(self.proof, source='Other')],
                      [dict(self.proof, owner='Other')], [dict(self.proof, graph_digest='Changed')]):
            with self.subTest(proof=proof), self.assertRaises(self.worker['ExportError']):
                self.worker['_capture_dress_snapshot']({'dress_surfaces': proof}, [self.rig, self.source])
        self.assertNotIn(('strip', 'Dress'), self.calls)
        self.assertIn(self.overlay, self.source.modifiers)

    def test_worker_rejects_roles_and_old_collider_explicitly_in_FBX_inventory(self):
        job = {'dress_surfaces': [self.proof]}
        for helper in (self.cloth, self.neutral, self.collider):
            with self.subTest(helper=helper.name), self.assertRaises(self.worker['ExportError']):
                self.worker['_capture_dress_snapshot'](job, [self.rig, self.source, helper])
        self.assertNotIn(('strip', 'Dress'), self.calls)

    def test_strip_boundary_removes_only_overlay_and_retains_original_native_inputs(self):
        captured = self.worker['_capture_dress_snapshot']({'dress_surfaces': [self.proof]}, [self.rig, self.source])
        result = self.worker['_strip_dress_snapshot'](captured)
        self.assertEqual(self.source.modifiers, [self.arm, self.generic, self.subsurf])
        self.assertEqual(self.source.native_inputs, self.original)
        self.assertEqual(result, [{'source': 'Dress', 'owner': 'dress-owner', 'backend': BACKEND,
                                 'static_source_rest': True, 'simulation_baked': False}])

    def test_worker_captures_and_strips_before_first_animation_clear(self):
        class ReachedCleanup(Exception): pass
        def clear(obj):
            self.calls.append(('clear', obj.name)); raise ReachedCleanup()
        scene = SimpleNamespace(unit_settings=SimpleNamespace(),
            collection=SimpleNamespace(objects=SimpleNamespace(link=lambda _obj: None)))
        bpy = SimpleNamespace(data=SimpleNamespace(objects=Objects([self.rig, self.source]),
            scenes=SimpleNamespace(new=lambda _name: scene)), context=SimpleNamespace(window=SimpleNamespace()))
        self.worker.update(bpy=bpy, _clear_animation=clear, _disable_hair_simulation=lambda **_kw: None,
            _capture_hair_snapshot=lambda *_: None, _restore_image_buffers=lambda *_: None)
        with tempfile.TemporaryDirectory() as stage:
            job = {'stage': stage, 'filename': 'Character.fbx', 'objects': ['CharacterRig', 'Dress'],
                   'rig': 'CharacterRig', 'dress_surfaces': [self.proof]}
            rejected = {**job, 'dress_surfaces': [dict(self.proof, graph_digest='Changed')]}
            with self.assertRaises(self.worker['ExportError']): self.worker['export_job'](rejected)
            self.assertFalse(any(call[0] in {'clear', 'strip'} for call in self.calls))
            with self.assertRaises(ReachedCleanup): self.worker['export_job'](job)
        self.assertLess(self.calls.index(('validate', 'Dress')), self.calls.index(('clear', 'CharacterRig')))
        self.assertLess(self.calls.index(('strip', 'Dress')), self.calls.index(('clear', 'CharacterRig')))
        self.assertNotIn(self.overlay, self.source.modifiers)

    def test_publication_refuses_missing_or_false_claims_and_accepts_static_boundary(self):
        job = {'dress_surfaces': [self.proof]}
        facts = {'source': 'Dress', 'owner': 'dress-owner', 'backend': BACKEND,
                 'static_source_rest': True, 'simulation_baked': False}
        self.export['_dress_publication'](job, {'dress_surfaces': [facts]})
        for result in ({}, {'dress_surfaces': [dict(facts, simulation_baked=True)]},
                       {'dress_surfaces': [dict(facts, static_source_rest=1)]},
                       {'dress_surfaces': [dict(facts, simulation_baked=0)]}):
            with self.subTest(result=result), self.assertRaises(self.export['ExportError']):
                self.export['_dress_publication'](job, result)
        self.export['_dress_publication']({}, {})


if __name__ == '__main__': unittest.main()
