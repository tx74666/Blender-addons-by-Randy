# Character Designer 0.77.294 · Unity 武器库

Unity **Adventure → Weapon Library → Export Blender Library** 将现有武器统一写到 [Weapons 交换目录](<D:/Blender/Projects/Character/Animation/UnityExports/Weapons>)。以实际 Prefab GUID 识别武器，单 FBX 和多网格 Prefab 均可导出。

Blender **Animation → Animation Composition → Equip → Sync Unity Library** 导入清单。再次打开 **Equip** 可选择要挂在角色手上的武器。重复同步保留本地握点和名称。Unity 资产有新版时，提示并保留当前本地校准。

[Weapons.blend](<D:/Blender/Projects/Character/Animation/UnityExports/Weapons/Weapons.blend>) 是独立 Blender Collection 资产库，已存入 Blaster、HomingRPG、Launcher、Shotgun、SniperRifle 和 Sword。记录可见网格与模型尺寸，供摆姿和动画参考。各武器的握点与手指动作可再校准。

Unity 原生 EditMode 通过。Blender 5.2 原生验证覆盖六件模型的三角面和边界、保存重开、重复同步、切换 Blaster/HomingRPG/Sword、保留本地握点与原 Action、读取旧清单。正式 X 观察到正在进行的 Key Reduction 预览，交付时保留该预览和未保存工作。[交付记录](<D:/Blender/Projects/Character/X/validation/unified_weapon_library_20261128/delivery294.json>)
