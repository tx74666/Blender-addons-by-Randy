# Character Designer 0.77.291 · 一键装备与上半身动作

在 **Animation → Animations → Upper-Body Composition** 展开 **Equip**，选择一件已导入的装备。第一次装备默认挂到右手。换装备沿用当前挂点，使用该装备保存的位移、旋转和握点。

在 **Miscellaneous → Weapon → Edit Profile** 可给一件装备设置 **Equip Action**。选这件装备时，Action 从当前帧叠在正在播放的基础动作上，最后一帧继续保持。独立的 **Add Upper-Body Action** 和 **Return to Base Action** 也在 Animation 的同一栏。

在真实 Cosha 的 Walk Right FK 上验证了 Blaster 举枪。五个采样时刻的七根下半身骨骼保持原矩阵。Blaster 与 Sword 切换、错误动作回滚、重开、重复点击及原导入流程通过。示例举枪 Action 用于验证连接。用户可在自己的档案为武器指定正式作品动作。[真实 Cosha 验证](<D:/Blender/Projects/Character/X/validation/weapon_equip_20261127/cosha291.json>) · [切换与回滚](<D:/Blender/Projects/Character/X/validation/weapon_equip_20261127/probe291.json>)

Blender 5.2 和 X 两处安装逐件核对 329 个文件。当前打开的窗口可正常刷新插件后使用。[交付记录](<D:/Blender/Projects/Character/X/validation/weapon_equip_20261127/delivery291.json>)
