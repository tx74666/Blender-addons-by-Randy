"""Pure proof that inactive PG targets and unverified rig users fail early."""
import ast
import hashlib
from pathlib import Path
import sys
import tempfile
import tomllib
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1] / 'addons/character_designer'


def fail(message):
    raise ValueError(message)


def function(filename, name, namespace):
    tree = ast.parse((ROOT / filename).read_text(encoding='utf8'))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / filename), 'exec'), namespace)
    return namespace[name]


class DangerousPointer:
    type = 'POINTER'
    @property
    def fixed_type(self):
        raise AssertionError('Dereferenced an inactive native PG target')


class GuardTests(unittest.TestCase):
    def test_all_classes_precede_any_pointer_target_for_both_backends(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b'# verified fixture\n'
            (root / 'wiggle_2.py').write_bytes(payload)
            (root / '__init__.py').write_bytes(payload)
            (root / 'blender_manifest.toml').write_text('id="wiggle_bones"\nversion="1.1.2"\n')
            owner = NS(bl_rna=NS(properties=NS(get=lambda name: NS(type='BOOLEAN')
                       if name != 'wiggle' else DangerousPointer())))
            bpy = NS(types=NS(Scene=owner, Object=owner, PoseBone=owner))
            live = NS(is_registered=True)
            inactive = NS(is_registered=False)
            skirt = ModuleType('fixture_wiggle_2')
            skirt.__file__ = str(root / 'wiggle_2.py')
            skirt.bl_info = {'name': 'Wiggle 2', 'version': (2, 2, 4)}
            skirt.reset_bone = lambda: None
            skirt.WiggleScene, skirt.WiggleObject, skirt.WiggleBone = live, live, inactive
            hair = ModuleType('fixture_wiggle_bones')
            hair.__file__ = str(root / '__init__.py')
            hair.properties = NS(WiggleScene=live, WiggleObject=live, WiggleBoneSettings=inactive)
            cases = [('skirt_wiggle.py', skirt, dict(ModuleType=ModuleType, BACKEND_NAME='Wiggle 2',
                       BACKEND_SHA256=hashlib.sha256(payload).hexdigest())),
                     ('hair_wiggle_adapter.py', hair, dict(tomllib=tomllib, BACKEND_VERSION='1.1.2',
                       _HASHES={'__init__.py': hashlib.sha256(payload).hexdigest()}))]
            for filename, module, constants in cases:
                with self.subTest(filename=filename):
                    namespace = dict(sys=NS(modules={module.__name__: module}), Path=Path,
                        hashlib=hashlib, bpy=bpy, _fail=fail, **constants)
                    backend = function(filename, '_backend', namespace)
                    with self.assertRaisesRegex(ValueError, 'disabled'):
                        backend()

    def test_preflights_use_existing_verified_single_user_proof(self):
        for filename in ('hair_wiggle_adapter.py', 'skirt_wiggle.py'):
            for allowed in (False, True):
                with self.subTest(filename=filename, allowed=allowed):
                    rig = NS(name='Rig', library=None, override_library=None, is_editable=True,
                             data=NS(library=None, users=6, pose_position='POSE'))
                    scene = NS(objects={'Rig': rig})
                    context = NS(scene=scene, mode='OBJECT')
                    source = NS(library=None, override_library=None, is_editable=True,
                                get=lambda key: rig)
                    proof = Mock(return_value=allowed)
                    package = ModuleType('_physics_guard_package')
                    package.limb_ik = NS(_single_armature_user=proof)
                    namespace = dict(__package__=package.__name__, bpy=NS(context=context), _fail=fail,
                                     _SESSION=None)
                    if filename.startswith('hair'):
                        registry = {'strands': [{'strand_id': 'one', 'bones': []}]}
                        namespace.update(hair=NS(RIG_KEY='rig'), strands=NS(read=lambda *a, **k: registry),
                            _profile=Mock(side_effect=RuntimeError('next strict guard')))
                        args = (context, source, registry, {'one': {}}, ('one',), None)
                    else:
                        class Source(dict):
                            library = override_library = None
                            is_editable = True
                        source = Source(rig=rig)
                        namespace.update(skirt=NS(RIG_KEY='rig', _require_controls_for_setup=lambda source: None,
                            read_record=lambda source: {'chains': []}),
                            _graph=Mock(side_effect=RuntimeError('next strict guard')))
                        args = (context, source, None)
                    preflight = function(filename, '_preflight', namespace)
                    with patch.dict(sys.modules, {package.__name__: package}):
                        with self.assertRaisesRegex(RuntimeError if allowed else ValueError,
                                                    'next strict guard' if allowed else 'single-user'):
                            preflight(*args)
                    proof.assert_called_once_with(rig)


if __name__ == '__main__':
    unittest.main()
