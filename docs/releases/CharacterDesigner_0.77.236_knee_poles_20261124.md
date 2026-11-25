# Character Designer 0.77.236 · 膝极点对齐

生成 IK 控制器动作和切换腿部 IK/FK 时，膝盖箭头现在沿腿实际弯曲的方向定位。已有的 IK 动作可以使用 **Align Knee Poles** 生成对齐后的副本，同时保留原动作供比较或继续编辑。

In Air 第 46 帧原有右膝箭头偏转约 90°。修复后独立回读，右膝箭头与弯曲方向相差 0.00016°，左膝相差 0.00041°。旧动作整段修复覆盖 65 帧及子帧。新动作生成在第 43–49 帧验证，另已验证单肢和连续区间 IK/FK 切换，以及编辑膝盖关键帧后的保存重开。[功能验收](<D:/Blender/Projects/Character/X/validation/knee_pole_fix_20261124/acceptance236.json>)

隔离 X 从 0.77.235 热刷新至最终候选后，第 26 帧的当前动作、选取、姿势、权重、左右膝极点和 6 个可见网格核对一致。561 项离线测试有 556 项通过，其余 5 项在 0.77.235 基线上同样失败。[隔离热刷新](<D:/Blender/Projects/Character/X/validation/knee_pole_delivery_20261124/refresh04.json>) · [回归对照](<D:/Blender/Projects/Character/X/validation/knee_pole_fix_20261124/pure236.json>)

正式 ZIP 和 Blender 5.2 用户插件、X 项目插件均按最终投影核对了 306 个文件。相对 0.77.235，修改 8 个文件、新增 3 个文件，295 个文件一致。正式 X 窗口的刷新和修复动作接续由「Animation 接续」负责，此记录覆盖磁盘交付。[交付回执](<D:/Blender/Projects/Character/X/validation/knee_pole_delivery_20261124/delivery236.json>) · [安装包](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.236.zip>)
