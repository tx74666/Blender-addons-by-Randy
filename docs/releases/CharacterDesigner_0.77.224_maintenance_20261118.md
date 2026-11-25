# Character Designer 0.77.224

2026-11-18，本机 Blender 5.2.0 LTS 与 X 项目均已更新。完整包包含 297 个文件，相对 0.77.223 更新 7 个文件。

## 脚部控制器

重建脚部控制器时会恢复先前选用的鞋，并在恢复自定义箭头外形后重新校准。旧记录指向的鞋已遗失时，插件会在重建前提示选择替代鞋。重新拟合中途失败时，后跟箭头回到拟合前的位置；直接升级旧版无鞋绑定也会纠正早期版本留下的倾斜。

内置 FOOT 线框可在选定鞋子后正常创建，不需要手工整理其轻微起伏。手工修改过的非平面线框继续受到保护。脚部显示遇到失效骨骼或约束时，该脚保留最后的有效位置，其他脚继续更新。

测试中，重建前后鞋底圈与后跟箭头的最大点位偏差分别为 0.00087 和 0.00121 毫米。重新拟合故障回退后的偏差为零。六个整脚姿势的脚底倾角误差均低于 0.001°；单独抬后跟和弯趾时，支撑轮廓测得位移为零。[脚部重建](D:/Blender/Projects/Character/X/validation/cd_review_20261118/fit_after06.json) · [双脚姿势](D:/Blender/Projects/Character/X/validation/cd_review_20261118/poses01.json)

## 几何读取耗时

每个鞋顶点只遍历一次权重。使用同一 X 副本，在同一 Blender 后台进程交替运行旧、新实现，预热后各测四次。左右脚各 12,681 个点的坐标和顺序完全一致，无权重网格也通过逐点对比。下列中位数仅计鞋子几何读取与左右脚分组；本次没有测量整次拟合操作或视口播放帧率。[原始计时](D:/Blender/Projects/Character/X/validation/cd_review_20261118/shoe01.json)

| 每侧几何读取 | 0.77.223 | 0.77.224 |
| --- | ---: | ---: |
| 左脚 | 460 ms | 237 ms |
| 右脚 | 298 ms | 134 ms |

## 动作准备

准备期间出现重复来源或已有记录不完整时，队列会提示处理并保留原动作。若这段时间内动作已由其他操作准备完成，会保留完成的结果。132 项动作队列、工作进程及返回测试和 20 项注册与注销测试均通过。[测试记录](D:/Blender/Projects/Character/X/validation/cd_review_20261118/queue_tests.json)

现场更新保留第 26 帧的姿势、选择和原有未保存状态。仅更新插件代码。两处安装及 ZIP 文件已核对。[现场记录](D:/Blender/Projects/Character/X/validation/cd_review_20261118/live02.json) · [本机交付](D:/Blender/Projects/Character/X/validation/cd_review_20261118/delivery.json)
