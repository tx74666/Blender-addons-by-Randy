# 插件测试

这里保留 Character Designer 自建夹具测试，以及两个插件共同加载的检查。
测试目录保持平面结构，便于现有测试互相导入夹具。

使用 Blender 5.2，在本仓库根目录的 PowerShell 中运行：

```powershell
$blenderExe = 'D:\Blender5.2\blender.exe'
$selectedTests = @(
    'test_addons_together_blender.py'
    'test_ui_pages_blender.py'
    'test_limb_ik_blender.py'
    'test_limb_ik_auto_align_default_blender.py'
    'test_limb_ik_fk_blender.py'
    'test_limb_ik_fk_ui_blender.py'
    'test_foot_controls_blender.py'
    'test_foot_controls_ui_blender.py'
    'test_torso_controls_blender.py'
    'test_torso_controls_ui_blender.py'
    'test_eye_controls_blender.py'
    'test_eye_controls_ui_blender.py'
    'test_eye_display_spacing_blender.py'
    'test_spine_ik_fk_blender.py'
    'test_spine_ik_fk_ui_blender.py'
    'test_root_control_blender.py'
    'test_limb_fk_visuals_blender.py'
    'test_body_controls_ui_blender.py'
    'test_head_neck_visuals_blender.py'
    'test_bone_collections_blender.py'
    'test_accessory_bone_collections_blender.py'
    'test_animation_import_blender.py'
    'test_animation_retarget_blender.py'
    'test_forearm_twist_addon_enable_blender.py'
    'test_skirt_ui_blender.py'
    'test_skirt_physics_blender.py'
    'test_hair_bones_mirror_controls_blender.py'
    'test_hair_bones_groups_blender.py'
    'test_hair_bones_ui_blender.py'
    'test_hair_bones_variants_blender.py'
    'test_hair_bones_legacy_compat_blender.py'
    'test_hair_bones_binding_blender.py'
    'test_hair_bones_existing_mirror_binding_blender.py'
    'test_hair_bones_binding_guards_blender.py'
    'test_hair_bones_cleanup_dependencies_blender.py'
)
foreach ($testName in $selectedTests) {
    & $blenderExe --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python (Join-Path 'tests' $testName)
    if ($LASTEXITCODE -ne 0) { throw "Failed: $testName" }
}
```

每项在独立的临时 Blender 场景中运行；不要在工作中的实时场景里执行测试脚本。
眼睛显示间距测试覆盖多帧动画不变、整体前移、重复设置、保存重开和原显示位置恢复。
Head/Neck 显示测试覆盖身体权重定比例、长发排除、骨骼 Roll 和物体变换、可编辑线框、原形状／颜色／显示锚点恢复、保存重开、原生姿态动画保留，以及依赖检查和失败回滚。
Spine IK/FK 测试覆盖三／四段脊柱、混合手脚模式与眼睛控制器共存、带姿势匹配、真实端点求解、曲率／无拉伸限制、整体清零、保存重开、动画／依赖保护和失败回滚。公开按钮测试验证模式、控制器选择、集合和移除顺序。
Eye Controls 测试覆盖双眼与单眼视线、Head 跟随、非平行原眼骨、带姿势接入、清零、权重与原骨保留、移除、保存重开、失败回滚及动画／外部依赖保护。公开按钮测试同时验证配色、骨骼集合和基础 Rig 移除保护。
Foot Controls 测试覆盖左右脚、Stable／Direct、脚跟／前脚掌／脚尖支点、脚趾独立运动、任意姿势添加与移除、失败回滚和依赖保护；IK／FK、集合与公开按钮测试同时覆盖新增控制器。
Animation 测试验证独立 BVH 预览、身体动作转移、坐标/缩放及原 Action 的保存与恢复。
Limb IK 测试验证新建默认 Auto Align 开启，以及保存重开和 Rebuild 保留开启或手动关闭状态。
集合测试覆盖原生/控制骨的逐肢体切换、保存重开、Build/Rebuild/Remove、失败恢复，
以及 Hair/Skirt 单组、旧裙子集合迁移和烘焙副本的可见性。
`python tests/test_animation_runtime.py` 单独验证外部进程协议与输出路径检查，无需 GPU。
真实 Kimodo 生成测试和 X 动作副本留在本机运行环境及 X 的 `outputs/animation` 中。
测试覆盖共同启用/卸载、Forearm 注册、裙子创建/回滚/碰撞体/烘焙、头发镜像的左右独立控制和中央单链。
需要保存重开的测试会使用临时文件。

头发兼容测试使用保留的 `dist/character_designer-0.40.2.zip` 创建实际旧 Grouped
文件，再用当前版本打开，验证旧权重、动画和引导线保留，以及重新生成独立骨链。
请保留这个冻结的历史安装包。新建共享链的接口会在创建数据之前拒绝执行。

0.41 原位绑定测试覆盖不复制网格/Armature、Head 帽部和根环权重、保存重开后
恢复绑定前状态、既有 Mirror/Armature 顺序、异常回滚、旧副本删除与外部引用保护。

其他已带入的测试覆盖基础角色工具、UI 分页、权重、Spline IK、Limb IK、裙子拓扑和头发捕获。
`test_skirt_rig_service.py` 是不依赖 Blender 的 Python 测试，可直接运行。
`test_skirt_topology_blender.py` 有可选的真实模型检查，找不到 X/Elaina 素材时会跳过该部分，
其自建网格测试仍可执行。

私人角色模型、真实场景验收脚本及实时 GUI 测试保留在 Blender 项目中。
RR Helper 此前使用 Builder6 的完整导出对比属于历史验证，见
[升级记录](../docs/releases/RRHelper_0.2.5_upgrade_20260908.md)。
# Quick binding and saved character references

`test_quick_bind_blender.py` verifies native nearest-face interpolation and bone
heat, normalized deform-only weights, mirrored side assignment and deformation,
unchanged mesh/shape-key/helper data, modifier reuse, and rollback on failure.
It also checks first-bind backup persistence, repeated recalculation, restoration
after saving/reopening, later unrelated edits, and refusal after incompatible topology changes.
`test_character_setup_accessories_blender.py` verifies saved references across
rename, reload and add-on registration, multiple Hair meshes, explicit overrides,
and Hair/Skirt binding through the saved main rig.

`test_character_bone_mapping_blender.py` verifies per-armature Hips/Head mappings,
unique detection, ambiguous/invalid choices, selected-bone capture, generated-rig
exclusion, saved reload, and Hair use of the shared Head.
`test_skirt_attachment_blender.py` verifies live attachment status, keeping the
current placement and animation channels during updates, persistent restoration,
failure rollback, parent-cycle validation, and physics guards.
`test_skirt_ui_blender.py` also exercises shared Hips and explicit Update/Restore.
`test_ui_pages_blender.py` verifies Rig Body/Hair/Skirt routing and the legacy
Clothing shortcut without dirtying the blend file.
