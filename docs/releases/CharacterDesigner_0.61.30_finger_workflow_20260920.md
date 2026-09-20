# Character Designer 0.61.30 — 统一手指工作流

## 范围与最短操作

基于本机规范源 0.61.29（包含此前 0.61.26–.29 的已部署改进），没有重做五指检测。
保留五对记录、局部失效、原表面参考和体积内直线。没有改 Animation、其他身体工具或 RR Helper。

1. 确认 Character Setup 的 Body Weight Mesh / Main Rig。无骨骼也能先做 Capture 和拓扑。
2. 网格编辑模式选一根手指的纵向上表面面带，Capture Detection，一次保存长度、内部轴和该指弯曲侧；可靠对侧自动同步。
3. 要校准 Roll：直接 Calibrate All；或在骨骼 Edit/Pose 模式选任一节，再 Calibrate Selected。后一种会扩展整指并同步对侧，多指去重。
4. 回网格编辑模式，Prepare Joints，查看彩色中心环与灰色支撑环。可调关节位置；选明确横截闭环后 Use Selected Loop 可指定当前关节中心。
5. Generate / Update Rings。勾选 Weight After Rings 时，成对生成和局部赋权一起提交；缺少条件则整次停止。
6. 只改权重用 Update Local Weights。Shift-click Prepare Joints 打开间距、补充环数、A 骨三环比例设置；B 使用剩余骨骼权重预算的互补比例。

Preview Bend 默认显示当前手指对；Shift-click 显示全部已捕获手指。绿色弧线表示正角度方向，并非实际姿势变形。橙色小十字是原骨骼关节位置，偏差连线不移动骨骼。

## 修改文件

| 文件（规范仓库相对路径） | 作用 |
|---|---|
| `addons/character_designer/__init__.py` | 版本 0.61.30 |
| `addons/character_designer/finger_targets.py`（新增） | 当前角色骨链匹配、空间/对称验证、All/Selected、整对 Roll 回滚 |
| `addons/character_designer/finger_workflow.py`（新增） | 每指/关节参数、共享源快照、成对三环、局部 A/B 权重、统一事务 |
| `addons/character_designer/finger_workflow_ui.py`（新增） | 三块式入口、参数弹窗、关节偏差标记、缓存预览 |
| `addons/character_designer/finger_bank.py` | 长面带法线复用、来源侧记录、独立记录冲突提示、多指局部更新 |
| `addons/character_designer/finger_bank_ui.py` | 骨链映射字段；捕获/清除/切指时清理旧下游预览 |
| `addons/character_designer/finger_bones.py` | 注册与主界面接入，保留旧 API |
| `addons/character_designer/finger_layout.py` | 提取原数据保护生成器的 staging 接口，支持不相交的多个计划及各轨道分数 |
| `addons/character_designer/finger_definition_ui.py` | 事件失效替代周期全量校验；缓存 GPU 批次 |
| `tests/test_finger_workflow_blender.py`（新增） | 12 项整合回归 |
| `tests/test_finger_workflow_gui.py`（新增） | 真正 Blender 事件循环、按键 Undo/Redo、绘制计时 |
| `docs/finger_joint_tool.md`、本文 | 行为、限制、测试说明 |

X 项目另增加 `tests/test_real_x_finger_workflow_blender.py` 和 `tests/test_real_x_finger_workflow_gui.py`，仅为真实模型隔离测试。部署复制到 X/addons 和本机 Blender 用户插件目录，不从部署副本反向开发。

## 已执行验证

环境：本机 Blender 5.2.0 LTS。以下为脚本自动化测试，不是用户当前 Blender 会话中的手工点击验收。

- **77 项聚焦后台用例通过**：既有 layout 7、internal 10、bank 8、flex 11、definition 11、range 7、root 2、joint 4、bones 5；新增 workflow 12。
- 新增覆盖：All 无选择、Selected 整链/多指/双方去重、不同节数拇指、控制骨过滤、歧义提示与骨骼选择提示、非中立姿势、骨链过期、第二侧失败整对回滚。
- 真实单手可用；仅删除对侧检测结果而实际仍有手指则拒绝；旋转角色有效；不一致 Mirror Object 对称基准拒绝。
- 明确中心环保位、非闭环/重叠范围拒绝、非均匀内外间距、重复生成无加边；保留 UV、自定义标量属性、Shape Keys 和自定义法线。
- 自动 A/B 权重、其他变形骨预算、非骨骼遮罩、目标锁定/余额不足、缺失组创建与回滚、后续新增组索引同步；手动改权重会阻止旧快照覆盖；切到其他指不会执行前一指尚未提交的参数。
- 保存重开、局部记录同步、同一角色多指连续生成后再 All、独立对侧法线冲突保留旧记录。
- **Blender GUI 事件循环测试通过**：通过事件模拟执行 All、生成+赋权、单独赋权，分别 Ctrl-Z / Ctrl-Shift-Z，比较 Roll、完整网格指纹和记录；不是仅调用 execute 后宣称撤销可用。两次运行都通过。
- **真实 X 模型隔离测试通过**：五根长面带共 50 个面，一侧采集自动形成十份内部参考；All 校准现有 30 根指骨；食指、中指连续成对生成、重复更新和局部权重；保留 10 个 Shape Keys、自定义法线，保存的 X.blend 哈希不变。
- **真实 View3D 截图已检查**：三环与偏差标记、0°、45°、90°、两关节各 45°。计算结果有限且方向测试通过，但视角存在邻指遮挡；这不是无夹陷/穿插/体积损失的质量认证。测试结束恢复姿势；没有保存原模型或改变用户正在使用的会话。

## 实测耗时

同一机器单次/少量样本；不是基准保证。GUI 测试是合成双手场景，30 次 `DRAW_WIN_SWAP`，包含 Blender 窗口绘制、同步及其他面板，不可当作纯插件 CPU 耗时。

| GUI 状态 | 首轮 ms/次重绘 | 复跑 ms/次重绘（有并行后台回归负载） | 计数到的全量扫描 |
|---|---:|---:|---:|
| 侧栏关闭 | 15.74 | 15.98 | 0 |
| 侧栏打开、预览关 | 16.29 | 17.30 | 0 |
| 单指入口预览（默认含对侧，共两指） | 17.25 | 17.72 | 0 |
| 十指预览 | 16.43 | 20.03 | 0 |

计数覆盖 `_snapshot`、网格 fingerprint、骨链 index、三环 build_plan。实际绘制使用缓存 GPU 批次；没有按帧重建。这些结果不能解释为“完全零性能影响”。

真实 X（约 3.4k 初始顶点、10 Shape Keys、已有 UV/法线/权重）两次隔离运行：

| 明确操作 | 测得时间 |
|---|---:|
| 每指 Capture（含对侧） | 0.28–0.34 s |
| All（30 根骨骼） | 0.64–0.76 s |
| 食指成对生成＋局部赋权 | 6.49–7.00 s |
| 加入中指、保留食指已提交结果 | 6.71–6.81 s |
| 当前手指对仅更新局部权重 | 1.35–1.41 s |
| 两指弯曲预览首次计算 | 0.23–0.24 s |
| 十指弯曲预览首次计算 | 1.13–1.17 s |

完整数据保护和真实模型多指重新对应仍有明显点击等待成本；本轮没有把它转移到持续后台扫描。后续可基于 profile 继续优化，不能跳过校验来制造更快结果。

## 明确限制与数据保护

- 不自动移动骨骼，不保证 .8/.5/.2 对所有角色都产生理想弯曲。中心环偏移、内外间距与原权重必须结合模型检查。
- 骨链仍需要现有可识别的指名/侧别；不从任意无名复杂控制 rig 猜测。歧义可用 Selected 指定候选，控制链不作为变形链。
- 动画/驱动/约束/非中立相关姿势、非均匀或镜像 rig scale 保持安全拒绝；没有自动清除这些数据。
- 仅规则、闭合、不分叉四边面指身。复杂手掌/指尖封口不重建；内外不等间距支撑跨越原截面时拒绝，需缩窄或使用等距。
- 生成前仍使用基础几何并保留原有 Shape Keys；Capture 不要求切换形态键。复杂未支持的数据层沿用现有 preflight，不静默丢弃；不是对所有 Blender 插件自定义数据/版本的兼容认证。
- 外部手工改网格、权重、Shape Keys 或属性后，不能合并时明确停止。点击 Prepare 旁的 X 释放本角色全部准备快照，保留网格/权重/参数，再 Prepare；不会偷偷把修改回退。其他手指的基础身份记录不会被全部清空。
- 已赋权结果切为仅拓扑也不会自动重置权重；保持自动赋权，或释放后用当前结果作新基准。
- Preview Bend 不需要逐骨选中；十指叠加图可能比较密，默认只显示当前一对。旧版兼容工具不保证享有新主流程的全部缓存性能。
- 未验证其他 Blender 版本、linked-library/override 复杂组合、跨不同变形器的所有生产场景；没有宣称完整项目全部非手指测试都跑过。

## 测试入口

后台：`blender --background --factory-startup --python-exit-code 1 --python tests/test_finger_workflow_blender.py`。

GUI：先用 `tests/test_finger_workflow_gui.py -- --build <临时fixture.blend>` 建测试文件，再用新的隔离 Blender 进程加 `--enable-event-simulate <fixture.blend> --python tests/test_finger_workflow_gui.py -- --run <临时result.json>`。

真实模型：X 的两份新脚本，均使用独立进程、`--disable-autoexec`，不保存 X.blend。GUI 输出位于本次临时测试目录，截图不作为正式模型修改。

本次为本地代码/打包/部署；未执行 git commit 或 push。
