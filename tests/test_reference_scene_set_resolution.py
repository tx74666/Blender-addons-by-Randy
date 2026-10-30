"""Actual reference resolver regression tests; fake RNA, no Blender QA."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
import uuid

SOURCE = Path(os.environ.get('REFERENCE_ENUM_TEST_SOURCE', str(
    Path(__file__).resolve().parents[1] / 'addons/character_designer/reference_views.py')))


def load_source():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    names = {'_marker_matches', '_scene_managed_set_summaries', '_resolved_scene_set_summary'}
    constants = {'REFERENCE_GENERATOR_ID', 'REFERENCE_OBJECT_VERSION',
        'REFERENCE_VIEW_DIRECTIONS', '_GENERATOR_KEY', '_VERSION_KEY', '_SET_ID_KEY',
        '_SET_NAME_KEY', '_KIND_KEY', '_VIEW_KEY', '_ENABLED_KEY'}
    nodes = [n for n in tree.body if
        isinstance(n, ast.FunctionDef) and n.name in names or
        isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in n.targets)]
    namespace = {'uuid': uuid}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    if not names <= namespace.keys() or not constants <= namespace.keys():
        raise AssertionError('Actual reference resolver definitions missing')
    return namespace


class ReadOnlySettings:
    def __init__(self, active='', choice='', forbid=()):
        object.__setattr__(self, '_values', {'active_set_id': active, 'scene_set_choice': choice})
        object.__setattr__(self, 'reads', [])
        object.__setattr__(self, 'forbid', set(forbid))

    def __getattr__(self, name):
        if name not in self._values:
            raise AttributeError(name)
        self.reads.append(name)
        if name in self.forbid:
            raise AssertionError('Forbidden RNA getter: ' + name)
        return self._values[name]

    def __setattr__(self, name, value):
        raise AssertionError('Resolver must not write RNA: ' + name)


class ReferenceSceneSetResolutionTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_source()
        self.resolve = self.ns['_resolved_scene_set_summary']
        self.summaries = self.ns['_scene_managed_set_summaries']
        self.a = '11111111-1111-4111-8111-111111111111'
        self.b = '22222222-2222-4222-8222-222222222222'

    def root(self, set_id, name):
        n = self.ns
        return {n['_GENERATOR_KEY']: n['REFERENCE_GENERATOR_ID'],
            n['_VERSION_KEY']: n['REFERENCE_OBJECT_VERSION'], n['_KIND_KEY']: 'ROOT',
            n['_SET_ID_KEY']: set_id, n['_SET_NAME_KEY']: name}

    def scene(self, *objects):
        return NS(objects=list(objects))

    def test_empty_scene_reads_neither_rna_getter(self):
        settings = ReadOnlySettings(forbid={'active_set_id', 'scene_set_choice'})
        self.assertIsNone(self.resolve(settings, self.scene()))
        self.assertEqual(settings.reads, [])

    def test_missing_scene_reads_neither_rna_getter(self):
        settings = ReadOnlySettings(forbid={'active_set_id', 'scene_set_choice'})
        self.assertIsNone(self.resolve(settings, None))
        self.assertEqual(settings.reads, [])

    def test_no_recoverable_markers_reads_neither_rna_getter(self):
        malformed = self.root('not-a-uuid', 'Malformed')
        wrong_version = self.root(self.a, 'Old')
        wrong_version[self.ns['_VERSION_KEY']] = 999
        settings = ReadOnlySettings(forbid={'active_set_id', 'scene_set_choice'})
        self.assertIsNone(self.resolve(settings, self.scene({}, malformed, wrong_version)))
        self.assertEqual(settings.reads, [])

    def test_settings_none_retains_single_set_fallback(self):
        scene = self.scene(self.root(self.a, 'A'))
        self.assertEqual(self.resolve(None, scene), self.summaries(scene)[0])
        scene.objects.append(self.root(self.b, 'B'))
        self.assertIsNone(self.resolve(None, scene))

    def test_valid_active_id_wins_without_enum_read(self):
        scene = self.scene(self.root(self.a, 'A'), self.root(self.b, 'B'))
        settings = ReadOnlySettings(active=self.b, choice=self.a, forbid={'scene_set_choice'})
        self.assertEqual(self.resolve(settings, scene)['set_id'], self.b)
        self.assertNotIn('scene_set_choice', settings.reads)

    def test_enum_recovers_valid_choice_when_active_id_stale(self):
        scene = self.scene(self.root(self.a, 'A'), self.root(self.b, 'B'))
        settings = ReadOnlySettings(active='gone', choice=self.a)
        self.assertEqual(self.resolve(settings, scene)['set_id'], self.a)
        self.assertEqual(settings._values, {'active_set_id': 'gone', 'scene_set_choice': self.a})

    def test_multiple_sets_placeholder_and_stale_choice_do_not_choose(self):
        scene = self.scene(self.root(self.a, 'A'), self.root(self.b, 'B'))
        for choice in ('__SELECT__', 'gone', ''):
            with self.subTest(choice=choice):
                self.assertIsNone(self.resolve(ReadOnlySettings(active='gone', choice=choice), scene))

    def test_single_set_stale_choice_retains_existing_fallback(self):
        scene = self.scene(self.root(self.a, 'A'))
        settings = ReadOnlySettings(active='gone', choice='gone')
        self.assertEqual(self.resolve(settings, scene), self.summaries(scene)[0])
        self.assertEqual(settings._values, {'active_set_id': 'gone', 'scene_set_choice': 'gone'})

    def test_enum_attribute_or_type_failure_retains_fallback(self):
        for error in (AttributeError, TypeError):
            with self.subTest(error=error):
                class MissingChoice(ReadOnlySettings):
                    def __getattr__(self, name):
                        if name == 'scene_set_choice':
                            raise error(name)
                        return super().__getattr__(name)
                scene = self.scene(self.root(self.a, 'A'))
                self.assertEqual(self.resolve(MissingChoice(), scene)['set_id'], self.a)
                scene.objects.append(self.root(self.b, 'B'))
                self.assertIsNone(self.resolve(MissingChoice(), scene))

    def test_current_scene_is_rescanned_after_sets_appear_or_disappear(self):
        scene = self.scene()
        empty = ReadOnlySettings(forbid={'active_set_id', 'scene_set_choice'})
        self.assertIsNone(self.resolve(empty, scene))
        scene.objects.append(self.root(self.a, 'A'))
        self.assertEqual(self.resolve(ReadOnlySettings(), scene)['set_id'], self.a)
        scene.objects.clear()
        self.assertIsNone(self.resolve(empty, scene))
        self.assertEqual(empty.reads, [])


if __name__ == '__main__':
    unittest.main()
