# Character Designer 0.77.230 · 权重绘制与控制器切换

在 Weight 页选中已绑定的网格，点 **Original Bones** 即可按当前姿势绘制权重。点 **Controls** 可保留刚画的权重和当前姿势，继续摆控制器，并恢复进入绘制前的 Auto Key 设置。在同一 Blender 会话，下次只选骨架也能回到上次的网格和权重组。主动选择另一个网格时使用新选择。Rig 页保留 **Edit Original Pose**。已有试姿会话和旧操作 ID 可以继续使用。

完整投影基于 0.77.229，6 个文件有改动，293 个文件逐字节一致。229 已发布的 Weight 页面保护条件随版本保留。官方 ZIP、Blender 5.2 和 X 的安装均通过逐文件校验。[交付记录](<D:/Blender/Projects/Character/X/validation/weight_bones_controls_20261119/delivery.json>)

69 项相关单元测试通过。Blender 5.2.0 LTS 在独立打开的 X 上实写了一个顶点权重，验证移动手腕后回到同一权重组、明确更换网格、两种 Auto Key 设置及旧试姿状态的退出。副本复现了左前臂已有 IK 约束静音时的警告，新绘制入口可正常使用；模拟一次退出失败后可重试，刚画的权重和约束设置均保留。[X 测试](<D:/Blender/Projects/Character/X/validation/weight_bones_controls_20261119/x01.json>) · [失败恢复](<D:/Blender/Projects/Character/X/validation/weight_bones_controls_20261119/recovery01.json>)

在当前正式 X 的第 26 帧观察到 Rig 页已显示新版 **Edit Original Pose**。现场核对限于界面观察。维护端没有在正式场景绘制、切换模式、重载或保存。私有实例另行验证了运行时代码更新和回滚。[窗口核对](<D:/Blender/Projects/Character/X/validation/weight_bones_controls_20261119/window_observation.json>) · [私有更新测试](<D:/Blender/Projects/Character/X/validation/weight_bones_controls_20261119/hot01.json>)
