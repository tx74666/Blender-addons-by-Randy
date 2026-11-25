# Character Designer 0.77.244 · Save Pose 与 Natural Bind

Save Pose 允许通过 Natural Bind 所有权、目标和堆栈验证的输出约束参与姿势保存。选区继续自动略过非身体骨，实际外部约束及目标被替换的约束仍会挡下保存。此版本的变更范围是捕获姿势。

以 X 的 In Air 第 50 帧复现 0.77.243 的左脚报错，再用同一份场景验证 0.77.244。正式共享库新增 Neutral 单帧姿势，56 根身体骨含 30 根指骨。资产回读逐骨矩阵最大元素差 4.77×10⁻⁷。原 Action、关键帧、帧、姿势、选取、其他资产和 X 文件均经核对保留。[复现](<D:/Blender/Projects/Character/X/validation/save_pose_bind_20261124/baseline.json>) · [原生验证](<D:/Blender/Projects/Character/X/validation/save_pose_bind_20261124/native.json>) · [正式资产回读](<D:/Blender/Projects/Character/X/validation/save_pose_bind_20261124/publish_neutral.json>)

Blender 5.2 用户目录和 X 项目目录各核对 310 个文件，零差异。27 项原有回归通过。独立场景的 Natural Bind 热刷新核对 26 项状态，正式 X 窗口在发布前已关闭。[回归检查](<D:/Blender/Projects/Character/X/validation/save_pose_bind_20261124/regression01.json>) · [隔离刷新](<D:/Blender/Projects/Character/X/validation/save_pose_bind_20261124/refresh03.json>) · [ZIP](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.244.zip>) · [交付回执](<D:/Blender/Projects/Character/X/validation/save_pose_bind_20261124/delivery244.json>)
