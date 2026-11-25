"""Workspace-scoped categories for the managed animation picker."""

from hashlib import sha256
from uuid import uuid4

from bpy.props import StringProperty
from bpy.types import Menu, Operator

from .animation_motion_pins import pin_owner, redraw_lists, row_key


_STORAGE = 'character_designer_motion_categories_v1'
_UNCATEGORIZED = 'Uncategorized'


def _hash(value):
    # ID property keys have a fixed byte limit. Workspace and motion keys do not.
    return sha256(value.encode('utf8')).hexdigest()[:48]


def _scope(scene):
    owner = pin_owner(scene) if scene is not None else None
    state = getattr(owner, 'character_designer_animation_worklist', None)
    identity = getattr(state, 'workspace_identity', '')
    return owner, _hash(identity) if identity else ''


def _target(scene):
    owner, scope = _scope(scene)
    return f'{owner.session_uid}:{scope}' if scope else ''


def _view(scene):
    owner, scope = _scope(scene)
    return owner.get(_STORAGE, {}).get(scope, {}) if scope else {}


def _selected(view):
    category_id = view.get('selected', '')
    return category_id if category_id in view.get('names', {}) else ''


def categories(scene):
    names = _view(scene).get('names', {})
    return tuple(sorted(names.items(), key=lambda entry: entry[1].casefold()))


def current_category(scene):
    return _selected(_view(scene))


def category_label(scene):
    view = _view(scene)
    return view.get('names', {}).get(_selected(view), _UNCATEGORIZED)


def _assigned(key, assignments, names):
    category_id = assignments.get(_hash(key), '') if key else ''
    return category_id if category_id in names else ''


def category_for(scene, row):
    view = _view(scene)
    return _assigned(row_key(row), view.get('assignments', {}), view.get('names', {}))


def has_members(context, rows):
    view = _view(context.scene)
    selected = _selected(view)
    assignments, names = view.get('assignments', {}), view.get('names', {})
    return any(_assigned(row_key(row), assignments, names) == selected for row in rows)


def filter_rows(ui_list, context, data, propname):
    view = _view(context.scene)
    selected = _selected(view)
    assignments = view.get('assignments', {})
    names = view.get('names', {})
    terms = ui_list.filter_name.casefold().split()
    if not assignments and not selected and not terms:
        return [], []
    flags = []
    for row in getattr(data, propname):
        matches = (_assigned(row_key(row), assignments, names) == selected
                   and all(term in row.name.casefold() for term in terms))
        flags.append(ui_list.bitflag_filter_item if matches else 0)
    return flags, []


def _validated_name(names, name, current=''):
    name = name.strip()
    if not name or not name.isprintable() or len(name) > 64:
        raise ValueError('Enter a category name of up to 64 characters.')
    folded = name.casefold()
    if folded == _UNCATEGORIZED.casefold() or any(
            category_id != current and label.casefold() == folded for category_id, label in names.items()):
        raise ValueError('That category name is already in use.')
    return name


class _CategoryEdit:
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        owner, scope = _scope(getattr(context, 'scene', None))
        return bool(scope and not owner.library and not owner.override_library)

    def execute(self, context):
        scene = context.scene
        try:
            if not self.poll(context) or self.target and self.target != _target(scene):
                raise ValueError('This animation workspace has changed. Reopen its category controls.')
            owner, scope = _scope(scene)
            store = owner.get(_STORAGE)
            previous = store.get(scope, {}) if store is not None else {}
            view = dict(names=dict(previous.get('names', {})), assignments=dict(previous.get('assignments', {})),
                        selected=_selected(previous))
            if self._update(scene, view):
                if store is None:
                    owner[_STORAGE] = {scope: view}
                else:
                    store[scope] = view
                redraw_lists(context)
        except (ValueError, RuntimeError, TypeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_motion_category_create(_CategoryEdit, Operator):
    bl_idname = 'character_designer.motion_category_create'
    bl_label = 'New Animation Category'
    bl_description = 'Create a category for organizing the motions in this workspace'

    name: StringProperty(name='Name')
    target: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def _update(self, _scene, view):
        names = view['names']
        name = _validated_name(names, self.name)
        names[uuid4().hex] = name
        return True


class CHARACTERDESIGNER_OT_motion_category_rename(_CategoryEdit, Operator):
    bl_idname = 'character_designer.motion_category_rename'
    bl_label = 'Rename Category'
    bl_description = 'Change the category name and keep its motion assignments'

    name: StringProperty(name='Name')
    category_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    target: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, _event):
        self.name = _view(context.scene).get('names', {}).get(self.category_id, '')
        return context.window_manager.invoke_props_dialog(self)

    def _update(self, _scene, view):
        names = view['names']
        if self.category_id not in names:
            raise ValueError('The category is no longer available.')
        name = _validated_name(names, self.name, current=self.category_id)
        if names[self.category_id] == name:
            return False
        names[self.category_id] = name
        return True


class CHARACTERDESIGNER_OT_motion_category_remove(_CategoryEdit, Operator):
    bl_idname = 'character_designer.motion_category_remove'
    bl_label = 'Remove Category'
    bl_description = 'Remove this category and return its motions to Uncategorized'

    category_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    target: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title='Remove Category?', confirm_text='Remove',
            message='The motions in this category will return to Uncategorized.',
        )

    def _update(self, _scene, view):
        if self.category_id not in view['names']:
            raise ValueError('The category is no longer available.')
        del view['names'][self.category_id]
        view['assignments'] = {key: value for key, value in view['assignments'].items()
                               if value != self.category_id}
        if view['selected'] == self.category_id:
            view['selected'] = ''
        return True


class CHARACTERDESIGNER_OT_motion_category_select(_CategoryEdit, Operator):
    bl_idname = 'character_designer.motion_category_select'
    bl_label = 'Show Category'
    bl_description = 'Browse the motions in this category'

    category_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    target: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def _update(self, _scene, view):
        if self.category_id and self.category_id not in view['names']:
            raise ValueError('The category is no longer available.')
        if view['selected'] == self.category_id:
            return False
        view['selected'] = self.category_id
        return True


class CHARACTERDESIGNER_OT_motion_category_assign(_CategoryEdit, Operator):
    bl_idname = 'character_designer.motion_category_assign'
    bl_label = 'Assign Motion Category'
    bl_description = 'Move this motion into the chosen category'

    key: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    category_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    target: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def _update(self, scene, view):
        state = scene.character_designer_animation_worklist
        rows = state.items if state.items else state.catalog
        if not self.key or sum(row_key(row) == self.key for row in rows) != 1:
            raise ValueError('The motion is no longer unique in this list.')
        if self.category_id and self.category_id not in view['names']:
            raise ValueError('The category is no longer available.')
        assignments = view['assignments']
        key = _hash(self.key)
        if assignments.get(key, '') == self.category_id:
            return False
        if self.category_id:
            assignments[key] = self.category_id
        else:
            assignments.pop(key, None)
        return True


class CHARACTERDESIGNER_OT_motion_category_move(Operator):
    bl_idname = 'character_designer.motion_category_move'
    bl_label = 'Move to Category'
    bl_description = 'Choose a category for this motion'

    key: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, _event):
        scene = context.scene
        workspace = _target(scene)
        key = self.key
        view = _view(scene)
        names = view.get('names', {})
        current = _assigned(key, view.get('assignments', {}), names)
        choices = (('', _UNCATEGORIZED), *categories(scene))

        def draw(menu, _context):
            for category_id, label in choices:
                item = menu.layout.operator('character_designer.motion_category_assign', text=label,
                                            icon='CHECKMARK' if category_id == current else 'BLANK1')
                item.key, item.category_id, item.target = key, category_id, workspace

        context.window_manager.popup_menu(draw, title=self.bl_label, icon='FILE_FOLDER')
        return {'FINISHED'}


class CHARACTERDESIGNER_MT_motion_categories(Menu):
    bl_idname = 'CHARACTERDESIGNER_MT_motion_categories'
    bl_label = 'Animation Categories'

    def draw(self, context):
        scene = context.scene
        workspace = _target(scene)
        selected = current_category(scene)
        for category_id, label in (('', _UNCATEGORIZED), *categories(scene)):
            button = self.layout.operator('character_designer.motion_category_select', text=label,
                                          icon='CHECKMARK' if category_id == selected else 'BLANK1')
            button.category_id, button.target = category_id, workspace


class CHARACTERDESIGNER_MT_manage_motion_category(Menu):
    bl_idname = 'CHARACTERDESIGNER_MT_manage_motion_category'
    bl_label = 'Manage Category'

    def draw(self, context):
        scene = context.scene
        workspace = _target(scene)
        selected = current_category(scene)
        self.layout.operator_context = 'INVOKE_DEFAULT'
        for op, icon in (('rename', 'GREASEPENCIL'), ('remove', 'TRASH')):
            button = self.layout.operator('character_designer.motion_category_' + op, icon=icon)
            button.category_id, button.target = selected, workspace


def draw_toolbar(layout, context):
    workspace = _target(context.scene)
    if not workspace:
        return
    bar = layout.row(align=True)
    bar.operator_context = 'INVOKE_DEFAULT'
    bar.menu('CHARACTERDESIGNER_MT_motion_categories', text=category_label(context.scene), icon='FILE_FOLDER')
    bar.operator('character_designer.motion_category_create', text='', icon='ADD').target = workspace
    if current_category(context.scene):
        bar.menu('CHARACTERDESIGNER_MT_manage_motion_category', text='', icon='PREFERENCES')


def draw_move(layout, context, item):
    key = row_key(item)
    if not key or not categories(context.scene):
        return
    line = layout.row(align=True)
    line.operator_context = 'INVOKE_DEFAULT'
    line.operator('character_designer.motion_category_move', text='', icon='FILE_FOLDER', emboss=False).key = key


CLASSES = (
    CHARACTERDESIGNER_OT_motion_category_create,
    CHARACTERDESIGNER_OT_motion_category_rename,
    CHARACTERDESIGNER_OT_motion_category_remove,
    CHARACTERDESIGNER_OT_motion_category_select,
    CHARACTERDESIGNER_OT_motion_category_assign,
    CHARACTERDESIGNER_OT_motion_category_move,
    CHARACTERDESIGNER_MT_motion_categories,
    CHARACTERDESIGNER_MT_manage_motion_category,
)
