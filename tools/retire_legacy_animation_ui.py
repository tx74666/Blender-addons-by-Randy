"""One-time UI retirement after the real Unity-to-Blender Walk acceptance.

Dry-run by default. Preserve legacy services, preferences, operator IDs, enum
ordinals, and every animation asset. --apply is for the coordinated migration
owner only, after acquiring the Unity script-edit window.
"""

import argparse
import ast
import difflib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNITY = Path(r"D:\Unity Projects\RandomRealm2")


def replace_once(text, old, new):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one source anchor, got {count}: {old[:100]!r}")
    return text.replace(old, new, 1)


def retire_rr(text):
    text = replace_once(
        text,
        '"description": "RandomRealm helper tools for Unity handoff, builder assets, and animation sync.",',
        '"description": "RandomRealm helper tools for Unity handoff and builder assets.",',
    )
    text = replace_once(
        text,
        'context.scene.rr_builder_export_settings.ui_page = self.page',
        '# Keep the saved ANIMATION enum ordinal and old scripts compatible.\n'
        '        context.scene.rr_builder_export_settings.ui_page = "EXPORTER" if self.page == "ANIMATION" else self.page',
    )
    text = replace_once(
        text,
        'settings.ui_page in {"EXPORTER", "BAKE", "MODELING", "LAYOUT", "ANIMATION"}',
        'settings.ui_page in {"EXPORTER", "BAKE", "MODELING", "LAYOUT"}',
    )
    text = replace_once(text, '            elif active_page == "ANIMATION":\n                self.draw_animation_page(layout)\n', '')
    start = text.index('    def draw_animation_page(self, layout):\n')
    end = text.index('    def draw_page_tabs(self, layout, active_page):\n', start)
    text = text[:start] + text[end:]
    text = replace_once(text, '        self.draw_page_tab(second_row, active_page, "ANIMATION", "Animation", "ACTION")\n', '')
    for description in (
        'Open the RandomRealm character Animation.blend source file',
        'Open the Unity folder that receives Blender animation sync imports',
        'Ask the Unity editor to import or refresh Animation.blend',
    ):
        anchor = f'    bl_description = "{description}"\n'
        text = replace_once(text, anchor, anchor + '    bl_options = {"INTERNAL"}\n')
    ast.parse(text)
    return text


def retire_unity_sync(text):
    for constant in ('OpenPreferredBlendMenuPath', 'ImportPreferredBlendMenuPath',
                     'ExportImportedAnimationMenuPath', 'ExportSoldierClipActionsMenuPath'):
        text = replace_once(text, f'        [MenuItem({constant})]\n', '')
    old_open = '''        public static void OpenWindow()
        {
            BlenderCharacterAnimationSyncWindow window = GetWindow<BlenderCharacterAnimationSyncWindow>(WindowTitle);
            window.minSize = new Vector2(520f, 420f);
            window.Show();
            window.Focus();
        }'''
    new_open = '''        public static void OpenWindow()
        {
            // Preserve callers while keeping the character-animation UI in one place.
            if (!EditorApplication.ExecuteMenuItem("Tools/Character Designer/Animation"))
                Debug.LogWarning("Character Designer Animation is unavailable. Install the Character Designer Unity companion.");
        }'''
    text = replace_once(text, old_open, new_open)
    start = text.index('        void OnGUI()\n')
    end = text.index('        void DrawPaths()\n', start)
    text = text[:start] + '''        void OnGUI()
        {
            // Old saved editor layouts may still instantiate this class.
            EditorGUILayout.HelpBox("Character animation tools moved to Character Designer.", MessageType.Info);
            if (GUILayout.Button("Open Character Designer Animation", GUILayout.Height(28f)))
                OpenWindow();
            EditorGUILayout.LabelField(m_Status, EditorStyles.wordWrappedMiniLabel);
        }

''' + text[end:]
    text = replace_once(
        text,
        'Use RR Helper > Animation > Import Unity Clip Action in Blender to turn this JSON into an Action.',
        'Legacy clip JSON is retained for compatibility. Use Tools > Character Designer > Animation to send evaluated motion to Blender.',
    )
    return text


def retire_weapon_duplicate(text):
    return replace_once(text, '            DrawBlenderAnimationSyncTools();\n', '')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unity-project', type=Path, default=UNITY)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--diff', action='store_true')
    args = parser.parse_args()
    jobs = (
        (ROOT / 'addons/random_realm_builder_exporter/__init__.py', retire_rr),
        (args.unity_project / 'Assets/Scripts/Editor/BlenderCharacterAnimationSyncWindow.cs', retire_unity_sync),
        (args.unity_project / 'Assets/Scripts/Editor/AdventureCharacterWeaponTuningWindow.cs', retire_weapon_duplicate),
    )
    edits = []
    # Calculate and validate every replacement before changing any file.
    for path, transform in jobs:
        original_bytes = path.read_bytes()
        original = original_bytes.decode('utf-8-sig').replace('\r\n', '\n')
        updated = transform(original)
        if args.diff:
            print(''.join(difflib.unified_diff(original.splitlines(True), updated.splitlines(True), fromfile=str(path), tofile=str(path))))
        edits.append((path, original_bytes, updated))
        print(f'{"Prepared" if not args.apply else "Validated"}: {path}')
    if not args.apply:
        print('No files changed. Apply only after real Walk acceptance and Unity ownership handoff.')
        return
    for path, original_bytes, _updated in edits:
        if path.read_bytes() != original_bytes:
            raise RuntimeError(f'Source changed during migration preparation: {path}')
    for path, original_bytes, updated in edits:
        newline = '\r\n' if b'\r\n' in original_bytes else '\n'
        encoding = 'utf-8-sig' if original_bytes.startswith(b'\xef\xbb\xbf') else 'utf-8'
        with path.open('w', encoding=encoding, newline=newline) as output:
            output.write(updated)
        print(f'Updated: {path}')


if __name__ == '__main__':
    main()
