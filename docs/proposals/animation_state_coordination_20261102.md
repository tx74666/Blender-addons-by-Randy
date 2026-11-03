# Animation 状态协调方案（待评审）

日期：2026-11-02（Asia/Shanghai）  
范围：Character Designer Animation；本文件是设计提案，不是已实现行为。

## 建议结论

保留现有 Source/Custom、独立试摆 Action、逐模块事务和恢复记录。在它们上方增加薄的状态协调层，统一解释“当前在做什么、这一步能否开始、被什么阻止”，逐步收拢各入口重复的组合判断。

不把所有状态压进一个大 Mode 枚举，也不让中央协调器接管骨架、关键帧、物理或 SESSION/REFS 的恢复。第一轮迁移应保持现有动作、拒绝条件、Rest/Base Pose 和物理行为完全一致。界面名称的改动列为单独评审项。

## 当前依据与问题

- `animation_pose_test._admission` 认识 Composition、Original、Unity preview、Rigging、Forearm 和 Blend Pose；`body_original_mode._enter/_leave` 又反向识别 Natural Pose。新功能必须了解多个模块的组合，容易出现入口之间理由不同、维护时漏掉组合的风险；这不是已证明现有组合会破坏数据。
- `animation_rest_pose.show_rest` 调用 `animation_pose_test.start_natural`，后者使用独立 scratch Action，并保持原生 `pose_position='POSE'`。因此产品按钮 Rest Pose 与 Blender 原生 REST 不是同一种状态。
- 主界面有 Rest Pose / Pose，Advanced 还有 Base Pose。Natural 是全身中立试摆；Base 是通过身体控制匹配的身体基准试摆。两者都保留原动画，仍有需要明确的用途边界。
- Physics 已有 permission、desired/applied mode、running/paused、pending 和 recovery 等不同事实；中央层必须读取这些事实，不能根据一个复选框推断运行状态。

以上依据来自 2026-11-02 读取的 canonical 动画模块；没有读取或操作艺术家场景。其他并行修正的最终版本需在实施前重新固定。

## 状态按独立维度表达

| 维度 | 读取内容 | 约束 |
|---|---|---|
| 动画绑定 | work item、Source/Custom、实际 Action/slot、路由和 Current Rig revision | 以原模块的真实引用和持久记录为准；不复制成第二套可写真相 |
| 骨骼编辑表示 | Controls / Original；另外读取 native display view 的所有者 | Original 可以与已支持的 Natural 试摆共存，不统一判为冲突 |
| 试摆 | None / Natural / Base；scratch、token、恢复所有者 | Natural 与 Base 不叠加；继续保留原有入口验证 |
| 原生显示 | POSE / REST，以及工具是否拥有临时显示标记 | 原生 REST 不等于 Natural；用户在 Blender 自行选的 REST 不能冒充工具所有权 |
| 其他会话 | Unity preview、Rigging Inputs、Composition、Forearm、Blend Pose 的实际 owner 和作用范围 | 逐操作定义兼容性，不建立“任何会话都独占整个应用”的规则 |
| 每个物理组件 | permission、请求模式、已应用模式、实际运行/暂停、pending、recovery | 保留现有后端和组件规则，不能把 permission=True 当作 solver 正在运行 |
| 恢复状况 | 各 owner 的 valid / pending / blocked / unknown | 是恢复质量，不是另一个编辑模式；unknown 不自动清除或修复 |

已单独处理的两项功能不新增状态轴：Export Base Pose 是每个 Action 保留的导出输入数据，不是活跃模式或会话；Capture 只记录已授权 Custom 的输入，保留现场姿势与关键帧，导出时仅在独立快照应用。依赖包的 locator 是同一 SHA256 文件的存储位置映射，不改变 Source/Custom 身份，也不产生或恢复任何动画/SESSION 权限。

快照只读取轻量标记与引用，不在面板绘制时扫描整份 Action、几何、作者数组或启动后端。界面快照只用于展示；执行应按操作实际影响范围做即时验证。局部手腕热键只验证编辑所有者、相关骨骼与旋转通道，不因统一入口重新加入整份 Action／Source、磁盘、hash 或跨场景扫描；完整证明留在明确的切换、依赖迁移、验证与导出操作。

## 统一入口，原模块继续负责事务

建议新增的协调接口仅包含三个职责：

1. `snapshot(context, rig)`：收集已支持状态的轻量只读视图，保留每条事实的来源和 owner。
2. `preflight(operation, snapshot)`：返回 allow / blocked / unknown、稳定的原因码、简短用户文字及可执行的下一步。不得在这里结束会话、切换 Action、暂停物理或写恢复记录。
3. `execute(operation, context)`：重新确认当前对象、绑定、owner 和上下文，调用原模块的 prepare/apply/rollback；只有原事务成功才发布过渡成功。中央层不保存第二份姿势、关键帧或 SESSION。

同一次同步操作可复用已证明确实相同的准备结果；不能跨用户操作沿用旧许可，也不能把轻量快照当作完整内容检查的替代品。异步任务或弹窗之后必须重新验证。

每个模块提供自己的状态适配器与操作契约。协调器按操作表组合这些契约，避免中央文件继续复制所有模块的私有字段和复杂实现。适配器首先包装现有检查；不要在重构中顺带放宽门槛。

Animation 正在独立验证的局部 edit-owner guard 若通过验收，可作为手腕入口的适配基础；中央层不再复制另一套完整 revision／Source 验证，也不把 UI 的可点击状态当作权限。每个 Action 的 Capture／Clear Export Base Pose 是保留或清除导出输入数据，不是新的活动姿势模式；已认证的 Dress／Rigging 专用导出契约同样属于该次工作的数据合同，不扩张全局 Mode。

### 首批过渡合同

| 操作 | 协调层负责 | 执行与失败归属 |
|---|---|---|
| 点击 Source / Custom | 选择目标 work item；检查当前会话是否允许离开 | 继续使用 worklist 与 `resume_for_operation`；目标拒绝时恢复进入前试摆和草稿 |
| 进入/重置 Natural | 给出其他试摆/预览阻止理由；保留已支持 Original 共存 | `start_natural` 保留动画并校验完整控制图；失败按原路回滚 |
| 进入/重置 Base | 明确它是身体试摆；先执行现有 Original、preview、Composition 等门槛 | `start_or_reset` 拥有身体匹配、scratch 和恢复 |
| 返回动画 | 找到当前试摆 owner；验证恢复资格 | `finish` 恢复原绑定；有内容的 scratch 仍保留为草稿 |
| Original / Controls | 按具体操作读取既有兼容条件；保留已支持的 Natural 共存与过渡，只解释实际被拒绝的冲突 | 原 Original 模块负责匹配、SESSION/REFS、回滚与已批准的 pending physics 处理；不新增“Natural 一律先退出”的总门槛 |
| Physics On/Off、Manual/Automatic | 用统一事实解释运行、等待和恢复 | 原组件后端负责启动/暂停/恢复；保留已批准的部分成功与错误，不全局撤销别的已完成过渡 |

先把现有行为逐项写进兼容表。表中未审阅的组合继续委托原模块检查；unknown 不作为新的成功路径。只有已证明实际冲突的短操作窗口需要互斥，读分析和不同数据的独立工作继续并行。

## 界面名称建议（单独评审，暂不实施）

界面保持原来的按钮数量和布局，不增加常驻状态横幅、总模式下拉框或多层确认。

| 当前名称 | 建议英文名称 | 中文含义与精确行为 |
|---|---|---|
| Rest Pose | Neutral Pose | 中立试摆：进入/重置现有 Natural scratch；可编辑，原动画保留，骨架 Rest 数据不变 |
| Pose | Animation Pose | 动画姿势：结束当前中立试摆，返回保留的动画；兼容原生 REST 的显示返回路径 |
| Advanced → Base Pose | Body Base Pose | 身体基准试摆：沿用现有身体控制匹配，继续放在 Advanced；不是修改绑定姿势 |
| Return to Animation | Return to Animation | 保留现有明确的退出动作和草稿保留规则 |
| Blender 原生 Rest Position | 保留 Blender 原生名称 | 查看真实骨架绑定 Rest；不把该状态重命名为 Natural，也不在此提案中添加新按钮 |

工具提示补充“试摆，原动画会保留”，不暴露内部 token、hash、schema 或 snapshot。错误只在当前操作需要用户处理时出现，例如当前操作的原合同拒绝活跃 Composition 时提示“先结束 Composition，再执行这一步”。不因 Natural 处于活跃状态就统一要求退出；已支持的 Original 共存与过渡照原合同执行。

例：正在编辑 Walk / Custom，点 Neutral Pose 后进入中立试摆；旋转手臂并插入关键帧；点 Animation Pose 返回 Walk，试摆内容保留为草稿。如果随后选择另一个动画失败，仍留在选择前的有效状态，不把失败呈现成已经切换。

这一步只是名称方案。实施前需对照用户现有用法确认 Neutral / Body Base 的区分；没有必要为了命名合并两种不同匹配流程。

## 渐进实施与验收

### 阶段 A：只读快照和兼容表

建立各模块适配器；固定当前允许/拒绝实例，包括 Original + Natural 的已支持组合。以纯测试验证状态判定和原因码，不调用 Blender 变更接口。保持所有执行路径与界面不变。

### 阶段 B：先迁移试摆与动画切换入口

协调器包装现有 admission；比较新旧判定相同后，逐条迁移统一错误来源。全内容、Rest/driver、owner、Source、Current Rig 和 rollback 检查继续留在原模块，不以新 gate 替换。

### 阶段 C：迁移 Original 与其他预览会话

每次只迁移一个操作族，建立双向进入/退出证据。经验证后移除对应重复组合判断；不得先删除旧检查再补测试。物理仍通过原后端契约，solver 行为不在该阶段更改。

### 阶段 D：单独实施已批准的名称调整

只改可见文字与 tooltip；沿用存档字段、操作 id 和兼容恢复读取。单独核对窄面板、选中态、原生 REST 与 Natural 的显示，避免视觉上宣称不同功能已合并。

### 必要验收

- 允许组合：Controls/Natural、已支持 Original/Natural、Base、Source/Custom、真实 Current Rig revision；判定与原行为一致。
- 拒绝组合：两种试摆叠加；当前已拒绝的 Unity/Rigging/Composition/Forearm/Blend Pose 冲突；无效或歧义 owner。检查完整理由及无写入。
- 新鲜性：prepare 后更换 Action、slot、rig、work item、revision、scene/view layer 或恢复载体，执行重新检查并拒绝旧许可。
- 回滚：切换目标失败、第二阶段匹配失败、退出恢复失败时，验证原 Action/slot、Source、草稿、姿势、帧、NLA、约束与 SESSION/REFS 没有被新协调器破坏。
- 历史：保存/重开、Undo/Redo 和旧恢复记录按原规范读取；未识别记录保留，不自动修复、伪造 owner 或重放历史许可。
- 物理：覆盖 permission Off、手动、自动、暂停、等待 Controls、保存后 stopped 与恢复失败。验证请求、运行、错误仍来自原后端，并保留已经批准的 UI 状态区分。
- 成本：面板只做轻量读取；完整 Action 证明只在必要执行阶段。对新引入的重复读取计数，不能用减少检查覆盖来换速度；本方案不重启已经济关闭的 X playback-FPS 分支。

优先复用现有 pure/native harness 和已固定不变的收据。状态/默认/所有权或恢复行为发生实际变化时，必须补对应 native 转换和作者数据/回滚证据；纯文字调整只做相关布局核对。艺术家现场更新需在准备、独立验证和完整投影完成后安排短窗口，不能把磁盘部署当作已刷新。

## 本次交付边界

本方案只描述尚待评审的协调层与名称建议。已有的独立修正包括读写性能、依赖定位/打包和每个 Action 的导出基准输入；它们分别按自身实现与验收交付，不代表协调器已经实现。此提案没有新增协调器、替换状态模块、改界面名称、改变 Natural/Base/Physics 语义、改 SESSION/REFS 格式或触碰艺术家状态。提案本身也没有部署、现场刷新、保存场景/偏好、Unity 操作或 Git 上传。

下一步评审只需决定是否采用“薄协调层 + 名称单独调整”的方向。已有性能、退出恢复和 Physics 状态修正按各自验收交付；它们不自动授权本提案的运行时改造。
