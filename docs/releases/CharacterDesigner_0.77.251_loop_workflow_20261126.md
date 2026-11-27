# Character Designer 0.77.251 · 循环动作准备与减键

Walk_N 的源周期为 23.2 帧。启用整帧循环对齐后，Prepare 生成 23 帧周期，第 24 帧保留闭合姿势，播放预览使用第 1–23 帧。切换动作时会记住该动作的自定义预览范围。实际测试中，Walk_N 的预览设为 5–9 后，切到 Neutral 显示单帧，再返回 Walk_N 恢复为 5–9；重置后恢复为 1–23。

减键预览支持普通 Repeat Motion Cycles。Walk_N 的 40,691 个关键点经周期对齐后，逐点比较的数值偏差为零。支持的循环曲线、特殊修改器处理、精度及耗时见[功能验收](<D:/Blender/Projects/Character/X/validation/loop_workflow_20261126/acceptance251.json>)。

在独立的 Blender 5.2 会话中，以 .250 开启 In Air 左脚预览并切至 Reduced，再热刷新至 .251。31 项原有状态一致，切换 Original、Reduced 和 Discard 均成功，原动作的 1,059 条曲线指纹一致。第一次验收脚本误将新版新增的空 skipped 字段计为变化。第二次同时验证了所有旧字段和新增字段。正式 Blender 及 X/addons 各核对 318 个文件、零差异。正式 X 窗口由使用者保留。[刷新验收](<D:/Blender/Projects/Character/X/validation/loop_workflow_delivery_20261126/refresh02.json>) · [交付回执](<D:/Blender/Projects/Character/X/validation/loop_workflow_delivery_20261126/delivery251.json>) · [安装包](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.251.zip>)
