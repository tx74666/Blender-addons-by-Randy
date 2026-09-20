"""Small shared workflow UI; preview plans/batches are built only on demand."""
import json
import textwrap
import time

import bpy
import bmesh
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, FloatProperty, IntProperty, PointerProperty, StringProperty, EnumProperty
from bpy.types import Operator, PropertyGroup
from mathutils import Vector

from . import finger_workflow as workflow, finger_bank as bank, finger_definition as definition
from . import finger_targets as targets, finger_definition_ui as definition_ui
from . import finger_preview_proof, finger_preview_cache

_preview = None
_bend_preview = None
_refresh_request = None
_handles = []
_rebuilding = False


def hide(*, keep_refresh=False, keep_enabled=False):
    """Explicit Hide clears intent; cache invalidation must preserve it."""
    global _preview, _bend_preview, _refresh_request
    if not keep_enabled:
        obj = bank.active_object(bpy.context)
        if obj: obj.character_designer_finger_workflow.preview_enabled = False
    _preview = None
    _bend_preview = None
    if not keep_refresh:
        _refresh_request = None
        if bpy.app.timers.is_registered(_refresh): bpy.app.timers.unregister(_refresh)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()


def _changed(self, context):
    if context: refresh(context)


def refresh(context):
    """Coalesce monitoring updates without changing the artist's eye setting."""
    global _refresh_request
    if _rebuilding: return
    obj = bank.active_object(context)
    if not obj or not obj.character_designer_finger_workflow.preview_enabled:
        return
    active = obj.character_designer_finger_bank.active
    if _refresh_request and _refresh_request['obj'] == obj and _refresh_request['active'] == active:
        # Read live joint values when rebuilding. Later notifications must not
        # replace the pre-invalidation reference snapshot with an empty cache.
        return
    if _preview and _preview['kind'] == 'RINGS' and _preview['obj'] == obj and _preview['active'] == active:
        _refresh_request = _preview
        _refresh_request['reference_cache'] = finger_preview_cache.snapshot()
    else:
        _refresh_request = dict(obj=obj, rig=None, kind='RINGS', all=False, active=active)
    if not bpy.app.timers.is_registered(_refresh): bpy.app.timers.register(_refresh, first_interval=0.)


def flush_reference_refresh(obj):
    """Outside drawing only: share a queued full proof with the guide consumer."""
    if not _rebuilding and _refresh_request and _refresh_request['obj'] == obj:
        _refresh()


class CharacterDesignerFingerJointSettings(PropertyGroup):
    position: FloatProperty(name='Joint', default=.34, min=.01, max=.99, subtype='FACTOR', update=_changed)
    width: FloatProperty(name='Half Width', default=.025, min=.002, max=.15, subtype='FACTOR', update=_changed)
    inner_spacing: FloatProperty(name='Inner Spacing', default=1, min=.25, max=2, update=_changed)
    outer_spacing: FloatProperty(name='Outer Spacing', default=1, min=.25, max=2, update=_changed)
    root_weight: FloatProperty(name='Root Ring A', default=.8, min=0, max=1, update=_changed)
    center_weight: FloatProperty(name='Center Ring A', default=.5, min=0, max=1, update=_changed)
    tip_weight: FloatProperty(name='Tip Ring A', default=.2, min=0, max=1, update=_changed)


class CharacterDesignerFingerPairSettings(PropertyGroup):
    joints: CollectionProperty(type=CharacterDesignerFingerJointSettings)
    automatic_defaults: StringProperty(options={'HIDDEN'})
    custom_defaults: StringProperty(options={'HIDDEN'})
    active_joint: IntProperty(name='Joint Number', default=1, min=1)
    between: IntProperty(name='Between Joints', default=1, min=0, max=8, update=_changed)
    auto_weight: BoolProperty(name='Weight After Rings', description='Assign only the previewed joint regions; invalid bone/weight prerequisites abort the complete operation', default=True)
    recipe: StringProperty(options={'HIDDEN'})
    labels: StringProperty(options={'HIDDEN'})
    applied: BoolProperty(options={'HIDDEN'})


class CharacterDesignerFingerWorkflow(PropertyGroup):
    pairs: CollectionProperty(type=CharacterDesignerFingerPairSettings)
    source: PointerProperty(type=bpy.types.Mesh)
    signature: StringProperty(options={'HIDDEN'})
    bodies: StringProperty(options={'HIDDEN'})
    results: StringProperty(options={'HIDDEN'})
    status: StringProperty(options={'SKIP_SAVE'})
    preview_enabled: BoolProperty(name='Monitor Joint Rings', default=False,
        description='Keep joint monitoring enabled until explicitly hidden or released; invalid references are labelled, never used to authorize writes')


def show(context, *, status=None, previous=None):
    global _rebuilding
    _rebuilding = True
    try: return _show(context, status=status, previous=previous)
    finally: _rebuilding = False


def _show(context, *, status=None, previous=None):
    global _preview
    # Missing preparation is metadata, not a reason to copy/scan the edit mesh.
    # Other fingers may have a source even when the active pair has no recipe.
    source_obj, source_state, active_pair = workflow.settings(context)
    if not source_state.source or not active_pair.recipe:
        raise ValueError('Prepare this finger first.')
    context.view_layer.update()
    old_proof = previous.get('source_proof') if previous else None
    source_proof = finger_preview_proof.verify(source_obj, source_state, old_proof)
    entries = workflow.plans(context, preview=True)
    obj = entries[0][3]['obj']
    groups, labels = [], []
    rig = None
    try: _, rig = targets.owner(context)
    except ValueError: pass
    chain_cache, pair_labels, warnings = {}, {}, []
    b = obj.character_designer_finger_bank
    chain_key = (source_proof, _rig_stamp(rig), tuple(tuple(row) for row in bank.plane(obj)),
                 b.survey, tuple((s.name, s.guide.record, s.guide.flip_bend, s.bones, s.error) for s in b.slots))
    if source_proof is not None and previous and previous.get('chain_key') == chain_key:
        chain_cache = previous['chain_cache'].copy()
    candidates = None
    for pair, key, recipe, plan in entries:
        if plan.get('warning'): warnings.append(plan['warning'])
        bone_labels = {}
        if rig:
            try:
                if pair.name not in chain_cache:
                    if candidates is None: candidates = targets.index(rig)
                    chain_cache[pair.name] = targets.pair(obj, rig, pair.name, candidates)
                chain = chain_cache[pair.name][key]
                for i, joint in enumerate(pair.joints):
                    try:
                        a, b, offset = workflow.joint_bones(obj, rig, chain, recipe, joint)
                        bone_labels[key+':'+str(i)] = {'A': a, 'B': b, 'offset': offset}
                    except ValueError:
                        if pair.auto_weight:
                            warnings.append(f'Joint {i+1}: no unique nearby bone junction; automatic weights unavailable here.')
            except ValueError: pass  # Markers can be moved into a valid region in preview.
        pair_labels.setdefault(pair.name, {}).update(bone_labels)
        for ring in plan['rings']:
            color = ((1, .28, .22, 1) if ring['joint'] % 2 == 0 else (.1, .8, 1, 1)) if ring['flank'] == 0 and ring['joint'] >= 0 else (.7, .75, .8, .8)
            if ring.get('blocked'): color = (1, .55, .1, 1)
            points = ring['points']
            segments = [tuple(p) for i in range(len(points)) for p in (points[i], points[(i+1) % len(points)])]
            groups.append((segments, color))
            if ring['flank'] == 0 and ring['joint'] >= 0:
                label = str(ring['joint']+1)
                if ring.get('boundary'): label += '  outside editable range (boundary shown)'
                elif ring.get('blocked'): label += '  adjust spacing'
                match = bone_labels.get(key+':'+str(ring['joint']))
                if rig and not match: label += '  bone junction unmatched'
                if rig and match:
                    from .finger_bones import _bone_collection, _head
                    bone = _bone_collection(rig).get(match['B'])
                    if bone:
                        head = obj.matrix_world.inverted() @ rig.matrix_world @ _head(bone)
                        center = sum(points, Vector())/len(points)
                        radius = sum((p-center).length for p in points)/len(points)
                        marker = [tuple(head), tuple(center)]
                        for axis in (Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))):
                            marker.extend((tuple(head-axis*radius*.16), tuple(head+axis*radius*.16)))
                        groups.append((marker, (1, .5, .1, 1)))
                        if (center-head).length > radius*.02: label += f"  offset {(center-head).length:.3g}"
                labels.append((points[0].copy(), label, color))
    for pair, _, _, _ in entries:
        encoded = json.dumps(pair_labels[pair.name])
        if pair.labels != encoded: pair.labels = encoded
    message = '\n'.join(dict.fromkeys(warnings)) if status is None else status
    if source_state.status != message: source_state.status = message
    unchanged = old_proof is not None and old_proof == source_proof
    stamp = previous.get('geometry_stamp') if unchanged else None
    if stamp and stamp[-1] != _rig_stamp(rig): stamp = None
    _install(context, obj, groups, labels, rig=rig, geometry_stamp=stamp)
    _preview['source_proof'] = source_proof
    _preview['chain_key'], _preview['chain_cache'] = chain_key, chain_cache
    if unchanged:
        finger_preview_cache.restore_verified(context, obj, previous.get('reference_cache'))


def _rig_stamp(rig):
    if not rig: return ()
    from .finger_bones import _bone_collection, _head, _tail
    return (rig.as_pointer(), rig.data.as_pointer(), tuple(tuple(row) for row in rig.matrix_world), rig.mode,
            tuple((b.name, b.parent.name if b.parent else '', b.use_deform,
                   getattr(b, 'hide', False), getattr(b, 'lock', False),
                   tuple(_head(b)), tuple(_tail(b)), tuple(b.x_axis)) for b in _bone_collection(rig)))


def _geometry_stamp(obj, rig):
    """Read-only drawing evidence, not authorization to generate or write data.

    RNA settings can tag the mesh as geometry-updated. Keep the last frame only
    when its actual geometry is unchanged while the coalesced refresh is pending.
    Full attribute/weight/key validation still runs in show() and on every Apply.
    """
    matrix = lambda m: tuple(tuple(row) for row in m)
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        geometry = (tuple((tuple(v.co), v.hide) for v in bm.verts),
                    tuple((tuple(v.index for v in f.verts), f.hide) for f in bm.faces))
    else:
        geometry = (tuple(tuple(v.co) for v in obj.data.vertices),
                    tuple(tuple(f.vertices) for f in obj.data.polygons))
    return (obj.data.as_pointer(), obj.mode, matrix(obj.matrix_world),
            obj.active_shape_key_index, geometry, _rig_stamp(rig))


def _install(context, obj, groups, labels, *, rig=None, kind='RINGS', all_fingers=False, geometry_stamp=None):
    global _preview, _bend_preview, _refresh_request
    # Preparing/flushing Edit Mode snapshots can queue geometry notifications.
    # Consume them before publishing the newly validated overlay, otherwise
    # _dirty() can hide that overlay on the very next event-loop tick.
    if kind == 'RINGS': context.view_layer.update()
    if kind == 'RINGS': _refresh_request = None
    new = {'obj': obj, 'rig': rig, 'kind': kind, 'all': all_fingers, 'groups': groups, 'labels': labels,
                'batches': None, 'active': obj.character_designer_finger_bank.active,
                'selected': bank.selected_digits(obj.character_designer_finger_bank)}
    if kind == 'BEND' and _preview and _preview['kind'] == 'RINGS':
        _bend_preview = new
    else:
        if kind == 'RINGS' and _preview and _preview['kind'] == 'BEND': _bend_preview = _preview
        _preview = new
    if kind == 'RINGS':
        if not obj.character_designer_finger_workflow.preview_enabled:
            obj.character_designer_finger_workflow.preview_enabled = True
        _preview['geometry_stamp'] = geometry_stamp or _geometry_stamp(obj, rig)
    if not _handles and not bpy.app.background:
        _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW'))
        _handles.append(bpy.types.SpaceView3D.draw_handler_add(_labels, (), 'WINDOW', 'POST_PIXEL'))
    for area in context.screen.areas if context.screen else []:
        if area.type == 'VIEW_3D': area.tag_redraw()


def show_bend(context, *, all_fingers=False):
    """Resolve once on the button event; drawing only consumes cached lines."""
    from . import finger_flex as flex
    from .finger_bones import _head, _tail
    obj = bank.active_object(context)
    if not obj: raise ValueError('Capture Finger Basic Setup first.')
    b = obj.character_designer_finger_bank
    rig = None
    try: _, rig = targets.owner(context)
    except ValueError: pass
    candidates = targets.index(rig) if rig else {}
    digits = bank.DIGITS if hasattr(bank, 'DIGITS') else ('THUMB', 'INDEX', 'MIDDLE', 'RING', 'PINKY')
    digits = digits if all_fingers else bank.selected_digits(b)
    groups, labels = [], []
    inverse = obj.matrix_world.inverted()
    for digit in digits:
        slots = [s for s in b.slots if s.name.split('.')[0] == digit and s.guide.record and not s.error]
        if not slots: continue
        chains = targets.pair(obj, rig, digit, candidates) if rig else {}
        for slot in slots:
            data = definition.frame(bank.scoped(context, slot.guide), require_bend=True)
            forward, bend, arcs, axes, current = [], [], [], [], []
            rows = []
            for bone in chains.get(slot.name, ()):
                head, tail = rig.matrix_world @ _head(bone), rig.matrix_world @ _tail(bone)
                x = bone.x_axis if rig.mode == 'EDIT' else bone.matrix_local.to_3x3().col[0]
                rows.append((head, tail, (rig.matrix_world.to_3x3() @ x).normalized()))
            if not rows: rows = [(data['root'], data['tip'], None)]
            for head, tail, x in rows:
                t, inward, axis = flex.frame(tail-head, data['bend'])
                length = (tail-head).length
                flex.arrow(forward, head, t, length, inward)
                flex.arrow(bend, head, inward, length*.35, t)
                flex.arc(arcs, head, t, axis, length)
                axes += [head-axis*length*.2, head+axis*length*.2]
                if x is not None: current += [head-x*length*.15, head+x*length*.15]
            for points, color in zip((forward, bend, arcs, axes, current),
                    ((.1, .65, 1, 1), (1, .4, .05, 1), (.2, 1, .3, 1), (.8, .35, 1, 1), (1, .12, .12, 1))):
                if points: groups.append(([tuple(inverse @ Vector(p)) for p in points], color))
            labels.append((inverse @ data['root'], slot.name, (.1, .65, 1, 1)))
    if not groups: raise ValueError('No valid bend definitions to preview.')
    _install(context, obj, groups, labels, rig=rig, kind='BEND', all_fingers=all_fingers)


def _refresh():
    global _refresh_request, _rebuilding
    previous, _refresh_request = _refresh_request, None
    if not previous: return
    try:
        obj = bank.active_object(bpy.context)
        if obj != previous['obj'] or not obj.character_designer_finger_workflow.preview_enabled or (not previous['all'] and
                obj.character_designer_finger_bank.active != previous['active']):
            return
        if previous['kind'] == 'BEND': show_bend(bpy.context, all_fingers=previous['all'])
        else: show(bpy.context, previous=previous)
    except (ValueError, ReferenceError, RuntimeError, KeyError, IndexError) as exc:
        obj = previous['obj']
        _rebuilding = True
        try:
            state = obj.character_designer_finger_workflow
            message = 'Monitoring: '+str(exc)
            if state.status != message: state.status = message
            _mark_unavailable(previous, str(exc))
            # Like successful publication, drain updates caused by edit-mesh
            # reads and our status write while self-enqueue is suppressed.
            # Subsequent real edits still go through the normal full validator.
            bpy.context.view_layer.update()
        except ReferenceError: return
        finally: _rebuilding = False


def _mark_unavailable(previous, message):
    """Keep an explicitly labelled comparison reference, not a valid new plan."""
    if previous and previous is _preview and previous.get('groups'):
        _preview['stale'] = message
        _preview['batches'] = None


def joint_preview_enabled(context):
    obj = bank.active_object(context)
    return bool(obj and obj.character_designer_finger_workflow.preview_enabled)


def _valid(context=None, preview=None):
    p = preview if preview is not None else _preview
    if not p: return None
    context = context if context is not None else bpy.context
    obj = p['obj']
    if context.object not in (obj, p['rig']) or not obj.visible_get(): return None
    if not p['all'] and obj.character_designer_finger_bank.active != p['active']: return None
    if p['kind'] == 'BEND' and not p['all'] and bank.selected_digits(obj.character_designer_finger_bank) != p['selected']: return None
    return p


def joint_preview_visible(context):
    """Use the actual drawable ring overlay, not any cached bend/other preview."""
    try:
        p = _valid(context)
        return bool(p and p['kind'] == 'RINGS' and p['obj'] == bank.active_object(context))
    except ReferenceError:
        return False


def draw_joint_visibility(row, context):
    visible = joint_preview_enabled(context)
    row.operator('character_designer.finger_workflow', text='',
                 icon='HIDE_OFF' if visible else 'HIDE_ON', depress=visible).action = 'PREVIEW'


def _draw():
    for candidate in (_preview, _bend_preview):
        if candidate: _draw_preview(_valid(preview=candidate))


def _draw_preview(p):
    if not p: return
    import gpu
    from gpu_extras.batch import batch_for_shader
    if p['batches'] is None:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        p['batches'] = shader, [(batch_for_shader(shader, 'LINES', {'pos': points}), (1, .55, .1, .8) if p.get('stale') else color) for points, color in p['groups']]
    shader, batches = p['batches']
    depth, width, blend = gpu.state.depth_test_get(), gpu.state.line_width_get(), gpu.state.blend_get()
    try:
        gpu.state.depth_test_set('NONE'); gpu.state.line_width_set(2); gpu.state.blend_set('ALPHA')
        with gpu.matrix.push_pop():
            gpu.matrix.multiply_matrix(p['obj'].matrix_world)
            shader.bind()
            for batch, color in batches:
                shader.uniform_float('color', color)
                batch.draw(shader)
    finally:
        gpu.state.depth_test_set(depth); gpu.state.line_width_set(width); gpu.state.blend_set(blend)


def _labels():
    for candidate in (_preview, _bend_preview):
        if candidate: _draw_labels(_valid(preview=candidate))


def _draw_labels(p):
    if not p or not bpy.context.region_data: return
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    blf.size(0, 14)
    labels = p['labels']
    if p.get('stale') and labels:
        labels = [(point, 'Reference only (not current) · '+text, (1, .55, .1, 1)) for point, text, _ in labels]
    for point, text, color in labels:
        xy = location_3d_to_region_2d(bpy.context.region, bpy.context.region_data, p['obj'].matrix_world @ point)
        if xy:
            blf.position(0, xy.x+6, xy.y+6, 0); blf.color(0, *color); blf.draw(0, text)


class CHARACTERDESIGNER_OT_finger_workflow(Operator):
    bl_idname = 'character_designer.finger_workflow'
    bl_label = 'Finger Setup Workflow'
    bl_options = {'REGISTER', 'UNDO'}
    all_fingers: BoolProperty(default=False, options={'SKIP_SAVE'})
    automatic_defaults: BoolProperty(default=False, options={'SKIP_SAVE'})
    action: EnumProperty(items=[(k, label, '') for k, label in (
        ('ALL', 'Calibrate All'), ('SELECTED', 'Calibrate Selected'), ('FLIP', 'Reverse Bend'),
        ('PREPARE', 'Prepare Joints'), ('LOOP', 'Use Selected Center Loop'), ('APPLY', 'Generate / Update Rings'),
        ('SAVE_DEFAULTS', 'Save Joint Positions as Defaults'), ('RESET_DEFAULTS', 'Restore Joint Position Defaults'),
        ('WEIGHTS', 'Update Local Weights'), ('PREVIEW', 'Show / Hide Joint Preview'), ('BEND', 'Preview Bend'), ('RELEASE', 'Release Prepared Results'))])

    @classmethod
    def description(cls, context, props):
        if props.action == 'PREVIEW': return 'Show / hide the current finger pair joint rings; does not hide the Basic Setup reference axes or change geometry'
        if props.action == 'BEND': return 'Toggle bend axes for the highlighted finger pairs. Shift-click previews all prepared fingers. Does not change pose'
        if props.action == 'PREPARE': return 'Prepare the captured finger pair. Shift-click edits joint spacing and weight ratios'
        if props.action in {'SAVE_DEFAULTS', 'RESET_DEFAULTS'}:
            obj = bank.active_object(context)
            digit = obj.character_designer_finger_bank.active.split('.')[0] if obj else ''
            name = bank.detect.LABELS.get(digit, 'current finger')
            if props.action == 'SAVE_DEFAULTS':
                return f'Save the current joint positions as defaults for {name}, both hands, in this character file. Does not save widths or weights'
            pair = obj.character_designer_finger_workflow.pairs.get(digit) if obj else None
            try:
                values = ', '.join(f'{v:.3f}' for v in workflow.default_positions(pair)) if pair else ''
                profile = json.loads(pair.custom_defaults or pair.automatic_defaults) if pair else {}
                if profile.get('source') == 'TOPOLOGY':
                    values += '; topology estimate, not detected joints. '+profile.get('reason', '')
            except ValueError: values = 'Prepare again first'
            return f'Restore {name} joint positions ({values}). Shift-click restores automatic preparation defaults without erasing your saved preset. Widths, weights and mesh are unchanged'
        if props.action == 'RELEASE': return 'Keep mesh, weights and joint parameters; release all prepared recipes for this character so current edits can become a new source'
        return 'Use the current character Finger Setup; validate both sides before writing'

    def invoke(self, context, event):
        if self.action == 'PREPARE' and event.shift:
            return bpy.ops.character_designer.finger_joint_options('INVOKE_DEFAULT')
        self.automatic_defaults = bool(event.shift) if self.action == 'RESET_DEFAULTS' else False
        self.all_fingers = bool(event.shift)
        return self.execute(context)

    def execute(self, context):
        global _preview, _bend_preview
        obj = None
        was_joint_visible = joint_preview_enabled(context)
        try:
            obj = bank.active_object(context)
            if self.action in {'ALL', 'SELECTED'}:
                started = time.perf_counter()
                result = targets.calibrate(context, self.action == 'SELECTED')
                obj, _ = targets.owner(context)
                lines = [f"Calibrated: {', '.join(result['success']) or 'none'} ({time.perf_counter()-started:.3f}s)"]
                lines += [f'{kind.title()} {key}: {message}' for kind in ('skipped', 'failed') for key, message in result[kind].items()]
                obj.character_designer_finger_workflow.status = '\n'.join(lines)
                self.report({'INFO'} if not result['skipped'] and not result['failed'] else {'WARNING'}, ' | '.join(lines))
                refresh(context)
                return {'FINISHED'} if result['success'] else {'CANCELLED'}
            if obj is None: raise ValueError('Capture Finger Basic Setup first.')
            s = obj.character_designer_finger_workflow
            if self.action == 'FLIP':
                b = obj.character_designer_finger_bank
                source = b.slots[b.active].guide
                source.flip_bend = not source.flip_bend
                source.confirmed = True
                for slot in b.slots:
                    if slot.name.split('.')[0] == b.active.split('.')[0] and slot.guide.record:
                        slot.guide.flip_bend, slot.guide.confirmed = source.flip_bend, True
                definition_ui.show()
                refresh(context)
            elif self.action == 'PREPARE':
                pair = workflow.prepare(context)
                # An explicitly unbound character can prepare topology; do not
                # pretend automatic weights succeeded or implicitly bind it.
                from . import character_setup
                if character_setup.preferred_rig(context) is None: pair.auto_weight = False
                show(context)
            elif self.action == 'LOOP': workflow.capture_joint(context); show(context)
            elif self.action in {'SAVE_DEFAULTS', 'RESET_DEFAULTS'}:
                values = (workflow.save_defaults(context) if self.action == 'SAVE_DEFAULTS' else
                          workflow.restore_defaults(context, automatic=self.automatic_defaults))
                name = bank.detect.LABELS[obj.character_designer_finger_bank.active.split('.')[0]]
                verb = 'Saved defaults' if self.action == 'SAVE_DEFAULTS' else 'Restored positions'
                self.report({'INFO'}, f'{name}: {verb} ({", ".join(f"{v:.3f}" for v in values)}).')
                s.status = ''
                refresh(context)
                return {'FINISHED'}
            elif self.action == 'PREVIEW':
                if joint_preview_enabled(context): hide()
                else:
                    s.preview_enabled = True
                    show(context)
            elif self.action == 'BEND':
                existing = _bend_preview or (_preview if _preview and _preview['kind'] == 'BEND' else None)
                if existing and existing['all'] == self.all_fingers:
                    if existing is _bend_preview: _bend_preview = None
                    else: _preview = None
                else: show_bend(context, all_fingers=self.all_fingers)
            elif self.action == 'RELEASE': hide(); workflow.release(context)
            else:
                result = workflow.apply(context, weights_only=self.action == 'WEIGHTS')
                definition_ui.redraw()
                s.status = f"{result['added']} vertices added; {result['weighted']} local vertices weighted ({result['seconds']:.3f}s)."
                try: show(context, status=s.status)
                except (ValueError, RuntimeError, KeyError, ReferenceError) as exc:
                    _mark_unavailable(_preview, str(exc))
                    s.status += ' Preview unavailable: '+str(exc)
                    # The data commit succeeded. Never discard its undo step
                    # just because the non-mutating overlay could not rebuild.
                self.report({'INFO'}, s.status)
                return {'FINISHED'}
            if self.action not in {'PREPARE', 'LOOP', 'PREVIEW'}: s.status = ''
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, IndexError, ReferenceError) as exc:
            if obj: obj.character_designer_finger_workflow.status = str(exc)
            # Failure is not a user request to stop monitoring. Keep a labelled
            # last reference if source validation fails; write guards stay strict.
            if was_joint_visible:
                try: show(context, status=str(exc))
                except (ValueError, RuntimeError, KeyError, IndexError, ReferenceError): _mark_unavailable(_preview, str(exc))
            elif self.action == 'PREVIEW' and obj:
                obj.character_designer_finger_workflow.status = 'Monitoring unavailable: '+str(exc)
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_finger_joint_options(Operator):
    bl_idname = 'character_designer.finger_joint_options'
    bl_label = 'Joint Spacing and Local Weights'
    def invoke(self, context, event):
        try: _, _, pair = workflow.settings(context)
        except ValueError as exc:
            self.report({'WARNING'}, str(exc)); return {'CANCELLED'}
        if not pair.joints: return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=400)
    def execute(self, context): return {'FINISHED'}
    def draw(self, context):
        _, _, pair = workflow.settings(context)
        self.layout.prop(pair, 'active_joint')
        joint = pair.joints[min(pair.active_joint-1, len(pair.joints)-1)]
        for field in ('width', 'inner_spacing', 'outer_spacing', 'root_weight', 'center_weight', 'tip_weight'):
            self.layout.prop(joint, field)
        self.layout.prop(pair, 'between')
        self.layout.label(text='A / B share the remaining deform-weight budget.')
        for key, label in json.loads(pair.labels or '{}').items():
            if key.endswith(':'+str(min(pair.active_joint-1, len(pair.joints)-1))):
                self.layout.label(text=key+': '+label['A']+' / '+label['B'])


def draw_controls(layout, context):
    obj = bank.active_object(context)
    if not obj: return
    s = obj.character_designer_finger_workflow
    box = layout.box()
    box.label(text='Bone Roll', icon='BONE_DATA')
    row = box.row(align=True)
    row.operator('character_designer.finger_workflow', text='Calibrate All').action = 'ALL'
    row.operator('character_designer.finger_workflow', text='Calibrate Selected').action = 'SELECTED'
    row = box.row(align=True)
    row.operator('character_designer.finger_workflow', text='Preview Bend').action = 'BEND'
    row.operator('character_designer.finger_workflow', text='', icon='ARROW_LEFTRIGHT').action = 'FLIP'
    if context.mode == 'EDIT_MESH':
        box = layout.box()
        digit = obj.character_designer_finger_bank.active.split('.')[0]
        name = bank.detect.LABELS.get(digit, '')
        box.label(text=f'Joint Topology & Weights ({name})', icon='MESH_GRID')
        row = box.row(align=True)
        row.operator('character_designer.finger_workflow', text='Prepare Joints').action = 'PREPARE'
        pair = s.pairs.get(digit)
        if pair and pair.recipe:
            row.operator('character_designer.finger_workflow', text='', icon='LOOP_BACK').action = 'RESET_DEFAULTS'
            row.operator('character_designer.finger_workflow', text='', icon='FILE_TICK').action = 'SAVE_DEFAULTS'
            row.operator('character_designer.finger_workflow', text='', icon='X').action = 'RELEASE'
            for i, j in enumerate(pair.joints): box.prop(j, 'position', text=f'Joint {i+1}', slider=True)
            row = box.row(align=True)
            row.prop(pair, 'active_joint', text='Joint')
            row.operator('character_designer.finger_workflow', text='Use Selected Loop').action = 'LOOP'
            row = box.row(align=True)
            row.prop(pair, 'auto_weight')
            draw_joint_visibility(row, context)
            box.operator('character_designer.finger_workflow', text='Generate / Update Rings').action = 'APPLY'
            if pair.applied: box.operator('character_designer.finger_workflow', text='Update Local Weights').action = 'WEIGHTS'
            if not pair.auto_weight: box.label(text='Topology only; weights will not be changed.')
    if s.status:
        for line in s.status.splitlines():
            for part in textwrap.wrap(line, 45): layout.label(text=part)


@persistent
def _invalidate(*args): hide(keep_enabled=True)


@persistent
def _resume(*args): refresh(bpy.context)


@persistent
def _dirty(scene, depsgraph):
    global _bend_preview
    if _rebuilding: return
    if _preview:
        owners = {_preview['obj'], _preview['obj'].data}
        if _preview['rig']: owners.update((_preview['rig'], _preview['rig'].data))
        if any((u.is_updated_geometry or u.is_updated_transform) and u.id.original in owners for u in depsgraph.updates):
            if _preview['kind'] == 'RINGS':
                refresh(bpy.context)
                try:
                    if _geometry_stamp(_preview['obj'], _preview['rig']) != _preview.get('geometry_stamp'):
                        _mark_unavailable(_preview, 'Source changed; checking current reference.')
                except (ReferenceError, RuntimeError): pass
                _bend_preview = None
            else: hide(keep_enabled=True)
    elif _refresh_request is None:
        # A mode/context change may have temporarily had no drawable frame.
        # Only recorded monitoring intent can schedule a new one.
        obj = bank.active_object(bpy.context)
        if obj and obj.character_designer_finger_workflow.preview_enabled:
            owners = {obj, obj.data}
            if any(u.id.original in owners for u in depsgraph.updates): refresh(bpy.context)


CLASSES = (CharacterDesignerFingerJointSettings, CharacterDesignerFingerPairSettings, CharacterDesignerFingerWorkflow,
           CHARACTERDESIGNER_OT_finger_workflow, CHARACTERDESIGNER_OT_finger_joint_options)


def register_runtime():
    bpy.types.Object.character_designer_finger_workflow = PointerProperty(type=CharacterDesignerFingerWorkflow)
    for group in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _invalidate not in group: group.append(_invalidate)
    for group in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _resume not in group: group.append(_resume)
    if _dirty not in bpy.app.handlers.depsgraph_update_post: bpy.app.handlers.depsgraph_update_post.append(_dirty)


def unregister_runtime():
    hide(keep_enabled=True)
    if bpy.app.timers.is_registered(_refresh): bpy.app.timers.unregister(_refresh)
    for h in _handles: bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
    _handles.clear()
    for group in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _invalidate in group: group.remove(_invalidate)
    for group in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _resume in group: group.remove(_resume)
    if _dirty in bpy.app.handlers.depsgraph_update_post: bpy.app.handlers.depsgraph_update_post.remove(_dirty)
    if hasattr(bpy.types.Object, 'character_designer_finger_workflow'): del bpy.types.Object.character_designer_finger_workflow
