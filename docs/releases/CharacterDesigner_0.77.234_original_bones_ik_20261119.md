# Character Designer 0.77.234 · 第 26 帧切换 Original Bones

第 26 帧的 Natural Bind 会接管四肢，主动暂停原来的 IK 约束。旧版把这个正常状态当成约束损坏，挡住了 Rig 页的 Original Bones。现在进入时会校验绑定关系和当前帧，保留当前外形，再让原骨接手。Weight 页的同名入口可在当前姿势下刷权重。[旧版复现](<D:/Blender/Projects/Character/X/validation/original_ik_names_20261119/repro01.json>) · [新版原骨往返](<D:/Blender/Projects/Character/X/validation/original_ik_names_20261119/round01.json>)

切回 Controls 时会优先沿用原有 IK/FK 设置。若改过的肢体在当前设置下无法还原新姿势，插件会尝试仅对该肢体切换 FK，并在成功时提示。第 26 帧右脚趾弯曲后沿用了原设置，左前臂翻掌后仅左臂转为 FK。若匹配失败，会保留原骨编辑会话和当前姿势。[切换失败恢复测试](<D:/Blender/Projects/Character/X/validation/original_ik_names_20261119/rollback01.json>)

新生成的四肢 IK 约束采用 Left Arm IK、Right Arm IK、Left Leg IK 和 Right Leg IK。已有约束继续沿用文件里原来的名字，以保留动画和驱动引用。提示按肢体、骨名和实际原因显示。旧 X 文件上的真实停用提示为 `Left Arm IK on 'forearm.L' is disabled.`。[约束命名验证](<D:/Blender/Projects/Character/X/validation/original_ik_names_20261119/names02.json>)

Blender 5.2.0 LTS 隔离进程核对了四种往返操作，包括只切换、弯右脚趾及左前臂绕两根轴旋转。逐顶点比较各个可见网格，进入与返回时的最大世界坐标偏差为 7.2e-07 Blender 单位。独立刷权重测试对原顶点组写入 0.431，往返后权重、动作曲线与动画槽保留。另有 89 项相关单元测试通过。[可见网格验证](<D:/Blender/Projects/Character/X/validation/original_ik_names_20261119/surface01.json>) · [刷权重验证](<D:/Blender/Projects/Character/X/validation/original_ik_names_20261119/paint01.json>) · [单元测试](<D:/Blender/Projects/Character/X/validation/original_ik_names_20261119/unit01.log>)

基于 0.77.233 修改 7 个文件、新增 1 个文件，292 个文件逐字节一致。官方 ZIP、Blender 5.2 安装和 X 项目均核对了 300 个文件。正式 Blender 窗口已打开，本次未操作该窗口或保存 X.blend，正在运行的插件版本未经验证。[部署核对](<D:/Blender/Projects/Character/X/validation/original_ik_names_20261119/delivery.json>)
