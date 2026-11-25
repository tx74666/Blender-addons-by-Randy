# Character Designer 0.77.237 · 手指骨集合与左右手选择集

在已有角色上执行 **Organize Finger Bones**，30 根手指骨会归入 **Body → Fingers**。在 Blender 的选择集中使用 **Fingers.L** 或 **Fingers.R**，可以一次选中对应手的 15 根指骨。新建、重建骨架及保存重开均通过原生验证。[功能验收](<D:/Blender/Projects/Character/X/validation/hand_edit_20261124/acceptance237.json>)

在隔离的 X 副本上，通过插件自带的 Refresh Add-on 从 0.77.236 升至最终候选。第 26 帧的动作、姿势、权重、可见网格、原有骨集合和选取均保持一致。手动从 Fingers.L 移除一根骨骼后再整理，原选择集保留 14 根，完整的 15 根放入 Fingers.L (1)。再次整理会复用这个完整选择集，右手选择集也保留。[隔离热刷新记录](<D:/Blender/Projects/Character/X/validation/hand_edit_delivery_20261124/refresh02.json>)

发布基于 0.77.236，修改 4 个文件、新增 1 个文件，302 个文件与上版一致。已核对 Weight 页原有的 Original Bones 切换条件。正式 ZIP 和 Blender 5.2、X 两处安装均按最终投影核对了 307 个文件。正式 X 窗口、In Air 左手工作动作及 Relax 姿势资产由「Animation 接续」负责。[交付回执](<D:/Blender/Projects/Character/X/validation/hand_edit_delivery_20261124/delivery237.json>) · [安装包](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.237.zip>)
