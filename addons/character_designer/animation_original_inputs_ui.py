"""Explicit original-Rig input controls; drawing never reads preparation files."""

import hashlib
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator


PAIR_KEY = 'character_designer_original_inputs_pair_v1'
_ERRORS = (ValueError, RuntimeError, OSError, ReferenceError, AttributeError, KeyError,
           TypeError, ImportError)


def _backend():
    from . import unity_animation_original_inputs
    return unity_animation_original_inputs


def _rig(context):
    try:
        scene = getattr(context, 'scene', None)
        setup = getattr(scene, 'character_designer_setup', None)
        rig = getattr(setup, 'rig', None)
        data = getattr(rig, 'data', None)
        objects = getattr(scene, 'objects', None)
        if (scene is None or rig is None or data is None or objects is None
                or getattr(rig, 'type', None) != 'ARMATURE' or not hasattr(rig, 'get')
                or rig.library is not None or data.library is not None
                or objects.get(rig.name) != rig
                or not rig.get('character_designer_worklist_main_session_v1')):
            return None
        return rig
    except (ReferenceError, AttributeError):
        return None


def has_original_inputs(rig):
    return bool(rig is not None and hasattr(rig, 'get') and rig.get(PAIR_KEY) is not None)


def _display(context):
    rig = _rig(context)
    if not has_original_inputs(rig):
        return None
    try:
        value = _backend().display_state(rig)
        return value if (value is not None and value['rig'] == rig
                         and value['scene'] == context.scene) else None
    except _ERRORS:
        return None


def original_inputs_live(context):
    """Cheap authenticated cached state only; a closed saved pair stays idle."""
    return _display(context) is not None


def _idle(context):
    from .animation_worklist_ui import _idle as worklist_idle
    return worklist_idle(context)


def _redraw(context):
    screen = getattr(context, 'screen', None)
    if screen is not None:
        for area in screen.areas:
            area.tag_redraw()


def _feedback(operator, context, error):
    operator.report({'ERROR'}, str(error))
    _redraw(context)
    return {'CANCELLED'}


def _owned_rig(context):
    rig = _rig(context)
    if rig is None or not _idle(context):
        raise ValueError("Use this scene's adopted Main Rig after the current animation operation finishes.")
    return rig


def _json_pin(filepath):
    if not isinstance(filepath, str) or not filepath.strip():
        raise ValueError('Choose the actual JSON file; no preparation or certificate was guessed.')
    path = Path(bpy.path.abspath(filepath)).expanduser().resolve()
    if path.suffix.lower() != '.json' or not path.is_file():
        raise ValueError('Choose an existing JSON file.')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return str(path), digest.hexdigest()


def _run(operator, context, operation, *args):
    try:
        rig = _owned_rig(context)
        backend = _backend()
        if not has_original_inputs(rig):
            raise ValueError('Attach the authenticated original input controls before continuing.')
        focus_gun = operation == 'edit_controls' and getattr(operator, 'focus_gun_motion', False)
        if focus_gun:
            from . import animation_composition as composition, animation_composition_weapon as weapon
            if context.active_object != rig or rig.mode != 'POSE':
                raise ValueError('Select the Main Rig in Pose Mode to edit Gun Motion.')
            gun, _objects = weapon.validate_owned(rig)
            if not isinstance(gun.get('rigging_session_owner'), str):
                raise ValueError('Gun Motion needs the current owned Rigging weapon preview.')
            session = backend.current(rig)
            if session is None or gun.get('rigging_session_owner') != session['owner']:
                raise ValueError('The displayed gun belongs to another input preview.')
            matches = [(row, binding) for row, binding in zip(session['native_bindings'], session['capture']['bindings'])
                       if row['native_side'] == 'R']
            if len(matches) != 1 or not matches[0][1]['constraintWeight'] or not matches[0][1]['targetPositionWeight']:
                raise ValueError('Gun Motion needs its existing enabled Right Target position input.')
            target = rig.pose.bones[matches[0][0]['target']]
            if target.bone.collections and not any(c.is_visible_effectively for c in target.bone.collections):
                raise ValueError('Show the Right Target collection in Bone Display, then choose Gun Motion.')
            owners = [(composition._pose_selection_owner(pb), pb == target) for pb in rig.pose.bones]
        getattr(backend, operation)(context, rig, *args)
        if focus_gun:
            try:
                for owner, selected in owners:
                    owner.select = selected
                rig.data.bones.active = target.bone
            except _ERRORS as error:
                operator.report({'WARNING'}, 'Inputs are open. Gun Motion selection failed: ' + str(error))
                _redraw(context)
                return {'FINISHED'}  # The successful edit transaction still needs its normal Undo.
        operator.report({'INFO'}, 'Gun Motion selected. Move the control, then Key Gun Motion.' if focus_gun else
            {'edit_controls': 'Editing Target/Hint inputs.',
            'key_current': 'Saved keys to the editable input Action.',
            'view_body': 'Viewing the original Body reference; its Action remains read-only.',
            'close_owned': 'Closed input preview; the saved input draft is retained.'}[operation])
        _redraw(context)
        return {'FINISHED'}
    except _ERRORS as error:
        return _feedback(operator, context, error)


class CHARACTERDESIGNER_OT_original_inputs_attach(Operator):
    bl_idname = 'character_designer.original_inputs_attach'
    bl_label = 'Attach Controls to Current Rig'
    bl_description = ('Choose an actual two-arm preparation report and clock certificate; '
                      'keep this original Rig, Body Actions and animation list')
    bl_options = {'REGISTER'}
    filepath: StringProperty(name='Preparation Report', subtype='FILE_PATH')
    clock_certificate_file: StringProperty(name='Clock Certificate', subtype='FILE_PATH')
    filter_glob: StringProperty(default='report.json;*.json', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None and _idle(context)

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, _context):
        self.layout.prop(self, 'clock_certificate_file')

    def execute(self, context):
        try:
            rig = _owned_rig(context)
            prepared, prepared_sha = _json_pin(self.filepath)
            clock, clock_sha = _json_pin(self.clock_certificate_file)
            _backend().import_inputs(context, rig, prepared, prepared_sha, clock, clock_sha)
            self.report({'INFO'}, 'Attached input controls to "' + rig.name + '"; the original Body is retained.')
            _redraw(context)
            return {'FINISHED'}
        except _ERRORS as error:
            return _feedback(self, context, error)


class CHARACTERDESIGNER_OT_original_inputs_edit(Operator):
    bl_idname = 'character_designer.original_inputs_edit'
    bl_label = 'Edit Target/Hint'
    bl_description = 'Edit the authenticated Custom input Action; the Source input Action is read-only'
    bl_options = {'REGISTER', 'UNDO'}
    focus_gun_motion: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return has_original_inputs(_rig(context)) and _idle(context)

    def execute(self, context):
        return _run(self, context, 'edit_controls')


class CHARACTERDESIGNER_OT_original_inputs_save(Operator):
    bl_idname = 'character_designer.original_inputs_save'
    bl_label = 'Save Input Keys'
    bl_description = 'Key the current Target/Hint inputs in Custom; do not export Body animation'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        value = _display(context)
        return bool(value is not None and value['enabled'] and value['tweak_enabled']
                    and value['edit_role'] == 'RIGGING' and _idle(context))

    def execute(self, context):
        return _run(self, context, 'key_current')


class CHARACTERDESIGNER_OT_original_inputs_body_view(Operator):
    bl_idname = 'character_designer.original_inputs_body_view'
    bl_label = 'View Body Reference'
    bl_description = 'View the original Source or Custom Body Action without editing its keys'
    bl_options = {'REGISTER'}
    side: EnumProperty(items=(('SOURCE', 'Source Body', 'View the original Source Body'),
                             ('CUSTOM', 'Custom Body', 'View the retained original Custom Body')),
                       default='SOURCE')

    @classmethod
    def poll(cls, context):
        return has_original_inputs(_rig(context)) and _idle(context)

    def execute(self, context):
        try:
            rig = _owned_rig(context)
            backend = _backend()
            if not has_original_inputs(rig):
                raise ValueError('Attach the original input controls before choosing a Body reference.')
            backend.view_body(context, rig, side=self.side)
            self.report({'INFO'}, 'Viewing original ' + self.side.title() + ' Body; no Body keys are written.')
            _redraw(context)
            return {'FINISHED'}
        except _ERRORS as error:
            return _feedback(self, context, error)


class CHARACTERDESIGNER_OT_original_inputs_publish(Operator):
    bl_idname = 'character_designer.original_inputs_publish'
    bl_label = 'Publish Input Return'
    bl_description = 'Save the Target/Hint Return and its publication receipt; do not export Body animation'
    bl_options = {'REGISTER'}
    filepath: StringProperty(name='Input Return', subtype='FILE_PATH', default='rigging-inputs.json')
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return has_original_inputs(_rig(context)) and _idle(context)

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            rig = _owned_rig(context)
            backend = _backend()
            if not has_original_inputs(rig):
                raise ValueError('Attach the authenticated input controls before publishing.')
            if not self.filepath.strip():
                raise ValueError('Choose a new input Return JSON destination.')
            path = Path(bpy.path.abspath(self.filepath)).expanduser().resolve()
            if path.suffix.lower() != '.json':
                raise ValueError('Choose a JSON destination for the input Return.')
            result = backend.publish_inputs(context, rig, str(path))
            if (not isinstance(result, dict) or set(result) != {'return', 'publication'}
                    or any(not isinstance(result[key], dict)
                           or set(result[key]) != {'path', 'sha256', 'bytes'} for key in result)):
                raise RuntimeError('The input publisher did not return its two actual saved file pins.')
            self.report({'INFO'}, 'Published inputs: ' + result['return']['path']
                        + '; publication SHA256 ' + result['publication']['sha256'])
            _redraw(context)
            return {'FINISHED'}
        except _ERRORS as error:
            return _feedback(self, context, error)


class CHARACTERDESIGNER_OT_original_inputs_close(Operator):
    bl_idname = 'character_designer.original_inputs_close'
    bl_label = 'Close Inputs'
    bl_description = 'Restore the original Body/context and retain the committed input draft'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return has_original_inputs(_rig(context)) and _idle(context)

    def execute(self, context):
        return _run(self, context, 'close_owned')


def draw_original_inputs(layout, context):
    rig = _rig(context)
    if rig is None:
        return
    box = layout.box()
    box.label(text='Original Rigging Inputs', icon='ARMATURE_DATA')
    box.label(text='Rig: ' + rig.name, icon='ARMATURE_DATA')
    attach = box.row()
    attach.operator_context = 'INVOKE_DEFAULT'
    attach.enabled = _idle(context)
    attach.operator('character_designer.original_inputs_attach', icon='IMPORT')
    value = _display(context)
    if value is None:
        if has_original_inputs(rig):
            box.label(text='Saved inputs await authenticated reload.', icon='INFO')
            row = box.row()
            row.enabled = _idle(context)
            row.operator('character_designer.original_inputs_edit', text='Resume Inputs').focus_gun_motion = False
            row.operator('character_designer.original_inputs_close')
        return
    box.label(text='Source inputs (read-only): ' + value['base_action'].name)
    box.label(text='Custom inputs: ' + value['input_action'].name)
    reference = box.row(align=True)
    reference.enabled = _idle(context)
    for side, label in (('SOURCE', 'View Source Body'), ('CUSTOM', 'View Custom Body')):
        op = reference.operator('character_designer.original_inputs_body_view', text=label,
                                depress=value['legacy_side'] == side)
        op.side = side
    if rig.get('character_designer_composition_weapon_v1') is not None:
        gun = box.row(align=True)
        gun.enabled = _idle(context)
        gun.operator('character_designer.original_inputs_edit', text='Gun Motion').focus_gun_motion = True
        key = gun.row()
        key.enabled = bool(value['enabled'] and value['tweak_enabled'] and value['edit_role'] == 'RIGGING')
        key.operator('character_designer.original_inputs_save', text='Key Gun Motion')
    edit = box.row(align=True)
    edit.enabled = _idle(context)
    edit.operator('character_designer.original_inputs_edit').focus_gun_motion = False
    save = edit.row()
    save.enabled = bool(value['enabled'] and value['tweak_enabled'] and value['edit_role'] == 'RIGGING')
    save.operator('character_designer.original_inputs_save')
    publish = box.row()
    publish.enabled = _idle(context)
    publish.operator_context = 'INVOKE_DEFAULT'
    publish.operator('character_designer.original_inputs_publish', icon='EXPORT')
    box.operator('character_designer.original_inputs_close')


ORIGINAL_INPUT_CLASSES = (
    CHARACTERDESIGNER_OT_original_inputs_attach,
    CHARACTERDESIGNER_OT_original_inputs_edit,
    CHARACTERDESIGNER_OT_original_inputs_save,
    CHARACTERDESIGNER_OT_original_inputs_body_view,
    CHARACTERDESIGNER_OT_original_inputs_publish,
    CHARACTERDESIGNER_OT_original_inputs_close,
)
