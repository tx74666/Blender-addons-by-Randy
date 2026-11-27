# Character Designer 0.77.252 · Rig Export 自动包含角色部件

在 X 中只勾选 **Rig**，Cosha 的导出会自动列入 **Cosha、Clothes、Dress、Hair、Shoes、Stocking** 六个网格。八条裙摆辅助线留在 Blender。

Blender 5.2 的 17 项原生选择测试通过。在隔离的 X 副本和 Unity 项目目录中，实际 Export 生成待审包，状态为 **Rig pending Unity review**，既有 FBX 与接收记录保持原样。Unity 玩家替换与物理迁移由 Character 对话继续验证。[选择验收](<D:/Blender/Projects/Character/X/validation/rig_dependencies_20261126/choices06-result.json>) · [导出验收](<D:/Blender/Projects/Character/X/validation/rig_dependencies_20261126/ledger06/operator-result.json>)

正式 Blender 5.2 与 X/addons 各核对 318 个文件，零差异。隔离热刷新前后的 28 项状态一致，刷新后的 Rig 选择再次得到相同六个网格。[热刷新](<D:/Blender/Projects/Character/X/validation/rig_dependencies_delivery_20261126/refresh01.json>) · [交付记录](<D:/Blender/Projects/Character/X/validation/rig_dependencies_delivery_20261126/delivery252.json>) · [安装包](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.252.zip>)
