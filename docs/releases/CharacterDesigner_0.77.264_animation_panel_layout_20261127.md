# Character Designer 0.77.264 · Animation 面板

`Save Pose` 位于动画列表上方。动画列表、分类和 FK/IK 控制先于连接、导出与物理设置。

- `Export Animations` 提供本地 FBX 导出或工作列表的 Unity 发送入口。`Send Selected to Unity` 发送选中的可编辑动作，`Send Changed Clips to Unity` 重新检查整个工作列表，再发送确认有修改的条目。
- `Unity` 同时放置角色连接、`Sync List`、导入设置和适用的已准备动作入口。`Sync List` 从 Unity 工作区核对动作列表。`Fit Loops to Whole Frames` 默认开启，将循环时长对齐到最近的整数帧。关掉可保留源时间。`Prepare Full IK Tracks` 默认关闭，对适用工作区的显式准备操作生成完整 IK 轨迹。
- `Motion Tools` 收纳所选动作的 FK 转完整 IK、切换已准备版本以及打开源动作等工具。
- `Editing & Recovery` 收纳未开启的叠加合成、编辑文件的打包和重新定位、基础姿势测试及工作列表登记。`Register Action in Worklist` 登记已经存在的本地 Action。进行中的合成和返回动画的恢复入口仍显示在主区。

Blender 5.2 背景验证涵盖 0.77.263 至 0.77.264 的注册刷新、本地与已连接工作列表的界面路径、姿势和 Action/Slot 身份，以及已保存的导入选项。初次验证使用了缺少合法编辑场景的测试数据，第二次补齐了该场景。两组 Python 测试共 312 项通过，另有六项旧断言在 0.77.263 上同样失败。[验证回执](<D:/Blender/Projects/Character/X/validation/animation_panel_layout_20261127/delivery264.json>)
