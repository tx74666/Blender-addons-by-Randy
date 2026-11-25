"""Pure production Rig admission/publication tests in tiny tempfile projects.

Native reports, FBX bytes and Unity assets here are synthetic fixtures. Passing
these tests grants no real capability, import, physics or artist acceptance.
"""
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '_pure_unity_rig_update_tests'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / 'addons/character_designer')]
sys.modules[PACKAGE] = package
subject = importlib.import_module(PACKAGE + '.unity_rig_update')
base, hair = subject.base, subject.hair


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else (json.dumps(value) + '\n').encode('utf8'))


def inventory(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}


class RigUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='production-rig-pure-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'UnityProject'
        self.formal = self.project / 'Assets/Character'
        self.stage = self.root / 'worker'
        self.model = self.formal / 'Character.fbx'
        self.manifest = self.model.with_suffix('.cdesigner.json')
        self.asset, self.rig, self.revision, self.target = (value * 32 for value in ('a', 'b', 'c', 'd'))
        self.uid = '331afdb7e5154ae1aaa39603a4f61768'
        self.parts = [{'part_id': format(index, '032x'), 'renderer_path': name, 'object_name': name,
                       'material_slots': ['material-' + name]} for index, name in
                      enumerate(('Clothes', 'Cosha', 'Dress', 'Hair', 'Shoes', 'Stocking'), 1)]
        self.hair_id = next(part['part_id'] for part in self.parts if part['object_name'] == 'Hair')
        self.old = self.bundle(self.formal, [4] * 32, b'OLD-FBX-FIXTURE' * 6)
        self.new = self.bundle(self.stage, [8] * 14 + [3] * 10 + [4] * 8, b'NEW-FBX-FIXTURE' * 6)
        self.new_manifest = self.stage / 'Character.cdesigner.json'
        write(self.model.with_name('Character.fbx.meta'), b'guid: eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee\n')
        self.ledger = self.project / '.codex/character-update-state' / (self.asset + '.json')
        self.state = {'schema': base.RECEIVER_SCHEMA, 'asset_id': self.asset, 'rig_id': self.rig,
            'target_guid': self.target, 'receiver_revision': self.revision,
            'model_asset': 'Assets/Character/Character.fbx', 'model_sha256': digest(self.model),
            'manifest_sha256': digest(self.manifest), 'parts': [
                {key: part[key] for key in ('part_id', 'renderer_path', 'material_slots')} for part in self.parts]}
        write(self.ledger, self.state)
        self.player = self.project / 'Assets/Player.prefab'
        self.profile = self.project / 'Assets/Physics.asset'
        write(self.player, b'SYNTHETIC-TARGET-PLAYER')
        write(Path(str(self.player) + '.meta'), ('guid: ' + self.target + '\n').encode())
        write(self.profile, b'SYNTHETIC-PHYSICS-PROFILE')
        write(Path(str(self.profile) + '.meta'), b'guid: ffffffffffffffffffffffffffffffff\n')
        write(self.project / 'ProjectSettings/ProjectVersion.txt', b'm_EditorVersion: 6000.5.9f1\n')
        self.cap_path = self.project / '.codex/character-update-receiver.json'
        self.report_path = self.project / '.codex/native-proof/rig.json'
        self.definition = {'schema': 'cdesigner.rig-source-definition/2', 'main_rig': 'Rig',
            'rig_id': self.rig, 'unit_scale': 1., 'source_uid': self.uid, 'dependencies': {},
            'objects': {'Rig': {}, **{part['object_name']: {'weights_sha256': '7' * 64} for part in self.parts}}}
        self.source_path = self.root / 'sealed/Character.rig-source.json'
        self.write_capability()

    def sidecar(self, counts, model_hash):
        strands, profiles, mapping = [], {}, {}
        for order, count in enumerate(counts):
            signature = hashlib.sha256(('owned-chain-' + str(order)).encode()).hexdigest()
            namespace = uuid.UUID(self.uid)
            identity = uuid.uuid5(namespace, 'strand:' + signature).hex
            bones = ['Hair_%02d.%03d' % (order, index) for index in range(count)]
            rest = [{'head': [float(order), 0., float(index)], 'tail': [float(order), 0., float(index + 1)],
                'matrix': [[1., 0., 0., float(order)], [0., 1., 0., 0.], [0., 0., 1., float(index)], [0., 0., 0., 1.]],
                'parent_index': index - 1, 'connected': bool(index), 'deform': True,
                'inherit_scale': 'FULL', 'inherit_rotation': True, 'local_location': True} for index in range(count)]
            strands.append({'strand_id': identity, 'chain_id': uuid.uuid5(namespace, 'chain:' + signature).hex,
                'signature': signature, 'order': order, 'bones': bones, 'rest': rest,
                'vertices': list(range(order * 4, order * 4 + 4)),
                'layers': [[order * 4, order * 4 + 1], [order * 4 + 2, order * 4 + 3]],
                'pair_id': None, 'mirror_id': None, 'side': 'U', 'pair_proof': 'NONE'})
            profiles[identity] = {'group': 'BACK', 'recovery': .7, 'damping': .65, 'gravity': 4.,
                'stretch': 0., 'sync_mirror': False, 'depth': [
                    {'position': 0., 'recovery': .9, 'damping': .8, 'mass': 1., 'gravity': 3.},
                    {'position': 1., 'recovery': .3, 'damping': .7, 'mass': 1.2, 'gravity': 4.}]}
            mapping.update({name: name for name in bones})
        registry = {'version': 1, 'axis': 'X', 'source_uid': self.uid, 'topology': 'f' * 64,
                    'strands': strands, 'diagnostics': []}
        return hair.build_payload({'name': 'Hair'}, registry, profiles, mapping, self.asset,
            model_hash, {'source_meters_per_unit': 1., 'export_meters_per_unit': 1.}, tool_version='pure-fixture')

    def bundle(self, folder, counts, model_bytes):
        write(folder / 'Character.fbx', model_bytes)
        model_hash = digest(folder / 'Character.fbx')
        payload = self.sidecar(counts, model_hash)
        write(folder / 'Character.hair-motion.json', payload)
        write(folder / 'Character.forearm.json', {'schema': 'cdesigner.forearm/1', 'fbx': 'Character.fbx',
            'fbxSha256': model_hash, 'meshes': [{'objectName': 'Cosha', 'sources': [{'vertex': 0}]}]})
        rest = []
        for name, parent in (('Hips', None), ('Head', 'Hips'), ('thigh.L', 'Hips'), ('thigh.R', 'Hips'),
                             ('shin.L', 'thigh.L'), ('shin.R', 'thigh.R'), ('foot.L', 'shin.L')):
            rest.append({'name': name, 'parent': parent, 'head': [0., 0., float(len(rest))],
                'axes': [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]], 'length': 1., 'terminal': False, 'restore': False})
        for strand in payload['strands']:
            for index, name in enumerate(strand['exported_bones']):
                rest.append({'name': name, 'parent': strand['exported_bones'][index - 1] if index else 'Head',
                    'head': strand['source_rest'][index]['head'], 'axes': [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]],
                    'length': 1., 'terminal': index == len(strand['exported_bones']) - 1,
                    'restore': index == len(strand['exported_bones']) - 1})
        files = {name: digest(folder / name) for name in ('Character.fbx', 'Character.hair-motion.json', 'Character.forearm.json')}
        record = {'schema': base.BASE_SCHEMA, 'ok': True, 'asset_id': self.asset, 'source_rig': 'Rig',
            'filename': 'Character.fbx', 'files': files, 'objects': ['Rig'] + [part['object_name'] for part in self.parts],
            'meshes': {part['object_name']: {'skinned': True} for part in self.parts},
            'rigs': {'Rig': [bone['name'] for bone in rest]},
            'bone_rest_lengths': {'schema': 'cdesigner.bone-rest-lengths/1', 'space': 'export-world-meters',
                'fbx_sha256': model_hash, 'rig_name': 'Rig', 'bones': rest},
            'hair_motion': {'active': True, 'file': 'Character.hair-motion.json', 'simulation_baked': False},
            'forearm_correction': {'file': 'Character.forearm.json'}}
        write(folder / 'Character.cdesigner.json', record)
        return record

    def write_capability(self, review_path=None, **proof_changes):
        sources = {}
        for relative in subject.PROOF_SOURCE_PATHS:
            name = relative.rsplit('/', 1)[-1]
            write(self.project / relative, ('// Synthetic native source ' + name).encode())
            sources[relative] = digest(self.project / relative)
        source = {'schema': 'cdesigner.rig-source/1', 'asset_id': self.asset, 'rig_id': self.rig,
            'candidate_fbx_sha256': digest(self.stage / 'Character.fbx'),
            'candidate_manifest_sha256': digest(self.new_manifest), 'definition': self.definition,
            'definition_sha256': hashlib.sha256(json.dumps(self.definition, sort_keys=True,
                ensure_ascii=False, separators=(',', ':')).encode('utf8')).hexdigest()}
        write(self.source_path, source)
        proof = {'schema': subject.PROOF_SCHEMA, 'passed': True, 'unityVersion': '6000.5.9f1',
            **{flag: True for flag in subject.PROOF_FLAGS}, 'hair_structure_verified_sources': sources,
            'hair_structure_source_inventory_schema': subject.SOURCE_INVENTORY_SCHEMA,
            'hair_structure_source_inventory_sha256': subject.SOURCE_INVENTORY_SHA256,
            'asset_id': self.asset, 'rig_id': self.rig, 'target_guid': self.target,
            'model_asset': 'Assets/Character/Character.fbx',
            'project_root': str(self.project), 'source_uid': self.uid, 'model_sha256': digest(self.model),
            'manifest_sha256': digest(self.manifest), 'receiver_revision': self.revision,
            'candidate_fbx_sha256': digest(self.stage / 'Character.fbx'),
            'candidate_manifest_sha256': digest(self.new_manifest), 'ledger_sha256': digest(self.ledger),
            'target_player_asset': 'Assets/Player.prefab', 'target_player_sha256': digest(self.player),
            'physics_profile_asset': 'Assets/Physics.asset', 'physics_profile_sha256': digest(self.profile),
            'physics_profile_guid': 'f' * 32,
            'reviewed_bundle_manifest': str(self.new_manifest), 'source_snapshot_file': str(self.source_path),
            'source_snapshot_sha256': digest(self.source_path)}
        if review_path is not None:
            review = json.loads(review_path.read_bytes())
            proof.update({flag: True for flag in subject.REST_PROOF_FLAGS})
            proof.update(reviewed_rest_manifest_sha256=digest(review_path), reviewed_rest_receiver_revision=self.revision,
                reviewed_rest_names=sorted(subject.REVIEWED_NAMES), reviewed_diff_sha256=review['reviewed_diff_sha256'],
                reviewed_rest_file=str(review_path))
        proof.update(proof_changes)
        write(self.report_path, proof)
        cap = {'schema': base.RECEIVER_SCHEMA, 'candidate_schema': base.SCHEMA,
            'project_root': str(self.project), 'inbox': str(self.project / '.codex/character-update-inbox'),
            'accepted_state_root': str(self.project / '.codex/character-update-state'),
            'supported': ['mesh_same_skeleton'], 'requires_review': True,
            'supports_rig': False, 'supports_full_reconcile': False, 'supports_forearm_mesh': True,
            'supports_hair_structure': True, 'supports_current_rig_reviewed_rest': review_path is not None,
            'hair_structure_verification_report': str(self.report_path),
            'hair_structure_verification_report_sha256': digest(self.report_path),
            'hair_structure_verified_sources': sources, 'hair_structure_native_verified_utc': '2026-11-11T00:00:00Z',
            'hair_structure_source_inventory_schema': subject.SOURCE_INVENTORY_SCHEMA,
            'hair_structure_source_inventory_sha256': subject.SOURCE_INVENTORY_SHA256,
            'reviewed_bundle_manifest': proof['reviewed_bundle_manifest'], 'source_snapshot_file': proof['source_snapshot_file'],
            'source_snapshot_sha256': proof['source_snapshot_sha256']}
        if review_path is not None:
            cap['reviewed_rest_file'] = str(review_path)
        cap.update({key: proof[key] for key in subject.AUTHORITY_FIELDS})
        write(self.cap_path, cap)
        return proof, cap

    def capture(self, review_path=None, bundle=True, **changes):
        snapshot = base.capture_base(self.model, self.manifest, asset_id=self.asset, rig_id=self.rig, parts=self.parts)
        options = dict(hair_source_name='Hair', hair_source_uid=self.uid, requested_mesh_ids=[self.hair_id],
            review_file=review_path, bundle_manifest=self.new_manifest if bundle else None,
            source_snapshot=copy.deepcopy(self.definition))
        options.update(changes)
        return subject.capture_plan(snapshot, **options)

    def publish(self, plan, **changes):
        options = dict(stage=self.stage, model_file='Character.fbx', files=[*self.new['files'], self.new_manifest.name],
                       parts=self.parts, update_id='9' * 32)
        options.update(changes)
        return subject.publish_candidate(plan, **options)

    def completed(self):
        return list((self.project / '.codex/character-update-inbox').rglob('candidate.json'))

    def review(self, foot_delta=0.):
        old = {bone['name']: bone for bone in self.old['bone_rest_lengths']['bones']}
        new = {bone['name']: bone for bone in self.new['bone_rest_lengths']['bones']}
        for name in subject.REVIEWED_NAMES:
            new[name]['head'][0] += .01
        new['foot.L']['axes'][0][1] += foot_delta
        write(self.new_manifest, self.new)
        old_evidence, new_evidence = self.root / 'sealed/old.json', self.root / 'sealed/new.json'
        write(old_evidence, self.manifest.read_bytes()); write(new_evidence, self.new_manifest.read_bytes())
        residuals = [] if not foot_delta else [{'name': 'foot.L', 'before': old['foot.L'], 'after': new['foot.L'],
            'head_max_delta_meters': 0., 'axes_max_component_delta_dimensionless': foot_delta,
            'length_delta_meters': 0., 'classification': 'nonzero_raw_summary_drift_within_explicit_combined_budget_not_exactly_unchanged'}]
        review = {'schema': subject.REVIEW_SCHEMA, 'receiver_revision': self.revision, 'source_asset_id': self.asset,
            'baseline_fbx_sha256': digest(self.model), 'candidate_fbx_sha256': digest(self.stage / 'Character.fbx'),
            'baseline_manifest_sha256': digest(self.manifest), 'candidate_manifest_sha256': digest(self.new_manifest),
            'baseline_manifest_file': str(old_evidence), 'candidate_manifest_file': str(new_evidence),
            'reviewed_diff_sha256': '8' * 64, 'world_summary_budgets': dict(subject.WORLD_SUMMARY_BUDGETS),
            'world_summary_residuals': residuals,
            'rows': [{'name': name, 'before': old[name], 'after': new[name]} for name in sorted(subject.REVIEWED_NAMES)]}
        path = self.root / 'review/Character.reviewed-rest.json'
        write(path, review)
        self.write_capability(path)
        return path

    def test_capture_and_display_are_read_only_and_plan_revalidates(self):
        before = inventory(self.project)
        plan = self.capture()
        subject.revalidate_plan(plan)
        display = subject.read_capabilities(self.model, bundle_manifest=self.new_manifest)
        self.assertIs(display['supports_hair_structure'], True)
        self.assertIs(display['supports_rig'], False)
        self.assertEqual(inventory(self.project), before)
        self.assertEqual(plan.hair_part_id, self.hair_id)

    def test_publish_is_marker_last_and_preserves_formal_target_and_ledger(self):
        plan = self.capture()
        before = inventory(self.formal), self.ledger.read_bytes(), self.player.read_bytes(), self.profile.read_bytes()
        renamed, observations = base._rename_noreplace, []
        def inspect(source, destination):
            if destination.name == 'candidate.json':
                self.assertFalse(destination.exists())
                for name in (*self.new['files'], self.new_manifest.name):
                    self.assertEqual((destination.parent / name).read_bytes(), (self.stage / name).read_bytes())
                observations.append(True)
            return renamed(source, destination)
        with mock.patch.object(base, '_rename_noreplace', side_effect=inspect):
            path = self.publish(plan)
        record = json.loads(path.read_bytes())
        self.assertEqual(observations, [True])
        self.assertEqual(record['rig_update']['requested_mesh_ids'], [self.hair_id])
        self.assertEqual(len(record['rig_update']['dependency_mesh_ids']), 5)
        self.assertEqual(record['selection']['mesh_ids'], [part['part_id'] for part in self.parts])
        self.assertEqual(record['rig_update']['source_snapshot'], {'schema': 'cdesigner.rig-source/1',
            'file': 'Character.rig-source.json', 'sha256': digest(self.source_path)})
        self.assertEqual((inventory(self.formal), self.ledger.read_bytes(), self.player.read_bytes(), self.profile.read_bytes()), before)

    def test_missing_false_or_truthy_dedicated_capability_refuses_without_inbox(self):
        for value in (None, False, 'true', 1):
            with self.subTest(value=value):
                _proof, cap = self.write_capability()
                if value is None: cap.pop('supports_hair_structure')
                else: cap['supports_hair_structure'] = value
                write(self.cap_path, cap)
                with self.assertRaises(ValueError): self.capture()
                self.assertFalse(self.completed())

    def test_every_native_flag_missing_or_false_refuses(self):
        for flag in subject.PROOF_FLAGS:
            for value in (None, False):
                with self.subTest(flag=flag, value=value):
                    proof, cap = self.write_capability()
                    if value is None: proof.pop(flag)
                    else: proof[flag] = value
                    write(self.report_path, proof)
                    cap['hair_structure_verification_report_sha256'] = digest(self.report_path)
                    write(self.cap_path, cap)
                    with self.assertRaises(ValueError): self.capture()

    def test_report_binding_target_source_model_revision_and_candidate_are_exact(self):
        for field in ('asset_id', 'rig_id', 'target_guid', 'source_uid', 'project_root', 'model_sha256',
                      'manifest_sha256', 'receiver_revision', 'ledger_sha256', 'candidate_fbx_sha256', 'candidate_manifest_sha256'):
            with self.subTest(field=field):
                self.write_capability(**{field: '0' * (64 if 'sha256' in field else 32)})
                with self.assertRaises(ValueError): self.capture()

    def test_report_source_version_target_profile_or_guid_drift_refuses(self):
        plan = self.capture()
        for path in (self.report_path, self.player, self.profile, Path(str(self.player) + '.meta'),
                     self.project / 'ProjectSettings/ProjectVersion.txt'):
            with self.subTest(path=path):
                original = path.read_bytes(); path.write_bytes(original + b'changed')
                with self.assertRaises(ValueError): subject.revalidate_plan(plan)
                path.write_bytes(original)
        relative = next(iter(json.loads(self.report_path.read_bytes())['hair_structure_verified_sources']))
        with (self.project / relative).open('ab') as stream: stream.write(b'changed')
        with self.assertRaises(ValueError): self.publish(plan)
        self.assertFalse(self.completed())

    def test_announcement_must_repeat_the_exact_native_context_and_inventory_manifest(self):
        proof, cap = self.write_capability()
        for key in (*subject.AUTHORITY_FIELDS, 'hair_structure_source_inventory_schema',
                    'hair_structure_source_inventory_sha256'):
            with self.subTest(key=key):
                changed = dict(cap); changed.pop(key)
                write(self.cap_path, changed)
                with self.assertRaises(ValueError): self.capture()
        write(self.cap_path, cap)
        wrong_model = 'Assets/Other/Character.fbx'
        self.write_capability(model_asset=wrong_model)
        with self.assertRaises(ValueError): self.capture()
        self.assertFalse(self.completed())

    def test_profile_guid_and_exact_required_source_paths_are_bound(self):
        proof, cap = self.write_capability()
        write(Path(str(self.profile) + '.meta'), b'guid: eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee\n')
        with self.assertRaisesRegex(ValueError, 'GUID'): self.capture()
        write(Path(str(self.profile) + '.meta'), b'guid: ffffffffffffffffffffffffffffffff\n')
        sources = dict(proof['hair_structure_verified_sources'])
        old = next(iter(subject.PROOF_SOURCE_PATHS)); moved = 'Assets/Moved/' + Path(old).name
        write(self.project / moved, (self.project / old).read_bytes())
        sources[moved] = sources.pop(old)
        proof['hair_structure_verified_sources'] = cap['hair_structure_verified_sources'] = sources
        write(self.report_path, proof); cap['hair_structure_verification_report_sha256'] = digest(self.report_path)
        write(self.cap_path, cap)
        with self.assertRaisesRegex(ValueError, 'source inventory'): self.capture()
        self.assertFalse(self.completed())

    def test_wrong_or_empty_requested_meshes_and_native_identity_refuse(self):
        for requested in ([], [self.parts[0]['part_id']], [self.hair_id, self.parts[0]['part_id']]):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                self.capture(requested_mesh_ids=requested)
        for key, value in (('hair_source_name', 'OtherHair'), ('hair_source_uid', 'f' * 32)):
            with self.subTest(key=key), self.assertRaises(ValueError): self.capture(**{key: value})

    def test_missing_ledger_and_changed_ledger_refuse(self):
        plan = self.capture(); original = self.ledger.read_bytes()
        self.ledger.unlink()
        with self.assertRaises(ValueError): subject.revalidate_plan(plan)
        write(self.ledger, original + b' ')
        with self.assertRaises(ValueError): self.publish(plan)
        self.assertFalse(self.completed())

    def test_fresh_worker_bytes_cannot_reuse_other_candidate_native_proof(self):
        plan = self.capture(bundle=False)
        (self.stage / 'Character.fbx').write_bytes(b'ANOTHER-FRESH-WORKER-FBX' * 6)
        with self.assertRaises(ValueError): self.publish(plan)
        self.assertFalse(self.completed())

    def test_changed_part_name_or_incomplete_file_context_refuses(self):
        plan = self.capture()
        parts = copy.deepcopy(self.parts); parts[0]['object_name'] = 'Renamed'
        with self.assertRaises(ValueError): self.publish(plan, parts=parts)
        with self.assertRaises(ValueError): self.publish(plan, files=list(self.new['files']))
        self.assertFalse(self.completed())

    def test_payload_copy_failure_cleans_only_owned_candidate(self):
        plan = self.capture(); before = inventory(self.project)
        copied = base._copy_file
        def fail_inbox(pin, destination):
            if destination.is_relative_to(self.project):
                raise OSError('Injected copy failure')
            return copied(pin, destination)
        with mock.patch.object(base, '_copy_file', side_effect=fail_inbox):
            with self.assertRaises(OSError): self.publish(plan)
        self.assertEqual(inventory(self.project), before)
        self.assertFalse(self.completed())

    def test_ledger_race_before_marker_preserves_new_ledger_and_no_candidate(self):
        plan = self.capture(); copied, observed = base._copy_file, []
        def race(pin, destination):
            result = copied(pin, destination)
            if not observed and destination.is_relative_to(self.project):
                state = dict(self.state, receiver_revision='f' * 32); write(self.ledger, state); observed.append(True)
            return result
        with mock.patch.object(base, '_copy_file', side_effect=race):
            with self.assertRaises(ValueError): self.publish(plan)
        self.assertEqual(observed, [True]); self.assertFalse(self.completed())
        self.assertEqual(json.loads(self.ledger.read_bytes())['receiver_revision'], 'f' * 32)

    def test_combined_review_is_copied_and_owned_without_modifying_complete_manifest(self):
        review = self.review(foot_delta=.00001)
        manifest_bytes = self.new_manifest.read_bytes()
        plan = self.capture(review)
        path = self.publish(plan)
        record = json.loads(path.read_bytes())
        self.assertEqual((path.parent / review.name).read_bytes(), review.read_bytes())
        self.assertEqual(record['files'][review.name], digest(review))
        self.assertNotIn(review.name, record['rig_update']['required_sidecars'])
        self.assertEqual(self.new_manifest.read_bytes(), manifest_bytes)

    def test_review_rows_budget_and_permission_native_flags_remain_exact(self):
        review = self.review()
        for flag in subject.REST_PROOF_FLAGS:
            with self.subTest(flag=flag):
                self.write_capability(review, **{flag: False})
                with self.assertRaises(ValueError): self.capture(review)
        self.write_capability(review)
        plan = self.capture(review)
        data = json.loads(review.read_bytes()); data['rows'][0]['after']['head'][0] += .1
        write(review, data)
        with self.assertRaises(ValueError): self.publish(plan)
        self.assertFalse(self.completed())

    def test_hair_only_keeps_one_micro_nonhair_rest_gate(self):
        review = self.review(foot_delta=.00001)
        self.write_capability()
        plan = self.capture()
        with self.assertRaisesRegex(ValueError, 'Non-Hair Rest changed'): self.publish(plan)
        self.assertFalse(self.completed())

    def test_combined_large_unapproved_residual_refuses_even_with_synthetic_native_claim(self):
        review = self.review(foot_delta=.000021)
        plan = self.capture(review)
        with self.assertRaises(ValueError): self.publish(plan)
        self.assertFalse(self.completed())

    def test_current_native_definition_is_required_and_must_match_byte_bound_snapshot(self):
        with self.assertRaises(ValueError): self.capture(source_snapshot=None)
        changed = copy.deepcopy(self.definition)
        changed['objects']['Hair']['weights_sha256'] = '6' * 64
        with self.assertRaises(ValueError): self.capture(source_snapshot=changed)
        plan = self.capture()
        with self.assertRaises(ValueError): subject.revalidate_plan(plan, source_snapshot=changed)
        self.assertFalse(self.completed())

    def test_source_snapshot_file_drift_refuses_before_publication(self):
        plan = self.capture()
        self.source_path.write_bytes(self.source_path.read_bytes() + b' ')
        with self.assertRaises(ValueError): self.publish(plan)
        self.assertFalse(self.completed())

    def test_internal_reviewed_bundle_default_and_explicit_override_cannot_redirect(self):
        plan = self.capture(bundle=False)
        self.assertEqual(plan.bundle_manifest, self.new_manifest)
        alternate = self.root / 'alternate/Character.cdesigner.json'
        write(alternate, self.new_manifest.read_bytes())
        with self.assertRaises(ValueError): self.capture(bundle_manifest=alternate)
        self.assertFalse(self.completed())

    def test_required_receiver_source_omission_is_not_replaced_by_an_unrelated_source(self):
        for omitted in subject.PROOF_SOURCE_PATHS:
            with self.subTest(omitted=omitted):
                proof, cap = self.write_capability()
                sources = dict(proof['hair_structure_verified_sources'])
                sources.pop(omitted)
                extra = self.project / 'Assets/Scripts/Editor/Unrelated.cs'
                write(extra, b'// Synthetic unrelated source')
                sources['Assets/Scripts/Editor/Unrelated.cs'] = digest(extra)
                self.assertEqual(len(sources), len(subject.PROOF_SOURCE_NAMES))
                proof['hair_structure_verified_sources'] = cap['hair_structure_verified_sources'] = sources
                write(self.report_path, proof)
                cap['hair_structure_verification_report_sha256'] = digest(self.report_path)
                write(self.cap_path, cap)
                with self.assertRaises(ValueError): self.capture()
        self.assertFalse(self.completed())

    def test_latest_accepted_body_forearm_assets_metas_and_publication_payloads_are_pinned(self):
        revision = '4' * 32
        mesh = self.project / ('Assets/GeneratedParts/' + revision + '.asset')
        forearm = self.project / ('Assets/GeneratedForearms/' + revision + '.asset')
        write(mesh, b'LATEST-ACCEPTED-BODY-WEIGHTS')
        write(forearm, b'LATEST-ACCEPTED-FOREARM-CALIBRATION')
        write(Path(str(mesh) + '.meta'), b'guid: 11111111111111111111111111111111\n')
        write(Path(str(forearm) + '.meta'), b'guid: 22222222222222222222222222222222\n')
        body = next(item for item in self.state['parts'] if item['part_id'] == self.parts[1]['part_id'])
        body.update(mesh_asset=mesh.relative_to(self.project).as_posix(), mesh_sha256=digest(mesh),
                    forearm_asset=forearm.relative_to(self.project).as_posix(), forearm_sha256=digest(forearm))
        publication = self.project / '.codex/character-update-inbox' / self.asset / revision
        payload = publication / 'context.fbx'
        write(payload, b'ACCEPTED-BODY-PUBLICATION-CONTEXT' * 3)
        marker = publication / 'candidate.json'
        write(marker, {'schema': base.SCHEMA, 'ok': True, 'asset_id': self.asset, 'rig_id': self.rig,
            'update_id': revision, 'selection': {'mesh_ids': [body['part_id']]},
            'files': {'context.fbx': digest(payload)}})
        write(self.ledger, self.state)
        self.write_capability()
        plan = self.capture()
        for path in (mesh, forearm, Path(str(mesh) + '.meta'), Path(str(forearm) + '.meta'), marker, payload):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b'CHANGED')
                with self.assertRaises(ValueError): subject.revalidate_plan(plan)
                path.write_bytes(original)
        self.assertEqual(self.completed(), [marker])

    def test_combined_review_rejects_foreign_report_before_reading_it(self):
        review = self.review()
        foreign = self.root / 'foreign-native-proof.json'
        write(foreign, self.report_path.read_bytes())
        cap = json.loads(self.cap_path.read_bytes())
        cap['hair_structure_verification_report'] = str(foreign)
        write(self.cap_path, cap)
        read = base._read_json
        def reject_foreign_read(path):
            if Path(path) == foreign:
                raise AssertionError('The foreign native report was read before admission')
            return read(path)
        with mock.patch.object(base, '_read_json', side_effect=reject_foreign_read):
            with self.assertRaises(ValueError): self.capture(review)
        self.assertFalse(self.completed())


if __name__ == '__main__':
    unittest.main(verbosity=2)
