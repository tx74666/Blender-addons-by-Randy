# Character Designer 0.77.250 · Neutral 基础动作

打开 X 的 **Animation → Animations → Uncategorized → Neutral** 即可编辑基础姿势。首次打开时，插件将项目中的 [Neutral 动作模板](<D:/Blender/Projects/Character/X/assets/animations/CoshaRig/Neutral.blend>) 载入当前工程。模板来自 CoshaRig 现有的 Neutral 姿势，使用 FK 控制，第 1 帧记录全身和手指。在第 1 帧给修改过的控制器打键并保存工程，下次打开即可继续。动作可分配到已有分类。

日常 Advanced 面板已移除 Rest Pose 与 Pose 按钮。旧临时姿势和原生 Rest 显示仍可通过 Return to Animation 返回。进行中的减键预览需要先 Apply 或 Discard，然后才能切换到 Neutral。

Blender 5.2 副本验证了从 Walk_N 第 8 帧进入 Neutral、修改左手指、返回 Walk_N、保存重开和再次编辑。已核对姿势的 56 根原骨，矩阵最大元素差约 0.00000154，返回 Walk_N 的差值为零，动作曲线指纹一致。隔离热刷新 28 项现场数据一致。113 项界面测试中，95 项通过，18 项与 .249 有相同的失败。双安装位置各 316 文件，零差异。[原生验收](<D:/Blender/Projects/Character/X/validation/neutral_motion_20261126/native01.json>) · [测试对照](<D:/Blender/Projects/Character/X/validation/neutral_motion_20261126/ui_compare.json>) · [交付记录](<D:/Blender/Projects/Character/X/validation/neutral_motion_20261126/delivery250.json>)
