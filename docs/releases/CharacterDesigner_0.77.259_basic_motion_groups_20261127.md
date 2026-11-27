# Character Designer 0.77.259 · 默认动画分组

Animations 默认打开 **Group1**，用于浏览尚未手动归类的工作列表动作。切换到 **Basic** 即可打开 **Neutral**。两个分组都支持右键移动动作。已有的自定义分类及其成员继续按保存的设置显示。

在正式 X 文件的独立 Blender 5.2 会话中，Basic 仅显示 Neutral，Group1 显示 22 个动作，Daily 显示 Walk Forward、Idle、In Air 和 Wall Jump。Archived Imports 保存的三条分组记录没有匹配到当前目录、工作列表或已退役条目。仅在独立会话中移除这两个旧分类后，Group1 可访问全部 26 个工作列表动作，当前动作、槽、帧及源动作和控制器绑定均通过比对。[分组验收](<D:/Blender/Projects/Character/X/validation/basic_motion_groups_20261127/native259.json>)

正式插件由 0.77.258 的完整版本构建，共 320 个文件。113 项工作列表界面测试通过。独立 Blender 连续刷新两次，每次比对的 28 项状态一致，右键菜单各保留一个归类入口。[刷新记录](<D:/Blender/Projects/Character/X/validation/basic_motion_groups_20261127/refresh01.json>) · [交付记录](<D:/Blender/Projects/Character/X/validation/basic_motion_groups_20261127/delivery259.json>)

正式 X 已热刷新。下拉菜单显示 Basic、Group1、Archived Imports、Daily；核对后收起菜单。当前选择仍为 Archived Imports，窗口保留第 0 帧、左脚 IK 控制器及未保存标记。
