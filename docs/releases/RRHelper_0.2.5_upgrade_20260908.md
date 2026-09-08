# RR Helper 0.2.5 升级记录

安装位置：`C:\Users\Randy\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\random_realm_builder_exporter`
安装包：`D:\Blender\Addons\MyAddon\random_realm_builder_exporter.zip`
源码备份：`D:\Blender\Addons\MyAddon\backups\RRHelper_before_0.2.5_20260908_040443.zip`
旧安装包备份：`D:\Blender\Addons\MyAddon\backups\RRHelper_previous_installer_20260908_040443.zip`
生效方式：在当前 Blender 点一次 **Refresh Add-on**，或下次启动 Blender。

## 变化

- 按显式父组、直接父对象、共享组 ID 查询归属，跳过无关组的重复成员扫描；保持原顺序和嵌套过滤。
- 面板每次绘制独立查询缓存；关闭 Group 时跳过其查询，已合理或已忽略的名称不再计算建议名称与包围盒。缓存不跨帧。
- 图标固定输出所选尺寸的 8-bit RGBA PNG；导出结束或异常时恢复原渲染设置。清理临时相机 object 与 data，避免重复导出积累孤立数据。
- 空网格在输出前拒绝；保留点、边、Geometry Nodes 生成几何和实例；评估失败明确报错。
- 导出完成提示显示总耗时。

## 验证

Blender 5.2 后台读取同一个已保存的 Builder6 场景，顺序执行升级前后同一玻璃墙变体组的 Model + Icon 导出；1024px，EEVEE 64 samples。输出、缓存和导入请求隔离在 TEMP，没有触发正式 Unity 替换。

- 整组耗时：15.089 秒 → 7.208 秒，本次耗时减少 52.2%。前一次并行审计期间的 baseline_raw 有明显负载干扰，不用于结论；计时有普通运行波动，不代表所有场景。
- 两个 FBX 重新导入后的变换、顶点、面、材质槽和 UV 一致；两个 1024×1024 RGBA 图标像素完全一致。
- 导出清单一致，比较时仅忽略时间戳和 FBX 文件哈希（FBX 自带时间信息）。
- 327 个原对象、8 个合成对象的有序父组结果一致；嵌套组、重复 ID、显式关联和编辑后刷新通过。
- 最终版本再次通过全场景 46 个根、正常身份、稳定 ID/当前 ID/历史 ID 冲突与恢复后无陈旧缓存检查。
- 面板 6 种状态的 UILayout 调用记录一致；异常恢复、跨帧刷新、返回列表隔离通过。最大组展开时仅 panel 查询微基准 120.51 → 52.15 ms；此项不含原生 UI 绘制，不能换算 FPS。
- 渲染与空几何 20 项检查通过：重复渲染、JPEG 37% → PNG 1024、EXR32 恢复、渲染失败、相机创建失败、清理失败、空网格无文件或导入请求、GN mesh 和未 Realize 实例等。
- 相机数据增量 2 → 1；保留的是现有功能使用的预览相机，临时渲染相机不再遗留。
- Builder6.blend 未修改，SHA256：`65eccbe90088979b83c6a43ddf198a7f5a94f96e653037a22822252cb5473df2`。

已安装 __init__.py SHA256：`97b12b516331b172f05681ae7681c4bdae002ca9f4eb7b65676bb3306f60d8c3`。

## 保持的导出契约

没有简化资产身份冲突验证，也没有修改 Unity 自动替换规则。Skip Existing 复用输出的 icon-only 语义保留；若后续升级新资产复用模型流程，需要 Blender 和 Unity 共同定义可验证的模型 fallback 契约，不能仅猜测磁盘文件存在就跳过模型导出。
