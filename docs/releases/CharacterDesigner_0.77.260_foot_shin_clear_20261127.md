# Character Designer 0.77.260 · 脚底 IK 相对小腿清旋转

在 Pose 模式选中左脚底 `CTRL_foot_IK.L` 或右脚底 `CTRL_foot_IK.R`，鼠标放在 3D 视口，按 **Alt+R**。脚踝会恢复它相对同侧小腿的绑定角度。双选时各按各自的小腿计算，开启姿态镜像时单选可同时重置两脚。踮脚和脚趾仍可通过各自的控制器编辑。

在 X 的 Walk_N 第 0 帧，旧版清旋转后左脚踝相对小腿偏离绑定角度约 17.27°，新版误差低于 0.001°。六个左右脚样本中，脚底控制器位置变化均为 0，脚踝位移均低于 0.001 毫米。另行核对了可见脚底轮廓、与膝盖 pole 混选、踮脚与脚趾控制器混选、自动打键回放及写键失败回滚。独立 Blender 完成两次刷新，28 项状态比对一致。[原生测试](<D:/Blender/Projects/Character/X/validation/foot_shin_clear_20261127/native05.json>) · [旧版对照](<D:/Blender/Projects/Character/X/validation/foot_shin_clear_20261127/old03.json>) · [交付记录](<D:/Blender/Projects/Character/X/validation/foot_shin_clear_20261127/delivery260.json>)
