# Character Designer 0.77.285 · Convert to IK 后自动打开

在角色场景里手动转换单个片段时，转换成功后会自动打开新 IK Action。如果当前已经打开该片段，则在原帧重新评估 IK Action。等待期间切换片段、时间点或编辑姿势时，转换结果照常保留，当前编辑上下文继续使用。批量准备沿用原流程。

99 项相关测试及 Blender 5.2 隔离验证通过。后者给发布流程提供已转换 Action，验证原生 F-Curve、IK/FK 权重及驱动约束一起生效。Blender 5.2 和 X 各安装 328 个文件，0 差异。当前 X 会话未刷新。

[交付回执](<D:/Blender/Projects/Character/X/validation/convert_to_ik_activate_20261127/delivery285.json>)
