"""Explicit discovery and official enable of already-installed physics add-ons.

Drawing reads cached evidence only. Discovery, source hashes and registration
proofs run on an explicit Check/Enable action. No package is downloaded,
preferences saved, foreign add-on disabled, or simulation started here.
"""
import ast
import hashlib
import importlib
from pathlib import Path
import sys
import tomllib


_CACHE = {}
_OWNED_REGISTRATIONS = {}
_KINDS = {'HAIR': ('Wiggle 2 2.2.4', 'skirt_wiggle'),
          'SKIRT': ('Wiggle 2 2.2.4', 'skirt_wiggle')}
_BACKEND_ID = 'WIGGLE2'


class PhysicsDependencyError(ValueError):
    pass


def _adapter(component):
    return importlib.import_module(__package__ + '.' + _KINDS[component][1])


def _result(component, state='UNCHECKED', *, installed=None, enabled=None,
            module=None, message='', details=(), enable_available=False):
    return dict(component=component, backend=_BACKEND_ID, label=_KINDS[component][0], state=state,
        installed=installed, enabled=enabled, available=state == 'READY',
        enable_available=enable_available, module=module, message=message,
        details=list(details), preferences_saved=False)


def _kind(module):
    info = getattr(module, 'bl_info', None)
    if isinstance(info, dict) and info.get('name') == 'Wiggle 2':
        return _BACKEND_ID
    path = getattr(module, '__file__', None)
    if path:
        manifest = Path(path).parent / 'blender_manifest.toml'
        if manifest.is_file():
            try:
                if tomllib.loads(manifest.read_text(encoding='utf8')).get('id') == 'wiggle_bones':
                    return 'LEGACY'
            except (OSError, ValueError):
                pass
    return None


def _source_proof(component, module):
    path = Path(module.__file__).resolve()
    adapter = _adapter(component)
    if tuple(module.bl_info.get('version', ())) != (2, 2, 4):
        raise PhysicsDependencyError('Installed Wiggle 2 must be version 2.2.4 for Hair and Skirt.')
    hashes = {path.name: adapter.BACKEND_SHA256}
    for filename, expected in hashes.items():
        payload = (path.parent / filename).read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(payload).hexdigest() != expected:
            raise PhysicsDependencyError('Installed physics source differs from the verified release: ' + filename)


def _inventory(addon_utils):
    rows = []
    for module in addon_utils.modules(refresh=True):
        backend = _kind(module)
        if backend is None:
            continue
        name = module.__name__
        _saved, enabled = addon_utils.check(name)
        rows.append(dict(backend=backend, module=name, object=module, enabled=bool(enabled),
                         path=str(Path(module.__file__).resolve())))
    return rows


def _registration_state(extra_modules=()):
    """Read native schema/handler identities, including non-installed modules."""
    import bpy
    schema = []
    for owner in (bpy.types.Scene, bpy.types.Object, bpy.types.PoseBone):
        for prop in owner.bl_rna.properties:
            if prop.identifier.startswith('wiggle'):
                # unregister_class can leave .wiggle PointerRNA referring to
                # freed StructRNA. Even reading fixed_type can native-crash.
                # PropertyRNA itself remains in its owning built-in struct;
                # compare its opaque address, never dereference its target.
                schema.append((owner.__name__, prop.identifier, prop.type, prop.as_pointer()))
    classes = set()
    for name in dir(bpy.types):
        if name.lower().startswith('wiggle'):
            value = getattr(bpy.types, name)
            if isinstance(value, type) and getattr(value, 'is_registered', False):
                classes.add((name, id(value)))
    for module in extra_modules:
        if module is None:
            continue
        for name, value in vars(module).items():
            if (isinstance(value, type) and getattr(value, 'is_registered', False)
                    and getattr(value, '__module__', '').startswith(module.__name__)):
                classes.add((name, id(value)))
    handlers = {name: tuple(id(function) for function in getattr(bpy.app.handlers, name))
                for name in ('frame_change_pre', 'frame_change_post', 'render_pre',
                             'render_post', 'render_cancel', 'load_post')}
    known_named_handlers = [function.__name__ for name in handlers
        for function in getattr(bpy.app.handlers, name)
        if getattr(function, '__name__', '').startswith('wiggle_')]
    return dict(schema=sorted(schema), classes=sorted(classes), handlers=handlers,
                wiggle_named_handlers=known_named_handlers)


def _flat_declarations(module):
    """Literal constructor contract from the already hash-verified 2.2.4 source."""
    tree = ast.parse(Path(module.__file__).read_bytes())
    register = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'register')
    declarations = {}
    for node in ast.walk(register):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Attribute):
            continue
        constructor = call.func.attr
        if constructor not in {'BoolProperty', 'FloatProperty', 'EnumProperty', 'PointerProperty'}:
            continue
        for target in node.targets:
            if not isinstance(target, ast.Attribute) or target.attr == 'wiggle':
                continue
            owner_expr = ast.unparse(target.value)
            if owner_expr not in {'bpy.types.Scene', 'bpy.types.Object', 'bpy.types.PoseBone'}:
                continue
            values = {}
            for keyword in call.keywords:
                if keyword.arg in {'update', 'poll'}:
                    continue  # RNA exposes no callback getter; never execute it.
                if keyword.arg == 'type':
                    values['fixed_type'] = ast.unparse(keyword.value)
                else:
                    values[keyword.arg] = ast.literal_eval(keyword.value)
            declarations[(owner_expr.rsplit('.', 1)[1], target.attr)] = constructor, values
    return declarations


def _verified_flat_residue(component, module, native):
    """Recognize only previously authenticated, unchanged inactive Wiggle RNA.

    Official unregister leaves dangling PG pointers. Their targets cannot be
    safely inspected; a prior live proof is required. No RNA is changed here.
    """
    if (not native['schema'] or native['classes']
            or native['wiggle_named_handlers']):
        return False
    owned = _OWNED_REGISTRATIONS.get((_BACKEND_ID, module.__name__))
    if (owned is not None and owned['module_identity'] == id(sys.modules.get(module.__name__))
            and owned['path'] == str(Path(module.__file__).resolve())
            and owned['schema'] == native['schema']):
        # Captured only after the original adapter authenticated this exact
        # source, registered schema and handlers while the pointers were live.
        # The old shared PG target may now be freed: do not inspect it.
        return True
    return False  # Unknown pointer provenance is never guessed from its name.


def _matches_native_schema(module, native):
    kinds = {'BoolProperty': 'BOOLEAN', 'FloatProperty': 'FLOAT',
             'EnumProperty': 'ENUM', 'PointerProperty': 'POINTER'}
    expected = {key: kinds[declaration[0]] for key, declaration in _flat_declarations(module).items()}
    expected.update({(owner, 'wiggle'): 'POINTER' for owner in ('Scene', 'Object', 'PoseBone')})
    actual = {(owner, name): kind for owner, name, kind, _token in native['schema']}
    return actual == expected


def status(component, *, refresh=False):
    if component not in _KINDS:
        raise PhysicsDependencyError('Choose Hair or Skirt physics dependency.')
    if not refresh:
        cached = _CACHE.get(component)
        if cached is None or cached.get('backend') != _BACKEND_ID:
            return _result(component, message='Check or enable ' + _KINDS[component][0] + ' before starting.',
                           enable_available=True)
        return dict(cached, details=list(cached['details']))
    import addon_utils
    try:
        rows = _inventory(addon_utils)
        candidates = [row for row in rows if row['backend'] == _BACKEND_ID]
        if not candidates:
            result = _result(component, 'MISSING', installed=False, enabled=False,
                message=_KINDS[component][0] + ' is not installed. Install its verified release first.',
                details=['Enable only uses Blender-installed add-ons; it never downloads or copies a package.'])
        else:
            enabled_rows = [row for row in candidates if row['enabled']]
            selected = enabled_rows if enabled_rows else candidates
            if len(selected) != 1:
                result = _result(component, 'AMBIGUOUS', installed=True,
                    enabled=bool(enabled_rows), message='More than one matching physics add-on is installed or enabled.',
                    details=[row['module'] + ': ' + row['path'] for row in selected])
            else:
                row = selected[0]
                try:
                    _source_proof(component, row['object'])
                except (ValueError, OSError, AttributeError, TypeError) as error:
                    result = _result(component, 'INCOMPATIBLE', installed=True, enabled=row['enabled'],
                        module=row['module'], message=str(error), details=[row['path']])
                else:
                    conflicting = [other for other in rows if other['backend'] != _BACKEND_ID and other['enabled']]
                    if conflicting:
                        result = _result(component, 'CONFLICT', installed=True, enabled=row['enabled'],
                            module=row['module'], message='A legacy Wiggle backend owns the shared .wiggle RNA schema.',
                            details=[other['module'] + ': ' + other['path'] for other in conflicting])
                    elif not row['enabled']:
                        native = _registration_state()
                        residue = _verified_flat_residue(component, row['object'], native)
                        if (native['schema'] or native['classes'] or native['wiggle_named_handlers']) and not residue:
                            result = _result(component, 'CONFLICT', installed=True, enabled=False,
                                module=row['module'], message='A registered or leftover Wiggle schema is already present.',
                                details=['No backend was enabled over the existing native registration.',
                                         repr(native['schema']), repr(native['classes']),
                                         repr(native['wiggle_named_handlers'])])
                        else:
                            result = _result(component, 'DISABLED', installed=True, enabled=False,
                                module=row['module'], message=_KINDS[component][0] + ' is installed but not enabled.',
                                details=[row['path']], enable_available=True)
                            result['verified_flat_residue'] = residue
                    else:
                        try:
                            backend = _adapter(component)._backend()
                            if (backend.__name__ != row['module']
                                    or str(Path(backend.__file__).resolve()) != row['path']):
                                raise PhysicsDependencyError('The registered Wiggle 2 is not the inspected installed module.')
                            if not _matches_native_schema(backend, _registration_state()):
                                raise PhysicsDependencyError('The registered Wiggle schema contains missing or unknown shared fields.')
                        except (ValueError, RuntimeError, AttributeError, ReferenceError, TypeError) as error:
                            result = _result(component, 'INCOMPATIBLE', installed=True, enabled=True,
                                module=row['module'], message=str(error),
                                details=['The add-on is enabled, but its source/schema/core/handler proof failed.', row['path']])
                        else:
                            _remember_registration(component, row['module'])
                            result = _result(component, 'READY', installed=True, enabled=True,
                                module=row['module'], message=_KINDS[component][0] + ' is enabled and verified.',
                                details=['Hair and Skirt share this backend; preview permissions are independent.', row['path']])
    except (ValueError, RuntimeError, OSError, AttributeError, TypeError) as error:
        result = _result(component, 'INCOMPATIBLE', message='Physics dependency inspection failed: ' + str(error))
    _publish(result)
    return dict(result, details=list(result['details']))


def _invalidate(component):
    # Both features use the same verified module. An explicit dependency action
    # invalidates its one availability proof, without stopping either session.
    adapter = _adapter(component)
    adapter._BACKEND_CACHE, adapter._BACKEND_CACHE_TIME = None, 0.0
    for name in ('hair_wiggle_adapter', 'hair_wiggle2_adapter'):
        hair = sys.modules.get(__package__ + '.' + name)
        if hair is not None and hasattr(hair, '_AVAILABLE_CACHE'):
            hair._AVAILABLE_CACHE, hair._AVAILABLE_CACHE_TIME = None, 0.0


def _publish(result):
    """One backend proof, projected as the two independent feature permissions."""
    for component in _KINDS:
        _CACHE[component] = dict(result, component=component, details=list(result['details']))


def enable(component):
    """Enable one source-verified installed module, without saving preferences."""
    import addon_utils
    inspected = status(component, refresh=True)
    if inspected['state'] == 'READY':
        _invalidate(component)
        return inspected
    if inspected['state'] != 'DISABLED':
        raise PhysicsDependencyError(inspected['message'] + (' ' + '; '.join(inspected['details']) if inspected['details'] else ''))
    name = inspected['module']
    errors = []
    failed_modules = []
    native_before = _registration_state()
    def handle_error(*args):
        errors.append(str(args[0] if args else sys.exc_info()[1]))
        failed_modules.append(sys.modules.get(name))
    try:
        module = addon_utils.enable(name, default_set=False, persistent=False, handle_error=handle_error)
        if module is None or errors or not addon_utils.check(name)[1]:
            raise PhysicsDependencyError('Blender could not enable ' + name + ': ' + '; '.join(errors))
        _invalidate(component)
        checked = status(component, refresh=True)
        if checked['state'] != 'READY':
            raise PhysicsDependencyError(checked['message'])
        return checked
    except Exception as error:
        # Roll back only the module this action changed from disabled to
        # enabled; never disable the already-running other backend.
        cleanup_errors = []
        try:
            # Official enable may have cleared __addon_enabled__ or removed
            # sys.modules after a partial register failure. Call official
            # disable regardless; inspect native leftovers independently.
            addon_utils.disable(name, default_set=False,
                handle_error=lambda *args: cleanup_errors.append(str(args[0] if args else sys.exc_info()[1])))
        except Exception as cleanup_error:
            cleanup_errors.append(str(cleanup_error))
        _invalidate(component)
        result = status(component, refresh=True)
        result['message'] = 'Physics add-on enable failed: ' + str(error)
        result['details'].extend(cleanup_errors)
        native_after = _registration_state(failed_modules)
        result['cleanup_exact'] = native_after == native_before
        if not result['cleanup_exact']:
            result.update(state='CLEANUP_FAILED', available=False, enable_available=False,
                          message=result['message'] + ' Native registration cleanup is incomplete; do not retry activation.')
            result['details'].append('Native differences: ' + repr({key: native_after[key]
                for key in native_before if native_before[key] != native_after[key]}))
        _publish(result)
        raise PhysicsDependencyError(result['message'] + (' Cleanup: ' + '; '.join(cleanup_errors) if cleanup_errors else '')) from error


def describe_failure(component, error):
    """Publish a fresh dependency diagnosis after a refused explicit start."""
    inspected = status(component, refresh=True)
    if inspected['state'] == 'READY':
        return str(error)  # Ownership/context/callback refusals are separate.
    return inspected['message'] + ' Start refused: ' + str(error)


def note_verified(component, backend):
    """Record an actual adapter proof, without rescanning the add-on catalog."""
    if component not in _KINDS:
        raise PhysicsDependencyError('Choose Hair or Skirt physics dependency.')
    name = backend['name'] if isinstance(backend, dict) else backend.__name__
    if not _remember_registration(component, name):
        raise PhysicsDependencyError('The registered Wiggle 2 contains missing or unknown shared schema fields.')
    _publish(_result(component, 'READY', enabled=True, module=name,
        message=_KINDS[component][0] + ' is registered and verified.',
        details=['Hair and Skirt share this backend; their preview permissions and sessions are independent.',
                 'The source, native schema and exact handler checks passed on the explicit start action.']))


def _remember_registration(component, module_name):
    module = sys.modules.get(module_name)
    if module is not None and getattr(module, '__file__', None):
        native = _registration_state()
        if not _matches_native_schema(module, native):
            return  # Never adopt extra native fields as this source's residue.
        _OWNED_REGISTRATIONS[(_BACKEND_ID, module_name)] = dict(
            module_identity=id(module), path=str(Path(module.__file__).resolve()),
            schema=native['schema'])
        return True
    return False
