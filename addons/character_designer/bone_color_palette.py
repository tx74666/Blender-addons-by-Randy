"""Character-owned bone display colors, changed only by explicit operators.

Pose colors override native Bone colors without changing them. Preferences and
the first recovery point live in the .blend; drawing and frame changes never
reapply a scheme or replace an artist's later color edits.
"""
import copy
import json
import math
import re
import uuid

import bpy
from bpy.props import EnumProperty, FloatVectorProperty, StringProperty
from bpy.types import Operator


CONFIG_KEY = 'character_designer_bone_color_palette_v1'
BACKUP_KEY = '_cd_bone_palette_before_v1'
REFS_KEY = '_cd_bone_palette_rigs_v1'
MAIN_KEY = '_cd_bone_palette_main_v1'
DISPLAY_KEY = '_cd_bone_palette_display_before_v1'
GROUPS = ('BODY', 'ARMS', 'LEGS', 'HAIR', 'DRESS')
CHANNELS = ('normal', 'select', 'active')
LABELS = {'BODY': 'Body', 'ARMS': 'Arms', 'LEGS': 'Legs',
          'HAIR': 'Hair', 'DRESS': 'Dress'}
DEFAULTS = {
    'BODY': ((.66, .56, .34), (.85, .73, .47), (1.00, .90, .68)),
    'ARMS': ((.35, .51, .65), (.52, .73, .88), (.76, .89, 1.00)),
    'LEGS': ((.30, .57, .53), (.46, .78, .69), (.72, .94, .83)),
    'HAIR': ((.53, .44, .68), (.72, .62, .88), (.89, .81, 1.00)),
    'DRESS': ((.68, .43, .55), (.88, .61, .74), (1.00, .81, .90)),
}
_HELPER = re.compile(r'^(?:MCH|ORG|ORI|VIS|WGT|HELPER)[_\-]', re.I)
_ARM_WORDS = {'arm', 'upperarm', 'lowerarm', 'forearm', 'wrist', 'hand', 'finger',
              'thumb', 'index', 'middle', 'ring', 'pinky', 'little', 'shoulder', 'clavicle', 'clav', 'collar'}
_LEG_WORDS = {'leg', 'upperleg', 'lowerleg', 'thigh', 'shin', 'calf', 'knee', 'ankle', 'foot', 'feet', 'toe'}


def _colors(value):
    if isinstance(value, (tuple, list)) and len(value) == 3:
        value = dict(zip(CHANNELS, value))
    if not isinstance(value, dict) or set(value) != set(CHANNELS):
        raise ValueError('Choose Normal, Selected and Active RGB colors.')
    result = {}
    for field in CHANNELS:
        rgb = value[field]
        if (not isinstance(rgb, (tuple, list)) or len(rgb) != 3
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or not 0 <= v <= 1 for v in rgb)):
            raise ValueError('Bone display RGB colors must be finite values from 0 to 1.')
        result[field] = [float(v) for v in rgb]
    return result


def _default_config():
    return {'version': 1, 'id': uuid.uuid4().hex, 'enabled': True,
            'groups': {group: _colors(colors) for group, colors in DEFAULTS.items()}}


def _read(main):
    if CONFIG_KEY not in main:
        return None
    try:
        saved = json.loads(main[CONFIG_KEY])
        if (not isinstance(saved, dict) or saved.get('version') != 1
                or not isinstance(saved.get('id'), str) or not saved['id']
                or not isinstance(saved.get('enabled'), bool)
                or not isinstance(saved.get('groups'), dict) or set(saved['groups']) != set(GROUPS)):
            raise ValueError()
        saved['groups'] = {group: _colors(saved['groups'][group]) for group in GROUPS}
        return saved
    except (ValueError, TypeError, KeyError):
        raise ValueError('The saved Bone Display Color Palette is invalid; recover a saved copy before changing colors.') from None


def group_colors(main, group):
    """Read a saved scheme, or the proposed defaults, without creating settings."""
    if group not in GROUPS:
        raise ValueError('Choose a Bone Display color group.')
    saved = _read(main)
    return copy.deepcopy(saved['groups'][group] if saved else _colors(DEFAULTS[group]))


def main_for(rig):
    """Follow exact attachment/Original references, never proximity or names."""
    from . import skirt_rig, body_original_mode
    if rig is None or rig.type != 'ARMATURE':
        return None
    original = rig.get(body_original_mode.DISPLAY_OWNER)
    if isinstance(original, bpy.types.Object) and original.type == 'ARMATURE':
        return original
    if rig.get(skirt_rig.OWNER_KEY):
        source = rig.get(skirt_rig.SOURCE_KEY)
        if source is not None:
            attachment = skirt_rig.attachment_status(source)
            if attachment is None:
                raise ValueError('The Dress source record is missing; recover it before changing colors.')
            if attachment['attached']:
                return attachment['character']
        return rig
    owner = rig.get(MAIN_KEY)
    return owner if isinstance(owner, bpy.types.Object) and owner.type == 'ARMATURE' else rig


def _helper(pb):
    from . import limb_ik
    tokens, _base = limb_ik._name_parts(pb.name)
    return bool(_HELPER.match(pb.name.rsplit(':', 1)[-1])
                or {'mch', 'org', 'ori', 'vis', 'wgt', 'helper', 'mechanism'}.intersection(tokens))


def _semantic(pb):
    """Only used after exact native membership or tool ownership is established."""
    from . import limb_ik
    tokens, _base = limb_ik._name_parts(pb.name.rsplit(':', 1)[-1])
    words = {re.sub(r'\d+$', '', token) for token in tokens}
    if _ARM_WORDS.intersection(words):
        return 'ARMS'
    if _LEG_WORDS.intersection(words):
        return 'LEGS'
    return 'BODY'


def _json_record(owner, key, label):
    if key not in owner:
        return None
    try:
        value = json.loads(owner[key])
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ValueError(f'{label} ownership data is invalid; recover it before changing colors.') from None


def _source_groups(main):
    """Saved limb mappings and tagged chains establish arbitrary-name anatomy."""
    from . import limb_ik, body_calibration, limb_fk_visuals
    result, chains = {}, []
    calibration = _json_record(main, body_calibration.KEY, 'Body Calibration')
    if calibration is not None:
        mappings = calibration.get('mappings')
        if calibration.get('version') != 1 or not isinstance(mappings, dict):
            raise ValueError('Body Calibration mappings are invalid; recover them before changing colors.')
        for key, names in mappings.items():
            if not isinstance(key, str):
                raise ValueError('Body Calibration mapping keys are invalid.')
            kind = key.split('.')[0]
            if kind in {'ARM', 'LEG'}:
                chains.append((kind, names))
    for pb in main.pose.bones:
        bone = pb.bone
        if bone.get(limb_ik.OWNER_KEY) != limb_ik.OWNER_VALUE or limb_ik.CHAIN_KEY not in bone:
            continue
        kind = bone.get(limb_ik.KIND_KEY)
        if kind in {'ARM', 'LEG'}:
            try:
                chains.append((kind, json.loads(bone[limb_ik.CHAIN_KEY])))
            except (ValueError, TypeError):
                raise ValueError('A saved limb source chain is invalid; recover it before changing colors.') from None
    fk = _json_record(main.data, limb_fk_visuals.RECORD_KEY, 'FK Rings')
    if fk is not None:
        if not isinstance(fk.get('bindings'), dict):
            raise ValueError('FK Rings source bindings are invalid.')
        for entry in fk['bindings'].values():
            if not isinstance(entry, dict) or entry.get('kind') not in {'ARM', 'LEG'}:
                raise ValueError('FK Rings source bindings are invalid.')
            chains.append((entry['kind'], entry.get('chain')))
    for kind, names in chains:
        if (not isinstance(names, (list, tuple)) or len(names) != 3
                or any(not isinstance(name, str) or name not in main.data.bones for name in names)):
            raise ValueError('A saved limb source chain is incomplete; recover it before changing colors.')
        group = 'ARMS' if kind == 'ARM' else 'LEGS'
        for name in names:
            if name in result and result[name] != group:
                raise ValueError('The saved Arm and Leg mappings overlap; choose separate source chains.')
            result[name] = group
        end = main.data.bones[names[-1]]
        # Descendants still require exact Original membership when collected.
        # This handles arbitrary-name finger/toe branches below a known end.
        for bone in end.children_recursive:
            result.setdefault(bone.name, group)
        upper = main.data.bones[names[0]]
        if kind == 'ARM' and upper.parent and _semantic(main.pose.bones[upper.parent.name]) == 'ARMS':
            result.setdefault(upper.parent.name, group)
    widgets = _json_record(main.data, limb_ik.SOURCE_WIDGETS_KEY, 'Source Controls')
    if widgets is not None:
        if not isinstance(widgets.get('bones'), dict):
            raise ValueError('Source Controls bindings are invalid.')
        for name, entry in widgets['bones'].items():
            if not isinstance(entry, dict):
                raise ValueError('Source Controls bindings are invalid.')
            if entry.get('kind') in {'FINGER', 'SHOULDER'}:
                result[name] = 'ARMS'
    return result


def _control_group(pb, sources=None):
    from . import control_colors, limb_ik, hair_bones_rig, skirt_rig, limb_fk_visuals
    bone, shape = pb.bone, pb.custom_shape
    if _helper(pb):
        return None
    if bone.get(hair_bones_rig.OWNER_KEY) == hair_bones_rig.OWNER_VALUE:
        return 'HAIR'
    if bone.get(skirt_rig.OWNER_KEY):
        return 'DRESS' if shape is not None else None
    # Dedicated legacy/new separate Dress rigs put exact ownership on their
    # rig and shapes; only shared rigs also tag each Bone with its source.
    dress_owner = pb.id_data.get(skirt_rig.OWNER_KEY)
    if dress_owner and shape is not None:
        source = pb.id_data.get(skirt_rig.SOURCE_KEY)
        if shape.get(skirt_rig.OWNER_KEY) == dress_owner and shape.get(skirt_rig.SOURCE_KEY) == source:
            record = skirt_rig.read_record(source)
            if record is None or record['owner'] != dress_owner:
                raise ValueError('The generated Dress control source record is missing.')
            controls, _deform, _mechanisms = skirt_rig._bone_collection_layout(record)
            return 'DRESS' if pb.name in controls else None
    owner = bone.get(control_colors.OWNER_KEY)
    shape_owner = shape.get(control_colors.OWNER_KEY) if shape else None
    if owner == 'limb_ik':
        role = bone.get(limb_ik.ROLE_KEY)
        if role not in limb_ik.CONTROL_VISUAL_ROLES:
            return None
        if role == 'MASTER':
            return 'BODY'
        return {'ARM': 'ARMS', 'LEG': 'LEGS'}.get(bone.get(limb_ik.KIND_KEY))
    if owner == 'foot_controls':
        return 'LEGS' if bone.get('character_designer_foot_role') in {'FOOT_ROLL', 'TOE_BEND'} else None
    if shape is None or (owner not in control_colors.OWNERS and shape_owner not in control_colors.OWNERS):
        return None
    if shape_owner == limb_fk_visuals.OWNER_VALUE:
        return (sources or _source_groups(pb.id_data)).get(pb.name, _semantic(pb))
    if shape_owner == 'limb_ik' and shape.get(limb_ik.KIND_KEY) in {'FINGER', 'SHOULDER'}:
        return 'ARMS'
    return (sources or {}).get(pb.name, _semantic(pb) if owner is None else 'BODY')


def _hair_sources(main):
    """Validate generated hair names against source records without mesh scans."""
    from . import hair_bones_rig as hair
    sources = {pb.bone.get(hair.SOURCE_KEY) for pb in main.pose.bones
               if pb.bone.get(hair.OWNER_KEY) == hair.OWNER_VALUE and not _helper(pb)}
    allowed = set()
    for source in sources:
        if (not isinstance(source, bpy.types.Object) or source.type != 'MESH'
                or source.get(hair.RIG_KEY) != main):
            raise ValueError('Hair bone source ownership is incomplete; recover it before changing colors.')
        record = hair._read_records(source)
        if record is None:
            raise ValueError('Hair bone source records are missing; recover them before changing colors.')
        for chain in record['chains']:
            for name in chain['bones']:
                bone = main.data.bones.get(name)
                if (bone is None or bone.get(hair.OWNER_KEY) != hair.OWNER_VALUE
                        or bone.get(hair.SOURCE_KEY) != source or not bone.use_deform
                        or bone.get(hair.SIGNATURE_KEY) != chain['signature']):
                    raise ValueError('Hair source records do not match their owned bones.')
                allowed.add(name)
    for pb in main.pose.bones:
        if pb.bone.get(hair.OWNER_KEY) == hair.OWNER_VALUE and not _helper(pb) and pb.name not in allowed:
            raise ValueError('An owned Hair bone is missing from its source record.')
    return allowed


def members(main, context):
    """Read-only exact character targets; mechanisms and other characters stay out."""
    from . import body_original_mode, bone_display, limb_ik, hair_bones_rig, skirt_rig, bone_collections
    result = {group: [] for group in GROUPS}
    source_groups = _source_groups(main)
    hair_allowed = _hair_sources(main)
    assigned = {}

    def add(rig, name, group, *, native=False):
        pb = rig.pose.bones.get(name)
        if pb is None:
            raise ValueError(f"Saved display bone '{name}' is missing; recover it before changing colors.")
        if _helper(pb):
            return
        if native:
            owner = pb.bone.get(limb_ik.OWNER_KEY)
            if owner in limb_ik.GENERATED_CONTROL_OWNERS:
                return
            if owner and owner not in {'limb_fk_visuals', 'head_neck_visuals', 'body_detail_visuals'}:
                return
            if group not in {'HAIR', 'DRESS'} and (pb.bone.get(hair_bones_rig.OWNER_KEY) or pb.bone.get(skirt_rig.OWNER_KEY)):
                return
        key = rig.as_pointer(), name
        previous = assigned.get(key)
        if previous is not None and previous != group:
            if group in {'HAIR', 'DRESS'}:
                result[previous].remove(pb)
            else:
                return
        if previous != group:
            result[group].append(pb)
            assigned[key] = group

    original = main.data.collections_all.get('Original')
    if original is not None and original.get(bone_collections.GROUP_KEY) != 'Original':
        raise ValueError('Original is not a managed source collection; organize Bone Collections before changing native colors.')
    dress_records = bone_display.dress_rigs(context, main)
    dress_allowed = {}
    dress_all = {}
    for rig, record in dress_records:
        controls, deform, _mechanisms = skirt_rig._bone_collection_layout(record)
        dress_allowed.setdefault(rig, set()).update(controls | deform)
        dress_all.setdefault(rig, set()).update(controls | deform | _mechanisms)
    for pb in main.pose.bones:
        if pb.bone.get(skirt_rig.OWNER_KEY) and pb.name not in dress_all.get(main, set()):
            raise ValueError('An owned Dress bone is missing from its source record.')
    native = body_original_mode._native_groups(context, main)
    for role, targets in native.items():
        for rig, names in targets.items():
            if rig != main and rig.get(body_original_mode.DISPLAY_OWNER) != main:
                if not (role == 'DRESS' and rig in dress_allowed):
                    raise ValueError('A saved Original display reference belongs to another character.')
            if role == 'BODY' and rig != main:
                raise ValueError('Saved Original Body membership belongs to another armature.')
            if role == 'DRESS' and not set(names).issubset(dress_allowed.get(rig, set())):
                raise ValueError('Saved Dress display membership does not match the owned dress sources.')
            for name in sorted(names):
                pb = rig.pose.bones.get(name)
                if pb is None:
                    raise ValueError(f"Saved display bone '{name}' is missing.")
                if role == 'HAIR' and not _helper(pb):
                    managed_hair = any(c.get(bone_collections.GROUP_KEY) == 'Hair'
                                       or c.get(hair_bones_rig.OWNER_KEY) == hair_bones_rig.OWNER_VALUE
                                       for c in pb.bone.collections)
                    if name not in hair_allowed and not (rig == main and managed_hair and not pb.bone.get(limb_ik.OWNER_KEY)):
                        raise ValueError('Saved Hair display membership does not match its owned source bones.')
                group = source_groups.get(name, _semantic(pb)) if role == 'BODY' else role
                add(rig, name, group, native=True)
    # Hair owner tags also work before compact Bone Collections are organized.
    for pb in main.pose.bones:
        group = _control_group(pb, source_groups)
        if group and group != 'DRESS':
            add(main, pb.name, group)
    for rig, record in dress_records:
        controls, deform, _mechanisms = skirt_rig._bone_collection_layout(record)
        for name in sorted(controls | deform):
            add(rig, name, 'DRESS')
    return result


def _validate_color(state):
    from . import control_colors
    if not isinstance(state, dict) or state.get('palette') not in {'DEFAULT', 'CUSTOM', *(f'THEME{i:02}' for i in range(1, 21))}:
        raise ValueError('A saved bone color is invalid.')
    _colors({name: state[name] for name in CHANNELS})
    if not isinstance(state.get('constraints', False), bool):
        raise ValueError('A saved bone color flag is invalid.')
    return state


def _backup(pb):
    try:
        saved = json.loads(pb[BACKUP_KEY])
        if (not isinstance(saved, dict) or saved.get('version') != 1
                or not isinstance(saved.get('owner'), str) or not saved['owner']
                or saved.get('group') not in GROUPS or 'control_backup' not in saved):
            raise ValueError()
        _validate_color(saved['color'])
        if saved.get('control_backup') is not None:
            _validate_color(json.loads(saved['control_backup']))
        return saved
    except (ValueError, KeyError, TypeError):
        raise ValueError(f"The saved palette color for '{pb.name}' is invalid; recover a saved copy before changing colors.") from None


def palette_assigned(pb):
    return BACKUP_KEY in pb


def _set_color(pb, state):
    from . import control_colors
    control_colors._set_color(pb, state)


def _property(owner, key, value):
    if value is None:
        owner.pop(key, None)
    else:
        owner[key] = value


def _refs(main):
    """Deleted generated rigs have no surviving colors to restore.

    ID pointers may become None after authorized Dress removal. Filter those
    only in memory; operators commit cleaned references with their transaction.
    Live references still require exact palette ownership during preflight.
    """
    result = {}
    stored = main.get(REFS_KEY, {})
    if not hasattr(stored, 'items'):
        raise ValueError('Saved palette armature references are invalid.')
    for key, rig in stored.items():
        if not isinstance(key, str) or not key.isdigit():
            raise ValueError('Saved palette armature reference keys are invalid.')
        if rig is None:
            continue
        try:
            if not isinstance(rig, bpy.types.Object) or rig.type != 'ARMATURE':
                raise ValueError('A saved palette armature reference is invalid.')
            if bpy.data.objects.get(rig.name) != rig:
                continue
        except ReferenceError:
            continue
        result[key] = rig
    return result


def _record_bone(pb, saved, group):
    from . import control_colors
    if BACKUP_KEY in pb:
        previous = _backup(pb)
        if previous['owner'] != saved['id']:
            raise ValueError(f"Bone '{pb.name}' already belongs to another character's palette.")
        previous['group'] = group
    else:
        if control_colors.BACKUP_KEY in pb:
            control_colors._saved_color(pb)
        previous = {'version': 1, 'owner': saved['id'], 'group': group,
                    'color': control_colors._color_state(pb),
                    'control_backup': pb.get(control_colors.BACKUP_KEY)}
    pb[BACKUP_KEY] = json.dumps(previous, separators=(',', ':'), allow_nan=False)


def _paint(pb, colors):
    _set_color(pb, {'palette': 'CUSTOM', 'constraints': False, **colors})


def _display(rig):
    if DISPLAY_KEY not in rig.data:
        rig.data[DISPLAY_KEY] = bool(rig.data.show_bone_colors)
    rig.data.show_bone_colors = True


def _targets_checkpoint(main, targets, rigs=None):
    from . import control_colors
    rigs = list(dict.fromkeys(rigs if rigs is not None else (pb.id_data for pb in targets)))
    return {'config': main.get(CONFIG_KEY), 'refs': dict(main.get(REFS_KEY, {})),
            'bones': [(pb, control_colors._color_state(pb), pb.get(BACKUP_KEY), pb.get(control_colors.BACKUP_KEY)) for pb in targets],
            'rigs': [(rig, rig.data.show_bone_colors, rig.data.get(DISPLAY_KEY), rig.get(MAIN_KEY)) for rig in rigs]}


def _rollback(main, checkpoint):
    from . import control_colors
    # Use the lower-level setter so a failure injected in _set_color cannot
    # also prevent rollback of the already changed bones.
    for pb, color, backup, control_backup in checkpoint['bones']:
        control_colors._set_color(pb, color)
        _property(pb, BACKUP_KEY, backup)
        _property(pb, control_colors.BACKUP_KEY, control_backup)
    for rig, display, backup, owner in checkpoint['rigs']:
        rig.data.show_bone_colors = display
        _property(rig.data, DISPLAY_KEY, backup)
        _property(rig, MAIN_KEY, owner)
    _property(main, CONFIG_KEY, checkpoint['config'])
    _property(main, REFS_KEY, checkpoint['refs'] or None)


def _apply(main, context, saved, groups):
    from . import bone_display, control_colors
    bone_display._editable(main)
    existing = _read(main)
    membership = members(main, context)
    targets = [pb for group in groups for pb in membership[group]]
    rigs = list(dict.fromkeys(pb.id_data for pb in targets))
    for rig in rigs:
        bone_display._editable(rig)
    # Validate every retained recovery point, even if this operation changes
    # just one group. A corrupt unrelated group must not become half-restorable.
    saved_refs = _refs(main)
    retained_rigs = list(dict.fromkeys([main, *saved_refs.values(), *rigs]))
    for rig in retained_rigs:
        if not isinstance(rig, bpy.types.Object) or rig.type != 'ARMATURE':
            raise ValueError('A palette recovery armature is missing; recover it before changing colors.')
        if rig != main and rig in saved_refs.values() and rig.get(MAIN_KEY) != main:
            raise ValueError('A palette recovery armature belongs to another character.')
        if DISPLAY_KEY in rig.data and not isinstance(rig.data[DISPLAY_KEY], bool):
            raise ValueError('A saved palette display flag is invalid.')
        for pb in rig.pose.bones:
            if BACKUP_KEY in pb:
                backup = _backup(pb)
                if not existing or backup['owner'] != existing['id']:
                    raise ValueError('Palette recovery ownership does not match this character.')
            if pb in targets and control_colors.BACKUP_KEY in pb:
                control_colors._saved_color(pb)
    checkpoint = _targets_checkpoint(main, targets, rigs)
    try:
        saved['enabled'] = True
        main[CONFIG_KEY] = json.dumps(saved, separators=(',', ':'), sort_keys=True, allow_nan=False)
        refs = saved_refs.copy()
        for rig in rigs:
            if rig != main:
                if rig.get(MAIN_KEY) not in (None, main):
                    raise ValueError('An attached armature is already assigned to another character palette.')
                rig[MAIN_KEY] = main
            if rig not in refs.values():
                refs[str(max((int(k) for k in refs), default=-1) + 1)] = rig
            _display(rig)
        if refs:
            main[REFS_KEY] = refs
        else:
            main.pop(REFS_KEY, None)
        for group in groups:
            for pb in membership[group]:
                _record_bone(pb, saved, group)
                _paint(pb, saved['groups'][group])
    except Exception:
        _rollback(main, checkpoint)
        raise
    return len(targets)


def apply_palette(main, context):
    main = main_for(main)
    saved = _read(main) or _default_config()
    return _apply(main, context, saved, GROUPS)


def edit_group(main, group, colors, context):
    main = main_for(main)
    if group not in GROUPS:
        raise ValueError('Choose a Bone Display color group.')
    saved = _read(main) or _default_config()
    saved['groups'][group] = _colors(colors)
    return _apply(main, context, saved, (group,))


def restore_palette(main, context):
    from . import bone_display, control_colors
    main = main_for(main)
    bone_display._editable(main)
    saved = _read(main)
    if saved is None:
        return 0
    rigs = list(dict.fromkeys([main, *_refs(main).values()]))
    targets = []
    for rig in rigs:
        bone_display._editable(rig)
        if rig != main and rig.get(MAIN_KEY) != main:
            raise ValueError('A palette recovery armature belongs to another character.')
        if DISPLAY_KEY in rig.data and not isinstance(rig.data[DISPLAY_KEY], bool):
            raise ValueError('A saved palette display flag is invalid.')
        for pb in rig.pose.bones:
            if BACKUP_KEY in pb:
                backup = _backup(pb)
                if backup['owner'] != saved['id']:
                    raise ValueError('Palette recovery ownership does not match this character.')
                targets.append(pb)
    checkpoint = _targets_checkpoint(main, targets, rigs)
    try:
        for pb in targets:
            backup = _backup(pb)
            _set_color(pb, backup['color'])
            _property(pb, control_colors.BACKUP_KEY, backup['control_backup'])
            del pb[BACKUP_KEY]
        for rig in rigs:
            if DISPLAY_KEY in rig.data:
                rig.data.show_bone_colors = bool(rig.data[DISPLAY_KEY])
                del rig.data[DISPLAY_KEY]
            rig.pop(MAIN_KEY, None)
        saved['enabled'] = False
        main[CONFIG_KEY] = json.dumps(saved, separators=(',', ':'), sort_keys=True, allow_nan=False)
        main.pop(REFS_KEY, None)
    except Exception:
        _rollback(main, checkpoint)
        raise
    return len(targets)


def colors_for_control(pb):
    """Saved scheme for a newly generated owned controller, or None."""
    main = main_for(pb.id_data)
    saved = _read(main)
    if not saved or not saved['enabled']:
        return None
    group = _control_group(pb)
    return copy.deepcopy(saved['groups'][group]) if group else None


def style_control(pb):
    """Called by generator styling; never called by a draw/frame callback."""
    main = main_for(pb.id_data)
    saved = _read(main)
    group = _control_group(pb)
    if not saved or not saved['enabled'] or not group:
        return False
    if pb.id_data != main and pb.id_data.get(MAIN_KEY) not in (None, main):
        raise ValueError('An attached armature is assigned to another character palette.')
    checkpoint = _targets_checkpoint(main, [pb])
    try:
        _record_bone(pb, saved, group)
        _paint(pb, saved['groups'][group])
        _display(pb.id_data)
        refs = _refs(main)
        if pb.id_data not in refs.values():
            refs[str(max((int(k) for k in refs), default=-1) + 1)] = pb.id_data
            main[REFS_KEY] = refs
        if pb.id_data != main:
            pb.id_data[MAIN_KEY] = main
    except Exception:
        _rollback(main, checkpoint)
        raise
    return True


def style_generated_dress(rig, source):
    """Initialize new owned native Dress bones from the saved character scheme.

    Called once by the Dress builder after its source record is saved. Existing
    assigned/artist Pose colors remain authoritative; mechanisms stay excluded.
    """
    from . import skirt_rig
    main = main_for(rig)
    saved = _read(main)
    if not saved or not saved['enabled']:
        return 0
    record = skirt_rig.read_record(source)
    if record is None or source.get(skirt_rig.RIG_KEY) != rig:
        raise ValueError('Generated Dress ownership is incomplete.')
    _controls, deform, _mechanisms = skirt_rig._bone_collection_layout(record)
    targets = [rig.pose.bones[name] for name in sorted(deform)
               if BACKUP_KEY not in rig.pose.bones[name] and rig.pose.bones[name].color.palette == 'DEFAULT']
    if not targets:
        return 0
    checkpoint = _targets_checkpoint(main, targets)
    try:
        for pb in targets:
            _record_bone(pb, saved, 'DRESS')
            _paint(pb, saved['groups']['DRESS'])
        _display(rig)
        refs = _refs(main)
        if rig not in refs.values():
            refs[str(max((int(k) for k in refs), default=-1) + 1)] = rig
            main[REFS_KEY] = refs
        if rig != main:
            if rig.get(MAIN_KEY) not in (None, main):
                raise ValueError('The generated Dress belongs to another character palette.')
            rig[MAIN_KEY] = main
    except Exception:
        _rollback(main, checkpoint)
        raise
    return len(targets)


def migrate_rig_reference(main, old):
    """Complete an already validated Dress copy before deleting its old rig."""
    saved = _read(main)
    if saved is None:
        return
    refs = _refs(main)
    if old not in refs.values():
        return
    # Shared-rig migration already copied Pose colors and their raw backups.
    copied = [pb for pb in main.pose.bones if BACKUP_KEY in pb]
    for pb in copied:
        if _backup(pb)['owner'] != saved['id']:
            raise ValueError('Migrated Dress palette ownership does not match this character.')
    refs = {key: rig for key, rig in refs.items() if rig != old}
    if copied and main not in refs.values():
        refs[str(max((int(k) for k in refs), default=-1) + 1)] = main
    checkpoint = _targets_checkpoint(main, (), (main,))
    try:
        if copied:
            _display(main)
        _property(main, REFS_KEY, refs or None)
    except Exception:
        _rollback(main, checkpoint)
        raise


def preserve_restoration(pb, state):
    """A removed source widget must not replace an explicitly chosen palette."""
    if BACKUP_KEY not in pb or 'palette_backup' in state:
        return False
    previous = _backup(pb)
    _validate_color(state['color'])
    previous['color'] = state['color']
    previous['control_backup'] = state.get('backup')
    if previous['control_backup'] is not None:
        _validate_color(json.loads(previous['control_backup']))
    pb[BACKUP_KEY] = json.dumps(previous, separators=(',', ':'), allow_nan=False)
    return True


def _effective_colors(pb):
    color = pb.color
    if color.palette == 'DEFAULT':
        color = pb.bone.color
    if color.palette == 'CUSTOM':
        return {name: list(getattr(color.custom, name)) for name in CHANNELS}
    if color.palette.startswith('THEME'):
        sets = bpy.context.preferences.themes[0].bone_color_sets
        entry = sets[int(color.palette[-2:]) - 1]
        return {name: list(getattr(entry, name))[:3] for name in CHANNELS}
    return None


def group_status(main, context):
    """Actual displayed colors, including manual edits and theme colors."""
    membership = members(main, context)
    result = {}
    for group, bones in membership.items():
        values = [_effective_colors(pb) for pb in bones]
        signature = lambda value: tuple(round(v, 5) for name in CHANNELS for v in value[name]) if value else None
        mixed = len({signature(value) for value in values}) > 1
        result[group] = {'count': len(bones), 'mixed': mixed, 'pb': bones[0] if bones else None,
                         'colors': values[0] if values and values[0] else group_colors(main, group),
                         'default': bool(values and values[0] is None)}
    return result


def _poll(context):
    from . import bone_display
    try:
        main = bone_display.character_rig(context)
        bone_display._editable(main)
        return True
    except (ValueError, TypeError, ReferenceError):
        return False


def _operator_main(context, name):
    from . import bone_display
    main = bone_display.character_rig(context)
    if main is None or (name and main.name != name):
        raise ValueError('Select the same character used to open the Color Palette.')
    return main


def _redraw(context):
    for area in context.screen.areas if context.screen else ():
        area.tag_redraw()


class CHARACTERDESIGNER_OT_bone_color_palette(Operator):
    bl_idname = 'character_designer.bone_color_palette'
    bl_label = 'Bone Display Color Palette'
    bl_description = 'Apply this character\'s five color groups to original bones and owned controls, or restore their saved colors'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('APPLY', 'Apply Palette', ''), ('RESTORE', 'Restore Previous Colors', '')))
    rig_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        try:
            main = _operator_main(context, self.rig_name)
            count = apply_palette(main, context) if self.action == 'APPLY' else restore_palette(main, context)
        except (ValueError, RuntimeError, TypeError, KeyError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        _redraw(context)
        self.report({'INFO'}, f"{'Colored' if self.action == 'APPLY' else 'Restored'} {count} bones for {main.name}.")
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_bone_color_group(Operator):
    bl_idname = 'character_designer.bone_color_group'
    bl_label = 'Edit Bone Color Group'
    bl_description = 'Save and apply Normal, Selected and Active colors for this group on this character'
    bl_options = {'REGISTER', 'UNDO'}
    group: EnumProperty(items=tuple((group, LABELS[group], '') for group in GROUPS))
    rig_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    normal: FloatVectorProperty(name='Normal', subtype='COLOR_GAMMA', size=3, min=0, max=1, default=DEFAULTS['BODY'][0])
    select: FloatVectorProperty(name='Selected', subtype='COLOR_GAMMA', size=3, min=0, max=1, default=DEFAULTS['BODY'][1])
    active: FloatVectorProperty(name='Active', subtype='COLOR_GAMMA', size=3, min=0, max=1, default=DEFAULTS['BODY'][2])

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def invoke(self, context, event):
        try:
            main = _operator_main(context, self.rig_name)
            self.rig_name = main.name
            status = group_status(main, context)[self.group]
            colors = status['colors'] if status['count'] else group_colors(main, self.group)
            for name, color in colors.items():
                setattr(self, name, color)
        except (ValueError, RuntimeError, TypeError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=310)

    def draw(self, context):
        self.layout.label(text=f'{LABELS[self.group]} — {self.rig_name}')
        for name in CHANNELS:
            self.layout.prop(self, name)

    def execute(self, context):
        try:
            main = _operator_main(context, self.rig_name)
            colors = {name: list(getattr(self, name)) for name in CHANNELS}
            count = edit_group(main, self.group, colors, context)
        except (ValueError, RuntimeError, TypeError, KeyError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        _redraw(context)
        self.report({'INFO'}, f'Saved {LABELS[self.group]} colors for {main.name}; colored {count} bones.')
        return {'FINISHED'}


def draw(layout, context, main):
    """Compact read-only scheme; editable swatches appear in the UNDO dialog."""
    try:
        saved = _read(main)
        status = group_status(main, context)
    except (ValueError, RuntimeError, TypeError, KeyError, ReferenceError) as exc:
        layout.label(text=str(exc), icon='ERROR')
        return
    layout.label(text=f'Character: {main.name}', icon='OUTLINER_OB_ARMATURE')
    header = layout.row(align=True)
    header.label(text='Group')
    header.label(text='Normal / Select / Active')
    for group in GROUPS:
        state = status[group]
        row = layout.row(align=True)
        button = row.operator('character_designer.bone_color_group', text=LABELS[group])
        button.group, button.rig_name = group, main.name
        swatches = row.row(align=True)
        # Static RGBA values are copied into these native widgets. Referencing
        # an operator button's RNA from a separate prop widget is unsafe: its
        # properties can be freed when the edit dialog is cancelled.
        for name, color in state['colors'].items():
            swatches.template_node_socket(color=(*color, 1.0))
        if state['mixed']:
            row.label(text='Mixed', icon='INFO')
        elif state['default']:
            row.label(text='Default / Scheme')
        elif not state['count']:
            row.label(text='Scheme')
    row = layout.row(align=True)
    button = row.operator('character_designer.bone_color_palette', text='Apply Palette', icon='BRUSH_DATA')
    button.action, button.rig_name = 'APPLY', main.name
    restore = row.row(align=True)
    restore.enabled = bool(saved and saved['enabled'])
    button = restore.operator('character_designer.bone_color_palette', text='Restore', icon='LOOP_BACK')
    button.action, button.rig_name = 'RESTORE', main.name
    if not saved:
        layout.label(text='Apply to save this character\'s five-color scheme.', icon='INFO')


BONE_COLOR_PALETTE_CLASSES = (CHARACTERDESIGNER_OT_bone_color_palette, CHARACTERDESIGNER_OT_bone_color_group)
