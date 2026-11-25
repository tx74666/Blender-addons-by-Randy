# Character Designer 0.77.242 · 武器动作打键

在与 Unity 工作列表相连的 Blender 场景中，要单独加入一个已准备的带枪动作，在 **Animation → Animations → Advanced → Add Prepared Animation** 选择 Unity 准备好的工作区 JSON。此入口用于已接入的 Main Rig。已有的 [Walk_N · Blaster 工作文件](<D:/Blender/Projects/Character/X/validation/gun_walk_roundtrip_20261103/delivery/blaster_walk_ready02.blend>) 可以直接打开。

选择 **Custom**。**Edit Base** 用于编辑手指，可用 Blender 原生插帧。**Edit Controls** 用于调整手腕目标和肘部提示，再点 **Key Controls** 记录当前帧。枪随右腕移动。在 **Original Rigging Inputs** 中，**Select Weapon Hand** 会直接选中右腕，两组控制共用一个打键按钮。

Composition 只在工作项带有可加载的武器时提供 **Load Attachment**。加载成功后可用 **Remove Attachment** 移除。普通 Rigging 工作项的附件随它自己的预览管理。

本版保留 0.77.241 的 Save Pose 修复。正式 ZIP 及 Blender 5.2 和 X 两个安装目录均已逐文件核对。真实 Blaster 与手部打键经过隔离 Blender 验证。正式 X 窗口的运行版本需在该窗口刷新后确认。[交付记录](<D:/Blender/Projects/Character/X/validation/weapon_workflow_cleanup_20261124/delivery242.json>) · [完整投影](<C:/Users/Randy/.codex/visualizations/2026/11/02/01a19a87-d90c-7b80-bc66-c90306e3d445/weapon_workflow_cleanup_20261124/saveguard01_242/stage/projection.json>) · [前版交付](<D:/Blender/Projects/Character/X/validation/save_pose_guard_20261124/delivery241.json>)
