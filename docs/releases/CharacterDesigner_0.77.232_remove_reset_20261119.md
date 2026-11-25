# Character Designer 0.77.232 · 移除两项姿势复位命令

Rig 页移除了 **Reset Selected Chains**，Animation 页移除了 **Reset Body to Edit Pose**。两者共用的操作已取消注册。日常复位时，在姿态模式选中控制骨，按 **Alt+R** 清旋转、**Alt+G** 清位移。按 **A** 可选中全部可选姿态骨，改过缩放时可用 **Alt+S**。

基于已发布的 0.77.230，8 个文件有改动，291 个文件逐字节一致。独立的 0.77.231 脚尖及原骨编辑试验继续暂停。官方 ZIP、Blender 5.2 和 X 的两份安装均逐文件比对通过。[交付记录](<D:/Blender/Projects/Character/X/validation/remove_reset_20261119/delivery.json>)

36 项针对性测试通过。在独立打开的 X 上检查了手腕、脚和全选 232 根姿态骨的 Alt+R、Alt+G 对应操作及按键注册。全选清旋转保留已有位移和缩放。另行验证了移除旧操作的运行时更新、失败回滚及卸载。更大范围的 54 个单元测试有 7 个失败结果（含子测试），均在 0.77.230 上复现。[Blender 测试](<D:/Blender/Projects/Character/X/validation/remove_reset_20261119/x01.json>) · [更新与回滚](<D:/Blender/Projects/Character/X/validation/remove_reset_20261119/hot01.json>) · [与上一版本对照](<D:/Blender/Projects/Character/X/validation/remove_reset_20261119/suite.json>)

正式 X 窗口处于姿态模式第 26 帧。用户正在使用其他前台窗口，本次只核对磁盘部署；当前进程是否已载入 0.77.232 尚未验证。下次刷新插件或启动 Blender 即可载入。 本次没有保存正式场景或首选项。
