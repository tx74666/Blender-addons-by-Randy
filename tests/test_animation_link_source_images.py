"""Image restoration regression checks without importing bpy or the add-on."""

import ast
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_link_source.py'
tree = ast.parse(SOURCE.read_text(encoding='utf-8'), filename=str(SOURCE))
helper = next(node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name == '_restore_image_settings')
namespace = {}
exec(compile(ast.Module(body=[helper], type_ignores=[]), str(SOURCE), 'exec'), namespace)
restore = namespace['_restore_image_settings']


class EnumSetting:
    def __init__(self, current, valid, *, readonly=False):
        self._value = current
        self.valid = valid
        self.readonly = readonly
        self.assignments = []

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self.assignments.append(value)
        if self.readonly:
            raise AttributeError('This unchanged native setting is read-only.')
        if value not in self.valid:
            raise ValueError('Invalid enum: ' + repr(value))
        self._value = value


class Colorspace(EnumSetting):
    name = property(lambda self: self.value,
                    lambda self, value: setattr(self, 'value', value))


class Image:
    def __init__(self, colorspace, alpha='STRAIGHT', *, readonly=False):
        self.colorspace_settings = Colorspace(colorspace, {'sRGB', 'Non-Color', 'Linear'}, readonly=readonly)
        self.alpha = EnumSetting(alpha, {'STRAIGHT', 'PREMUL', 'NONE', 'CHANNEL_PACKED'}, readonly=readonly)

    alpha_mode = property(lambda self: self.alpha.value,
                          lambda self, value: setattr(self.alpha, 'value', value))


class RestoreImageSettingsTests(unittest.TestCase):
    def test_unset_render_result_colorspace_is_preserved_without_invalid_assignment(self):
        image = Image('')
        restore([(image, '', 'STRAIGHT')])
        self.assertEqual(image.colorspace_settings.name, '')
        self.assertEqual(image.colorspace_settings.assignments, [])
        self.assertEqual(image.alpha.assignments, [])

    def test_unchanged_readonly_settings_are_never_assigned(self):
        image = Image('sRGB', 'PREMUL', readonly=True)
        restore([(image, 'sRGB', 'PREMUL')])
        self.assertEqual(image.colorspace_settings.assignments, [])
        self.assertEqual(image.alpha.assignments, [])

    def test_changed_valid_colorspace_is_restored_once(self):
        image = Image('Non-Color')
        restore([(image, 'sRGB', 'STRAIGHT')])
        self.assertEqual(image.colorspace_settings.name, 'sRGB')
        self.assertEqual(image.colorspace_settings.assignments, ['sRGB'])
        self.assertEqual(image.alpha.assignments, [])

    def test_changed_alpha_is_restored_without_touching_colorspace(self):
        image = Image('sRGB', 'PREMUL')
        image.colorspace_settings.readonly = True
        restore([(image, 'sRGB', 'STRAIGHT')])
        self.assertEqual(image.alpha_mode, 'STRAIGHT')
        self.assertEqual(image.alpha.assignments, ['STRAIGHT'])
        self.assertEqual(image.colorspace_settings.assignments, [])

    def test_importer_changed_unset_colorspace_fails_closed(self):
        image = Image('sRGB')
        with self.assertRaisesRegex(ValueError, 'Invalid enum'):
            restore([(image, '', 'STRAIGHT')])
        self.assertEqual(image.colorspace_settings.name, 'sRGB')
        self.assertEqual(image.colorspace_settings.assignments, [''])

    def test_mixed_images_restore_both_changed_settings_and_preserve_unset_image(self):
        render_result = Image('', readonly=True)
        imported_texture = Image('Linear', 'CHANNEL_PACKED')
        restore([(render_result, '', 'STRAIGHT'), (imported_texture, 'Non-Color', 'NONE')])
        self.assertEqual(render_result.colorspace_settings.assignments, [])
        self.assertEqual(render_result.alpha.assignments, [])
        self.assertEqual(imported_texture.colorspace_settings.name, 'Non-Color')
        self.assertEqual(imported_texture.alpha_mode, 'NONE')
        self.assertEqual(imported_texture.colorspace_settings.assignments, ['Non-Color'])
        self.assertEqual(imported_texture.alpha.assignments, ['NONE'])


if __name__ == '__main__':
    unittest.main()
