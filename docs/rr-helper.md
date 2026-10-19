# RR Helper

0.2.64 在 **Miscellaneous → Sidebar Tabs** 加入明确的 **Enable Tab Reordering** 入口：工作区插件过滤阻止排序时，点击此按钮显示当前工作区的全部插件标签并启用排序；然后选中列表项，用 **Up / Down** 调整顺序。顺序自动记住。见 [0.2.64 更新记录](releases/RRHelper_0.2.64_sidebar_sorting_enable_20261019.md)。

0.2.63 将 **Layout** 页面改名为 **Miscellaneous**，保留原来的布局快照和 Save Layout／Restore。新增 **Sidebar Tabs** 列表；新启用插件后可按刷新重新收集。排序仅管理 RR Helper 自己的空白定位面板，不重新注册其他插件或覆盖 Blender 的 `userpref.blend`；原生固定或优先显示的标签不进入可移动列表。见 [0.2.63 更新记录](releases/RRHelper_0.2.63_sidebar_tabs_20261019.md)。

0.2.60 将导入的 Unity 设备屏幕改为不发光的空白灰屏，保留尺寸、姿态和设备身份。旧板子可用 **Unity Layout → Blank Imported Screens** 直接清空画面，无需重导；设备外壳纹理及用户共享材质保留。见 [设备布局流程](unity_device_layout.md) 和 [0.2.60 更新记录](releases/RRHelper_0.2.60_blank_device_screens_20261008.md)。

Ring 的固定图案烘焙与 Unity Standard 导入已通过原生 Blender/Unity 验证；Unity 材质顶部提供联动线条与发光的 **Ring Color** 和独立 **Emission Strength**，地板底色独立。0.2.59 发布时已打包、部署到 Blender 5.2，并核实 Builder6 加载该版本。操作见 [Ring 烘焙与导入流程](standard_ring_bake.md)，验证、实际 addons_core 加载路径及场景保护记录见 [0.2.59 更新记录](releases/RRHelper_0.2.59_ring_pbr_bake_20261008.md)。

Surface Text 导出的正面材质识别修复见 [0.2.58 更新记录](releases/RRHelper_0.2.58_surface_text_front_20261006.md)。选择背光材质槽不会再改变文字的正面导出描述。

当前导出使用 `Model` / `Icon` 两个开关：勾选 Model 就重新生成模型，勾选 Icon 就生成图标；只导图标时取消 Model。旧场景的 Skip 值不再跳过模型。Icon-only 保留旧 FBX 绑定的来源和 Surface Text 数据，详见 [0.2.24 更新记录](releases/RRHelper_0.2.24_model_icon_20260929.md)。

Core 星标与自动相对布局见 [0.2.20 更新记录](releases/RRHelper_0.2.20_core_object_20260929.md)：`Export Core Object` 只控制本批是否导出 Core 本身。保留 0.2.22 的无边框图标和 0.2.23 的顶部 Core 存在状态。下面保留仓库迁入时的 0.2.5 背景说明。

插件显示名为 **RR Helper**，Python 模块名为 `random_realm_builder_exporter`，
界面位于 N 侧栏的 **RandomRealm** 标签。

本仓库的八个运行源码文件来自已安装的 0.2.5，并与现用安装包逐字节核对。
较早的 `_package_random_realm_builder_exporter` 暂存目录仍是 0.2.2，本次未采用。

主要工作包括对象/父组管理、建造资产身份与命名、图标和 PBR 工作流、Unity 导出、
建模原点及点位书签。0.2.5 的具体变化和此前验证见
[升级记录](releases/RRHelper_0.2.5_upgrade_20260908.md)。

## 本机项目配置

RR Helper 当前仍使用 Randy 的本机项目路径，例如
`D:\Unity Projects\RandomRealm2` 与
`D:\Blender\Projects\Character\Animation\Animation.blend`。
复制仓库到其他机器后，应先配置实际项目路径再使用导出或动画同步。
相关常量主要位于 `rr_builder_constants.py` 和 `__init__.py`。

迁入保留现用版本的行为与内部模块名。源码目录不包含安装缓存、历史备份或私人场景文件。

## 验证范围

仓库的共同加载检查在临时 Blender 场景中启用、卸载并重新启用两个插件。
此前 RR Helper 的 Builder6 导出对比依赖本机素材，其结果保存在升级记录中；
本次目录整理无需再次触发 Unity 导出。
