"""Collection management on the existing Animation page; imports never play."""
from pathlib import Path
import textwrap
import bpy
from bpy.props import CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator, UIList
from . import animation_library as library

_job = None


def stop():
    global _job
    if _job:
        try:_job._wm.event_timer_remove(_job._timer)
        except (ReferenceError,RuntimeError):pass
        _job=None


class CD_UL_animation_collections(UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        layout.prop(item,'name',text='',emboss=False,icon='ASSET_MANAGER')
        layout.label(text=str(len(item.clips)))


class CD_UL_animation_clips(UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        row=layout.row(align=True)
        row.prop(item,'selected',text='')
        row.label(text=item.name,icon='ACTION')
        use=row.operator('character_designer.animation_library',text='',icon='PLAY')
        use.action='USE';use.index=index


class CHARACTERDESIGNER_OT_animation_library(Operator):
    bl_idname='character_designer.animation_library'
    bl_label='Animation Collection'
    bl_options={'REGISTER','UNDO'}
    action: EnumProperty(items=[(x,x.title().replace('_',' '),'') for x in
        ('NEW','REMOVE_COLLECTION','ADD','SCAN','IMPORT','USE','RESTORE','REMOVE','SELECT_ALL','SELECT_NONE')])
    index: IntProperty(default=-1)
    filepath: StringProperty(subtype='FILE_PATH')
    directory: StringProperty(subtype='DIR_PATH')
    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    filter_glob: StringProperty(default='*.cdanim.json;*.cdcollection.json',options={'HIDDEN'})

    @classmethod
    def poll(cls,context):return _job is None

    def invoke(self,context,event):
        global _job
        if self.action=='ADD':
            self.filepath=str(exchange(context))+'/'
            context.window_manager.fileselect_add(self);return {'RUNNING_MODAL'}
        if self.action=='IMPORT':
            c,_=library.current(context)
            if not c or not library.target(context):return self.execute(context)
            self._items=[i for i in c.clips if i.selected];self._collection=c
            if not self._items:return self.execute(context)
            self._success=0;self._failed=0;self._total=len(self._items)
            self._target=library.target(context);self._wm=context.window_manager;self._scene=context.scene
            library.state(context).status='Preparing selected actions… Esc cancels remaining imports.'
            self._timer=self._wm.event_timer_add(.1,window=context.window)
            _job=self;self._wm.modal_handler_add(self)
            if context.area:context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        return self.execute(context)

    def modal(self,context,event):
        if _job is not self:return {'CANCELLED'}
        if event.type=='ESC' or context.scene!=self._scene or library.target(context)!=self._target:
            stop();library.state(context).status='Import stopped; completed Actions retained, current animation unchanged.'
            return {'FINISHED'}
        if event.type!='TIMER':return {'PASS_THROUGH'}
        try:
            item=self._items.pop(0)
            good,bad=library.import_selected(context,self._collection,items=[item])
            self._success+=len(good);self._failed+=len(bad)
            library.state(context).status=f'{self._success+self._failed}/{self._total}: {item.name} · {item.status}'
            if context.area:context.area.tag_redraw()
            if not self._items:
                stop();library.state(context).status=f'{self._success} ready, {self._failed} incompatible. Current animation unchanged.'
                return {'FINISHED'}
        except Exception as exc:
            stop();library.state(context).status=str(exc);self.report({'ERROR'},str(exc));return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def execute(self,context):
        s=library.state(context);c,item=library.current(context)
        try:
            if self.action=='NEW':library.add_collection(context)
            elif self.action=='ADD':
                paths=[Path(self.directory)/f.name for f in self.files] if self.files else [self.filepath]
                library.add_files(context,paths,c)
            elif self.action=='SCAN':
                folder=exchange(context);paths=sorted(folder.glob('*.cdcollection.json'))
                if not paths:paths=sorted(folder.glob('*.cdanim.json'))
                library.add_files(context,paths,c)
            elif self.action=='IMPORT':library.import_selected(context)
            elif self.action=='USE':
                if c and self.index>=0:c.active=self.index
                library.use(context)
            elif self.action=='RESTORE':library.restore(context)
            elif self.action=='REMOVE':library.remove_clip(context)
            elif self.action=='REMOVE_COLLECTION' and c:
                if any(s.session_clip==i.source_id+':'+i.revision for i in c.clips):library.restore(context)
                s.collections.remove(s.active);s.active=max(0,min(s.active,len(s.collections)-1))
                s.status='Collection removed; Unity resources and existing Actions retained.'
            elif self.action in {'SELECT_ALL','SELECT_NONE'} and c:
                for i in c.clips:i.selected=self.action=='SELECT_ALL'
        except Exception as exc:
            s.status=str(exc);self.report({'ERROR'},str(exc));return {'CANCELLED'}
        if context.area:context.area.tag_redraw()
        return {'FINISHED'}


def exchange(context):
    from .animation import unity_exchange_folder
    folder=library.state(context).folder
    return Path(bpy.path.abspath(folder)) if folder else unity_exchange_folder(context)


def button(row,action,text='',icon='NONE'):
    op=row.operator('character_designer.animation_library',text=text,icon=icon);op.action=action


def draw(layout,context):
    s=library.state(context);c,item=library.current(context);rig=library.target(context)
    box=layout.box();box.label(text='Unity Action Library',icon='ACTION')
    box.prop(s,'target',text='Target')
    if not s.target:box.label(text='Setup: '+rig.name if rig else 'Set Main Rig in Character Setup.',icon='ARMATURE_DATA' if rig else 'INFO')
    row=box.row();row.template_list('CD_UL_animation_collections','',s,'collections',s,'active',rows=2)
    tools=row.column(align=True);button(tools,'NEW',icon='ADD');button(tools,'REMOVE_COLLECTION',icon='REMOVE')
    row=box.row(align=True);button(row,'ADD','Add Files',icon='FILE_FOLDER');button(row,'SCAN','Read Exchange',icon='FILE_REFRESH')
    if c:
        box.template_list('CD_UL_animation_clips','',c,'clips',c,'active',rows=5)
        row=box.row(align=True);button(row,'SELECT_ALL','All');button(row,'SELECT_NONE','None');button(row,'REMOVE',icon='X')
        row=box.row(align=True);row.enabled=bool(rig)
        button(row,'IMPORT','Import Selected',icon='IMPORT');button(row,'USE','Use Action',icon='PLAY')
    if item:
        box.label(text='Source: '+item.source_name,icon='OUTLINER_OB_ARMATURE')
        # Status is event-built; never analyze meshes/rigs from panel drawing.
        for line in textwrap.wrap(item.status or 'Source ready',42):box.label(text=line)
    if s.session:
        box.label(text='Current: '+(s.session_action.name if s.session_action else 'Missing Action'),icon='ACTION')
        row=box.row(align=True)
        playing=context.screen and context.screen.is_animation_playing
        row.operator('character_designer.animation_play_pause',text='Pause' if playing else 'Play',icon='PAUSE' if playing else 'PLAY')
        button(row,'RESTORE','Restore',icon='LOOP_BACK')
        box.prop(context.scene,'frame_current',text='Frame')
    box.prop(s,'folder',text='Exchange')
    if s.status:
        for line in textwrap.wrap(s.status,46):box.label(text=line)


CLASSES=(CD_UL_animation_collections,CD_UL_animation_clips,CHARACTERDESIGNER_OT_animation_library)
