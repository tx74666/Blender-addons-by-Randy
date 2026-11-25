# Character Designer 0.77.225

指定鞋网格后，生成、重建或运行 Fit Foot Controls 时，左右脚的 Toe Bend 控制圈会分别套在各自的鞋头。插件沿鞋底从后跟向前取鞋长 80% 处的横截面，用鞋网格与该平面的交点构建略宽于鞋面的 32 边轮廓。它随原生趾骨显示，弯趾支点仍在趾根。轮廓在拟合时计算，动画播放使用骨骼变换。

无鞋、无法测量截面、手工改过的圈形或已有显示偏移动画时，保留现有趾控显示。点集鞋只有在截面附近有足够宽、高的采样时才拟合。原有旋转通道、蒙皮权重和鞋底 IK 沿用现有绑定。

## Blender 5.2 和 X 的验证

X 的两只鞋各取得 143 个截面交点。计入角色缩放，控制圈较趾根向前移约 6.73 厘米。在当前帧、抬脚、弯趾和抬后跟时，测得控制圈与鞋面相应截面的最小间隙约 1.38 毫米。双侧单独弯趾和抬后跟时，鞋底 IK 轮廓的位移为零。针对生成、重建、更换窄鞋与厚鞋、保留手工圈形、无鞋和故障回退的独立测试均通过。[模型测量](D:/Blender/Projects/Character/X/validation/toe_placement_20261119/verify06.json) · [生成与重建](D:/Blender/Projects/Character/X/validation/toe_placement_20261119/generated01.json)

正式发布包及 Blender 5.2、X 两处安装各核对 298 个文件。当前 X 会话已更新双脚，保留原选取、姿势、动画和角色网格，停在第 26 帧。场景仍待用户保存。[现场记录](D:/Blender/Projects/Character/X/validation/toe_placement_20261119/live01.json) · [安装核对](D:/Blender/Projects/Character/X/validation/toe_placement_20261119/delivery.json)
