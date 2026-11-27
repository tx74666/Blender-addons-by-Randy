# Character Designer 0.77.247 · 动画数据清理

新准备的 FK 和 IK 工作动作会自动清理整组三轴始终为 1 的缩放曲线，以及连接骨骼整组三轴始终为 0 的位移曲线。已有工作动作可点 **Clean Animation Data** 清理当前动作槽。切换这些动作时，CD 会恢复清理时记录的默认输入。

在姿态模式使用 **Preview Key Reduction**，可以查看选中控制骨的原始与精简关键点数，在同一视口比较 Original 和 Reduced。确认 **Apply Reduction** 才写回动作，也可以点 Discard 放弃预览。Blender 原生界面已验证撤销与重做。

在 X 副本中，In Air 的曲线从 1,216 条降为 961 条，移除 42,339 个冗余关键点。对 422 根骨骼取 11 个整帧及子帧样本，测得姿态矩阵最大差为 0。另在左脚 IK 的可选精简预览中，选中的变换关键点从 585 个降到 192 个。192 项单元测试通过。此次验证涵盖 CD 直接管理的工作动作与动作槽，外部指派 Action、NLA 混合、正式窗口的用户编辑及 Unity 往返均未实测。[功能验收](<D:/Blender/Projects/Character/X/validation/animation_cleanup_20261126/acceptance247.json>)

Blender 5.2 用户插件目录和 X/addons 各核对 315 个文件，零差异。X 隔离副本从 .246 热刷新至 .247，29 项状态一致，并实际运行了清理、精简预览与放弃预览。磁盘交付期间，正式 X 窗口由 Animation 接续。[隔离刷新](<D:/Blender/Projects/Character/X/validation/animation_cleanup_delivery_20261126/refresh01.json>) · [安装包](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.247.zip>) · [磁盘交付](<D:/Blender/Projects/Character/X/validation/animation_cleanup_delivery_20261126/delivery247.json>)
