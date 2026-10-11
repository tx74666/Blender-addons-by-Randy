"""Native RNA and saved recipe contracts in a disposable background Blender.

Run with --background --factory-startup --python-exit-code 1. Never run this
script in an artist session: it saves and reopens only its tempfile fixture.
No Unity process, real picker window, FBX worker or nested Blender is started.
"""

import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace

import bpy


assert bpy.app.background and not bpy.data.filepath, 'Use a fresh --background --factory-startup process.'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
import character_designer
from character_designer import unity_export, unity_export_ui as export_ui
from character_designer import unity_material_match_ui as match_ui


class Event:
    """Only fields verified against actual Blender Event RNA are exposed."""
    __slots__ = ('type', 'value')

    def __init__(self, event_type):
        self.type, self.value = event_type, 'NOTHING'


class Action:
    def __init__(self, rna):
        object.__setattr__(self, '_rna', rna)

    def __setattr__(self, name, value):
        assert name in self._rna.properties, (self._rna.identifier, name)
        object.__setattr__(self, name, value)


class Layout:
    """Record real draw callbacks, checking routes against registered RNA."""
    def __init__(self):
        self.labels, self.buttons, self.actions, self.fields = [], [], [], []
        self.operator_context = 'EXEC_DEFAULT'

    def row(self, **_kwargs): return self
    def box(self): return self
    def separator(self): pass

    def label(self, **kwargs):
        self.labels.append(kwargs.get('text', ''))

    def prop(self, data, name, **_kwargs):
        assert name in data.bl_rna.properties, name
        self.fields.append(name)

    def operator(self, identifier, **kwargs):
        category, name = identifier.split('.')
        rna = getattr(getattr(bpy.ops, category), name).get_rna_type()
        action = Action(rna)
        self.buttons.append((identifier, kwargs, self.operator_context))
        self.actions.append((identifier, action))
        return action

    def assert_english(self):
        text = self.labels + [kwargs.get('text', '') for _id, kwargs, _context in self.buttons]
        assert not any(re.search(r'[\u3400-\u9fff]', value) for value in text), text


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def rig(name):
    data = bpy.data.armatures.new(name + ' Skeleton')
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bone = data.edit_bones.new('Root')
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bone.use_deform = True
    bpy.ops.object.mode_set(mode='OBJECT')
    assert obj.pose is not None and 'Root' in obj.pose.bones
    return obj


def body(name, armature, material):
    data = bpy.data.meshes.new(name + ' Mesh')
    data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    data.materials.append(material)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = armature
    obj.vertex_groups.new(name='Root').add([0, 1, 2], 1.0, 'REPLACE')
    modifier = obj.modifiers.new('Character Armature', 'ARMATURE')
    modifier.object = armature
    modifier.use_vertex_groups = True
    modifier.use_bone_envelopes = False
    bpy.context.view_layer.update()
    return obj


def snapshot(entry):
    return {**{name: getattr(entry, name) for name in
               ('source_material', 'material_guid', 'material_local_id', 'material_path',
                'material_name', 'project_root', 'export_asset_id')},
            'material': entry.material.name if entry.material else None}


def profile(root, armature, material, asset_id, material_guid, local_id):
    directory = root / 'Assets/Characters' / armature.name
    directory.mkdir(parents=True)
    filename = armature.name.removesuffix('Rig') + '.fbx'
    model = directory / filename
    model.write_bytes(b'Native UI metadata fixture: not a real imported FBX')
    warning = 'Material "' + material.name + '" uses a custom shader; set it up in Unity.'
    manifest = model.with_suffix('.cdesigner.json')
    write_json(manifest, {'schema': 'cdesigner.unity-character.v1', 'ok': True,
                         'asset_id': asset_id, 'filename': filename,
                         'materials': [material.name], 'warnings': [warning]})
    unity_material = root / 'Assets/Materials' / (material.name + '.mat')
    unity_material.parent.mkdir(exist_ok=True)
    unity_material.write_text('%YAML 1.1\nMaterial:\n  m_Name: Fixture Material\n', encoding='utf-8')
    unity_material.with_name(unity_material.name + '.meta').write_text(
        'fileFormatVersion: 2\nguid: ' + material_guid + '\n', encoding='utf-8')
    config = armature.character_designer_unity_export
    config.directory, config.filename, config.asset_id = str(directory), filename, asset_id
    config.last_report, config.last_status, config.show_warnings = str(manifest), 'Exported · 1 warning(s)', True
    entry = config.unity_material_matches.add()
    entry.material, entry.source_material = material, material.name
    entry.material_guid, entry.material_local_id = material_guid, str(local_id)
    entry.material_path, entry.material_name = unity_material.relative_to(root).as_posix(), 'Fixture Material'
    entry.project_root, entry.export_asset_id = root.as_posix(), asset_id
    receipt = {**match_ui._entry(entry), 'schema': 'cdesigner.material-match-receipt/1',
               'model_path': model.relative_to(root).as_posix(), 'status': 'matched',
               'fbx_sha256': hashlib.sha256(model.read_bytes()).hexdigest(),
               'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
    receipt_path = (root / '.codex/character-material-matches/receipts' / asset_id /
                    (hashlib.sha256(material.name.encode('utf-8')).hexdigest() + '.json'))
    write_json(receipt_path, receipt)
    return config, entry, warning


character_designer.register()
character_designer.register()
character_designer._validate_registration_integrity()
try:
    assert set(Event.__slots__) <= set(bpy.types.Event.bl_rna.properties.keys())
    assert 'timer' not in bpy.types.Event.bl_rna.properties.keys()
    assert not hasattr(Event('TIMER'), 'timer')
    rna = bpy.ops.character_designer.unity_match_material.get_rna_type()
    assert rna.properties['material_name'].type == 'STRING'
    assert match_ui.CHARACTERDESIGNER_OT_unity_match_material.bl_label == 'Match Unity Material'
    entry_rna = match_ui.CharacterDesignerUnityMaterialMatch.bl_rna.properties
    for name in ('material_local_id', 'source_material', 'material_guid', 'material_path',
                 'material_name', 'project_root', 'export_asset_id'):
        assert entry_rna[name].type == 'STRING' and not entry_rna[name].is_skip_save, name
    assert entry_rna['material'].type == 'POINTER' and not entry_rna['material'].is_skip_save
    assert export_ui.CharacterDesignerUnityExport.bl_rna.properties['unity_material_matches'].type == 'COLLECTION'
    print('PASS native Material-match RNA registration and Event shape', flush=True)

    with tempfile.TemporaryDirectory(prefix='cd-material-native-ui-') as temporary:
        folder = Path(temporary).resolve()
        root = folder / 'UnityProject'
        (root / 'Assets').mkdir(parents=True)
        (root / 'ProjectSettings').mkdir()
        main, second = rig('CoshaRig'), rig('SoldierRig')
        crystal, skin = bpy.data.materials.new('Crystal_Blue'), bpy.data.materials.new('Soldier_Skin')
        crystal.use_nodes = skin.use_nodes = True
        main_body, second_body = body('Cosha Body', main, crystal), body('Soldier Body', second, skin)
        bpy.context.scene.character_designer_setup.rig = main
        bpy.context.scene.character_designer_setup.body = main_body
        config, entry, warning = profile(root, main, crystal, 'a' * 32, 'b' * 32, 1657602633327794031)
        other, other_entry, _ = profile(root, second, skin, 'c' * 32, 'd' * 32, 9223372036854775807)
        collected = unity_export.collect_character(bpy.context, main, config)['objects']
        assert main in collected and main_body in collected
        assert second not in collected and second_body not in collected
        saved, other_saved = snapshot(entry), snapshot(other_entry)
        assert match_ui._entry(entry)['material_local_id'] == 1657602633327794031
        assert type(match_ui._entry(entry)['material_local_id']) is int
        assert match_ui.status(main, config, entry)[0] == 'Matched'
        assert match_ui.status(second, other, other_entry)[0] == 'Matched'

        layout = Layout()
        match_ui.draw_matches(layout, bpy.context, main, config, False)
        assert 'Unity Materials' in layout.labels
        assert 'Matched · Fixture Material' in layout.labels
        match_actions = [(kwargs, context) for identifier, kwargs, context in layout.buttons
                         if identifier == 'character_designer.unity_match_material']
        assert match_actions == [({'text': 'Match'}, 'INVOKE_DEFAULT')]
        assert [action.material_name for identifier, action in layout.actions
                if identifier == 'character_designer.unity_match_material'] == ['Crystal_Blue']
        layout.assert_english()
        actions = Layout()
        export_ui._warning_actions(actions, bpy.context, config, warning)
        assert [(identifier, kwargs['text'], context) for identifier, kwargs, context in actions.buttons] == [
            ('character_designer.unity_match_material', 'Match Unity Material', 'INVOKE_DEFAULT'),
            ('character_designer.unity_simple_material', 'Use Simplified', 'INVOKE_DEFAULT')]
        actions.assert_english()
        full_panel = Layout()
        export_ui.CHARACTERDESIGNER_PT_unity_export.draw(SimpleNamespace(layout=full_panel), bpy.context)
        assert 'Unity Materials' in full_panel.labels
        assert not any(label.startswith('Warnings ·') for label in full_panel.labels), full_panel.labels
        full_panel.assert_english()

        # Background Blender cannot dispatch an interactive INVOKE_DEFAULT
        # operator. Registered RNA and draw routing are verified above; actual
        # desktop button invocation remains a separate interactive acceptance.
        assert not match_ui._ACTIVE
        print('PASS native draw English labels, registered RNA routes and Matched status; interactive button not exercised', flush=True)

        assert bpy.ops.character_designer.unity_simple_material(material_name=crystal.name, enabled=True) == {'FINISHED'}
        assert snapshot(entry) == saved and snapshot(other_entry) == other_saved
        assert match_ui.status(main, config, entry)[0] == 'Using Simplified'
        assert not other.simple_materials
        assert match_ui.capture(config, [main_body], Path(config.directory), config.asset_id) == []
        assert bpy.ops.character_designer.unity_simple_material(material_name=crystal.name, enabled=False) == {'FINISHED'}
        assert snapshot(entry) == saved and match_ui.status(main, config, entry)[0] == 'Matched'
        assert match_ui.capture(config, [main_body], Path(config.directory), config.asset_id)[0]['material_local_id'] == 1657602633327794031
        assert bpy.ops.character_designer.unity_simple_material(material_name=crystal.name, enabled=True) == {'FINISHED'}

        blend = folder / 'Material Match Profiles.blend'
        assert bpy.ops.wm.save_as_mainfile(filepath=str(blend)) == {'FINISHED'}
        assert bpy.ops.wm.open_mainfile(filepath=str(blend), use_scripts=False) == {'FINISHED'}
        # Reacquire native RNA after loading: references from the old Main are
        # invalid. Both rigs deliberately have distinct recipe IDs/pointers.
        main, second = bpy.data.objects['CoshaRig'], bpy.data.objects['SoldierRig']
        config, other = main.character_designer_unity_export, second.character_designer_unity_export
        assert len(config.unity_material_matches) == len(other.unity_material_matches) == 1
        assert snapshot(config.unity_material_matches[0]) == saved
        assert snapshot(other.unity_material_matches[0]) == other_saved
        assert config.unity_material_matches[0].material == bpy.data.materials['Crystal_Blue']
        assert other.unity_material_matches[0].material == bpy.data.materials['Soldier_Skin']
        assert config.unity_material_matches[0].material_local_id == '1657602633327794031'
        assert other.unity_material_matches[0].material_local_id == '9223372036854775807'
        assert [item.material.name for item in config.simple_materials] == ['Crystal_Blue']
        assert not other.simple_materials
        assert match_ui.status(main, config, config.unity_material_matches[0])[0] == 'Using Simplified'
        assert match_ui.status(second, other, other.unity_material_matches[0])[0] == 'Matched'
        assert not match_ui._ACTIVE
        print('PASS .blend save/reopen retains per-rig recipes, Material pointers and exact int64 strings', flush=True)
finally:
    character_designer.unregister()
    assert not match_ui._ACTIVE
    assert not hasattr(bpy.types.Object, 'character_designer_unity_export')

print('UNITY_MATERIAL_MATCH_UI_PASSED', flush=True)
