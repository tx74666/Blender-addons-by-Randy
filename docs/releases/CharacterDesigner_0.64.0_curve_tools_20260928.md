# Character Designer 0.64.0 — Miscellaneous / Curve Tools

本地交付，2026-09-28。未 commit、未 push；保留仓库中的既有修改。

## 使用位置

Character Designer → Miscellaneous → Curve Tools → Mode：General / Hair。

- 顶层 Hair 建模页移到此处。Rig → Hair 的骨骼、绑定和控制器功能保持原位置。
- General：从支持的 strip / tube 横截面提取中心线，初始不添加截面厚度，不套用 Hair 的 Half、收尖或外侧朝向规则。已有 General 曲线刷新时保留截面、Radius 和 Tilt。
- Hair：继续使用原 Half、宽度、收尖与外侧朝向算法，保留 Centered / Surface / Blend。
- Generate / Update：网格 Edit Mode 中选择支持的横截面。已有曲线按对象记录的模式更新；切换下拉框本身不改几何。旧版未标记的中心线仍按 Hair 处理。
- Recover Applied Curve：共用既有恢复与事务流程，按实际几何识别半圆、整圆或支持的矩形截面，不由 Mode 强行指定截面。
- Curve to Mesh (Copy)：Object Mode 选中 Curve 后可用。生成当前求值结果的 Mesh 副本，保留原 Curve、其 Shape Keys 和修改器。

这里的中心线提取／恢复具有拓扑要求，并非任意实体网格都能无损变成曲线。无法可靠判断的输入会拒绝处理。

## 修改范围

相对不可变 0.63.0 ZIP，只有三个运行文件不同：

1. `__init__.py`：页面、模式分流、共享生成／更新接入；修复恢复截面采样、方向和浮点边界。
2. `ui_constants.py`：四个顶层页面及旧 Hair 路由兼容，保留既有 RNA 数字值。
3. `curve_tools.py`（新增）：模式身份及保留源曲线的网格转换。

没有复制第二套中心线算法，没有改动 Rig → Hair 实现。

独立代码审查发现并修复：General 反向选择后更新会错配 Radius/Tilt；模式切换遗漏预览缓存失效；圆环相位可能改变纵向连接。实际 Blender 验证又发现并修复当前版本截面顶点数、矩形／圆形误判、浮点伪 Extrude，以及弯曲方形的等价方向选择问题。

## 实际验证

Blender 5.2.0 LTS，串行运行，全部使用测试夹具或独立副本。

新增测试 **27 项通过**，其中参数矩阵包含额外子场景：

- `test_curve_modes_blender.py`：13 项。General 不调用 Hair 朝向；旧 Hair 兼容；两模式共存；刷新保留参数；反向更新；预览缓存；切换及面板绘制不改几何；失败清理。
- `test_curve_to_mesh_blender.py`：6 项。求值几何、材质覆盖及空槽、原 Shape Keys 保留、裸中心线、选择失败回滚、Edit Mode 拒绝。
- `test_curve_recovery_modes_blender.py`：8 项。三类截面；18 种分辨率／Extrude 组合；弯曲＋渐变 Tilt；完整顶点对应、边和面连接；极小 Extrude 精确恢复或原子拒绝；失败回滚。

回归通过：原中心线／Hair 48 项；页面路由 5 项；Rig 层级 3 场景；Hair Bones UI 脚本；Hair Bones Rig 9 项；旧 Pose／权重切换 14 项。

GUI 实测：独立 `Curve_Tools_validation.blend` 内查看新页面与 General / Hair 下拉框、原 Hair 三个对齐选项，并点击 Curve to Mesh (Copy)，确认产生 Mesh 且源 Curve 保留。验证窗口已正常关闭。

热加载先在已注册 0.63.0 的独立副本通过预演，并验证卸载／再次注册；随后已应用到正在运行的用户 Blender。安装前后核对所有曲线数据、网格、权重、Shape Keys、骨架 Rest、Pose、选择和模式保持一致。原会话位于旧 Hair 页，仅将 UI 转为 Miscellaneous → Hair；`X.blend` 未保存，dirty 状态仍为 false。

未验证：真实角色头发上的任意新转换、所有第三方截面／拓扑、所有 Blender 历史版本，以及新转换按钮的 GUI Undo/Redo。恢复不能保证任意不规则截面可表示为单一 Curve；数值上不可可靠区分的微小 Extrude 会拒绝，极低分辨率几何可能有等价截面类型。

## 安装与精确快照

源码、Blender 用户安装目录、X 的验证副本已通过 deploy `--check`，113 文件一致，两个目标各更新 3 文件。

- 包：`D:\MyRepository\Blender-addons-by-Randy\dist\character_designer-0.64.0.zip`
- SHA256：`ca73d341ca84c6ca446ee46a70f674f514a22c16c7f9c1f3346ba70c7a58af19`
- 基础 Git HEAD：`acaa4b61b39a3b8989e8e9be6c7b909c4c2cd032`，交付内容以 ZIP 及文件清单为准，非此 HEAD 单独可重现。
- 证据目录：`D:\Blender\Projects\Character\X\task_artifacts\curve_tools_20260928`
- 精确清单：`release_snapshot.json`；本次补丁：`runtime_diff_from_0.63.0.patch`；测试源快照：`validation_tests.zip`。
- 当前会话安装校验：`installation.json`；独立预演：`installation_dryrun.json`；测试日志：该目录的 `*_final.log`。
- 旧安装备份：`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260928-024515-86c8302f`。

当前会话的旧 RNA 默认页与一条 Recovery 静态提示文字在正常重启后更新，不影响已经生效的新页面、模式和操作。
