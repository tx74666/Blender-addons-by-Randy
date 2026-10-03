"""Worklist ownership and saved Action lifecycle in disposable Blender only.

Run with --background --factory-startup. Only the first model-import boundary
is replaced: it builds the same native fixture in a new scene and runs the real
packet baker. Library-backed Sources, Custom copies, worklist transitions and
saved .blend associations use production code. No worker or Unity is launched.
"""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import animation_worklist as worklist
from character_designer import animation_link as link, animation_link_source as source
from character_designer import animation, animation_export, animation_retarget, unity_animation as ua
import test_unity_animation_blender as native


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def action_content(action):
    """Independent motion fingerprint, insensitive to Action display names."""
    return tuple(sorted((curve.data_path, curve.array_index, curve.mute, curve.extrapolation,
                         tuple((tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
                                point.interpolation, point.handle_left_type, point.handle_right_type)
                               for point in curve.keyframe_points))
                        for curve in animation_retarget._curves(action)))


def nla_content(rig):
    return tuple((track.name, track.mute, track.is_solo,
                  tuple((strip.name, strip.action.name, strip.frame_start, strip.frame_end,
                         strip.blend_type, strip.influence, strip.mute)
                        for strip in track.strips)) for track in rig.animation_data.nla_tracks)


def model_rig():
    rig = native.make_rig()
    bpy.ops.object.mode_set(mode='EDIT')
    hand = rig.data.edit_bones['hand.L']
    hand.head = hand.parent.tail
    hand.use_connect = True
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.update()
    return rig


class WorklistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()
        character_designer._validate_registration_integrity()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cd-animation-worklist-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name).resolve()
        self.original_scene = bpy.data.scenes.new('Untouched Authoring')
        bpy.context.window.scene = self.original_scene
        native.reset()
        self.original = model_rig()
        self.original_name = self.original.name
        self.model = self.folder / 'Character.fbx'
        self.model.write_bytes(b'Model import is the explicit fixture boundary, never an FBX worker.')
        self.links, self.packets = [], []
        clips = []
        for index, name in enumerate(('Walk', 'Turn')):
            directory = self.folder / name
            directory.mkdir()
            packet_path, _, packet = native.package(self.original, directory, name + '.cdanim.json',
                                                     stretch_bones=('hand.L',))
            packet.update(targetGuid='a' * 32, clipGuid=('b' if index == 0 else 'c') * 32,
                          clipLocalId=1657602633327794031 + index, clipName=name,
                          loopTime=index == 0)
            # The real Link parser requires one explicit unweighted object root.
            # Mapping verifies native bind positions and skips this wrapper.
            for bone in packet['bones']:
                bone['parent'] += 1
            packet['bones'].insert(0, {'name': 'CoshaRig', 'path': 'CoshaRig', 'parent': -1,
                'restSource': 'hierarchy', 'weighted': False, 'rest': native.flat(Matrix.Identity(4))})
            for frame in packet['frames']:
                frame['poses'].insert(0, {'matrix': native.flat(Matrix.Identity(4))})
                if index:
                    # A second real motion, not merely another name for Walk.
                    for pose in frame['poses'][1:]:
                        pose['matrix'][3] += .08 * frame['time']
            packet_path.write_text(json.dumps(packet), encoding='utf-8')
            manifest = {'schema': link.SCHEMA, 'linkId': str(uuid.uuid4()),
                'targetGuid': packet['targetGuid'], 'clipGuid': packet['clipGuid'],
                'clipLocalId': packet['clipLocalId'], 'sourcePackage': str(packet_path),
                'sourcePackageSha256': digest(packet_path), 'modelFile': str(self.model),
                'modelSha256': digest(self.model), 'revision': 0}
            manifest_path = directory / link.MANIFEST_NAME
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            self.links.append(manifest_path)
            self.packets.append(packet_path)
            clips.append({'clipGuid': packet['clipGuid'], 'clipLocalId': packet['clipLocalId'],
                          'clipName': name, 'sourceName': 'Fixture Controller',
                          'linkManifest': str(manifest_path)})
        self.workspace = self.folder / 'character_animation_workspace.json'
        self.workspace.write_text(json.dumps({'schema': 'randomrealm.animation-workspace/1',
            'workspaceId': str(uuid.uuid4()), 'targetGuid': 'a' * 32, 'targetName': 'Cosha',
            'modelFile': str(self.model), 'modelSha256': digest(self.model), 'clips': clips}), encoding='utf-8')

        self.original.pose.bones['Hips'].location = (.12, -.03, .01)
        self.original.pose.bones['Hips'].keyframe_insert('location', frame=1)
        self.original_action = self.original.animation_data.action
        strip_action = self.original_action.copy()
        strip_action.name = 'Artist NLA'
        track = self.original.animation_data.nla_tracks.new()
        track.strips.new('Keep Artist Strip', 1, strip_action)
        self.original.animation_data.action_blend_type = 'ADD'
        self.original.animation_data.action_influence = .4
        native.ua._set_frame(self.original_scene, 77.375)
        bpy.context.view_layer.update()
        self.original_snapshot = self.original_state()
        self.packet_hashes = [digest(path) for path in self.packets]
        self.model_imports = 0
        importer = patch.object(source, '_import_prepared_source', side_effect=self.import_fixture)
        importer.start()
        self.addCleanup(importer.stop)
        process = patch.object(animation_export.subprocess, 'Popen',
                               side_effect=AssertionError('A worklist test launched a worker'))
        process.start()
        self.addCleanup(process.stop)
        user_resource = bpy.utils.user_resource
        cache = patch.object(bpy.utils, 'user_resource', side_effect=lambda kind, **options:
            str(self.folder / 'source-cache') if kind == 'DATAFILES' and
            options.get('path') == 'character_designer/animation_sources'
            else user_resource(kind, **options))
        cache.start()
        self.addCleanup(cache.stop)
        worklist.connect(bpy.context, str(self.workspace))

    def original_state(self):
        context = SimpleNamespace(scene=self.original_scene, screen=None)
        return (ua._snapshot(context, self.original), native.rest_hash(self.original),
                self.original.animation_data.action.name, action_content(self.original.animation_data.action),
                nla_content(self.original))

    def import_fixture(self, context, manifest, packet, export_name, model_file, model_hash, *, start_frame=1):
        self.model_imports += 1
        manifest_path = manifest['_manifest_path']
        self.assertEqual(model_hash, digest(self.model))
        self.assertEqual(Path(model_file), self.model)
        scene = bpy.data.scenes.new('Worklist Model Fixture')
        scene.render.fps, scene.render.fps_base = 24, 1.001
        scene.unit_settings.scale_length = 1.0
        context.window.scene = scene
        rig = model_rig()
        imported = ua._import_package_action(context, rig, packet, start_frame=start_frame)
        link.bind_action(rig, imported.action, manifest_path, export_name, str(self.model))
        rig['character_designer_animation_source_model_sha256'] = digest(self.model)
        return source.SourceResult(rig, imported.action, imported.first_frame, imported.last_frame,
            scene, scene.collection, str(self.model), digest(self.model), export_name)

    def add(self, index=0):
        catalog = worklist.state(bpy.context).catalog
        return worklist.add(bpy.context, catalog[index].clip_key)

    def item(self, item_id):
        return next(item for item in worklist.state(bpy.context).items if item.item_id == item_id)

    def active_state(self):
        state = worklist.state(bpy.context)
        rig = state.rig
        return (tuple(item.item_id for item in state.items), state.active_index,
                rig.animation_data.action if rig and rig.animation_data else None,
                ua._snapshot(bpy.context, rig) if rig else None,
                tuple((item.item_id, item.source_action, item.custom_action,
                       action_content(item.source_action), action_content(item.custom_action))
                      for item in state.items))

    def assert_original_unchanged(self):
        self.assertEqual(self.original_state(), self.original_snapshot)
        self.assertEqual([digest(path) for path in self.packets], self.packet_hashes)

    def test_two_clips_share_one_rig_and_keep_independent_sources_and_customs(self):
        with patch.object(ua, 'load_package', wraps=ua.load_package) as load:
            first = self.add(0)
        self.assertEqual(load.call_count, 1, 'First Add parsed a duplicate live packet')
        first_id, first_custom, first_source = first.item_id, first.custom_action, first.source_action
        baseline = action_content(first_source)
        with patch.object(ua, 'load_package', wraps=ua.load_package) as load:
            second = self.add(1)
        self.assertEqual(load.call_count, 1)
        self.assertEqual(self.model_imports, 1)
        self.assertIs(first.rig, second.rig)
        self.assertIsNot(first.rig, self.original)
        self.assertTrue(self.original.data.bones['hand.L'].use_connect)
        self.assertFalse(any(bone.use_connect for bone in first.rig.data.bones))
        self.assertEqual(native.rest_hash(first.rig), native.rest_hash(self.original))
        self.assertEqual(len({first_source.as_pointer(), first_custom.as_pointer(),
                              second.source_action.as_pointer(), second.custom_action.as_pointer()}), 4)
        for item in (first, second):
            self.assertIsNotNone(item.source_action.library)
            self.assertFalse(item.source_action.is_editable)
            self.assertIsNone(item.custom_action.library)
            self.assertEqual(action_content(item.source_action), action_content(item.custom_action))
            with bpy.data.libraries.load(item.source_file, link=True) as (available, _selected):
                self.assertEqual(available.objects, [])
                self.assertEqual(available.scenes, [])
                self.assertEqual(available.armatures, [])
                self.assertEqual(len(available.actions), 1)
        second_content = action_content(second.custom_action)
        worklist.activate(bpy.context, first_id, side='CUSTOM')
        curve = next(curve for curve in animation_retarget._curves(first_custom)
                     if 'forearm.L' in curve.data_path and 'rotation' in curve.data_path)
        curve.keyframe_points[-1].co.y += .3
        curve.update()
        self.assertNotEqual(action_content(first_custom), baseline)
        self.assertEqual(action_content(first_source), baseline)
        self.assertEqual(action_content(second.custom_action), second_content)
        self.assert_original_unchanged()

    def test_source_selection_blocks_sync_and_custom_sync_keeps_exact_identity(self):
        item = self.add()
        worklist.activate(bpy.context, item.item_id, side='SOURCE')
        before = self.active_state()
        with patch.object(link, 'begin_linked_export') as begin:
            with self.assertRaises(ValueError):
                worklist.sync(bpy.context)
            begin.assert_not_called()
        self.assertEqual(self.active_state(), before)
        worklist.activate(bpy.context, item.item_id, side='CUSTOM')
        job = {'fixture_job': True}
        with patch.object(link, 'begin_linked_export', return_value=job) as begin, \
                patch.object(bpy.app.timers, 'register') as timer, \
                patch.object(animation, '_export_window_manager', None):
            self.assertIs(worklist.sync(bpy.context), job)
            self.assertEqual(begin.call_count, 1)
            self.assertIs(begin.call_args.args[1], item.rig)
            timer.assert_called_once()
        association = link.get_link(item.rig)
        self.assertEqual(association['identity']['clipLocalId'], 1657602633327794031)
        self.assertIs(item.rig[link.ACTION_KEY], item.custom_action)

    def test_action_editor_context_follows_source_and_custom_instead_of_unrelated_object(self):
        item = self.add()
        other = bpy.data.objects.new('Unrelated animated object', None)
        bpy.context.scene.collection.objects.link(other)
        for frame, value in ((1, 0.), (25, .4)):
            other.location.x = value
            other.keyframe_insert('location', frame=frame)
        other_action = other.animation_data.action
        other_slot = other.animation_data.action_slot.handle
        other_motion = action_content(other_action)
        area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
        old_type, old_ui_type = area.type, area.ui_type
        selected, active = tuple(bpy.context.selected_objects), bpy.context.view_layer.objects.active
        try:
            area.type = 'DOPESHEET_EDITOR'
            area.spaces.active.mode = 'ACTION'
            region = next(region for region in area.regions if region.type == 'WINDOW')
            for obj in bpy.context.selected_objects:
                obj.select_set(False)
            other.select_set(True)
            bpy.context.view_layer.objects.active = other
            with bpy.context.temp_override(area=area, region=region):
                self.assertIs(bpy.context.active_action, other_action)
                for side, action, slot in (('SOURCE', item.source_action, item.source_slot),
                                           ('CUSTOM', item.custom_action, item.custom_slot)):
                    worklist.activate(bpy.context, item.item_id, side=side)
                    self.assertIs(bpy.context.object, item.rig)
                    self.assertIs(bpy.context.active_action, action)
                    self.assertIs(item.rig.animation_data.action, action)
                    self.assertEqual(item.rig.animation_data.action_slot.handle, slot)
                    self.assertIs(other.animation_data.action, other_action)
                    self.assertEqual(other.animation_data.action_slot.handle, other_slot)
                    self.assertEqual(action_content(other_action), other_motion)
        finally:
            area.type = old_type
            area.ui_type = old_ui_type
            for obj in bpy.context.selected_objects:
                obj.select_set(False)
            for obj in selected:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = active
        self.assertEqual(area.type, old_type)
        self.assert_original_unchanged()

    def test_reorder_and_removal_change_membership_without_deleting_actions(self):
        first, second = self.add(0), self.add(1)
        first_id, second_id = first.item_id, second.item_id
        retained = {action.as_pointer() for item in (first, second)
                    for action in (item.source_action, item.custom_action)}
        worklist.move(bpy.context, second_id, -1)
        self.assertEqual([item.item_id for item in worklist.state(bpy.context).items], [second_id, first_id])
        worklist.activate(bpy.context, first_id, side='CUSTOM')
        self.assertEqual(link.get_link(worklist.state(bpy.context).rig)['identity']['clipLocalId'],
                         1657602633327794031)
        worklist.remove(bpy.context, second_id)
        self.assertEqual([item.item_id for item in worklist.state(bpy.context).items], [first_id])
        self.assertTrue(retained <= {action.as_pointer() for action in bpy.data.actions})

    def test_existing_owned_single_link_reuses_edited_custom_without_reimport(self):
        result = source.import_source(bpy.context, self.links[0], str(self.model))
        edited = result.action
        curve = next(curve for curve in animation_retarget._curves(edited)
                     if 'forearm.L' in curve.data_path and 'rotation' in curve.data_path)
        curve.keyframe_points[-1].co.y += .25
        curve.update()
        edited_motion = action_content(edited)
        legacy_link = link.get_link(result.rig)
        worklist.connect(bpy.context, str(self.workspace))
        item = self.add()
        self.assertEqual(self.model_imports, 1, 'Worklist imported another model instead of adopting the owned Link rig')
        self.assertIs(item.rig, result.rig)
        self.assertIs(item.custom_action, edited)
        self.assertEqual(action_content(item.custom_action), edited_motion)
        self.assertNotEqual(action_content(item.source_action), edited_motion)
        self.assertEqual(link.get_link(item.rig)['identity'], legacy_link['identity'])
        self.assertIs(item.rig[link.ACTION_KEY], edited)
        self.assert_original_unchanged()

    def test_reprepared_legacy_clip_is_rejected_without_changing_the_existing_preview(self):
        result = source.import_source(bpy.context, self.links[0], str(self.model))
        rig, edited = result.rig, result.action
        curve = next(curve for curve in animation_retarget._curves(edited)
                     if 'forearm.L' in curve.data_path and 'rotation' in curve.data_path)
        curve.keyframe_points[-1].co.y += .25
        curve.update()
        worklist.connect(bpy.context, str(self.workspace))
        before = (ua._snapshot(bpy.context, rig), action_content(edited), dict(edited.items()),
                  dict(rig.items()), rig.data, rig.data.use_fake_user)
        ids = set(bpy.data.user_map())
        packet_bytes, manifest_bytes = self.packets[0].read_bytes(), self.links[0].read_bytes()
        packet = json.loads(packet_bytes)
        packet['frames'][-1]['poses'][4]['matrix'][3] += .1
        self.packets[0].write_text(json.dumps(packet), encoding='utf-8')
        manifest = json.loads(manifest_bytes)
        manifest['sourcePackageSha256'] = digest(self.packets[0])
        self.links[0].write_text(json.dumps(manifest), encoding='utf-8')
        try:
            with self.assertRaisesRegex(ValueError, 'rebuilt|revision'):
                self.add()
            self.assertFalse(worklist.state(bpy.context).items)
            self.assertIsNone(worklist.state(bpy.context).rig)
            self.assertEqual(self.model_imports, 1)
            self.assertEqual((ua._snapshot(bpy.context, rig), action_content(edited), dict(edited.items()),
                              dict(rig.items()), rig.data, rig.data.use_fake_user), before)
            self.assertIs(ua.active_preview(rig), edited)
            self.assertEqual(set(bpy.data.user_map()), ids)
        finally:
            self.packets[0].write_bytes(packet_bytes)
            self.links[0].write_bytes(manifest_bytes)
        self.assert_original_unchanged()

    def test_inactive_legacy_custom_properties_and_fake_user_roll_back_on_source_write_failure(self):
        result = source.import_source(bpy.context, self.links[0], str(self.model))
        rig, edited = result.rig, result.action
        ua.restore_preview(bpy.context, rig)
        edited['artist_note'] = 'Keep this inactive edited Action and its recovery properties'
        edited.use_fake_user = False
        other = edited.copy()
        other.name, other.use_fake_user = 'Artist selected a different Action', True
        rig.animation_data_create().action = other
        rig.animation_data.action_slot = other.slots[0]
        ua._set_frame(bpy.context.scene, 8.375)
        bpy.context.view_layer.update()
        worklist.connect(bpy.context, str(self.workspace))
        self.assertIn(ua.SESSION_KEY, edited)
        before = (ua._snapshot(bpy.context, rig), action_content(edited), dict(edited.items()),
                  edited.use_fake_user, dict(other.items()), dict(rig.items()),
                  rig.data, rig.data.use_fake_user)
        ids = set(bpy.data.user_map())

        def fail_after_cleaning(*_args):
            self.assertNotIn(ua.SESSION_KEY, edited)
            self.assertTrue(edited.use_fake_user)
            raise RuntimeError('Injected inactive legacy Source write failure')

        with patch.object(worklist, '_linked_baseline', side_effect=fail_after_cleaning):
            with self.assertRaisesRegex(RuntimeError, 'Injected inactive legacy'):
                self.add()
        self.assertEqual((ua._snapshot(bpy.context, rig), action_content(edited), dict(edited.items()),
                          edited.use_fake_user, dict(other.items()), dict(rig.items()),
                          rig.data, rig.data.use_fake_user), before)
        self.assertIs(rig.animation_data.action, other)
        self.assertIs(rig[link.ACTION_KEY], edited)
        self.assertIsNone(ua.active_preview(rig))
        self.assertFalse(worklist.state(bpy.context).items)
        self.assertIsNone(worklist.state(bpy.context).rig)
        self.assertEqual(set(bpy.data.user_map()), ids)
        self.assertEqual(self.model_imports, 1)
        self.assert_original_unchanged()

    def test_source_remains_viewable_when_its_custom_action_was_deleted(self):
        item = self.add()
        original = item.source_action
        source_content, source_hash = action_content(original), item.source_hash
        bpy.data.actions.remove(item.custom_action, do_unlink=True)
        self.assertIsNone(item.custom_action)
        self.assertIs(worklist.activate(bpy.context, item.item_id, side='SOURCE'), original)
        self.assertIs(item.rig.animation_data.action, original)
        self.assertEqual(item.rig.animation_data.action_slot.handle, item.source_slot)
        self.assertEqual(item.side, 'SOURCE')
        snapshot = ua._snapshot(bpy.context, item.rig)
        with patch.object(link, 'begin_linked_export') as begin:
            with self.assertRaises(ValueError):
                worklist.sync(bpy.context)
            begin.assert_not_called()
        with self.assertRaises(ValueError):
            worklist.activate(bpy.context, item.item_id, side='CUSTOM')
        self.assertEqual(ua._snapshot(bpy.context, item.rig), snapshot)
        self.assertIs(item.rig.animation_data.action, original)
        self.assertEqual(action_content(original), source_content)
        self.assertEqual(item.source_hash, source_hash)
        self.assertEqual(digest(item.source_file), source_hash)
        self.assert_original_unchanged()

    def test_failed_source_publication_restores_existing_action_nla_and_preview_state(self):
        item = self.add()
        rig = item.rig
        nla_action = item.custom_action.copy()
        nla_action.name = 'Keep worklist NLA'
        track = rig.animation_data.nla_tracks.new()
        track.strips.new('Keep worklist strip', 1, nla_action)
        rig.animation_data.use_nla = True
        rig.animation_data.action_blend_type = 'ADD'
        rig.animation_data.action_influence = .3
        ua._set_frame(bpy.context.scene, 8.375)
        bpy.context.view_layer.update()
        before = self.active_state()
        tracks = nla_content(rig)
        data, fake_user = rig.data, rig.data.use_fake_user
        actions = {action.as_pointer() for action in bpy.data.actions}
        association = rig[link.LINK_KEY]
        cache_root = (self.folder / 'source-cache').resolve()
        cached_before = set(cache_root.rglob('*.blend'))
        written = []
        hash_file = link._sha256

        def fail_baseline_hash(path):
            candidate = Path(path).resolve()
            if candidate.suffix == '.blend' and candidate.is_relative_to(cache_root) and candidate not in cached_before:
                written.append(candidate)
                self.assertTrue(candidate.is_file())
                raise RuntimeError('Injected Source write failure')
            return hash_file(path)

        with patch.object(link, '_sha256', side_effect=fail_baseline_hash):
            with self.assertRaisesRegex(RuntimeError, 'Injected'):
                self.add(1)
        self.assertEqual(self.active_state(), before)
        self.assertEqual(nla_content(rig), tracks)
        self.assertIs(rig.data, data)
        self.assertEqual(rig.data.use_fake_user, fake_user)
        self.assertEqual({action.as_pointer() for action in bpy.data.actions}, actions)
        self.assertEqual(rig[link.LINK_KEY], association)
        self.assertIsNone(ua.active_preview(rig))
        self.assertEqual(len(written), 1)
        self.assertFalse(written[0].exists())
        self.assertEqual(set(cache_root.rglob('*.blend')), cached_before)
        self.assert_original_unchanged()

    def test_action_switch_during_failed_bake_cannot_abort_snapshot_rollback(self):
        item = self.add()
        rig = item.rig
        unrelated = item.custom_action.copy()
        unrelated.name, unrelated.use_fake_user = 'Preexisting unrelated Action', True
        unrelated['keep_this'] = 'unrelated animation survives the failed bake'
        track = rig.animation_data.nla_tracks.new()
        track.strips.new('Preexisting NLA', 1, unrelated)
        rig.animation_data.use_nla = True
        rig.animation_data.action_blend_type = 'ADD'
        rig.animation_data.action_influence = .3
        ua._set_frame(bpy.context.scene, 8.375)
        bpy.context.view_layer.update()
        before = self.active_state()
        data, fake_user = rig.data, rig.data.use_fake_user
        action_properties, rig_properties = dict(item.custom_action.items()), dict(rig.items())
        unrelated_motion, unrelated_properties = action_content(unrelated), dict(unrelated.items())
        tracks, ids = nla_content(rig), set(bpy.data.user_map())
        import_action = source._import_action

        def switch_after_bake(context, target, packet, start_frame):
            result = import_action(context, target, packet, start_frame)
            self.assertIs(ua.active_preview(target), result.action)
            target.animation_data.action = unrelated
            target.animation_data.action_slot = unrelated.slots[0]
            raise RuntimeError('Injected Action switch after native bake')

        with patch.object(source, '_import_action', side_effect=switch_after_bake):
            with self.assertRaisesRegex(RuntimeError, 'Injected Action switch'):
                self.add(1)
        self.assertEqual(self.active_state(), before)
        self.assertIs(rig.data, data)
        self.assertEqual(rig.data.use_fake_user, fake_user)
        self.assertEqual(dict(item.custom_action.items()), action_properties)
        self.assertEqual(dict(rig.items()), rig_properties)
        self.assertEqual(action_content(unrelated), unrelated_motion)
        self.assertEqual(dict(unrelated.items()), unrelated_properties)
        self.assertEqual(nla_content(rig), tracks)
        self.assertEqual(set(bpy.data.user_map()), ids)
        self.assertIsNone(ua.active_preview(rig))
        self.assert_original_unchanged()

    def test_first_add_failure_after_activation_restores_authoring_animation_target(self):
        settings = bpy.context.window_manager.character_designer_animation
        settings.target = self.original
        scene, active = bpy.context.scene, bpy.context.view_layer.objects.active
        selected = tuple(bpy.context.selected_objects)
        ids = set(bpy.data.user_map())
        cache_root = self.folder / 'source-cache'
        cached_before = set(cache_root.rglob('*.blend'))
        newly_written = []
        activate = worklist.activate

        def fail_after_activation(context, *args, **kwargs):
            action = activate(context, *args, **kwargs)
            self.assertIsNot(context.scene, scene)
            self.assertIs(settings.target, worklist.state(context).rig)
            self.assertIs(settings.target.animation_data.action, action)
            newly_written.extend(set(cache_root.rglob('*.blend')) - cached_before)
            raise RuntimeError('Injected failure after first worklist activation')

        with patch.object(worklist, 'activate', side_effect=fail_after_activation):
            with self.assertRaisesRegex(RuntimeError, 'Injected failure after first'):
                self.add()
        self.assertIs(bpy.context.scene, scene)
        self.assertIs(bpy.context.view_layer.objects.active, active)
        self.assertEqual(tuple(bpy.context.selected_objects), selected)
        self.assertIs(settings.target, self.original)
        self.assertFalse(worklist.state(bpy.context).items)
        self.assertIsNone(worklist.state(bpy.context).rig)
        self.assertEqual(set(bpy.data.user_map()), ids)
        self.assertEqual(len(newly_written), 1)
        self.assertFalse(newly_written[0].exists())
        self.assertEqual(set(cache_root.rglob('*.blend')), cached_before)
        self.assertEqual(self.model_imports, 1)
        self.assert_original_unchanged()

    def test_stale_packet_and_changed_model_fail_without_mutating_the_worklist(self):
        first = self.add()
        before = self.active_state()
        second_bytes = self.packets[1].read_bytes()
        self.packets[1].write_bytes(second_bytes + b' ')
        with self.assertRaises(ValueError):
            self.add(1)
        self.assertEqual(self.active_state(), before)
        self.assertEqual(self.model_imports, 1)
        self.packets[1].write_bytes(second_bytes)
        model_bytes = self.model.read_bytes()
        self.model.write_bytes(model_bytes + b' changed')
        with self.assertRaises(ValueError):
            self.add(1)
        self.assertEqual(self.active_state(), before)
        self.model.write_bytes(model_bytes)
        manifest_bytes = self.links[1].read_bytes()
        packet = json.loads(second_bytes)
        packet['bones'][4]['rest'][3] += .02
        self.packets[1].write_text(json.dumps(packet), encoding='utf-8')
        manifest = json.loads(manifest_bytes)
        manifest['sourcePackageSha256'] = digest(self.packets[1])
        self.links[1].write_text(json.dumps(manifest), encoding='utf-8')
        actions = {action.as_pointer() for action in bpy.data.actions}
        with self.assertRaisesRegex(ValueError, 'differ|match'):
            self.add(1)
        self.assertEqual(self.active_state(), before)
        self.assertEqual({action.as_pointer() for action in bpy.data.actions}, actions)
        self.packets[1].write_bytes(second_bytes)
        self.links[1].write_bytes(manifest_bytes)
        self.assert_original_unchanged()

    def test_saved_worklist_keeps_action_slots_and_link_identity_after_reopen(self):
        first, second = self.add(0), self.add(1)
        first_id, second_id = first.item_id, second.item_id
        worklist.activate(bpy.context, first_id, side='CUSTOM')
        before = [(item.item_id, item.clip_key, item.source_action.name, item.custom_action.name,
                   item.source_slot, item.custom_slot, action_content(item.source_action),
                   action_content(item.custom_action)) for item in worklist.state(bpy.context).items]
        current_scene = bpy.context.scene.name
        path = self.folder / 'Saved Worklist.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path), use_scripts=False)
        bpy.context.window.scene = bpy.data.scenes[current_scene]
        after = [(item.item_id, item.clip_key, item.source_action.name, item.custom_action.name,
                  item.source_slot, item.custom_slot, action_content(item.source_action),
                  action_content(item.custom_action)) for item in worklist.state(bpy.context).items]
        self.assertEqual(after, before)
        worklist.activate(bpy.context, second_id, side='CUSTOM')
        second = self.item(second_id)
        self.assertIs(second.rig.animation_data.action, second.custom_action)
        self.assertEqual(second.rig.animation_data.action_slot.handle, second.custom_slot)
        self.assertEqual(link.get_link(second.rig)['identity']['clipLocalId'], 1657602633327794032)
        source_bytes = Path(second.source_file).read_bytes()
        source_before = action_content(second.source_action)
        # A changed cache must not be relabelled as a fresh trusted baseline.
        Path(second.source_file).write_bytes(source_bytes + b' changed')
        active = self.active_state()
        with self.assertRaises(ValueError):
            worklist.activate(bpy.context, second_id, side='SOURCE')
        self.assertEqual(self.active_state(), active)
        self.assertEqual(action_content(second.source_action), source_before)
        Path(second.source_file).write_bytes(source_bytes)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
