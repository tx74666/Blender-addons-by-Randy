# Character Designer 0.77.241 · Save Pose 骨架引用修复

Save Pose 只读取当前求值姿势并生成独立 Action。旧版沿用了修改骨架显示的单一引用检查，把动作持有的 Armature 引用也算作共用。新版验证实际使用骨架的 Object，允许一个角色带有额外 Action 引用，链接骨架、覆盖对象及第二个共用骨架的真实对象继续受保护。

普通 Controls 的保存使用已有的姿势检查，验证实际脚控形状、身体约束及驱动。显示集合容器由维护或重建流程管理。Original Bones 仍使用已认证的原骨 Rest 与约束检查。

真实 X 副本复现旧报错，确认 Armature 有 3 个引用。站姿通过共享入口生成 56 根骨骼的单帧资产，其中两手共 30 根指骨。回读的最大矩阵误差约 0.0000006，原工作 Action、关键帧、选取、网格和骨架共 20 项状态保持一致。另一个实际 Object 共用同一骨架时仍拒绝。空中姿势确有剪切时保留精确捕获的拒绝条件。[真实 X 回读](<C:/Users/Randy/.codex/visualizations/2026/11/17/01a1ea71-a5c1-7bf0-b904-da5ed3ad9615/save_pose_guard/native03/result.json>)

27 项原生与界面检查通过。隔离热刷新从 0.77.240 到 0.77.241 保留动作、选取、姿势及未打键旋转。完整投影只改保存入口和版本。这次验收覆盖保存与回读。对 X 的全 IK 动作直接套用 Neutral 在探索候选中触发姿势匹配保护，正式共享库尚未发布 Neutral。[回归检查](<C:/Users/Randy/.codex/visualizations/2026/11/17/01a1ea71-a5c1-7bf0-b904-da5ed3ad9615/save_pose_guard/regressions01.json>) · [热刷新](<D:/Blender/Projects/Character/X/validation/save_pose_guard_20261124/refresh01.json>)

正式 ZIP、Blender 5.2 与 X 两处安装均核对 310 个文件。[安装包](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.241.zip>) · [交付回执](<D:/Blender/Projects/Character/X/validation/save_pose_guard_20261124/delivery241.json>)
