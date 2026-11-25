# Character Designer 0.77.243 · Save Pose 选区筛选

Save Pose 的 Selected Region 从当前选区读取身体骨和公开控制器，自动略过头发、裙子和内部机制骨。混合选择直接保存，全部选到非身体骨时仅提示选择身体骨。手指继续由 Include Fingers 决定。

X 副本验证了身体控制器和 346 根非身体选项混选时，导出的 22 根身体骨与只选身体时的姿势数据一致。全选捕获了 56 根身体骨，导出后回读一致。原姿势、选取、动作与关键帧均保留。无可保存的选区未生成资产。第一份候选曾在共用解析函数中应用筛选，正式版限定在 Save Pose，保留动画偏移原有的选区规则。[X 副本验证](<D:/Blender/Projects/Character/X/validation/save_pose_filter_20261124/native02.json>) · [27 项回归检查](<D:/Blender/Projects/Character/X/validation/save_pose_filter_20261124/regression02.json>)

隔离热刷新从 0.77.242 升到 0.77.243，28 项姿势与动作状态一致。ZIP、Blender 5.2 用户目录和 X 项目目录均核对 310 个文件。[热刷新](<D:/Blender/Projects/Character/X/validation/save_pose_filter_20261124/refresh01.json>) · [安装包](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.243.zip>) · [交付回执](<D:/Blender/Projects/Character/X/validation/save_pose_filter_20261124/delivery243.json>)
