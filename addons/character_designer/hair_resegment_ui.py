"""Explicit Hair versions; no update callbacks or playback services."""
import json

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty

from . import hair_resegment_selection as selection


def service():
    from . import hair_resegment
    return hair_resegment


def current_source(context):
    obj = context.active_object
    if obj is not None:
        candidate = obj if obj.type == 'MESH' else obj.get('character_designer_hair_resegment_mesh')
        if candidate is not None and service().is_version(candidate):
            return candidate
        # An explicit active original Hair outranks a previously remembered
        # visible version from another family. Let hair_bones resolve it.
        from . import hair_bones_rig as hair
        if obj.type=='MESH' and hair.RECORD_KEY in obj:
            return None
        if obj.type=='ARMATURE':
            active=getattr(context,'active_pose_bone',None)
            if active is not None and active.bone.get(hair.SOURCE_KEY) is not None:
                return None
    state = getattr(context.window_manager, 'character_designer_hair_bones', None)
    candidate = state.source if state is not None else None
    if candidate is not None and service().is_version(candidate) and candidate.visible_get():
        return candidate
    return None


def is_version(source):
    return source is not None and service().is_version(source)


def source_before_switch(context, source):
    from . import hair_bones_variants as variants
    original = service().root_source(source)
    if is_version(source) or any(item.get(variants.MODE_KEY) == 'RESEGMENTED'
                                 for item in variants.variants_for(original)):
        service().guard_switch(context, source)
    return original


def _refresh_after_commit(operator, context, source, checked_signatures=()):
    """UI cache failure must not discard a completed native Undo operation."""
    browser_ui = None
    try:
        from . import hair_strand_list_ui as browser_ui
        browser_ui.refresh_ui(context, source, checked_signatures=checked_signatures)
    except Exception as error:
        # The data transaction already succeeded. A damaged UI cache must not
        # turn it into CANCELLED and remove its native Undo entry.
        try:
            if browser_ui is not None:
                browser_ui.invalidate_ui(context)
        except Exception:
            pass
        operator.report({'WARNING'}, 'Hair changed; refresh the strand list. ' + str(error))


class CHARACTERDESIGNER_OT_hair_resegment(bpy.types.Operator):
    bl_idname = 'character_designer.hair_resegment'
    bl_label = 'Change Hair Segments'
    bl_description = 'Change the selected Hair strands together; retain all other strands and the original version'
    bl_options = {'REGISTER', 'UNDO'}

    bone_count: IntProperty(name='New Bones per Strand', default=6, min=1, max=12)
    scope: EnumProperty(name='Strands', items=(
        ('SELECTED', 'Selected Strands', 'Change only strands containing the selected Hair bones'),
        ('ALL', 'All Strands in This Mesh', 'Explicitly change every bound strand in this Hair mesh'),
    ), default='SELECTED', options={'SKIP_SAVE'})
    selection_pin: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.mode in {'OBJECT', 'POSE', 'EDIT_MESH'}

    @staticmethod
    def _fingerprint(record):
        return dict(source_id=record['source_id'], topology=record['topology'],
            chains=[dict(signature=chain['signature'], bones=list(chain['bones']), count=chain['count'])
                    for chain in record['chains']])

    @staticmethod
    def _identity(obj):
        # Operator history stores plain identity strings, never native ID users.
        return dict(name=obj.name, session_uid=str(obj.session_uid))

    @staticmethod
    def _resolve_identity(identity):
        if (not isinstance(identity, dict) or type(identity.get('name')) is not str
                or not identity['name'] or type(identity.get('session_uid')) is not str
                or not identity['session_uid']):
            raise ValueError('The saved Hair identity is unreadable; choose its strands again.')
        obj = bpy.data.objects.get(identity['name'])
        if obj is None or str(obj.session_uid) != identity['session_uid']:
            raise ValueError('The Hair object was renamed or replaced; choose its strands again.')
        return obj

    def _capture(self, context):
        from . import hair_bones
        source = hair_bones._source(context)
        record, rig = selection.binding(source)
        chosen, error = (), ''
        try:
            chosen = selection.selected(context, source)
        except (ValueError, RuntimeError) as exc:
            error = str(exc)
        self.selection_pin = json.dumps(dict(source=self._identity(source), rig=self._identity(rig),
            binding=self._fingerprint(record),
            selected=list(chosen), selected_error=error), ensure_ascii=False, separators=(',', ':'))

    def _targets(self, context):
        from . import hair_bones
        if not self.selection_pin:
            self._capture(context)
        pin = json.loads(self.selection_pin)
        source = self._resolve_identity(pin['source'])
        if hair_bones._source(context) is not source:
            raise ValueError('The active Hair changed while this dialog was open; choose its strands again.')
        record, rig = selection.binding(source)
        if rig is not self._resolve_identity(pin['rig']) or self._fingerprint(record) != pin['binding']:
            raise ValueError('The Hair binding changed while this dialog was open; choose its strands again.')
        if self.scope == 'ALL':
            return source, None, selection.summarize(record['chains'])
        if self.scope != 'SELECTED':
            raise ValueError('Choose Selected Strands or explicitly choose All Strands.')
        if pin['selected_error'] or not pin['selected']:
            raise ValueError(pin['selected_error'] or 'Select the Hair strands to change in Pose Mode.')
        current = selection.selected(context, source)
        if list(current) != pin['selected']:
            raise ValueError('The selected Hair strands changed while this dialog was open; open it again.')
        return source, current, selection.summarize(record['chains'], current)

    def invoke(self, context, event):
        try:
            self._capture(context)
            if not self.is_property_set('bone_count'):
                settings = context.window_manager.character_designer_hair_bones
                self.bone_count = settings.bone_count
        except (ValueError, RuntimeError, KeyError, TypeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'scope')
        try:
            pin = json.loads(self.selection_pin)
            if self.scope == 'SELECTED' and pin['selected_error']:
                layout.label(text=pin['selected_error'], icon='ERROR')
                summary = None
            else:
                summary = selection.summarize(pin['binding']['chains'],
                    pin['selected'] if self.scope == 'SELECTED' else None)
            layout.label(text='Hair: ' + pin['source']['name'], icon='OUTLINER_OB_MESH')
            if summary:
                label = 'selected' if self.scope == 'SELECTED' else 'all bound'
                layout.label(text=f"{summary['strands']} {label} strands")
                for chain in summary['chains'][:8]:
                    layout.label(text=f"{chain['bones'][0]}: {chain['count']} → {self.bone_count} bones")
                if len(summary['chains']) > 8:
                    layout.label(text=f"… and {len(summary['chains']) - 8} more strands")
        except (ValueError, RuntimeError, KeyError, TypeError):
            layout.label(text='Choose the Hair strands again.', icon='ERROR')
        layout.prop(self, 'bone_count')
        if self.scope == 'SELECTED':
            layout.label(text='Only these strands change; other strands stay as they are.')
            layout.label(text='Select bones on both sides to change a pair together.')
        layout.label(text='Retains the original; new bending and motion are sampled.')

    def execute(self, context):
        try:
            source, signatures, summary = self._targets(context)
            result = service().create(context, source, bone_count=self.bone_count,
                                      strand_signatures=signatures)
            settings = context.window_manager.character_designer_hair_bones
            settings.source = result['mesh']
            settings.bone_count = self.bone_count
            settings.last_message = (f"Changed {summary['strands']} Hair strands to {self.bone_count} bones; "
                                     'other strands and the original retained.')
            _refresh_after_commit(self, context, result['mesh'], result['selected_signatures'])
            self.report({'INFO'}, settings.last_message)
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_hair_show_version(bpy.types.Operator):
    bl_idname = 'character_designer.hair_show_version'
    bl_label = 'Show Hair Version'
    bl_description = 'Switch to a retained hair version without changing the original'
    bl_options = {'REGISTER', 'UNDO'}

    collection_name: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        from . import hair_bones, hair_bones_variants as variants
        try:
            source = hair_bones._source(context)
            original = source_before_switch(context, source)
            collection = bpy.data.collections.get(self.collection_name)
            if collection is None or collection not in variants.variants_for(original):
                raise ValueError('This Hair version is no longer available.')
            mesh = service().show_version(context, source, collection)
            context.window_manager.character_designer_hair_bones.source = mesh
            _refresh_after_commit(self, context, mesh)
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_hair_show_original_version(bpy.types.Operator):
    bl_idname = 'character_designer.hair_show_original_version'
    bl_label = 'Show Original Hair'
    bl_description = 'Return to the retained original binding in Object Mode'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from . import hair_bones
        try:
            original = service().show_original(context, hair_bones._source(context))
            context.window_manager.character_designer_hair_bones.source = original
            _refresh_after_commit(self, context, original)
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


def draw_versions(layout, context, source, bound):
    from . import hair_bones_variants as variants
    if bound:
        button = layout.operator('character_designer.hair_resegment',
            text='Change Selected Strands' if context.mode == 'POSE' else 'Change Hair Segments',
            icon='BONE_DATA')
        button.scope = 'SELECTED'
        if context.mode == 'POSE':
            try:
                signatures = selection.selected(context, source)
                record, unused_rig = selection.binding(source)
                summary = selection.summarize(record['chains'], signatures)
                counts = ', '.join(str(count) for count in summary['current_counts'])
                layout.label(text=f"{summary['strands']} selected strands · {counts} bones")
            except (ValueError, RuntimeError) as error:
                layout.label(text=str(error), icon='INFO')
    if source is None:
        return
    original = service().root_source(source)
    if any(c.get(variants.MODE_KEY) == 'RESEGMENTED' for c in variants.variants_for(original)):
        layout.operator('character_designer.hair_show_original_version', text='Original Hair',
                        icon='OUTLINER_OB_MESH', depress=original.visible_get())
    for collection in variants.variants_for(original):
        if collection.get(variants.MODE_KEY) != 'RESEGMENTED':
            continue
        row = layout.row(align=True)
        item = row.operator('character_designer.hair_show_version',
            text=collection.name, depress=not collection.hide_viewport, icon='ARMATURE_DATA')
        item.collection_name = collection.name


CLASSES = (CHARACTERDESIGNER_OT_hair_resegment, CHARACTERDESIGNER_OT_hair_show_version,
           CHARACTERDESIGNER_OT_hair_show_original_version)
