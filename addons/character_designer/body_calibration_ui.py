"""One body workflow: calibration checklist, Generate, then animation controls."""
import bpy
import textwrap
import math
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, PointerProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup, Operator
from . import body_calibration as service, body_calibration_display as display

def _redraw(_self, context):
    if context and context.window_manager:
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D': area.tag_redraw()

_AXES = [('X','+X Right',''),('NX','-X Left',''),('Y','+Y Back',''),('NY','-Y Forward',''),
         ('Z','+Z Up',''),('NZ','-Z Down',''),('VECTOR','Custom vector','Armature-local XYZ')]


class CharacterDesignerBodyCalibration(PropertyGroup):
    tab: EnumProperty(items=[('SETUP', 'Setup', ''), ('CONTROLS', 'Controls', '')])
    part: EnumProperty(items=[(p, p.title(), '', i) for i,p in enumerate(service.STORED_PARTS)],update=_redraw)
    preset: EnumProperty(items=[('DEFAULT', 'Default', 'Character local: elbows back, knees forward; wrists follow forearms'),
                               ('CUSTOM', 'Custom', 'Candidate settings only; use Apply to change Rest')],update=_redraw)
    show_directions: BoolProperty(name='Preview Directions', description='Preview the arm and wrist axes together. Wrist Roll follows the candidate forearm; no bones are added or moved by drawing', default=False, update=_redraw)
    show_details: BoolProperty(name='Details', default=False, update=_redraw)
    last_error: StringProperty(options={'HIDDEN','SKIP_SAVE'})
    last_error_signature: StringProperty(options={'HIDDEN','SKIP_SAVE'})
    arm_axis: EnumProperty(name='Elbow direction',items=_AXES,default='Y',update=_redraw)
    leg_axis: EnumProperty(name='Knee direction',items=_AXES,default='NY',update=_redraw)
    arm_direction: FloatVectorProperty(name='Elbow target (local)', default=(0, 1, 0), size=3)
    leg_direction: FloatVectorProperty(name='Knee target (local)', default=(0, -1, 0), size=3)
    allow_bend: BoolProperty(name='Allow small bone length change', default=False)
    minimum_bend: FloatProperty(name='Minimum bend (degrees)', default=5, min=1, max=15)
    max_shift: FloatProperty(name='Max joint shift / chain length', default=.1, min=0, max=.5)
    max_length: FloatProperty(name='Max length change / bone length', default=.005, min=0, max=.05)
    pole_distance: FloatProperty(name='Pole distance / chain length', default=.75, min=.25, max=3)
    include_arms: BoolProperty(name='Arms', default=True)
    include_legs: BoolProperty(name='Legs', default=True)
    include_fingers: BoolProperty(name='Validate existing fingers', default=True)
    palm_side: EnumProperty(items=[('L','Left',''),('R','Right','')],options={'HIDDEN'})
    palm_reference: EnumProperty(items=[('AUTO','Saved / A-pose','Keep existing references, otherwise use the A-pose plane'),
        ('PLANE','A-pose plane','A linked plane following each hand long axis'),('MESH','Mesh reference','One chosen palm drives both hands')],default='AUTO',update=_redraw)
    palm_tilt: FloatProperty(name='Palm tilt',subtype='ANGLE',default=0,min=-math.pi,max=math.pi,update=_redraw)
    palm_plane_flip: BoolProperty(name='Reverse palm side',default=False,update=_redraw)
    palm_object: PointerProperty(name='Palm mesh', type=bpy.types.Object, poll=lambda self,obj: obj.type == 'MESH')
    palm_face: IntProperty(name='Face index', default=0, min=0)
    palm_flip: BoolProperty(name='Reverse palm side')
    palm_acknowledged: BoolProperty(name='I checked the wrist axes', default=False)


class CHARACTERDESIGNER_OT_body_calibration(Operator):
    bl_idname = 'character_designer.body_calibration'
    bl_label = 'Body Calibration'
    bl_options = {'REGISTER','UNDO'}
    action: EnumProperty(items=[(p,p.title(),'') for p in ('PREVIEW','APPLY','CONFIRM','PALM','LOCATE','PLANE_REF','MESH_REF','SAVED_REF')])
    acknowledge: BoolProperty(name='I reviewed the displacement and bone length changes', default=False)
    signature: StringProperty(options={'HIDDEN','SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return bool(context.object and context.object.type == 'ARMATURE' and context.mode in {'OBJECT','POSE','EDIT_ARMATURE'})

    @classmethod
    def description(cls, context, properties):
        return {'PREVIEW':'Show the arm and wrist candidate together. Does not change Rest.',
                'APPLY':'Apply the reviewed candidate to Rest. Undo with Ctrl Z; current pose is never cleared.',
                'CONFIRM':'Confirm this applied calibration for Generate.',
                'LOCATE':'Select bones with local pose offsets in Pose Mode. Does not clear pose, animation or Rest.',
                'PALM':'Use the explicitly confirmed palm side.'}.get(properties.action,'Body Calibration')

    def invoke(self, context, event):
        try:
            rig = context.object
            p = service.plan(context,rig,service.selected_part(rig))
            if self.action == 'APPLY' and p['needs_acknowledgment']:
                self.signature = p['signature']
                self.acknowledge = False
                return context.window_manager.invoke_props_dialog(self, width=470)
        except (ValueError,RuntimeError,KeyError,TypeError) as exc:
            self.report({'WARNING'},str(exc))
            return {'CANCELLED'}
        return self.execute(context)

    def draw(self, context):
        # Blender also calls this for the last-operation panel, after Preview,
        # Confirm and successful Apply. Only a pending oversized Apply needs the
        # displacement warning; it is not a generic operation result.
        if self.action != 'APPLY':
            return
        part = service.selected_part(context.object)
        p = service.plan(context,context.object,part)
        if p['needs_acknowledgment'] and not p['errors']:
            self.layout.label(text='This adjustment needs your approval.')
            _metrics(self.layout,p)
            self.layout.prop(self,'acknowledge')

    def execute(self, context):
        rig = context.object
        s = service.settings(rig)
        part = service.selected_part(rig)
        try:
            if self.action == 'PREVIEW':
                s.show_directions = True
                service.preview(context,rig,part)
            elif self.action == 'LOCATE': service.select_pose_blockers(context,rig,part)
            elif self.action == 'PALM':
                from . import body_calibration_hands
                body_calibration_hands.capture_pair(context,rig,s.palm_object,s.palm_face,s.palm_flip,s.palm_acknowledged)
                s.palm_acknowledged = False
            elif self.action in {'PLANE_REF','MESH_REF','SAVED_REF'}:
                s.palm_reference={'PLANE_REF':'PLANE','MESH_REF':'MESH','SAVED_REF':'AUTO'}[self.action]
                s.show_directions=True
            elif self.action == 'CONFIRM': service.confirm(context,rig,part)
            else:
                if self.signature and self.signature != service.plan(context,rig,part)['signature']:
                    raise ValueError('Candidate changed while confirmation was open; preview again.')
                service.apply(context,rig,part,acknowledge=self.acknowledge)
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D': area.tag_redraw()
            s.last_error = ''
            s.last_error_signature = ''
            return {'FINISHED'}
        except (ValueError,RuntimeError,KeyError,TypeError,IndexError) as exc:
            s.last_error = str(exc)
            s.last_error_signature = service.status(context,rig,part).get('plan',{}).get('signature','')
            s.show_details = True
            self.report({'WARNING'},str(exc))
            return {'CANCELLED'}


def _metrics(layout, plan):
    for item in plan['limbs']:
        if 'shift' not in item: continue
        angle = item['direction_error_degrees']
        layout.label(text=f"{item['side']}: bend {item.get('bend_degrees',0):.2f} deg; move {item['shift']:.3g}")
        layout.label(text=f"Bend-plane direction error: {'unstable' if angle is None else f'{angle:.2f} deg'}")
        a,b = item['length_changes']
        if max(abs(a),abs(b)) > 1e-6:
            layout.label(text=f'Length change: {a:+.2%} / {b:+.2%}')


def draw_advanced(layout, context):
    """Calibration options inside the existing Advanced section; no state writes."""
    rig = context.object
    if rig is None or rig.type != 'ARMATURE': return
    part = service.selected_part(rig)
    if part not in service.PARTS: return
    s = service.settings(rig)
    layout.prop(s, 'preset', expand=True)
    if s.preset == 'CUSTOM':
        axis = 'arm_axis' if part == 'ARMS' else 'leg_axis'
        layout.prop(s,axis)
        if getattr(s,axis) == 'VECTOR':
            layout.prop(s, 'arm_direction' if part == 'ARMS' else 'leg_direction')
        for name in ('allow_bend', 'minimum_bend', 'max_shift', 'max_length', 'pole_distance'):
            layout.prop(s, name)


def draw(layout, context):
    rig = context.object
    if rig is None or rig.type != 'ARMATURE':
        layout.label(text='Select the character armature.', icon='INFO')
        return True
    s = service.settings(rig)
    part = service.selected_part(rig)
    from . import body_setup_ui
    generated = body_setup_ui._has_generated(rig)
    if part in service.PARTS:
        layout.prop(s, 'show_directions', text='Preview Directions', icon='HIDE_OFF' if s.show_directions else 'HIDE_ON')
    items = {which:display.status(context,rig,which) for which in service.PARTS}
    item = items.get(part, {})
    p = item.get('plan')
    issue = service.apply_readiness(context,rig,p['changes']) if not generated and p and not p['errors'] and not p['skipped'] else None
    names = {'UNSET':'Setup','READY':'Ready','CONFIRMED':'Confirmed','REVIEW':'Review','ERROR':'Check','SKIPPED':'Skipped'}
    for which in service.PARTS:
        row = layout.split(factor=.5, align=True)
        row.prop_enum(s, 'part', which)
        state = items[which]['state']
        row.label(text='Pose' if which==part and issue and issue['code']=='POSE' else names[state],
                  icon='CHECKBOX_HLT' if state=='CONFIRMED' else 'CHECKBOX_DEHLT')
    row = layout.split(factor=.5, align=True)
    row.prop_enum(s, 'part', 'FINGERS')
    fingers = service.finger_setup_status(context,rig)
    row.label(text=names[fingers['state']],
              icon='CHECKBOX_HLT' if fingers['state']=='CONFIRMED' else 'CHECKBOX_DEHLT')
    body_setup_ui.draw_actions(layout,context,
                              ready=service.generation_ready(context,rig,items=items,fingers=fingers))
    if generated:
        # The caller draws the existing Controls here, without a tab change.
        # Update/Remove remains available even for an old or damaged rig.
        return False
    if part == 'FINGERS':
        # The existing Fingers panel owns Capture Detection and its eye toggle.
        # Do not audit its mesh references or duplicate its calibration here.
        if fingers['state'] in {'UNSET','REVIEW'}:
            for line in textwrap.wrap(fingers['message'], 40):
                layout.label(text=line)
        return True
    box = layout.box()
    if part in {'ARMS', 'LEGS'}:
        box.prop(s, 'include_' + part.lower())
    if issue:
        warning = box.column(align=True)
        warning.label(text=issue['message'],icon='ERROR')
        if issue['bones']:
            warning.label(text=issue['bones'][0] + (f" (+{len(issue['bones'])-1})" if len(issue['bones'])>1 else ''))
        if issue['code']=='POSE':
            warning.operator('character_designer.body_calibration',text='Select Posed Bones',icon='RESTRICT_SELECT_OFF').action='LOCATE'
    elif item['state']=='ERROR':
        box.label(text=part.title()+' needs review',icon='ERROR')
    elif s.last_error and s.last_error_signature == (p or {}).get('signature',''):
        box.label(text='Apply needs attention; see Details',icon='ERROR')
    elif item['state']=='READY':
        box.label(text='Applied. Confirm to continue.',icon='CHECKMARK')
    row = box.row(align=True)
    row.operator('character_designer.body_calibration',text='Preview',icon='HIDE_OFF').action='PREVIEW'
    apply_row = row.row(align=True)
    apply_row.enabled = bool(p and not p['errors'] and not issue and not p['skipped'] and
                             service.record(rig).get('previews',{}).get(part)==p['signature'])
    apply_row.operator('character_designer.body_calibration',text='Apply Calibration').action='APPLY'
    row = box.row()
    row.enabled = item['state'] in {'READY','CONFIRMED'}
    row.operator('character_designer.body_calibration',text='Confirm',icon='CHECKMARK').action='CONFIRM'
    if part=='ARMS':
        row=box.row()
        row.enabled=item['state'] in {'READY','CONFIRMED'} and context.mode in {'OBJECT','POSE'}
        row.operator('character_designer.forearm_twist_start',text='Forearm Twist Setup',icon='DRIVER_ROTATIONAL_DIFFERENCE')
    failed = bool(s.last_error and s.last_error_signature == (p or {}).get('signature',''))
    box.prop(s,'show_details',text='Details',icon='TRIA_DOWN' if s.show_details else 'TRIA_RIGHT',emboss=False)
    if s.show_details:
        details = box.column(align=True)
        if part=='ARMS':
            if p and p.get('limbs'):
                errors=[f"{limb['side']} {limb['direction_error_degrees']:.2f}°"
                        if limb.get('direction_error_degrees') is not None else f"{limb['side']} unstable"
                        for limb in p['limbs']]
                details.label(text='Elbow direction: '+' / '.join(errors))
            details.label(text='Wrist axes follow the forearm')
            frames=(p or {}).get('wrists',())
            if frames:
                angles=[frame['axis_difference_degrees'] for frame in frames]
                angle=f'{angles[0]:.2f}°' if max(angles)-min(angles)<.01 else ' · '.join(
                    f"{frame['side']} {frame['axis_difference_degrees']:.2f}°" for frame in frames)
                details.label(text='Hand / forearm: '+angle)
            details.label(text='Axes: Armature local / Rest')
        elif p: _metrics(details,p)
        message = (issue['detail'] if issue else s.last_error if failed else
                   item['message'] if item['state']=='ERROR' else
                   'Inputs changed; preview again.' if item['state']=='REVIEW' else '')
        for line in textwrap.wrap(message,34): details.label(text=line)
        if s.last_error and s.last_error != message and s.last_error_signature==(p or {}).get('signature',''):
            for line in textwrap.wrap(s.last_error,34): details.label(text=line)
    return True


def register():
    if not hasattr(bpy.types.Object, 'character_designer_body_calibration'):
        bpy.utils.register_class(CharacterDesignerBodyCalibration)
        bpy.utils.register_class(CHARACTERDESIGNER_OT_body_calibration)
        bpy.types.Object.character_designer_body_calibration = PointerProperty(type=CharacterDesignerBodyCalibration)
    from . import body_calibration_overlay
    body_calibration_overlay.register()


def unregister():
    from . import body_calibration_overlay
    body_calibration_overlay.unregister()
    if hasattr(bpy.types.Object, 'character_designer_body_calibration'):
        del bpy.types.Object.character_designer_body_calibration
        bpy.utils.unregister_class(CHARACTERDESIGNER_OT_body_calibration)
        bpy.utils.unregister_class(CharacterDesignerBodyCalibration)
