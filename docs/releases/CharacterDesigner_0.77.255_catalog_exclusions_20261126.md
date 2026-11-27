# Character Designer 0.77.255 · X 动作目录去重

X 的动作目录按工作区、角色和动作 ID 排除三个重复条目，分别为 Idle、旧 Walk_N 和 Great Sword Slash。目录由 29 条变为 26 条，Prepare Remaining Clips 从 27 条变为 24 条。已有的 Ready Walk_N 仍可打开，实际动作曲线指纹一致。排除规则保存在 X 的项目设置中。[X 项目规则](<D:/Blender/Projects/Character/X/project_dependencies/animation_catalog_exclusions.json>)

隔离 Blender 5.2 在 X 副本中从 .254 原生刷新至 .255。第 11 帧的 Cosha · Walk_N · FK、槽 OBCoshaRig.002、422 根骨骼的求值姿势等 25 项保持一致。14 项原生功能检查通过，包含重新打开、Sync List 和实际加入待准备队列。两版回归测试均为 164/166，两个 Composition 样例均缺少 sampleRate。[功能验收](<D:/Blender/Projects/Character/X/validation/prepare_preflight_20261126/acceptance255.json>) · [隔离热刷新](<D:/Blender/Projects/Character/X/validation/animation_exclusions_delivery_20261126/refresh01.json>)

正式 ZIP 与两处安装各核对 319 个文件。Animation 已在正式 X 中使用 Refresh Add-on 刷新至 .255，界面待准备数由 27 变为 24。刷新前后均为 Pose 模式、Walk_N FK 第 17 帧，选中 CTRL_foot_IK.R，保留未保存标记。批量 Prepare 尚未启动。[现场刷新回执](<D:/Blender/Projects/Character/X/validation/prepare_preflight_20261126/live255.json>) · [交付记录](<D:/Blender/Projects/Character/X/validation/animation_exclusions_delivery_20261126/delivery255.json>)
