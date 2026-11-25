# Character Designer 0.77.240 · 保存姿势到共享库

在 **Animation → Animations → Advanced → Save Pose** 或 **Rig → Body → Body Controls → Save Pose** 使用 **Save To**。**Current File** 默认保存到当前 .blend，随文件保存。**Pose Library / Cosha** 将当前姿势写成共享库的一份独立资产，保存后会尝试在当前窗口已打开的 Asset Browser 中显示该库并搜索新姿势。共享文件在点击确认时写入，重名时提示改名。新建的共享姿势使用资产图标，原有姿势随资产保留原预览数据。

核对了 Relax Pose 的左手 15 根指骨和 360 条曲线，从现有备份保留原姿势 Action 与元数据，按现有 Cosha 目录 ID 发布。共享库现在可找 Relax Pose、Arm Flat 和 Fist。X 现场原有的本地姿势条目随当前编辑保留，直接选择具名 Pose Library 时只浏览这份共享库。[共享 Relax Pose](<D:/Blender/Helper/Asset-Libraries/Costom/Pose Library/Saved/Actions/Relax Pose.asset.blend>)

28 项检查通过，涵盖捕获、原生共享回读、重名保护、写入失败回滚以及本地保存。独立 Blender 的 X 副本从 0.77.239 热刷新后，动作、姿势、选取、可见网格和未打键右腕旋转在内的 28 项状态一致。正式窗口由正在进行的「Animation 接续」工作使用。[功能验收](<D:/Blender/Projects/Character/X/validation/pose_library_share_20261124/validation03.json>) · [隔离热刷新](<D:/Blender/Projects/Character/X/validation/pose_library_share_20261124/refresh01.json>)

正式 ZIP 和 Blender 5.2、X 两处安装均逐文件核对，310 个文件与最终投影一致。[安装包](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.240.zip>) · [交付回执](<D:/Blender/Projects/Character/X/validation/pose_library_share_20261124/delivery240.json>)
