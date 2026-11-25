# Character Designer 0.77.246 · 关键帧精简预览

姿态模式下选好有动画的控制骨，点 Preview Key Reduction，在同一视口和当前帧切换 Original 与 Reduced。可以调整位置和旋转容差。Apply 将精简过的控制曲线写回原 Action，Discard 结束预览。用户正在编辑的临时 Natural Bind 姿势会阻止切换 Action。

在 X 副本的 In Air 第 3 帧，用默认容差 2 mm / 1° 精简左脚 IK。每个位置轴的关键点由 186 减到 62，其余 1,210 条曲线保持一致。实测 14 个帧及子帧中脚趾最大偏移为 1.533 mm。原生 GUI 验证了 Undo 和 Redo。三个既有 Physics 面板测试在 .245 和 .246 出现相同失败。[功能与测试范围](<D:/Blender/Projects/Character/X/validation/key_preview_20261125/acceptance246.json>)

Blender 5.2 用户插件目录和 X/addons 各核对 314 个文件，零差异。X 隔离副本从 .245 刷新至 .246，29 项状态一致。正式 X 窗口由 Animation 接续，本次磁盘交付未操作正式窗口。[隔离刷新](<D:/Blender/Projects/Character/X/validation/key_preview_delivery_20261125/refresh01.json>) · [安装包](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.246.zip>) · [磁盘交付](<D:/Blender/Projects/Character/X/validation/key_preview_delivery_20261125/delivery246.json>)
