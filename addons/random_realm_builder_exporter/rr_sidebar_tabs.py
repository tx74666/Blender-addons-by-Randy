"""Order existing 3D sidebar tabs through RR Helper's own empty Panels.

The list and up/down arrows are not native dragging. Original Panel classes are
never edited, registered or unregistered. Only this module's HIDE_HEADER/order-0
anchors change. Item, Tool and View have a fixed prefix; categories already led
by an original HIDE_HEADER/order-0 root keep their native position and are not
movable. An information tooltip explains those pinned categories.

Anchors mirror registered original roots' context and poll without copying their
owners. In workspaces using owner filtering every anchor is hidden and the move
arrows are disabled, retaining that workspace's native order. An explicit Enable
Tab Reordering action can show all add-on tabs in the current workspace. Tool is an always
available native 3D tab; Item and View require corresponding visible Python roots.

Only a small JSON in CONFIG is persisted. WindowManager list properties are
SKIP_SAVE caches. Startup is bounded, draw is read-only, and all registration and
rollback operations are confined to identifiable anchor classes owned here.
"""

import json
import os
from pathlib import Path
import tempfile

import bpy

FIXED_PREFIX = ("Item", "Tool", "View")
FIXED_CATEGORIES = frozenset(FIXED_PREFIX)
SCHEMA = "rr-helper.sidebar-tabs"
CONFIG_VERSION = 1
CONFIG_FILENAME = "rr_helper_sidebar_tabs.json"
VIEW_PROPERTY = "rr_helper_sidebar_tab_items"
INDEX_PROPERTY = "rr_helper_sidebar_tab_index"
ANCHOR_MARKER = "_rr_helper_sidebar_anchor"
MAX_CATEGORIES = 1024
MAX_CONFIG_BYTES = 128 * 1024
STARTUP_DELAYS = (0.5, 1.0, 2.0)

_enabled = False
_busy = False
_error = ""
_startup_attempt = 0
_anchor_serial = 0
_anchors = []
_display_order = []
_poll_errors = set()

class SidebarTabsError(RuntimeError):
    """The requested order could not be applied safely."""

def get_error():
    return _error

def _config_path():
    directory = bpy.utils.user_resource("CONFIG")
    if not directory:
        raise SidebarTabsError("Blender's configuration directory is unavailable.")
    return Path(directory) / CONFIG_FILENAME

def _validate_order(order):
    if not isinstance(order, (list, tuple)) or len(order) > MAX_CATEGORIES:
        raise SidebarTabsError("Sidebar tab order must be a bounded list of names.")
    result = []
    for name in order:
        if (not isinstance(name, str) or not name or not name.strip()
                or len(name) > 1024 or "\0" in name):
            raise SidebarTabsError("Sidebar tab order contains an invalid name.")
        if name in FIXED_CATEGORIES:
            raise SidebarTabsError("Item, Tool and View have a fixed order.")
        if name in result:
            raise SidebarTabsError("Sidebar tab order contains a duplicate name.")
        result.append(name)
    return result

def _read_config():
    path = _config_path()
    try:
        if not path.exists():
            return None
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise SidebarTabsError("The saved sidebar tab order is too large.")
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except SidebarTabsError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise SidebarTabsError("Cannot read the saved sidebar tab order: " + str(exc)) from exc
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or type(value.get("version")) is not int
            or value["version"] != CONFIG_VERSION):
        raise SidebarTabsError("The saved sidebar tab order has an unsupported format.")
    return _validate_order(value.get("order"))

def _write_config(order):
    """Replace the dedicated order file only after a complete durable write."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": SCHEMA, "version": CONFIG_VERSION, "order": list(order)}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", prefix=path.name + ".",
                suffix=".tmp", dir=str(path.parent), delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass

def _registered_class(identifier):
    try:
        return bpy.types.Panel.bl_rna_get_subclass_py(identifier, None)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return None

def _class_identifier(cls):
    # A registered subclass has its own bl_rna. Inherited RNA must not make an
    # unregistered Python subclass look registered.
    rna = cls.__dict__.get("bl_rna")
    return getattr(rna, "identifier", "") if rna is not None else ""

def _all_registered_panels():
    found = []
    seen = set()
    stack = list(reversed(bpy.types.Panel.__subclasses__()))
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(reversed(cls.__subclasses__()))
        identifier = _class_identifier(cls)
        if identifier and _registered_class(identifier) is cls:
            found.append(cls)
    return found

def _string(cls, name):
    value = getattr(cls, name, "")
    return value if isinstance(value, str) else ""

def _number(cls, name, default=0):
    value = getattr(cls, name, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default

def _is_sidebar(cls):
    return _string(cls, "bl_space_type") == "VIEW_3D" and _string(cls, "bl_region_type") == "UI"


def _is_anchor(cls):
    return cls.__dict__.get(ANCHOR_MARKER) is True


def _root_category(cls):
    if _is_anchor(cls) or not _is_sidebar(cls) or _string(cls, "bl_parent_id"):
        return ""
    return _string(cls, "bl_category")


def _collect():
    """Read class registrations only; do not call any original poll here."""
    panels = [cls for cls in _all_registered_panels() if not _is_anchor(cls)]
    positions = {cls: index for index, cls in enumerate(panels)}
    roots = [cls for cls in panels if _root_category(cls)]
    roots.sort(key=lambda cls: (_number(cls, "bl_order"), positions[cls]))
    root_map = {}
    pinned = []
    for cls in roots:
        name = _root_category(cls)
        root_map.setdefault(name, []).append({
            "cls": cls, "identifier": _class_identifier(cls), "category": name,
            "context": _string(cls, "bl_context"),
        })
        options = getattr(cls, "bl_options", ())
        if (isinstance(options, (set, frozenset, tuple, list))
                and "HIDE_HEADER" in options and _number(cls, "bl_order") <= 0
                and name not in FIXED_CATEGORIES and name not in pinned):
            pinned.append(name)
    movable = [name for name in root_map if name not in FIXED_CATEGORIES and name not in pinned]
    if len(movable) > MAX_CATEGORIES:
        raise SidebarTabsError("Too many sidebar tabs to order safely.")
    return root_map, movable, pinned


def get_pinned():
    """Return native headerless/order-0 categories that cannot be moved."""
    return _collect()[2]


def get_order():
    """Return movable names in the applied order, followed by newly added tabs."""
    available = _collect()[1]
    result = [name for name in _display_order if name in available]
    result.extend(name for name in available if name not in result)
    return result


def _workspace_filtered(context):
    workspace = getattr(context, "workspace", None)
    return bool(getattr(workspace, "use_filter_by_owner", False))


_MODE_CONTEXTS = {
    "OBJECT": "objectmode", "EDIT_MESH": "mesh_edit", "EDIT_CURVE": "curve_edit",
    "EDIT_SURFACE": "surface_edit", "EDIT_TEXT": "text_edit",
    "EDIT_ARMATURE": "armature_edit", "EDIT_METABALL": "mball_edit",
    "EDIT_LATTICE": "lattice_edit", "EDIT_GREASE_PENCIL": "grease_pencil_edit",
    "EDIT_POINTCLOUD": "pointcloud_edit", "POSE": "posemode", "SCULPT": "sculpt_mode",
    "SCULPT_CURVES": "curves_sculpt", "PAINT_WEIGHT": "weightpaint",
    "PAINT_VERTEX": "vertexpaint", "PAINT_TEXTURE": "imagepaint",
    "PARTICLE": "particlemode", "PAINT_GREASE_PENCIL": "grease_pencil_paint",
    "SCULPT_GREASE_PENCIL": "grease_pencil_sculpt",
    "WEIGHT_GREASE_PENCIL": "greasepencil_weight",
    "VERTEX_GREASE_PENCIL": "greasepencil_vertex",
}


def _context_matches(value, context):
    if not value:
        return True
    # Dot-prefixed tool/popover contexts deliberately do not match sidebar
    # contexts. Unknown strings stay hidden instead of manufacturing empty tabs.
    mode = getattr(context, "mode", "")
    return value == _MODE_CONTEXTS.get(mode)


def _category_visible(category, root_refs, context):
    if _workspace_filtered(context):
        return False
    area = getattr(context, "area", None)
    if area is not None and getattr(area, "type", None) != "VIEW_3D":
        return False
    if category == "Tool":
        return True
    for ref in root_refs:
        cls = ref["cls"]
        if (_registered_class(ref["identifier"]) is not cls or _is_anchor(cls)
                or _root_category(cls) != category
                or _string(cls, "bl_context") != ref["context"]
                or not _context_matches(ref["context"], context)):
            continue
        try:
            poll = getattr(cls, "poll", None)
            if poll is None or bool(poll(context)):
                return True
        except Exception as exc:
            # A failing original poll must not become an always-visible empty
            # category. Keep the console useful instead of repeating per redraw.
            key = (ref["identifier"], type(exc).__name__, str(exc))
            if key not in _poll_errors:
                if len(_poll_errors) < 64:
                    _poll_errors.add(key)
                    print("[RR Helper] Sidebar tab visibility unavailable: " + str(exc))
    return False


def _anchor_poll(cls, context):
    return _enabled and _category_visible(cls.bl_category, cls._rr_sidebar_roots, context)


def _anchor_draw(self, context):
    pass


def _new_anchor(category, root_refs):
    global _anchor_serial
    for _ in range(MAX_CATEGORIES * 4):
        _anchor_serial += 1
        identifier = "RRHELPER_PT_sidebar_anchor_" + str(_anchor_serial)
        if _registered_class(identifier) is None:
            break
    else:
        raise SidebarTabsError("Cannot reserve a sidebar position panel.")
    return type(identifier, (bpy.types.Panel,), {
        "__module__": __name__, "bl_idname": identifier,
        "bl_label": "RR Helper Sidebar Position", "bl_space_type": "VIEW_3D",
        "bl_region_type": "UI", "bl_category": category,
        "bl_options": {"HIDE_HEADER"}, "bl_order": 0, "bl_owner_id": "",
        ANCHOR_MARKER: True, "_rr_sidebar_roots": tuple(root_refs),
        "poll": classmethod(_anchor_poll), "draw": _anchor_draw,
    })


def _register_anchor(cls):
    if not _is_anchor(cls):
        raise SidebarTabsError("Refused to register an original sidebar panel.")
    if _registered_class(cls.__name__) is not None:
        raise SidebarTabsError("A sidebar position panel ID is already registered.")
    from _bpy import _bl_owner_id_get, _bl_owner_id_set
    previous = _bl_owner_id_get()
    try:
        # Only our own anchors receive an empty owner. Original native owners
        # are never read, guessed, copied, or rewritten.
        _bl_owner_id_set("")
        bpy.utils.register_class(cls)
    finally:
        _bl_owner_id_set(previous or "")


def _unregister_anchor(cls):
    if not _is_anchor(cls):
        raise SidebarTabsError("Refused to unregister an original sidebar panel.")
    current = _registered_class(cls.__name__)
    if current is cls:
        bpy.utils.unregister_class(cls)
    elif current is not None:
        raise SidebarTabsError("A sidebar position panel was replaced externally.")


def _remove_anchors(classes):
    failures = []
    for cls in reversed(classes):
        try:
            _unregister_anchor(cls)
        except Exception as exc:
            failures.append(str(exc))
    for cls in classes:
        if _registered_class(cls.__name__) is cls:
            failures.append("A sidebar position panel is still registered: " + cls.__name__)
    return failures


def _restore_anchors(old, new):
    failures = _remove_anchors(new)
    # A failed removal may leave only part of the previous sequence registered.
    # Rebuild the complete owned sequence so native insertion order is restored.
    failures.extend(_remove_anchors(old))
    for cls in old:
        current = _registered_class(cls.__name__)
        if current is None:
            try:
                _register_anchor(cls)
            except Exception as exc:
                failures.append(str(exc))
        elif current is not cls:
            failures.append("A previous sidebar position panel was replaced externally.")
    for cls in old:
        if _registered_class(cls.__name__) is not cls:
            failures.append("A previous sidebar position panel could not be restored.")
    if failures:
        raise SidebarTabsError("Sidebar order recovery needs attention: " + "; ".join(failures))


def _redraw():
    manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(manager, "windows", ()):
        for area in getattr(getattr(window, "screen", None), "areas", ()):
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _apply_order(order, *, persist=True, context=None):
    global _busy, _anchors, _display_order, _error
    if _busy:
        raise SidebarTabsError("A sidebar tab update is already in progress.")
    requested = _validate_order(order)
    if persist:
        _read_config()
    root_map, available, pinned = _collect()
    if persist and any(name in pinned for name in requested):
        raise SidebarTabsError("Native headerless tabs keep their original position.")
    if context is not None and _workspace_filtered(context):
        raise SidebarTabsError("This workspace uses its original sidebar tab order.")
    requested = [name for name in requested if name not in pinned]
    actual = [name for name in requested if name in available]
    actual.extend(name for name in available if name not in actual)
    saved = requested + [name for name in actual if name not in requested]
    new = [_new_anchor(name, root_map.get(name, ())) for name in (*FIXED_PREFIX, *actual)]
    old = list(_anchors)
    _busy = True
    try:
        try:
            # Stage new owned panels first. Old anchors remain effective until
            # every new registration succeeds, and are then removed in reverse.
            for cls in new:
                _register_anchor(cls)
            for cls in new:
                if _registered_class(cls.__name__) is not cls:
                    raise SidebarTabsError("A sidebar position panel did not register.")
            for cls in reversed(old):
                _unregister_anchor(cls)
            if any(_registered_class(cls.__name__) is cls for cls in old):
                raise SidebarTabsError("An old sidebar position panel did not unregister.")
            if persist:
                _write_config(saved)
        except Exception as exc:
            try:
                _restore_anchors(old, new)
            except Exception as recovery:
                # Retain references to all surviving owned classes so a later
                # unregister can still clean a partial recovery safely.
                _anchors = [cls for cls in (*old, *new)
                            if _registered_class(cls.__name__) is cls]
                raise SidebarTabsError(str(exc) + " " + str(recovery)) from exc
            raise SidebarTabsError("Sidebar tab order was not changed: " + str(exc)) from exc
        _anchors = new
        _display_order = actual
        _error = ""
        try:
            refresh_view(context)
        except Exception as exc:
            print("[RR Helper] Sidebar list refresh deferred: " + str(exc))
        _redraw()
        return actual
    finally:
        _busy = False


def apply_order(order, *, persist=True, context=None):
    """Apply only owned anchors; explicit pinned/fixed requests are rejected."""
    global _error
    try:
        return _apply_order(order, persist=persist, context=context)
    except Exception as exc:
        _error = str(exc)
        _redraw()
        if isinstance(exc, SidebarTabsError):
            raise
        raise SidebarTabsError(_error) from exc


def enable_sorting(context):
    """Explicitly show all add-on tabs in this workspace and enable ordering."""
    if not _enabled or _busy:
        raise SidebarTabsError("A sidebar tab update is unavailable or already in progress.")
    workspace = getattr(context, "workspace", None)
    if workspace is None or not getattr(workspace, "is_editable", True):
        raise SidebarTabsError("Choose an editable workspace before enabling tab reordering.")
    previous = workspace.use_filter_by_owner
    try:
        workspace.use_filter_by_owner = False
        return apply_order(get_order(), context=context)
    except Exception:
        workspace.use_filter_by_owner = previous
        _redraw()
        raise



def refresh_view(context=None):
    """Rebuild disposable UI rows. This is called by operators/timers, not draw."""
    context = context or bpy.context
    manager = getattr(context, "window_manager", None)
    names = get_order()
    if manager is None or not hasattr(manager, VIEW_PROPERTY):
        return names
    items = getattr(manager, VIEW_PROPERTY)
    index = getattr(manager, INDEX_PROPERTY, 0)
    active = items[index].name if 0 <= index < len(items) else ""
    if [item.name for item in items] != names:
        items.clear()
        for name in names:
            items.add().name = name
        setattr(manager, INDEX_PROPERTY, names.index(active) if active in names else 0)
    return names

def _startup():
    global _startup_attempt, _error
    if not _enabled:
        return None
    try:
        saved = _read_config()
        if saved is not None:
            apply_order(saved, persist=False)
        refresh_view()
    except Exception as exc:
        _error = str(exc)
        print("[RR Helper] " + _error)
        _redraw()
        return None
    _startup_attempt += 1
    if _startup_attempt < len(STARTUP_DELAYS):
        return STARTUP_DELAYS[_startup_attempt]
    return None

class RRSidebarTabItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Tab")

class RR_UL_sidebar_tabs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name)


class RR_OT_refresh_sidebar_tabs(bpy.types.Operator):
    bl_idname = "rr_helper.refresh_sidebar_tabs"
    bl_label = "Refresh Tabs"
    bl_description = "Refresh available sidebar tabs; native headerless tabs keep their position"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        global _error
        try:
            _read_config()
            if not _workspace_filtered(context):
                apply_order(get_order(), persist=False, context=context)
            refresh_view(context)
            _error = ""
        except Exception as exc:
            _error = str(exc)
            self.report({"ERROR"}, _error)
            return {"CANCELLED"}
        _redraw()
        return {"FINISHED"}


class RR_OT_sidebar_tabs_info(bpy.types.Operator):
    bl_idname = "rr_helper.sidebar_tabs_info"
    bl_label = "Sidebar Tab Order"
    bl_description = ("Item, Tool and View keep a fixed prefix. Native headerless tabs keep "
                      "their original position and are omitted. Owner-filtered workspaces "
                      "use Blender's original order until Enable Tab Reordering is chosen")
    bl_options = {"INTERNAL"}

    def execute(self, context):
        return {"FINISHED"}


class RR_OT_enable_sidebar_sorting(bpy.types.Operator):
    bl_idname = "rr_helper.enable_sidebar_sorting"
    bl_label = "Enable Tab Reordering"
    bl_description = "Show all add-on tabs in this workspace and enable the Up / Down order controls"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        try:
            enable_sorting(context)
        except (SidebarTabsError, RuntimeError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Tab reordering enabled. Select a tab and use Up / Down.")
        return {"FINISHED"}


class RR_OT_move_sidebar_tab(bpy.types.Operator):
    bl_idname = "rr_helper.move_sidebar_tab"
    bl_label = "Move Sidebar Tab"
    bl_description = "Move this sidebar tab and remember its position"
    bl_options = {"INTERNAL"}
    direction: bpy.props.EnumProperty(items=(("UP", "Up", "Move up"), ("DOWN", "Down", "Move down")))

    def execute(self, context):
        if _workspace_filtered(context):
            self.report({"INFO"}, "This workspace uses its original sidebar tab order.")
            return {"CANCELLED"}
        manager = context.window_manager
        names = refresh_view(context)
        index = getattr(manager, INDEX_PROPERTY)
        target = index + (-1 if self.direction == "UP" else 1)
        if not (0 <= index < len(names) and 0 <= target < len(names)):
            return {"CANCELLED"}
        names[index], names[target] = names[target], names[index]
        try:
            pinned = set(get_pinned())
            existing = [name for name in (_read_config() or []) if name not in pinned]
            iterator = iter(names)
            order = [next(iterator) if name in names else name for name in existing]
            order.extend(name for name in names if name not in order)
            apply_order(order, context=context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        setattr(manager, INDEX_PROPERTY, target)
        return {"FINISHED"}


def draw_sidebar_tabs(layout, context):
    """Draw cached rows only, with no registration, scan, poll or data writes."""
    box = layout.box()
    header = box.row(align=True)
    header.label(text="Sidebar Tabs")
    header.operator("rr_helper.sidebar_tabs_info", text="", icon="INFO", emboss=False)
    header.operator("rr_helper.refresh_sidebar_tabs", text="", icon="FILE_REFRESH")
    manager = getattr(context, "window_manager", None)
    if manager is None or not hasattr(manager, VIEW_PROPERTY):
        return
    items = getattr(manager, VIEW_PROPERTY)
    filtered = _workspace_filtered(context)
    if filtered:
        box.label(text="Add-on filtering is blocking sorting.", icon="INFO")
        enable = box.row()
        enable.enabled = not _busy
        enable.operator("rr_helper.enable_sidebar_sorting", text="Enable Tab Reordering", icon="SORTSIZE")
        box.label(text="Show all add-on tabs in this workspace.")
    else:
        box.label(text="Select a tab, then use Up / Down.", icon="INFO")
    row = box.row()
    row.template_list("RR_UL_sidebar_tabs", "", manager, VIEW_PROPERTY,
                      manager, INDEX_PROPERTY, rows=5)
    arrows = row.column(align=True)
    index = getattr(manager, INDEX_PROPERTY, 0)
    up = arrows.row(align=True)
    up.enabled = not _busy and not filtered and 0 < index < len(items)
    up.operator("rr_helper.move_sidebar_tab", text="Up", icon="TRIA_UP").direction = "UP"
    down = arrows.row(align=True)
    down.enabled = not _busy and not filtered and 0 <= index < len(items) - 1
    down.operator("rr_helper.move_sidebar_tab", text="Down", icon="TRIA_DOWN").direction = "DOWN"
    if _error:
        message = box.row()
        message.alert = True
        message.label(text=_error, icon="ERROR")


CLASSES = (RRSidebarTabItem, RR_UL_sidebar_tabs, RR_OT_refresh_sidebar_tabs,
           RR_OT_sidebar_tabs_info, RR_OT_enable_sidebar_sorting, RR_OT_move_sidebar_tab)

def register():
    global _enabled, _startup_attempt, _error
    if _enabled:
        return
    registered = []
    try:
        for cls in CLASSES:
            if not cls.__dict__.get("bl_rna"):
                bpy.utils.register_class(cls)
                registered.append(cls)
        if not hasattr(bpy.types.WindowManager, VIEW_PROPERTY):
            setattr(bpy.types.WindowManager, VIEW_PROPERTY,
                    bpy.props.CollectionProperty(type=RRSidebarTabItem, options={"SKIP_SAVE"}))
        if not hasattr(bpy.types.WindowManager, INDEX_PROPERTY):
            setattr(bpy.types.WindowManager, INDEX_PROPERTY,
                    bpy.props.IntProperty(default=0, min=0, options={"SKIP_SAVE"}))
        _error = ""
        _startup_attempt = 0
        _enabled = True
        if not bpy.app.timers.is_registered(_startup):
            bpy.app.timers.register(_startup, first_interval=STARTUP_DELAYS[0])
    except Exception:
        _enabled = False
        for name in (INDEX_PROPERTY, VIEW_PROPERTY):
            if hasattr(bpy.types.WindowManager, name):
                delattr(bpy.types.WindowManager, name)
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        raise


def unregister():
    global _enabled, _error, _anchors, _display_order
    _enabled = False
    if bpy.app.timers.is_registered(_startup):
        bpy.app.timers.unregister(_startup)
    failures = _remove_anchors(_anchors)
    _anchors = [cls for cls in _anchors if _registered_class(cls.__name__) is cls]
    if failures:
        _error = "; ".join(failures)
        print("[RR Helper] " + _error)
    _display_order = []
    _poll_errors.clear()
    for name in (INDEX_PROPERTY, VIEW_PROPERTY):
        if hasattr(bpy.types.WindowManager, name):
            delattr(bpy.types.WindowManager, name)
    for cls in reversed(CLASSES):
        if cls.__dict__.get("bl_rna"):
            bpy.utils.unregister_class(cls)
    _redraw()
