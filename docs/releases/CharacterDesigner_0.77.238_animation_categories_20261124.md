# Character Designer 0.77.238 · 动作分类

在 **Animation → Animations** 中，点 **+** 新建分类，点动作行右侧的文件夹将动作移入分类。已有动作默认显示在 **Uncategorized**。分类可以改名，删除分类后，其中的动作回到 Uncategorized。窄面板里，**Prepare** 变成 **Ready** 时动作名和文件夹按钮始终在同一列。列表移除了 All/Pinned、行内图钉和独立的 Hair Custom 行。[原生界面核对](<D:/Blender/Projects/Character/X/validation/animation_categories_20261124/layout04/layout_capture_verified.json>)

从 0.77.237 的完整投影继承 304 个文件，修改 3 个，新增 1 个。正式 ZIP 和 Blender 5.2 用户目录、X 项目目录均已逐文件核对。[安装包](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.238.zip>) · [交付回执](<D:/Blender/Projects/Character/X/validation/animation_categories_delivery_20261124/delivery238.json>)

145 项针对性测试通过。独立 X 副本验证了分类筛选、保存重开，以及动作和外部链接的保留。窄版布局以实际截图和控件记录复核，先前界面测试脚本的异常保留在验收目录。[功能验收](<D:/Blender/Projects/Character/X/validation/animation_categories_20261124/acceptance238.json>)

在隔离的 Blender 5.2 中用插件自带的 Refresh Add-on 从 0.77.237 升级，回调在后台同步执行。第 26 帧的 27 项状态在刷新前后一致，包括姿势、动作、选取和可见网格。刷新后把 In Air 移入新分类，动作绑定及选取保持一致。正式 X 窗口的刷新由「Animation 接续」负责。[隔离热刷新](<D:/Blender/Projects/Character/X/validation/animation_categories_delivery_20261124/refresh01.json>)
