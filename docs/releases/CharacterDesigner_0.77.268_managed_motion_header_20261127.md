# Character Designer 0.77.268 · 动作编辑器顶栏

在已连接的动作工作区，Action Editor 顶栏默认显示 **Motions**、当前动作短名和 **All Actions**。在 **Motions** 里选动作，日常的 **Walk_N** 和 **Walk Right** 各占一项。展开 **All Actions** 可以使用 Blender 原生的 Action 和槽位选择器。归档版本、FK/IK 派生以及 Rigging Inputs 继续作为底层 Action 保存。

用 X 的保存文件在隔离 Blender 5.2 中核对了这两个视图。切换时，当前 Action、槽位、帧、NLA、骨架和目录条目保持一致。动作浏览器的 43 项针对性测试通过。另有 6 项旧集成模拟测试报错，已在 0.77.266 上复现，见验证目录的 `baseline_fixture.log`。

[原生验证](<D:/Blender/Projects/Character/X/validation/managed_motion_header_20261127/native268.json>) · [交付回执](<D:/Blender/Projects/Character/X/validation/managed_motion_header_20261127/delivery268.json>) · [完整投影](<D:/Blender/Projects/Character/X/validation/managed_motion_header_20261127/stage268_01/projection.json>)
