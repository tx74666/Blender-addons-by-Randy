"""Finger runtime registration preserves existing native property definitions."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


def subject():
    definitions = []

    def pointer_property(**kwargs):
        result = NS(**kwargs)
        definitions.append(result)
        return result

    types = NS(Scene=type('Scene', (), {}), Object=type('Object', (), {}))
    handlers = NS(**{name: [] for name in ('load_pre', 'undo_pre', 'redo_pre',
                                          'load_post', 'undo_post', 'redo_post')})
    shared = dict(bpy=NS(types=types, app=NS(handlers=handlers)),
                  PointerProperty=pointer_property)
    functions = {}
    for filename in ('finger_definition_ui.py', 'finger_bank_ui.py'):
        path = SOURCE / filename
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'register_runtime')
        namespace = dict(shared, CharacterDesignerFingerDefinition=object(),
                         CharacterDesignerFingerBank=object(),
                         _invalidate=lambda *_args: None, _resume=lambda *_args: None)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
        functions[filename] = namespace['register_runtime']
    return types, handlers, definitions, functions


class FingerRuntimeRegistrationTests(unittest.TestCase):
    def test_first_registration_creates_the_three_expected_pointer_definitions(self):
        types, _handlers, definitions, functions = subject()
        for register in functions.values():
            register()
        self.assertEqual(len(definitions), 3)
        self.assertTrue(hasattr(types.Scene, 'character_designer_finger_definition'))
        self.assertTrue(hasattr(types.Object, 'character_designer_finger_bank'))
        self.assertIs(types.Scene.character_designer_finger_setup.type, types.Object)

    def test_repeated_registration_retains_existing_definitions_and_handlers(self):
        types, handlers, definitions, functions = subject()
        for register in functions.values():
            register()
        before = (types.Scene.character_designer_finger_definition,
                  types.Object.character_designer_finger_bank,
                  types.Scene.character_designer_finger_setup)
        handler_ids = {name: tuple(map(id, entries)) for name, entries in vars(handlers).items()}
        for register in functions.values():
            register()
            register()
        after = (types.Scene.character_designer_finger_definition,
                 types.Object.character_designer_finger_bank,
                 types.Scene.character_designer_finger_setup)
        self.assertTrue(all(left is right for left, right in zip(before, after)))
        self.assertEqual(len(definitions), 3)
        self.assertEqual({name: tuple(map(id, entries)) for name, entries in vars(handlers).items()}, handler_ids)

    def test_existing_definitions_are_preserved_without_constructor_or_schema_writes(self):
        types, _handlers, definitions, functions = subject()
        originals = tuple(object() for _index in range(3))
        types.Scene.character_designer_finger_definition = originals[0]
        types.Object.character_designer_finger_bank = originals[1]
        types.Scene.character_designer_finger_setup = originals[2]
        for register in functions.values():
            register()
        actual = (types.Scene.character_designer_finger_definition,
                  types.Object.character_designer_finger_bank,
                  types.Scene.character_designer_finger_setup)
        self.assertTrue(all(left is right for left, right in zip(originals, actual)))
        self.assertEqual(definitions, [])


if __name__ == '__main__':
    unittest.main()
