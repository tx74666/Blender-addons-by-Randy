"""Pure metadata worklist contract checks; no Blender or Unity process."""

import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_workspace.py'
spec = importlib.util.spec_from_file_location('animation_workspace', SOURCE)
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='animation-workspace-contract-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / workspace.MANIFEST_NAME
        self.manifest = {
            'schema': workspace.SCHEMA, 'workspaceId': str(uuid.uuid4()),
            'targetGuid': 'a' * 32, 'targetName': 'Cosha.Player',
            'modelFile': str(self.root / 'Cosha.fbx'), 'modelSha256': 'b' * 64,
            'clips': [
                {'clipGuid': 'c' * 32, 'clipLocalId': 1657602633327794031,
                 'clipName': 'Walk', 'sourceName': 'Locomotion',
                 'sourceAsset': 'Assets/Animation/Walk.fbx',
                 'linkManifest': str(self.root / 'Walk/character_animation_link.json')},
                {'clipGuid': 'd' * 32, 'clipLocalId': -1657602633327794031,
                 'clipName': 'Idle'},
            ],
            'projectId': 'RandomRealm2', 'avatarHash': 'opaque dependency hash',
            'futureMetadata': {'currentClipLocalId': 9223372036854775807, 'values': [True, None, 'keep']},
        }

    def parse(self, value=None):
        return workspace.parse_workspace(json.dumps(self.manifest if value is None else value))

    def test_unknown_fields_and_exact_int64_survive(self):
        self.manifest['clips'][0].update(isCustom=False, currentClipName='Walking',
                                        currentClipLocalId=9223372036854775807)
        actual = self.parse()
        self.assertEqual(actual, self.manifest)
        self.assertIs(type(actual['clips'][0]['clipLocalId']), int)
        self.assertEqual(actual['clips'][0]['clipLocalId'], 1657602633327794031)
        self.assertIs(type(actual['clips'][0]['currentClipLocalId']), int)
        self.assertEqual(actual['futureMetadata'], self.manifest['futureMetadata'])

    def test_int64_boundaries_and_no_float_bool_string_coercion(self):
        for value in (-(2**63), 2**63 - 1, -1, 1):
            with self.subTest(valid=value):
                self.manifest['clips'][0]['clipLocalId'] = value
                self.assertEqual(self.parse()['clips'][0]['clipLocalId'], value)
        for value in (-(2**63) - 1, 2**63, 0, True, False, 1.0, 1e18, '1657602633327794031', None):
            with self.subTest(invalid=value):
                self.manifest['clips'][0]['clipLocalId'] = value
                with self.assertRaisesRegex(workspace.AnimationWorkspaceError, 'exact nonzero signed 64-bit'):
                    self.parse()

    def test_duplicate_slot_identity_is_rejected_case_insensitively(self):
        self.manifest['clips'][1].update(clipGuid='C' * 32,
                                        clipLocalId=self.manifest['clips'][0]['clipLocalId'])
        with self.assertRaisesRegex(workspace.AnimationWorkspaceError, 'duplicate clip identity'):
            self.parse()

    def test_same_asset_multiple_ids_and_same_names_are_distinct_slots(self):
        self.manifest['clips'][1].update(clipGuid='C' * 32, clipName='Walk')
        clips = self.parse()['clips']
        self.assertNotEqual(workspace.clip_identity(clips[0]), workspace.clip_identity(clips[1]))

    def test_two_slots_cannot_share_one_link_manifest(self):
        self.manifest['clips'][0]['linkManifest'] = 'D:/Exchange/Walk/character_animation_link.json'
        self.manifest['clips'][1]['linkManifest'] = 'd:\\exchange\\walk\\character_animation_link.json'
        with self.assertRaisesRegex(workspace.AnimationWorkspaceError, 'share one linkManifest'):
            self.parse()

    def test_missing_links_remain_visible_without_eager_io(self):
        self.manifest['clips'].append({'clipGuid': 'e' * 32, 'clipLocalId': 42,
                                      'clipName': 'New Action', 'linkManifest': ''})
        self.manifest['clips'].append({'clipGuid': 'f' * 32, 'clipLocalId': 43,
                                      'clipName': 'Unprepared', 'linkManifest': None})
        with patch.object(Path, 'open', side_effect=AssertionError('Parser opened a file')), \
                patch.object(Path, 'resolve', side_effect=AssertionError('Parser resolved a path')), \
                patch.object(Path, 'is_file', side_effect=AssertionError('Parser inspected a file')):
            result = self.parse()
            workspace.workspace_identity(result)
            for item in result['clips']:
                workspace.clip_identity(item)
        self.assertEqual(len(result['clips']), 4)
        self.assertTrue(all(workspace.probe_link_status(item) == workspace.NOT_PREPARED
                            for item in result['clips']))

    def test_explicit_link_probe_checks_existence_only_not_validity(self):
        item = self.manifest['clips'][0]
        link = Path(item['linkManifest'])
        link.parent.mkdir()
        link.write_bytes(b'Not even JSON: availability does not mean validation')
        with patch.object(Path, 'open', side_effect=AssertionError('Probe read a Link')), \
                patch.object(Path, 'resolve', side_effect=AssertionError('Probe resolved a path')):
            self.assertEqual(workspace.probe_link_status(item), workspace.LINK_AVAILABLE)
        link.unlink()
        self.assertEqual(workspace.probe_link_status(item), workspace.NOT_PREPARED)
        link.mkdir()
        self.assertEqual(workspace.probe_link_status(item), workspace.NOT_PREPARED)

    def test_snapshot_identity_protects_target_and_model_not_ui_metadata(self):
        original = workspace.workspace_identity(self.manifest)
        changed = copy.deepcopy(self.manifest)
        changed.update(targetName='Renamed', _manifest_path='ignored', avatarHash='new opaque hash')
        changed['clips'].reverse()
        changed['clips'][0]['clipName'] = 'Renamed Clip'
        self.assertEqual(workspace.workspace_identity(changed), original)
        for field, value in (('workspaceId', str(uuid.uuid4())), ('targetGuid', '1' * 32),
                             ('modelFile', str(self.root / 'New.fbx')), ('modelSha256', '1' * 64)):
            with self.subTest(field=field):
                self.assertNotEqual(workspace.workspace_identity({**self.manifest, field: value}), original)
        self.assertEqual(self.manifest, self.parse())

    def test_identity_normalizes_uuid_hex_and_windows_path_without_io(self):
        self.manifest['modelFile'] = 'D:/Exchange/models/../Cosha.fbx'
        other = copy.deepcopy(self.manifest)
        other.update(workspaceId=uuid.UUID(other['workspaceId']).hex.upper(),
                     targetGuid='A' * 32, modelSha256='B' * 64, modelFile='d:\\exchange\\cosha.fbx')
        self.assertEqual(workspace.workspace_identity(self.manifest), workspace.workspace_identity(other))
        other['modelFile'] = r'\\server\share\Cosha.fbx'
        self.assertTrue(workspace.workspace_identity(other))

    def test_invalid_required_header_fields_rejected(self):
        for field, value in (('schema', 'randomrealm.animation-workspace/2'), ('workspaceId', 'bad'),
                             ('workspaceId', True), ('targetGuid', 'a' * 31), ('targetName', ''),
                             ('modelFile', 'relative.fbx'), ('modelFile', 'C:relative.fbx'),
                             ('modelFile', 'D:/bad\x00name.fbx'), ('modelSha256', None),
                             ('modelSha256', 'z' * 64), ('clips', None), ('clips', {})):
            with self.subTest(field=field, value=value):
                with self.assertRaises(workspace.AnimationWorkspaceError):
                    self.parse({**self.manifest, field: value})
        for field in ('workspaceId', 'targetGuid', 'targetName', 'modelFile', 'modelSha256', 'clips'):
            with self.subTest(missing=field):
                value = copy.deepcopy(self.manifest)
                del value[field]
                with self.assertRaises(workspace.AnimationWorkspaceError):
                    self.parse(value)

    def test_empty_worklist_valid_and_invalid_items_rejected(self):
        self.assertEqual(self.parse({**self.manifest, 'clips': []})['clips'], [])
        for item in (None, [], 'Walk', {}, {'clipGuid': 'c' * 32, 'clipLocalId': 1, 'clipName': ''}):
            with self.subTest(item=item):
                with self.assertRaises(workspace.AnimationWorkspaceError):
                    self.parse({**self.manifest, 'clips': [item]})
        for field, value in (('sourceName', 12), ('sourceAsset', []), ('linkManifest', False),
                             ('linkManifest', 'relative.json')):
            with self.subTest(field=field):
                item = {**self.manifest['clips'][0], field: value}
                with self.assertRaises(workspace.AnimationWorkspaceError):
                    self.parse({**self.manifest, 'clips': [item]})

    def test_nonfinite_duplicate_malformed_and_non_utf8_json_rejected(self):
        valid = json.dumps(self.manifest)
        bad_inputs = [b'\xff', '{', '[]', 'null', 123, valid.encode('utf-16'),
                      valid[:-1] + ', "extra": NaN}', valid[:-1] + ', "extra": Infinity}',
                      valid[:-1] + ', "extra": 1e999}',
                      valid[:-1] + ', "schema": "' + workspace.SCHEMA + '"}',
                      valid[:-1] + ', "extra": {"same": 1, "same": 2}}', '[' * 2000 + ']' * 2000]
        for raw in bad_inputs:
            with self.subTest(raw=str(raw)[:50]):
                with self.assertRaises(workspace.AnimationWorkspaceError):
                    workspace.parse_workspace(raw)
        self.assertEqual(workspace.parse_workspace(b'\xef\xbb\xbf' + valid.encode('utf-8')), self.manifest)

    def test_size_bound_counts_utf8_bytes_and_load_read_is_bounded(self):
        raw = json.dumps(self.manifest).encode('utf-8')
        at_limit = raw + b' ' * (workspace.MAX_BYTES - len(raw))
        self.assertEqual(workspace.parse_workspace(at_limit), self.manifest)
        for too_big in (at_limit + b' ', '\u00e9' * (workspace.MAX_BYTES // 2 + 1)):
            with self.assertRaisesRegex(workspace.AnimationWorkspaceError, '1 MiB'):
                workspace.parse_workspace(too_big)
        stream = io.BytesIO(at_limit + b' ' * 4096)
        with patch.object(Path, 'open', return_value=stream), \
                patch.object(stream, 'read', wraps=stream.read) as reader:
            with self.assertRaisesRegex(workspace.AnimationWorkspaceError, '1 MiB'):
                workspace.load_workspace(self.path)
            reader.assert_called_once_with(workspace.MAX_BYTES + 1)

    def test_load_reads_only_index_and_preserves_old_snapshot_on_failure(self):
        self.path.write_text(json.dumps(self.manifest), encoding='utf-8')
        # The model and both clip packets deliberately do not exist.
        opened = []
        original_open = Path.open
        def track(path, *args, **kwargs):
            opened.append(path)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', track):
            loaded = workspace.load_workspace(self.path)
        self.assertEqual(opened, [self.path])
        self.assertEqual(loaded['_manifest_path'], str(self.path))
        saved = copy.deepcopy(loaded)
        self.path.write_text('{incomplete concurrent write', encoding='utf-8')
        with self.assertRaises(workspace.AnimationWorkspaceError):
            workspace.load_workspace(self.path)
        self.assertEqual(loaded, saved)
        self.path.unlink()
        with self.assertRaisesRegex(workspace.AnimationWorkspaceError, 'could not be read'):
            workspace.load_workspace(self.path)
        with self.assertRaisesRegex(workspace.AnimationWorkspaceError, 'Choose'):
            workspace.load_workspace(self.root / 'other.json')


if __name__ == '__main__':
    unittest.main()
