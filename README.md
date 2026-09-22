# Blender add-ons by Randy

RR Helper 和 Character Designer 的源码、安装包及插件测试集中维护在这里。

| 插件 | 当前版本 | 源码 | Blender 安装包 |
| --- | --- | --- | --- |
| RR Helper | 0.2.12 | [random_realm_builder_exporter](addons/random_realm_builder_exporter) | [rr_helper-0.2.12.zip](dist/rr_helper-0.2.12.zip) |
| Character Designer | 0.61.61 | [character_designer](addons/character_designer) | [character_designer-0.61.61.zip](dist/character_designer-0.61.61.zip) |

Character Designer 0.61.61 修复 Align Joints 将已有骨根误判为穿出手指的问题：沿现有骨段调整关节时保留原路径，检查新关节点；改变路径时才验证新骨段。显示继续使用固定缓存，左右差异不阻挡单侧工作。详见 [关节对齐与指根检查](docs/releases/CharacterDesigner_0.61.61_mark_root_20260922.md)。

Character Designer 0.61.53 修正 Capture 成功却被对侧配对错误误导的提示：显示已捕获的一侧，区分左右参考错误与两侧曲面差异；不再把两侧不一致描述成需要重绑当前侧。失败配对完整保留原对侧确认与弯曲设置。详见 [Capture 修复记录](docs/releases/CharacterDesigner_0.61.53_capture_pair_diagnostics_20260921.md)。

Character Designer 0.61.52 优化手指参考首次刷新：同一批五指共用一次临时网格读取与拓扑校验，弯曲预览复用已验证参考；缓存命中不再读取网格。单骨架编辑模式中的骨链操作不再来回切换模式，保持选区与镜像设置。详见 [性能验证](docs/releases/CharacterDesigner_0.61.52_preview_performance_20260921.md)。

Character Designer 0.61.51 修复清空手指后仍保留红错；Relax Bones 直接处理编辑模式选中的骨链，不依赖 Basic Setup。X Mirror 开启时同步已有对侧，以活动骨所在侧为源，双侧选区去重；保持端点、Roll、连接与选区。

Character Designer 0.61.50 移除 Joint Topology & Weights、自动关节环生成及其后台监视。保留手指基础检测、骨链 Align／拇指 Relax、Roll 校准、缓存弯曲预览和局部镜像；升级不改动已有网格、Shape Keys、权重或骨骼。以下为历史更新记录，已移除功能以[当前手指工具说明](docs/finger_joint_tool.md)为准。

Character Designer 0.61.29 修正内部直轴的横向偏移：长度方向面带／边路径作为横向中线依据，在整段体内安全与覆盖条件不变的前提下优先对齐选区拟合中线，深度仍自动求取；不再仅为了更大的表面间隙而向一侧偏移。排除指根大面和指尖封口对横向基准的干扰，左右参考及环线更新保留这项依据，无新增控件。旧参考不自动移动，重新 Capture 即可更新。详见 [手指工具说明](docs/finger_joint_tool.md)。

Character Designer 0.61.28 修复包含指根过渡面的长面带 Capture 被误拒绝：此时用实际闭合网格表面验证内部直轴，不再让第一圈规则环的虚拟封口挡住合法指根范围。搜索仍限制在用户所选范围内，不向手掌找更长轴、不降低整段内部间隙校验。兼容指尖面带末端轻微绕回，原始选区仍保留。移除 Basic Setup 的 L/R 按钮和字样，默认成对更新；环数一致显示一个数，不一致才提示差异。成功捕获清除旧的下游错误状态。详见 [手指工具说明](docs/finger_joint_tool.md)。

Character Designer 0.61.27 精简 **Finger → Basic Setup**：选面带、边路径或环线后一次 Capture，自动生成指体内部的单一直轴及左右参考，无需 Mark／Confirm／Swap。根据稳定截面厚度留出端部余量，用封闭指体和距离界限验证整段直线；不能安全覆盖时明确失败，不改成曲线、不静默大幅缩短。原始选区和拓扑范围独立保留，Ring Layout 不使用内缩后的两端。移除常驻 Basis、上表面和多步按钮，只保留状态、捕获、眼睛和清除等紧凑入口。修正旧定义与待完成 Start 十字叠显，失败保留旧结果并明确标注。本轮不创建或删除场景 Empty，不改模型／Shape Keys。详见 [手指工具说明](docs/finger_joint_tool.md)。

Character Designer 0.61.26 将 **Finger → Basic Setup** 改为一套操作、五组成对记录。选中某根手指的面或内部环线，**Capture Detection** 根据指身环线、封闭指尖和五指排列自动识别归属，不依赖骨骼绑定或名称。有效捕获只覆盖该指，并建立对侧参考；五个状态位显示已完成／待确认／对称异常，显示两侧检测环数。左右形状、环数、连接不符或缺少对侧时明确报警，不自动修网格。Start/End 不允许跨指串用，失败保留旧记录。每个网格分别保存五对数据；编辑一指后局部重新对应，其他指不因整网格拓扑指纹变化而全部失效。工具自己的环线更新自动重新检测，手工编辑后可点刷新复查。首次需要一侧五根可区分的规则指身；不猜测模糊拇指顺序，不统计复杂掌部。详见 [手指工具说明](docs/finger_joint_tool.md)。

Character Designer 0.61.25 增加统一的 **Finger Definition**：先选择一条表面／边路径、两端标记或现有骨链，**Capture Selection → Confirm**，显示起点、终点、蓝色方向和可选的橙色弯折方向。定义不要求根部闭环、规则三合一或骨骼绑定，也不缩短你选择的跨度；非 Basis Shape Key 下可以先标记。**Use Basis Reference** 只切换参考数据，不替你切换当前 Shape Key。确认后 **Prepare Rings** 与 **Calibrate Both Hands** 复用该定义，真正修改仍检查 Basis 和安全条件。标记是独立参考设置，用 X 清除，不占用网格撤销步骤；环线和双侧骨骼修改保留原生 Undo/Redo。详见 [手指工具说明](docs/finger_joint_tool.md)。

Character Designer 0.61.24 将手指骨骼校正改为**左右同步**：按顶面校正时选一侧骨链，点击 **Calibrate Both Hands**，自动处理已有的对应 `.L/.R` 骨骼，预览同时显示两边。镜像的是弯折方向而不是 Roll 数字，正向 Local X 在两手都朝各自内侧弯。旧 Roll Reference 入口也同步对应侧；两边同时选中会去重。按骨架本地 X 校验配对，缺失、锁定、连接不匹配或任一侧姿态不安全时整次停止；失败回滚两侧，并恢复 Blender 的 X Mirror 状态。不改骨骼位置、长度、网格或权重，不新建骨骼。

Character Designer 0.61.23 升级 **Rig → Body → Fingers → Finger Ring Layout**：选中一片根部表面（也可延伸选到指身），点击 **Capture Finger Root**，不要求三合一或闭环；工具根据封闭指尖与继续通向手掌的连接判断方向，以选区靠根部的极端位置定义长度基准。虚线根环只作参考，不重建复杂根部。珊瑚红／青蓝关节目标、三环宽度和中间补充环可调；**Slide Nearby Rings** 默认把合适的附近旧环滑到目标，缺少的才补环。滑移同步插值 UV、Shape Keys、权重和支持的属性，横向接缝／材质边界保留不动。仅修改规则指身，掌指连接、指尖封口和骨架保持不变。支持预览、重复更新、保存重开及 Undo/Redo；方向有歧义、指尖缺口或关节目标超出安全指身时拒绝修改。旧布局保持旧语义，重新捕获才使用新功能。详见 [手指工具说明](docs/finger_joint_tool.md)。

Character Designer 0.61.21 改进 **Mirror Selected Region** 的整束识别：比较双向表面覆盖、截面宽度和沿长度的偏差分布，不再把仅包围盒重叠的其他头发都当成对应束。完整选择时整体替换唯一对侧连通块，包括相连残边；局部选择以边界 loop 为界，保留根部。绑定与未绑定共用操作，已有权重随网格镜像，现有骨架不动。编辑模式面板只保留 **Mirror Selected Region / Preview Replacement**，移除绑定区分、Local X=0、Shift 设置提示和 Shift-click 特殊行为。可选参考平面设置仍可通过 F3 的 **Mirror Settings** 调用。

Character Designer 0.61.20 将几何镜像独立为 **Weight → Weight Symmetry → Mirror Selected Region**：无需骨骼、Armature 修改器或顶点组，支持整束封闭头发、单侧＋中线和安全接缝修补。默认平面为 Mesh Local X=0；Shift-click 主按钮设置独立参考对象和接缝容差。**Preview Mirror Plane / Target** 显示源侧、结果、平面和编号候选，对侧有歧义时由用户指定，不随意覆盖或追加重叠网格。保留 UV、材质、边属性、自定义法线、相对 Shape Keys 和权重；不支持安全保留的数据提前拒绝，支持事务回滚及单步 Undo/Redo。详见 [几何镜像说明](docs/mesh-mirror.md)。

Character Designer 0.61.19 按“手指顶面”解释面选择，支持沿手指延伸的一整条连续四边形面带，默认朝顶面内侧弯折。骨架编辑模式的预览用红线显示当前轴、紫色骨架轮廓显示目标 Roll，并把横向转轴和弧线放在原有关节中心。**Calibrate Bone Roll** 按钮始终可见，捕获顶面后选择一条指骨链即可应用；只校准 Roll，不移动骨骼 Head/Tail。旧版已保存的方向保持原样，重新捕获后采用顶面约定。

Character Designer 0.61.18 在 **Rig → Body → Fingers** 增加面/边定义的弯折方向：蓝箭头指向指尖，橙箭头由面法向定义弯折侧，绿弧线预览正向弯曲；支持翻转箭头。确认后在骨架编辑模式选择一条手指链并校准，使每节骨骼的 Local X 正角度朝橙箭头弯。定义随文件保存，应用支持回滚与 Undo；网格、权重及 Shape Keys 不变。详见插件 README。

Character Designer 0.61.17 增加保守的 **Miscellaneous → Finger Joint Prototype**：选中一个闭合环线后，只在两侧连续四边形带中各插入一圈，保留中间圈，支持 Side A / Side B 不同间距，并创建可追踪的关节标记。不支持的拓扑会拒绝执行；不改骨骼、权重或 Shape Key 值。

Character Designer 0.61.16 增强 Shape Key 清理的写入验收：现在同时核验 Mesh KeyBlock、Edit Mode 的 BMesh Shape Key 层，以及当前活动 Shape Key 的 BMesh 坐标；写入或回滚任一层不一致都会安全失败并报告，而不是留下拖动滑块后复发的假成功。

Character Designer 0.61.15 修正 Shape Key 清理的 Edit Mode 写回：同时更新 KeyBlock、BMesh Shape Key 层和活动 Shape Key 的 BMesh 坐标，并按正确顺序提交，避免清理后拖动滑块又恢复旧位移。完整双侧网格也会按 Basis 坐标清理两侧。

Character Designer 0.61.14 修正 Shape Key 清理：完整双侧网格即使没有 Mirror Modifier，也会按 Basis 坐标寻找真实对侧顶点，同时清理并选中两侧；半模型仍由 Mirror Modifier 生成对侧。这样脸部 Shape Key 不会只清左手而留下右手位移。

Character Designer 0.61.13 修正 Shape Key 清理的镜像场景：镜像配对使用 Basis 坐标而不是当前表情坐标；启用 object-local X Mirror 且存在真实对侧顶点时，会同时清理并选中两侧对应点。半模型没有真实对侧点时，清理源点即可由 Mirror Modifier 生成另一侧。

Character Designer 0.61.12 修正 Shape Key 清理：不再使用容易误解的 Active/All 两种模式，而是读取 Shape Keys 列表中通过 Shift 多选得到的 `ShapeKey.select`。点击 **Clear Selected from Chosen Keys** 后，只清除当前选中 Mesh 顶点在这些 Shape Key 中的变形；未选 Shape Key 和未选顶点保持不变。

Character Designer 0.61.11 为 Topology Mirror 增加只读 **Analyze Topology Boundary**。它把选中的单侧面区、中心线顶点、真实边界和虚拟中心线段整理成 Boundary Descriptor；触碰 X=0 中线的合法源区不再在分析层被直接判错。此版本只验证边界识别，不改变现有拓扑替换执行流程。

Character Designer 0.61.10 在 **Miscellaneous → Shape Key** 增加两个局部清理按钮。Edit Mode 选中顶点后，可只从当前 Shape Key 或所有相对 Shape Key 中清除这些顶点的变形，恢复到各自的 `relative_key`；未选顶点、Basis、权重、拓扑和 Shape Key 动画设置不变，操作支持 Undo。共享 Mesh、绝对 Shape Key、锁定 Shape Key 会在写入前拒绝。

Character Designer 0.61.9 移除容易混淆的 **Surface Mirror · Different Topology**。保留的 **Locate Unmatched Vertices** 已归入 Weight Symmetry，用来定位严格权重镜像失败的顶点；不同拓扑的权重插值不再作为主流程。局部删点／破洞请使用 **Topology Mirror · Repair Selection**。

Character Designer 0.61.8 在 Weight → Weight Symmetry 增加 **Topology Mirror · Repair Selection**。选中完整的单侧源面区后，修复模式把源面镜像到另一侧，按 Merge Distance 复用近处顶点，缺失顶点自动创建，删除由焊接顶点组成的旧目标面并重新接回源面；权重、Shape Key、UV 和网格属性继续事务验证。它专门处理局部删点／破洞，不会放宽原来的严格 Topology Mirror 匹配规则。

Character Designer 0.61.7 在 **Rig → Body → Fingers** 增加手指骨骼 Roll 检查、预览和校正。选中的身体骨骼会被忽略；预览用红色显示当前局部轴、绿色显示校正方向；只有在 Armature Edit Mode 明确应用时才改选中的指骨 Roll，保留 Head/Tail、长度、权重和网格。默认按每根手指的第一节作为参考，也可以使用当前活动的指骨作为共同参考。Local X 对应 `R X X`，也可选择 Local Z。

Character Designer 0.61.4 增加 **Topology Mirror · Replace Selected Region**。在网格编辑模式中把目标 `.L`/`.R` 组设为活动组，然后可选择左手源区域复制到右手，也可选择右手目标区域进行替换；插件检查单一边界环和对侧可匹配拓扑后，镜像替换目标并将接缝边界对齐，同时转移权重、Shape Key、UV 与网格属性。完成后源区与目标区会同时保持选中，便于确认范围，其他身体区域保持未选中。边界不闭合、匹配不唯一、共享网格或锁定组等情况会在写入前拒绝。

Character Designer 0.59.0 统一角色动画入口。Unity **Tools → Character Designer → Animation** 选择角色和已有动作，发送实际评估后的骨骼运动；Blender **Animation → Import Latest from Unity** 生成独立测试 Action，可播放、暂停、拖时间轴、完整恢复，并支持 Undo/Redo 与保存重开恢复。详见[动画流程](addons/character_designer/unity_runtime/ANIMATION.md)。这期只做 Unity → Blender，原 Kimodo 与旧回传服务保留兼容。

RR Helper 0.2.12 合入本机 0.2.11 的建筑导出、HDRI 和预览修复，再移除重复 Animation 页面。已有 Animation.blend、Unity Avatar／Controller、动画资源和武器调试功能保留。

Character Designer 0.58.1 在 Unity 导出警告中加入缺权重顶点定位，以及可撤回的“仅导出时使用简化 BSDF”选项。定位重新检查当前原始网格；简化材质只作用于导出副本，原着色器和权重保留。

Character Designer 0.58.0 为小臂校正增加 Unity 运行组件。**Misc → Unity Export** 将已确认的校准写入 FBX 旁的 `.forearm.json`；安装随包的 Unity companion 后，导入器自动生成例如 **Cosha.Runtime.prefab**。组件根据实际手腕扭转运行，保留原有蒙皮、表情和动画；停用／移除可恢复原网格。支持 ±120°、已保存的逐圈比例与边界，以及 Armature 后固定层级 Subdivision。转移的是额外校正；Unity 基础蒙皮的细分顺序与表面法线近似仍可能与 Blender 有差异。安装和限制见 [Unity companion 说明](addons/character_designer/unity_runtime/README.md)。

Character Designer 0.57.5 将 Unity 导出的正常 Skip 移到报告 notices，不再计入警告。面板显示真实警告概要，并兼容旧报告；导出完成与 Unity 导入未验证的状态分开，权重遗漏和材质适配需求继续明确提示。

Character Designer 0.57.4 为 Quick Bind 增加 **Remove Binding / Restore Binding**：解除骨架连接并保留已画权重，恢复时不重新计算。已绑定物体显示 **Rebind Weights**，旧的绑定前状态回退收进 **Previous Weights**；解除连接独立于拓扑相关的权重备份。

Character Designer 0.57.3 简化 Unity Export 的 Objects 显示：省去跳过项提示，主骨架仍在上方，身体权重源（如 Cosha）排在网格首位。

Character Designer 0.57.2 在完整移除 Generated Controls 后显示 Original，清理插件留下的冗余 Body 分类；**Misc → Unity Export** 只导出启用 Armature 绑定的网格，旧的未绑定额外物体名单不能绕过。独立进程处理导出副本，保留原场景、权重和表情 Shape Key；重复导出保留 Unity `.meta`，遇到外部修改或文件冲突时停止，失败时恢复此前输出。当前为模型交接，动画、Unity 材质适配与运行时物理不在此版本自动完成。

Character Designer 0.56.1 补全校准撤销与双侧移除：相同数值不产生空撤销步骤、不清空重做；两侧 Shape Key 名称冲突在移除前一并检查。

Character Designer 0.56.0 为小臂逐圈校准加入真实环线捕获、手动起止范围、视图选圈和补环。范围实际限制额外校正，浅蓝边界与亮黄当前圈跟随变形后的控制笼。新捕获采用强度 0.4 的空间缓入缓出；逐圈、批量、平滑和重建默认分布为独立操作，保留手调结果。支持预览取消、局部撤销、双侧失败回退及保存重开继续编辑。原有手腕局部 Y 旋转机制保留，并检查已有 X 角色的控制器、手骨和实际蒙皮。

Character Designer 0.55.4 移除独立 Eye Controls 面板，眼睛继续由 Body Setup 统一生成和移除。视图里的眼罩和双眼圆圈保留；显示距离及手动眼骨设置收进 Body Controls → Advanced。

Character Designer 0.55.3 让手腕的局部旋转轴跟随显示的手腕控制器，修正抬臂后 R Y Y 仍沿旧 IK 轴旋转的问题；保留 Global／View 的旋转方向。**Update Body Setup** 可更新现有控制器，无需重建骨骼。Forearm Twist 校准失效时显示 **Paused**，并允许保留资料直接停用；移除冗余 Both Arms 标签。

Character Designer 0.55.2 修正脚部 **Auto Align**：移动 IK 控制器时脚与脚尖跟随小腿，保留 Foot Roll／Toe Bend；Manual 保持目标朝向。新 Setup 自动采用，已有脚部通过一次 **Fix Foot Auto Align** 保持当前姿势升级，兼容 IK／FK、Root 与可逆移除。Body 面板保留统一生成／移除和动画操作，直接在视图中选择控制器。

Character Designer 0.54.5 修正左右手腕的旋转坐标，让视角旋转和全局旋转同向，兼容 Auto Align 开关及旋转后的全身 Root。旧设置在 **Rig → Body → Body Controls → Correct Wrist Rotation** 显式更新，保留当前姿势和原生骨骼／权重；已有相关动画时会保护旧动作。内部参考骨默认隐藏，IK／FK 姿势匹配与可移除流程继续保留。

Character Designer 0.54.4 将绑定、创建、更新、恢复和刷新按钮改为普通颜色，红色操作按钮保留给移除／删除；实际错误信息仍正常提示。此次仅调整界面颜色，不执行权重绑定或删除备份。

Character Designer 0.54.0 将日常骨骼显示整理为 **Body、Hair、Dress、Original**。Body 包含身体、头部、面部与手指的动画操作，Original 放最后；生成骨骼的完整性分组保留在 Body 下默认隐藏的内部子组。Rig 和 Weight 共用 **Bone Display**：**Show All Controls** 显示所有可操作控制器；**Original · Native Bones** 显示原生身体骨架，Hair／Dress 的 **Bones** 显示各自真实带权重的骨骼。**Restore Display** 恢复进入前的显隐和外形，保留期间的姿势、权重编辑；恢复资料随 blend 保存。裙子仍可保留独立骨架，由同一面板操作，不创建空集合或强行合并骨架。

Character Designer 0.53.2 修正左右 Foot Roll 与右脚 Bank 的反向旋转，让脚部跟随控制器的旋转方向，并保留脚跟、前脚掌与脚尖支点。旧设置使用 **Fix Roll Direction** 显式更新，保持当前姿势、权重与显示位置；已有相关动画或外部依赖时会阻止更新，避免改坏动作。

Character Designer 0.53.1 修正胸部圆环的包覆方向：中段向胸部前方凸出，上下边缘朝身体收回。保持原尺寸、位置、骨骼支点与恢复资料；只调整圆环的前后弧度。

两个插件独立安装，也可以同时启用。RR Helper 的内部模块名保留为
`random_realm_builder_exporter`，便于更新原来的安装。

Character Designer 0.53.0 在 **Rig → Body → Body Controls** 加入 **Add Breasts / Hips**：左右胸部使用略带弧度的圆环，Hips 使用骨盆椭圆环。沿用共用身体与附近登记衣物匹配比例；Hips 读取 Character Setup，胸部骨骼优先按名称辨识，无法确定时提供骨骼选择。保留原骨骼名称、旋转中心、权重、约束、动画与集合；恢复按钮取回先前显示和颜色，控制器网格可编辑，恢复资料随 blend 保存。

Character Designer 0.52.3 将新建全身 Root 的显示高度对齐到鞋底控制轮廓，沿用脚部显示的静止坐标与偏移，不再固定在骨架原点。Direct 与 Enhanced 生成共用定位规则；只移动显示，保留全身旋转支点与动画。已有显示和恢复快照保留。

Character Designer 0.52.2 将新建头部控制器的侧面顶部、底部转角改为圆弧，保留正面切角轮廓、脸前开口和小前向标记。只调整显示形状，保留原旋转支点、姿势与恢复功能；已有手工编辑的显示不会自动被覆盖。

Character Designer 0.52.1 将新建眼睛控制器的眼罩和圆圈显示整体前移，便于从脸前选取。**Rig → Body → Body Controls → Advanced → Eye Display Spacing** 可调整额外间距，设为 0 恢复原显示位置；数据随 blend 保存。只移动可点击的轮廓，保留视线、动画和原追踪目标，变换轴仍位于目标骨骼。

Character Designer 0.52.0 在 **Rig → Body → Body Controls** 加入 **Add Head / Neck**。Head 使用脸前留空、下巴略收、带前向小标记的立体切角头框；Neck 使用开口短领圈。沿用共用 Head 与身体权重源，根据头部主体匹配比例，不包含长发。只替换原骨骼显示外形，保留旋转支点、父子关系、姿势和权重；Head／Neck 按钮选中原骨骼后用 R 旋转。恢复按钮取回原显示与颜色，形状与恢复数据随 blend 保存。

Character Designer 0.51.0 在 **Rig → Body → Body Controls** 为 Direct 绑定补上可移除的 **Root · Whole Body**：一起移动原骨架与手脚 IK 输入，保留原骨骼 Rest；统一缩放用 **Root Scale**。**Add FK Rings / Remove FK Rings** 为手臂、腿的逐段 FK 提供圆环，已有自定义形状保留。**Fit IK Sizes / Restore IK Sizes** 调整默认手部与膝盖箭头比例，保留已手调的尺寸。IK／FK 继续使用匹配后的端点切换；手动混合时提示状态并同时显示两套输入。近乎伸直的手臂匹配不会再被不必要的肘部方向修正破坏。

Character Designer 0.50.0 在 **Rig → Body → Spine Controls** 加入可单独移除的 **Spine IK / FK**。FK 保留整体弯曲和逐段控制；IK 用 **Chest IK** 移动／旋转胸部，**Spine Shape** 调整弯曲方向。切换先匹配当前姿势，无法保持时回滚。**Reset Spine Pose** 让整个脊柱回到相对当前 Hips 的默认姿势，避免只清零可见控制器后仍保留内部弯曲。沿用现有骨骼、权重、集合与配色。当前需在制作动画前选定控制方式，脊柱暂不支持已有动画或 Auto Key 的切换匹配。

Character Designer 0.49.0 在 **Rig → Body → Eye Controls** 加入眼罩外框与左右圆圈。移动外框控制双眼，移动圆圈单独微调；自动沿用 Head 与原眼骨方向，保留接入前视线。三个控制器全选后 **Alt+G** 回到原眼骨的默认视线。眼睛继续使用现有权重；**Remove Eye Controls** 保留当前视线并恢复原生控制，支持保存重开和依赖保护。线框按角色比例生成，并沿用暗调／选中提亮配色。

Character Designer 0.48.0 为骨骼控制器加入柔和配色：左侧青绿／浅蓝，右侧莓粉／蜜桃，中轴浅紫。未选中保持暗调色彩，选中与活动状态逐级提亮。新建控制器自动使用，已有角色可在 **Rig → Body → Limb IK → Apply Colors** 应用，**Restore Colors** 恢复原颜色；保存重开、重建及失败回滚保留颜色和首次备份。

Character Designer 0.47.0 增加 **Rig → Body → Spine Controls**：沿用原有脊柱骨，提供整体弯曲和逐段 FK 微调，可移除并保留当前姿势。Foot Roll 的线框现在跟随实际脚骨的姿势；**Arrow Placement → Footwear / Fit Arrow / Restore** 可以按鞋跟定位并恢复显示。鞋子参考随角色保存，已有权重和脚部旋转支点保持不变。

Character Designer 0.46.0 在 **Rig → Body → Limb IK** 的腿部加入可移除的 **Foot Controls**。**Foot Roll** 用脚跟、前脚掌和脚尖支点滚动整只脚，**Toe Bend** 独立弯脚趾；线框外形来自 Rain，保留署名。支持现有 IK／FK 姿势匹配、保存重开与原集合显隐，沿用已有脚部权重。**Remove Foot Controls** 保留当前姿势；存在控制器动画或外部依赖时会先要求处理依赖。重建或移除基础 Limb IK 前，先移除 Foot Controls。

Character Designer 0.45.0 新建控制器默认启用 **Animation** 骨骼集合，隐藏已被控制器替代的原骨骼，其他部位继续用原生骨。**Rig → Body → Limb IK** 为每条手臂、腿提供 **IK / FK** 姿势匹配切换，支持 Stable 和 Direct。开启 Blender Auto Key 时记录切换，拖动时间轴同步显示当前控制方式。**Restore Bone Collections** 可恢复首次整理前的集合布局。重建或移除前需先匹配回 IK；已有动画仍受依赖保护，不会自动删除。

Character Designer 0.44.0 将骨架工具集中到 **Rig → Body / Hair / Skirt**。**Character Setup** 在 Weight、Rig 共用主骨架、身体权重源以及按主骨架保存的 Hips／Head 对应，支持使用选中骨骼快捷指定。裙子字段改为 **Attachment Bone**，显示实际跟随目标；**Update Attachment / Restore Attachment** 显式更新或恢复连接，保留权重、控制器和动画通道。已有物理碰撞器的裙子暂不允许更换挂接目标，避免旧碰撞绑定失效。Hair 顶层保留建模工具，Weight 保留通用快速绑定与恢复。

Character Designer 0.42.3 新增 **Rig → Limb IK → Simplify Bone Collections**，
把当前骨架整理为 **Original / Controls / Animation**。Animation 有控制骨时使用
控制骨，否则保留原生骨；Build、Rebuild、Remove 后自动更新。头发统一为 **Hair**，
独立裙子骨架统一为 **Skirt**，不再细分出一长串集合。

Character Designer 0.42.2 将新建 Limb IK 的 **Auto Align** 默认设为开启，
Stable 和 Direct 两种方式一致。保存重开及 Rebuild 保留已有状态，包括手动关闭；
旧 Rig 若处于关闭状态，开启一次并保存即可。

Character Designer 0.42.1 将裙子移除、前臂校准移除、旧头发副本清理统一为红色，
与已有的头发解绑、Limb IK / Spline IK 移除按钮保持一致。

Character Designer 0.42.0 新增 **Animation** 页，连接免费本地 Kimodo，支持生成、
独立骨架预览、应用为新的身体 Action 及恢复上一 Action。模型和 Python 环境独立安装，
不含付费插件或云端生成。见 [本地配置](docs/kimodo-local.md)。

Character Designer 0.41.3 移除 **Weight Flow**，Weight 页保留 **Weight Tools**
和 **Weight Symmetry**。日常权重平滑使用 Blender 原生工具。

Character Designer 0.41.2 将 Modeling 与 Reference 合并到 **Miscellaneous**，
并把 Delta Symmetry 面板和建立配对按钮统一命名为 **Build Symmetry**。

Character Designer 0.41.0 直接在原始头发上绑定，每束骨链加入角色 Armature 的
Head 下，不再生成网格副本或专用头发骨架。支持保存重开后移除本次绑定、恢复原权重，
并提供旧副本清理。头皮帽完全跟随 Head；Mirror 两侧独立，中央同一束使用单链。

## 安装与使用

在 Blender 的 **Edit → Preferences → Add-ons → Install from Disk** 中，
分别选择上表的 ZIP 并启用插件。安装时选择插件 ZIP，不要选择整个仓库的下载 ZIP。
当前版本使用 Blender 5.2 验证。

- **RR Helper**：3D View 的 N 侧栏 → **RandomRealm** → **RR Helper**。
  用于建造资产、对象管理、图标/PBR 工作流及 Unity 交接，见 [说明](docs/rr-helper.md)。
- **Character Designer**：3D View 的 N 侧栏 → **Character Designer**。
  包含角色建模、骨架、权重、头发及 **Clothing → Skirt Setup**，见
  [完整指南](addons/character_designer/README.md)。

## 维护与打包

后续插件修改以 `addons/` 为入口；模型文件与真实角色集成验证留在 Blender 项目中。
Blender 安装目录通过统一脚本从这里部署并校验，见 [源码与部署流程](docs/source-workflow.md)。
现有 Character Designer 署名说明保存在 [ATTRIBUTION.md](addons/character_designer/ATTRIBUTION.md)。

修改插件后更新其 `bl_info["version"]`，用 Python 3.10 或更新版本运行：

```powershell
python tools/build_releases.py
```

脚本分别生成可安装 ZIP，并更新 `dist/SHA256SUMS.txt`。已有版本的 ZIP 内容若与源码
不同，会要求先升版本；内容一致时保留原安装包。测试方法见 [tests/README.md](tests/README.md)。
本次迁入的检查结果见 [验证记录](docs/repository-validation.md)。

## 旧代码整理

已清理旧 PhysicalDress/SplineIK 的三个重复脚本或安装包，其三点 Hook 设计已由现版
裙子工具覆盖，见 [检查记录](docs/legacy-skirt-cleanup.md)。
Language-Switcher、Vertex-Cleaner、AnimationSwitcher 及原 Blender 示例仍保留原目录。
