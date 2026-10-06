# RR Helper 0.2.58 — Surface Text 正面材质

Surface Text 的可编辑导出描述现在使用文字实际用到的正面材质，排除专用背面发光材质。正面为 `Banner` 时，即使材质面板选中了 `Surface Text Rear Light` 或未使用的材质槽，导出的正面仍为 `Banner`。

保留对象级材质覆盖、字形材质分配和原有多正面材质的单个代表材质流程。导出不会修改材质选择或文字本身，发光强度为零仍明确输出。

Blender 5.2 工厂空场景中的可编辑文字契约 **20/20 通过**，其中五项覆盖本次材质修复。原生验证日志位于 `D:/Blender/Projects/Build/WIP/Validation/surface_text_local_darkness_20261006/editable_contract_20.log`。

安装新版本不会改写已有 manifest。Unity 的旧材质恢复、参数整理和 N/e 局部暗块现场验收另行记录，不能由这组 Blender 测试推断已完成。用户正在打开的 Blender 场景没有保存。
