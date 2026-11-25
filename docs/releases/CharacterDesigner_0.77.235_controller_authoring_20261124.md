# Character Designer 0.77.235 · 用控制器编辑导入动作

导入动作可以生成独立的 FK 控制器动作，并保留源动作。单条手臂或腿可以逐帧切换 IK/FK 并打键，也可以临时用 IK 摆姿势、把结果记到 FK。需要整段 IK 时可以另建动作，原 FK 稿保留。已有旧版 IK 编辑稿时优先复用。独立 FK、单肢切换后的保存重开，以及 GUI 撤销和重做都已在 X 副本验证。整段 IK 入口用剪成三帧并手改过的真实 In Air FK 动作验证。[Animation 验收](<D:/Blender/Projects/Character/X/validation/controller_authoring_20261124/acceptance235.json>)

在 X 副本上，首次无缓存打开 In Air，按钮在 0.20 秒返回，等待到可编辑共 129.14 秒，超过 60 秒目标。已有结果再次打开用时 0.27 秒。In Air 的 793 个回放采样最大偏差为 0.361 毫米和 0.0674°，另用 Walk Forward 验证。[首次准备](<D:/Blender/Projects/Character/X/validation/controller_authoring_20261124/cold05.json>) · [缓存重开](<D:/Blender/Projects/Character/X/validation/controller_authoring_20261124/warm01.json>) · [回放实测](<D:/Blender/Projects/Character/X/validation/controller_authoring_20261124/replay05.json>)

发布基于 0.77.234，修改 8 个文件、新增 3 个文件，292 个文件与上版一致。官方 ZIP、Blender 5.2 用户安装及 X 项目安装均按正式投影逐文件核对，303 个文件全部一致。[正式交付记录](<D:/Blender/Projects/Character/X/validation/controller_authoring_delivery_20261124/delivery235.json>)

隔离的 Blender 5.2.0 LTS 进程使用自带刷新流程从 0.77.234 升至 0.77.235，第 26 帧的当前动作、原始姿势、选取、权重和可见网格核对一致。正式 X 窗口在姿态模式、第 26 帧，保有未保存标记。检查过程中检测到用户输入，刷新按钮随后从界面消失。现场未执行刷新或保存操作，正在运行的版本未经独立读取。[副本热刷新实测](<D:/Blender/Projects/Character/X/validation/controller_authoring_delivery_20261124/private01.json>)
