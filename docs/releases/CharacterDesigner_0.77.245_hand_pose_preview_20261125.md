# Character Designer 0.77.245 · 左手姿势与共享预览

Natural Bind 接管前臂时，普通手指仍可独立摆动。对经过验证的原生手指，Apply Pose 现在使用当前姿势的四肢检查。X 的 In Air 第 50 帧选中左手腕和 15 根指骨后，Relax Pose 成功写入其 15 根指骨。逐骨核对显示手腕、其余身体骨和控制器均保持原值。原动作、关键帧、选区和绑定记录也通过核对。全身 Neutral 的套用仍属独立问题。[旧版复现](<D:/Blender/Projects/Character/X/validation/hand_pose_20261125/base01.json>) · [X 副本验收](<D:/Blender/Projects/Character/X/validation/hand_pose_20261125/candidate02.json>)

共享 CD 姿势捕获截图后，可用 Save Captured Preview 将预览写回共享资产。正式 Neutral 已采用已找回的截图，尺寸为 256×256。原 1,344 条姿势曲线与元数据通过逐项核对。[预览保存验证](<D:/Blender/Projects/Character/X/validation/neutral_preview_20261125/qa03.json>) · [正式缩略图](<D:/Blender/Projects/Character/X/validation/neutral_preview_20261125/published_preview.json>)

两处磁盘安装各核对 311 个文件，零差异。59 项回归通过。隔离刷新从 0.77.244 升至 0.77.245，核对了 26 项状态和预览菜单注册。正式 X 窗口核对时已运行 0.77.245。Pose Mode、In Air 第 50 帧、当前选区及未保存状态均已核对。刷新 Pose Library 后，Neutral 显示了用户截取的原图，Outliner 和窗口原有的最小化状态已恢复。 [隔离刷新](<D:/Blender/Projects/Character/X/validation/hand_pose_20261125/refresh03.json>) · [回归](<D:/Blender/Projects/Character/X/validation/hand_pose_20261125/regress02.json>) · [安装包](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.245.zip>) · [正式交付](<D:/Blender/Projects/Character/X/validation/hand_pose_20261125/delivery245.json>)
