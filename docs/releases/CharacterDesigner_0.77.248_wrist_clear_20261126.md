# Character Designer 0.77.248 · IK 手腕复位

在姿态模式只选手腕 IK 控制器时，Alt+R 会恢复手掌相对当前前臂的绑定角度。双选和姿态镜像按各自的前臂计算。Animation 页的 Match Wrist to Edit Pose 仍可用于匹配手骨的编辑姿势。

在独立 Blender 5.2 中对 In Air 的第 5、26、46、50 帧测试了左右单选、双选及镜像。24 个手腕样本的误差均低于 0.001°，腕关节位置变化均为 0 mm。另验证了自动打键后回放、失败回滚、无自动打键时整份 Action 一致，以及全选 Alt+R、Alt+G 后全部骨骼的姿态矩阵与 .247 一致。隔离热刷新前后 22 项现场状态一致。[原生验证](<D:/Blender/Projects/Character/X/validation/in_air_wrist_clear_20261126/validation01.json>)

17 项界面测试通过。105 个较早的测试方法在两版上的失败子项和原因相同，其中多处模拟对象已与当前接口脱节。[版本对照](<D:/Blender/Projects/Character/X/validation/in_air_wrist_clear_20261126/pure_compare.json>)

Blender 5.2 用户插件目录与 X/addons 各 315 个文件通过逐项核对。正式窗口留给当前编辑。[安装包](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.248.zip>) · [交付记录](<D:/Blender/Projects/Character/X/validation/in_air_wrist_clear_20261126/delivery248.json>)
