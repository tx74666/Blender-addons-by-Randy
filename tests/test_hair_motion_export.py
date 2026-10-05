"""Pure sidecar contract/transaction tests; no Blender process is launched."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
import uuid


MODULE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer' / 'hair_motion_export.py'
SPEC = importlib.util.spec_from_file_location('hair_motion_export', MODULE)
export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export)


def _rest(index):
    return {'head': [0.0, 0.0, float(index)], 'tail': [0.0, 0.0, float(index + 1)],
            'matrix': [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0],
                       [0.0, 0.0, 1.0, float(index)], [0.0, 0.0, 0.0, 1.0]],
            'parent_index': index - 1, 'connected': bool(index), 'deform': True,
            'inherit_scale': 'FULL', 'inherit_rotation': True, 'local_location': True}


def _fixture():
    source_uid = uuid.UUID('75d28249-6a94-4c96-8a46-a1802fc0f006').hex
    namespace = uuid.UUID(source_uid)
    strands = []
    for order, side in enumerate(('L', 'R')):
        signature = hashlib.sha256(('explicit-topology-' + side).encode()).hexdigest()
        strand = {'strand_id': uuid.uuid5(namespace, 'strand:' + signature).hex,
                  'chain_id': uuid.uuid5(namespace, 'chain:' + signature).hex,
                  'signature': signature, 'order': order,
                  'bones': [f'Hair Author {side}.{index + 1:02d}' for index in range(2)],
                  'rest': [_rest(index) for index in range(2)], 'anchor': None,
                  'vertices': list(range(order * 4, order * 4 + 4)),
                  'layers': [list(range(order * 4, order * 4 + 2)),
                             list(range(order * 4 + 2, order * 4 + 4))],
                  'pair_id': None, 'mirror_id': None, 'side': side, 'pair_proof': 'TOPOLOGY'}
        strands.append(strand)
    pair_id = uuid.uuid5(namespace, 'pair:' + ':'.join(sorted(
        strand['strand_id'] for strand in strands))).hex
    for strand, other in ((strands[0], strands[1]), (strands[1], strands[0])):
        strand['pair_id'] = pair_id
        strand['mirror_id'] = other['strand_id']
        strand['vertex_map'] = [list(pair) for pair in zip(strand['vertices'], other['vertices'])]
    registry = {'version': 1, 'source_uid': source_uid, 'topology': 'a' * 64,
                'axis': 'X', 'strands': strands, 'diagnostics': []}
    settings = {'group': 'SIDE', 'recovery': 0.7, 'damping': 0.65, 'gravity': 4.0,
                'stretch': 0.0, 'sync_mirror': True, 'depth': [
                    {'position': 0.0, 'recovery': 0.9, 'damping': 0.8, 'mass': 1.0, 'gravity': 3.0},
                    {'position': 1.0, 'recovery': 0.3, 'damping': 0.7, 'mass': 1.2, 'gravity': 4.0}]}
    profiles = {strand['strand_id']: copy.deepcopy(settings) for strand in strands}
    mapping = {bone: 'Cosha Export__' + bone for strand in strands for bone in strand['bones']}
    return {'source': types.SimpleNamespace(name='髮 Hair'), 'registry': registry, 'profiles': profiles,
            'bone_mapping': mapping, 'asset_id': uuid.uuid4().hex,
            'fbx_hash': hashlib.sha256(b'FBX fixture').hexdigest(),
            'units': {'source_meters_per_unit': 0.01, 'export_meters_per_unit': 1.0,
                      'fbx_global_scale': 1.0}}


class HairMotionExportTests(unittest.TestCase):
    def setUp(self):
        self.inputs = _fixture()
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.path = self.directory / 'Cosha.hair-motion.json'

    def tearDown(self):
        self.temporary.cleanup()

    def payload(self):
        return export.build_payload(**self.inputs, tool_version='0.76.0')

    def refuses(self, mutate):
        before = copy.deepcopy(self.inputs)
        mutate(self.inputs)
        changed = copy.deepcopy(self.inputs)
        with self.assertRaises(export.HairMotionExportError):
            self.payload()
        self.assertEqual(self.inputs, changed)
        self.inputs = before

    def test_deterministic_order_unicode_explicit_binding_and_no_author_mutation(self):
        before = copy.deepcopy(self.inputs)
        first = self.payload()
        self.inputs['registry']['strands'].reverse()
        self.inputs['profiles'] = dict(reversed(tuple(self.inputs['profiles'].items())))
        self.inputs['bone_mapping'] = dict(reversed(tuple(self.inputs['bone_mapping'].items())))
        self.assertEqual(first, self.payload())
        self.assertEqual(export._bytes(first), export._bytes(self.payload()))
        self.assertEqual(first['source_name'], '髮 Hair')
        self.assertEqual(first['tool']['version'], '0.76.0')
        for strand in first['strands']:
            self.assertEqual(strand['exported_bones'], [before['bone_mapping'][name]
                                                      for name in strand['source_bones']])
            self.assertEqual(strand['source_rest'], before['registry']['strands'][strand['order']]['rest'])
            self.assertNotIn('exported_rest', strand)
        detached = first['strands'][0]
        detached['depth'][0]['mass'] = 9.0
        detached['source_rest'][0]['head'][0] = 20.0
        self.assertEqual(self.inputs['profiles'], before['profiles'])
        self.assertEqual(sorted(self.inputs['registry']['strands'], key=lambda item: item['order']),
                         before['registry']['strands'])

    def test_no_configuration_means_no_sidecar_and_no_registry_read(self):
        reader = mock.Mock()
        for profiles in (None, {}):
            self.inputs['profiles'] = profiles
            self.inputs['registry'] = reader
            self.assertEqual(self.payload(), {})
        reader.read.assert_not_called()
        with self.assertRaises(export.HairMotionExportError):
            export.write_atomic(self.path, {})
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_strict_reader_native_proof_runs_before_binding(self):
        registry = copy.deepcopy(self.inputs['registry'])
        reader = mock.Mock()
        reader.read.return_value = registry
        self.inputs['registry'] = reader
        self.payload()
        reader.read.assert_called_once_with(self.inputs['source'], validate=True)

    def test_dirty_topology_or_native_bones_rejected_by_reader_without_write(self):
        class Reader:
            def read(self, source, *, validate):
                self.asserted = validate
                raise ValueError('Live source topology/Rest changed; explicit reconcile required.')
        reader = Reader()
        self.inputs['registry'] = reader
        with self.assertRaisesRegex(ValueError, 'topology/Rest changed'):
            self.payload()
        self.assertTrue(reader.asserted)
        self.assertFalse(self.path.exists())

    def test_invalid_digest_or_incomplete_unit_contract(self):
        for key, value in (('fbx_hash', 'short'), ('fbx_hash', 'g' * 64)):
            self.refuses(lambda data, k=key, v=value: data.update({k: v}))
        for value in (0.0, -0.01, True, float('inf'), float('nan')):
            self.inputs['units']['source_meters_per_unit'] = value
            with self.assertRaises(export.HairMotionExportError):
                self.payload()
        self.inputs = _fixture()
        self.refuses(lambda data: data['registry'].update(topology='a' * 63))
        self.refuses(lambda data: data['units'].pop('export_meters_per_unit'))

    def test_missing_mapping_and_duplicate_destination_even_for_unused_bones(self):
        bone = next(iter(self.inputs['bone_mapping']))
        destination = self.inputs['bone_mapping'][bone]
        self.refuses(lambda data: data['bone_mapping'].pop(bone))
        self.refuses(lambda data: data['bone_mapping'].update(UnusedBone=destination))
        self.refuses(lambda data: data['bone_mapping'].update({bone: {'name': destination, 'nearest': True}}))

    def test_duplicate_id_order_and_native_bone_ownership(self):
        self.refuses(lambda data: data['registry']['strands'].append(
            copy.deepcopy(data['registry']['strands'][0])))
        self.refuses(lambda data: data['registry']['strands'][1].update(order=0))
        self.refuses(lambda data: data['registry']['strands'][1]['bones'].__setitem__(
            0, data['registry']['strands'][0]['bones'][0]))
        self.refuses(lambda data: data['registry']['strands'][0].update(strand_id=uuid.uuid4().hex))

    def test_strict_reciprocal_pairs_and_exact_topology_layers(self):
        self.refuses(lambda data: data['registry']['strands'][1].update(mirror_id=None))
        self.refuses(lambda data: data['registry']['strands'][1].update(pair_id=uuid.uuid4().hex))
        self.refuses(lambda data: data['registry']['strands'][1].update(side='C'))
        self.refuses(lambda data: data['registry']['strands'][0]['vertex_map'].pop())
        self.refuses(lambda data: data['registry']['strands'][0]['vertex_map'][0].__setitem__(1, 5))
        self.refuses(lambda data: data['registry']['strands'][1]['layers'][0].append(6))

    def test_geometry_and_graph_proof_roundtrip_preserves_exact_maps_and_author_data(self):
        for proof in ('GEOMETRY', 'GRAPH'):
            with self.subTest(proof=proof):
                self.inputs = _fixture()
                first, second = self.inputs['registry']['strands']
                first.update(pair_proof=proof, boundary_map=[[8, 9]])
                second.update(pair_proof=proof, boundary_map=[[9, 8]])
                before = copy.deepcopy(self.inputs)
                payload = self.payload()
                export.write_atomic(self.path, payload)
                loaded = export.verify_read(self.path, payload)
                for saved, strand in zip(loaded['strands'], self.inputs['registry']['strands']):
                    self.assertEqual(saved['pair_proof'], proof)
                    self.assertEqual(saved['vertex_map'], strand['vertex_map'])
                    self.assertEqual(saved['boundary_map'], strand['boundary_map'])
                    self.assertEqual(set(dict(saved['vertex_map'])), set(saved['vertices']))
                self.assertEqual(self.inputs, before)
                corrupt = copy.deepcopy(payload)
                corrupt['strands'][0]['vertex_map'].pop()
                self.path.write_text(json.dumps(corrupt), encoding='utf-8')
                with self.assertRaises(export.HairMotionExportError):
                    export.verify_read(self.path)
                corrupt = copy.deepcopy(payload)
                corrupt['strands'][1]['boundary_map'] = [[9, 10]]
                self.path.write_text(json.dumps(corrupt), encoding='utf-8')
                with self.assertRaisesRegex(export.HairMotionExportError, 'Boundary proof'):
                    export.verify_read(self.path)

    def test_new_mapped_proofs_support_center_self_involutions_without_double_processing(self):
        for proof in ('GEOMETRY', 'GRAPH'):
            with self.subTest(proof=proof):
                self.inputs = _fixture()
                strand = self.inputs['registry']['strands'][0]
                self.inputs['registry']['strands'] = [strand]
                self.inputs['profiles'] = {strand['strand_id']: self.inputs['profiles'][strand['strand_id']]}
                own = strand['strand_id']
                namespace = uuid.UUID(self.inputs['registry']['source_uid'])
                strand.update(pair_id=uuid.uuid5(namespace, 'pair:' + own + ':' + own).hex,
                    mirror_id=own, side='C', pair_proof=proof,
                    vertex_map=[[0, 1], [1, 0], [2, 3], [3, 2]], boundary_map=[[8, 9], [9, 8]])
                payload = self.payload()
                self.assertEqual(len(payload['strands']), 1)
                saved = payload['strands'][0]
                self.assertEqual(saved['mirror_id'], own)
                self.assertEqual(saved['vertex_map'], strand['vertex_map'])
                export.write_atomic(self.path, payload)
                self.assertEqual(export.verify_read(self.path)['strands'][0], saved)
                self.refuses(lambda data: data['registry']['strands'][0]['vertex_map'][0].__setitem__(1, 0))
                self.refuses(lambda data: data['registry']['strands'][0]['boundary_map'][0].__setitem__(1, 8))

    def test_new_mapped_proofs_refuse_incomplete_ambiguous_cross_layer_or_nonreciprocal_maps(self):
        for proof in ('GEOMETRY', 'GRAPH'):
            with self.subTest(proof=proof):
                self.inputs = _fixture()
                for strand in self.inputs['registry']['strands']:
                    strand.update(pair_proof=proof, boundary_map=[])
                self.refuses(lambda data: data['registry']['strands'][0].pop('vertex_map'))
                self.refuses(lambda data: data['registry']['strands'][0]['vertex_map'].pop())
                self.refuses(lambda data: data['registry']['strands'][0]['vertex_map'][0].__setitem__(1, 5))
                self.refuses(lambda data: data['registry']['strands'][0]['vertex_map'][0].__setitem__(1, 100))
                self.refuses(lambda data: data['registry']['strands'][1]['vertex_map'][0].__setitem__(1, 1))
                self.refuses(lambda data: data['registry']['strands'][0].pop('boundary_map'))
                self.refuses(lambda data: data['registry']['strands'][0].update(boundary_map=[[True, 8]]))
                self.refuses(lambda data: data['registry']['strands'][0].update(boundary_map=[[8, 9], [8, 10]]))
                # This full reciprocal permutation still violates ordered layers.
                first, second = self.inputs['registry']['strands']
                first['vertex_map'] = [[0, 6], [1, 7], [2, 4], [3, 5]]
                second['vertex_map'] = [[6, 0], [7, 1], [4, 2], [5, 3]]
                with self.assertRaisesRegex(export.HairMotionExportError, 'source layer'):
                    self.payload()

    def test_mirror_provenance_does_not_invent_a_spatial_match(self):
        strands = self.inputs['registry']['strands']
        for strand in strands:
            strand['pair_proof'] = 'MIRROR'
        with self.assertRaisesRegex(export.HairMotionExportError, 'identical source layers'):
            self.payload()
        strands[1]['vertices'] = copy.deepcopy(strands[0]['vertices'])
        strands[1]['layers'] = copy.deepcopy(strands[0]['layers'])
        self.assertEqual(len(self.payload()['strands']), 2)

    def test_unpaired_and_center_strands_retain_explicit_identity(self):
        strand = self.inputs['registry']['strands'][0]
        self.inputs['registry']['strands'] = [strand]
        self.inputs['profiles'] = {strand['strand_id']: self.inputs['profiles'][strand['strand_id']]}
        strand.update(pair_id=None, mirror_id=None, side='U', pair_proof='NONE')
        self.assertIsNone(self.payload()['strands'][0]['mirror_id'])
        namespace = uuid.UUID(self.inputs['registry']['source_uid'])
        own = strand['strand_id']
        strand.update(pair_id=uuid.uuid5(namespace, 'pair:' + own + ':' + own).hex,
                      mirror_id=own, side='C', pair_proof='TOPOLOGY',
                      vertex_map=[[index, index] for index in strand['vertices']])
        self.assertEqual(self.payload()['strands'][0]['side'], 'C')

    def test_sync_pair_conflicts_fail_but_explicit_independent_sides_survive(self):
        left, right = [strand['strand_id'] for strand in self.inputs['registry']['strands']]
        self.inputs['profiles'][right]['recovery'] = 0.2
        with self.assertRaisesRegex(export.HairMotionExportError, 'Synchronized mirror'):
            self.payload()
        self.inputs['profiles'][right]['sync_mirror'] = False
        with self.assertRaises(export.HairMotionExportError):
            self.payload()
        self.inputs['profiles'][left]['sync_mirror'] = False
        result = self.payload()
        self.assertEqual([item['effective']['recovery'] for item in result['strands']], [0.7, 0.2])
        self.assertNotEqual(result['strands'][0]['exported_bones'], result['strands'][1]['exported_bones'])
        self.assertEqual(result['application']['component_layout'], 'independent_per_strand')

    def test_no_defaults_or_native_baseline_overwrite_and_no_simulation_bake_claim(self):
        result = self.payload()
        self.assertEqual(result['application'], {
            'baseline_policy': 'inherit_existing_native', 'require_explicit_apply': True,
            'component_layout': 'independent_per_strand', 'conversion_validation': 'pending'})
        self.assertFalse(result['simulation_baked'])
        self.assertEqual(result['conversion'], {'wiggle': 'wiggle-1.1.2-v1', 'magica': 'magica2-motion-v1'})
        self.assertEqual(result['parameter_contract']['gravity']['unit'], 'meters_per_second_squared')
        self.assertEqual(result['parameter_contract']['depth']['mass'], 'relative_mass')
        self.assertEqual(result['parameter_contract']['depth']['interpolation'], 'piecewise_linear')
        self.refuses(lambda data: data['profiles'].pop(next(iter(data['profiles']))))

    def test_runtime_tool_version_uses_loaded_package_without_importing_blender(self):
        name = 'character_designer_contract_fixture'
        package = types.SimpleNamespace(bl_info={'version': (0, 76, 0)})
        with mock.patch.object(export, '__package__', name), mock.patch.dict(sys.modules, {name: package}):
            result = export.build_payload(**self.inputs)
        self.assertEqual(result['tool'], {'name': 'Character Designer', 'version': '0.76.0',
                                         'exporter_version': '1.0.0'})

    def test_depth_coverage_ranges_and_unknown_fields_are_not_silently_repaired(self):
        key = next(iter(self.inputs['profiles']))
        self.refuses(lambda data: data['profiles'][key]['depth'][0].update(position=0.2))
        self.refuses(lambda data: data['profiles'][key]['depth'][-1].update(position=0.0))
        self.refuses(lambda data: data['profiles'][key]['depth'][0].update(stretch=0.1))
        self.refuses(lambda data: data['profiles'][key]['depth'][0].update(mass=0.0))
        self.refuses(lambda data: data['profiles'][key].update(damping=1.1))
        self.refuses(lambda data: data['profiles'][key].update(sync_mirror='yes'))
        self.refuses(lambda data: data['profiles'][key].update(native_stiffness=0.9))

    def test_opaque_native_overrides_are_detached_and_never_flattened_as_semantics(self):
        raw = {'magica': {'stiffness': {'value': 0.42, 'curve': [1.0, 0.1]}}, 'wiggle': {'damping': 3.0}}
        for profile in self.inputs['profiles'].values():
            profile['backend_overrides'] = copy.deepcopy(raw)
            profile['raw_config'] = {'author_note': 'baseline remains native', 'enabled': False}
        result = self.payload()
        strand = result['strands'][0]
        self.assertEqual(strand['backend_overrides'], raw)
        self.assertEqual(strand['effective']['damping'], 0.65)
        strand['backend_overrides']['magica']['stiffness']['curve'][0] = 0.0
        self.assertEqual(next(iter(self.inputs['profiles'].values()))['backend_overrides'], raw)
        key = next(iter(self.inputs['profiles']))
        self.refuses(lambda data: data['profiles'][key]['backend_overrides'].update(bad=Path('not-json')))

    def test_source_rest_and_optional_actual_exported_rest_must_be_complete(self):
        self.refuses(lambda data: data['registry']['strands'][0]['rest'][1].update(parent_index=-1))
        self.refuses(lambda data: data['registry']['strands'][0]['rest'][0].update(tail=[0.0, 0.0, 0.0]))
        self.refuses(lambda data: data['registry']['strands'][0]['rest'][0]['matrix'][3].__setitem__(3, 2.0))
        strand = self.inputs['registry']['strands'][0]
        for index, name in enumerate(strand['bones']):
            self.inputs['bone_mapping'][name] = {'name': self.inputs['bone_mapping'][name], 'rest': _rest(index)}
        self.assertEqual(self.payload()['strands'][0]['exported_rest'], strand['rest'])
        self.inputs['bone_mapping'][strand['bones'][1]].pop('rest')
        with self.assertRaisesRegex(export.HairMotionExportError, 'complete ordered strand'):
            self.payload()

    def test_atomic_roundtrip_actual_fbx_digest_and_deterministic_file(self):
        result = self.payload()
        fbx = self.directory / 'Cosha.fbx'
        fbx.write_bytes(b'FBX fixture')
        self.assertEqual(export.write_atomic(self.path, result), self.path)
        first = self.path.read_bytes()
        self.assertEqual(export.verify_read(self.path, result, fbx_path=fbx), result)
        export.write_atomic(self.path, result)
        self.assertEqual(self.path.read_bytes(), first)
        self.assertEqual(set(self.directory.iterdir()), {self.path, fbx})
        fbx.write_bytes(b'changed FBX')
        with self.assertRaisesRegex(export.HairMotionExportError, 'actual FBX'):
            export.verify_read(self.path, fbx_path=fbx)

    def test_invalid_payload_and_failed_replace_preserve_existing_file(self):
        result = self.payload()
        export.write_atomic(self.path, result)
        original = self.path.read_bytes()
        bad = copy.deepcopy(result)
        bad['strands'][1]['exported_bones'][0] = bad['strands'][0]['exported_bones'][0]
        with self.assertRaises(export.HairMotionExportError):
            export.write_atomic(self.path, bad)
        self.assertEqual(self.path.read_bytes(), original)
        with mock.patch.object(export.os, 'replace', side_effect=OSError('fixture denied replace')):
            with self.assertRaisesRegex(export.HairMotionExportError, 'atomically'):
                export.write_atomic(self.path, result)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_reader_rejects_json_duplicates_wrong_identity_and_application(self):
        result = self.payload()
        export.write_atomic(self.path, result)
        original = self.path.read_text(encoding='utf-8')
        self.path.write_text(original.replace('"schema_version": 1,', '"schema_version": 1, "schema_version": 1,'),
                             encoding='utf-8')
        with self.assertRaisesRegex(export.HairMotionExportError, 'duplicate JSON'):
            export.verify_read(self.path)
        for mutate in (
                lambda data: data.update(simulation_baked=True),
                lambda data: data['application'].update(require_explicit_apply=False),
                lambda data: data['application'].update(baseline_policy='overwrite_defaults'),
                lambda data: data['strands'][0].update(mirror_id=uuid.uuid4().hex),
                lambda data: data['strands'][0].update(exported_rest=[]),
                lambda data: data['coordinates'].update(source_rest_unit='meters')):
            bad = copy.deepcopy(result)
            mutate(bad)
            self.path.write_text(json.dumps(bad), encoding='utf-8')
            with self.assertRaises(export.HairMotionExportError):
                export.verify_read(self.path)

    def test_verify_expected_payload_rejects_silent_changes(self):
        result = self.payload()
        export.write_atomic(self.path, result)
        changed = copy.deepcopy(result)
        changed['asset_id'] = 'different-existing-asset'
        with self.assertRaisesRegex(export.HairMotionExportError, 'expected payload'):
            export.verify_read(self.path, changed)


if __name__ == '__main__':
    unittest.main()
