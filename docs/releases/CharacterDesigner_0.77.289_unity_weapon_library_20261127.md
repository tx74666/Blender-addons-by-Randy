# Character Designer 0.77.289 · Unity 武器库

在 RandomRealm2 的 Adventure **Weapon Library** 点 **Export Blender Library…**。在 Blender 打开 **Miscellaneous → Weapon → Import Unity Library**，选择生成的 `weapon_library.json`。库中武器按 Unity Prefab GUID 匹配，显示容易识别的武器名。选中武器后点 **Attach** 挂到右手，用 **Edit Profile** 调握点。

同一版本重复导入会复用原来的武器和握点。遇到不同的 Unity 版本，插件会报告差异并保留本地配置。导入内容用于握姿制作，包含实际 Prefab 的可见网格。现有捕获条件要求可识别的单一 FBX 来源，不符合条件的武器在清单中列原因。

Unity 6000.5.9f1 和 Blender 5.2 实际验证 Blaster、Sword。测试覆盖重复导入、网格尺寸、右手挂载和保存重开。HomingRPG 被识别为暂不可导出。正式 ZIP、Blender 5.2 与 X 的 329 个文件逐一核对一致。菜单在正常刷新插件后可见。[交付记录](<D:/Blender/Projects/Character/X/validation/unity_weapon_library_20261127/delivery289.json>) · [原生验证](<D:/Blender/Projects/Character/X/validation/unity_weapon_library_20261127/blender289.json>) · [可追加的武器库](<D:/Blender/Projects/Character/X/validation/unity_weapon_library_20261127/RandomRealm2 Weapons.blend>)
