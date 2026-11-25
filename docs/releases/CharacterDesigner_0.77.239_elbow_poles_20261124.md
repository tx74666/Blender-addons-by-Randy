# Character Designer 0.77.239 · 肘部极点对齐

Direct 手臂在生成 Full IK、从 FK 切到 IK 和给 IK 打键时，会根据肩、肘、腕的位置对齐肘部极点。对于已有的整段 Full IK 控制器动作，在工作列表选中动作后可用 **Align Elbow Poles** 建立对齐版本，原 Action 会归档保留。已有的混合 FK/IK 动作超出这个批量命令的适用范围。[验收记录](<D:/Blender/Projects/Character/X/validation/arm_pole_fix_20261124/acceptance239.json>)

真实 In Air 第 36 帧，左肘的指向偏差由 54.16° 降至 0.00046°，右肘由 39.60° 降至 0.000008° 以内。全段 257 个采样点中，肘部指向偏差最大为 0.866° 以内，另 1211 条曲线逐条一致。标准工作列表流程实测 183.073 秒，独立验证及动作回读实测 232.955 秒，两者产生的动作指纹一致。[工作列表实测](<D:/Blender/Projects/Character/X/validation/arm_pole_fix_20261124/worker_exact01.json>) · [全段回读](<D:/Blender/Projects/Character/X/validation/arm_pole_fix_20261124/verify_direct02.json>)

新建 Full IK 与双臂切换、打键在 stage239_02 上经原生验证。该候选与最终 stage239_03 唯一有差异的文件负责旧动作的批量修复，最终投影已验证该流程。[新建 Full IK 验证](<D:/Blender/Projects/Character/X/validation/arm_pole_fix_20261124/generate01.json>) · [切换与打键验证](<D:/Blender/Projects/Character/X/validation/arm_pole_fix_20261124/key01.json>)

从 0.77.238 热刷新到 0.77.239 时，隔离 Blender 中刻意加入的未打键右腕旋转得以保留。刷新前后 28 项状态一致，包含动作、选取、姿势、左右肘与膝极点和可见网格。正式 X 和其中的右腕编辑由「Animation 接续」负责。[隔离热刷新](<D:/Blender/Projects/Character/X/validation/arm_pole_delivery_20261124/refresh01.json>)

正式包沿用上版 300 个文件，修改 8 个，新增 2 个。ZIP 逐文件核验通过，Blender 5.2 用户插件目录和 X/addons 各核对 310 个文件，差异为零。[安装包](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.239.zip>) · [交付回执](<D:/Blender/Projects/Character/X/validation/arm_pole_delivery_20261124/delivery239.json>)
