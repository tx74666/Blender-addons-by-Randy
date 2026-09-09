# Blender add-ons by Randy

RR Helper 和 Character Designer 的源码、安装包及插件测试集中维护在这里。

| 插件 | 当前版本 | 源码 | Blender 安装包 |
| --- | --- | --- | --- |
| RR Helper | 0.2.5 | [random_realm_builder_exporter](addons/random_realm_builder_exporter) | [rr_helper-0.2.5.zip](dist/rr_helper-0.2.5.zip) |
| Character Designer | 0.53.2 | [character_designer](addons/character_designer) | [character_designer-0.53.2.zip](dist/character_designer-0.53.2.zip) |

Character Designer 0.53.2 修正左右 Foot Roll 与右脚 Bank 的反向旋转，让脚部跟随控制器的旋转方向，并保留脚跟、前脚掌与脚尖支点。旧设置使用 **Fix Roll Direction** 显式更新，保持当前姿势、权重与显示位置；已有相关动画或外部依赖时会阻止更新，避免改坏动作。

Character Designer 0.53.1 修正胸部圆环的包覆方向：中段向胸部前方凸出，上下边缘朝身体收回。保持原尺寸、位置、骨骼支点与恢复资料；只调整圆环的前后弧度。

两个插件独立安装，也可以同时启用。RR Helper 的内部模块名保留为
`random_realm_builder_exporter`，便于更新原来的安装。

Character Designer 0.53.0 在 **Rig → Body → Body Controls** 加入 **Add Breasts / Hips**：左右胸部使用略带弧度的圆环，Hips 使用骨盆椭圆环。沿用共用身体与附近登记衣物匹配比例；Hips 读取 Character Setup，胸部骨骼优先按名称辨识，无法确定时提供骨骼选择。保留原骨骼名称、旋转中心、权重、约束、动画与集合；恢复按钮取回先前显示和颜色，控制器网格可编辑，恢复资料随 blend 保存。

Character Designer 0.52.3 将新建全身 Root 的显示高度对齐到鞋底控制轮廓，沿用脚部显示的静止坐标与偏移，不再固定在骨架原点。Direct 与 Enhanced 生成共用定位规则；只移动显示，保留全身旋转支点与动画。已有显示和恢复快照保留。

Character Designer 0.52.2 将新建头部控制器的侧面顶部、底部转角改为圆弧，保留正面切角轮廓、脸前开口和小前向标记。只调整显示形状，保留原旋转支点、姿势与恢复功能；已有手工编辑的显示不会自动被覆盖。

Character Designer 0.52.1 将新建眼睛控制器的眼罩和圆圈显示整体前移，便于从脸前选取。**Rig → Body → Eye Controls → Display Spacing** 可调整额外间距，设为 0 恢复原显示位置；数据随 blend 保存。只移动可点击的轮廓，保留视线、动画和原追踪目标，变换轴仍位于目标骨骼。

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
