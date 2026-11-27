# Character Designer 0.77.253 · 动作列表右键归类

在 **Animations** 列表上右键点击动作名称，选择 **Move to Category…**，再选目标分类。列表中的文件夹按钮已移除，顶部保留分类选择和新建分类。Neutral、已准备、待准备和仅有源动作的名称都支持右键操作。实测将尚未 Prepare 的 Walk Forward 移入 Daily 时，角色保持当前动作和槽，也未启动 Prepare。

独立 Blender 图形界面验证了右键菜单与行对齐，113 项工作列表测试通过。在读取 X 的独立 Blender 5.2 会话中，从 .252 刷新至 .253 后再刷新一次，每次菜单都只有一个归类钩子，每次比对的 28 项场景状态一致。正式 Blender 5.2 和 X/addons 各核对 318 个文件，零差异。[功能验收](<D:/Blender/Projects/Character/X/validation/row_categories_20261126/acceptance253.json>) · [菜单截图](<D:/Blender/Projects/Character/X/validation/row_categories_20261126/gui03/menu.png>) · [刷新回执](<D:/Blender/Projects/Character/X/validation/row_categories_delivery_20261126/refresh01.json>) · [交付记录](<D:/Blender/Projects/Character/X/validation/row_categories_delivery_20261126/delivery253.json>)
