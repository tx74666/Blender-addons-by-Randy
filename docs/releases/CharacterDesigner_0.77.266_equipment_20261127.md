# Character Designer 0.77.266 · Equipment 与上半身动画合成

在 Character Designer 侧栏选择 **Equipment → Weapons**。导入 FBX、OBJ、glTF 或 GLB，选择导入的 Weapon Profile，再点 **Attach** 挂到右手。**Edit Profile** 保存名称、右手挂载位移和旋转，以及双手武器的左握点位移和旋转。双手武器可用 **Enable Left Grip** 开启跟随，解除挂载后可以复用原配置。配置随 .blend 保存，模型以 Blender Collection Asset 的形式保存。

播放基础走路、跑步或待机 Action 后，在 **Animation → Animations → Upper-Body Composition** 点 **Add Upper-Body Action**。选择同一骨架的上半身动作，并设置开始帧和淡入淡出帧数。可多次添加抬起、放下、持械待机、瞄准、发射、装填或道具攻击等已制作的 Action。**Hold Last Pose** 让覆盖保持末帧，**Loop Base Beyond Its End** 在覆盖延伸过基础动作时继续循环基础动作。可以为覆盖指定左手握持权重。对已有 Character Designer 手臂控制的骨架，覆盖可渐变切入左手 IK。**Return to Base Action** 恢复原 Action 和槽位。

在 **Animation → Animations → Export Animations** 选择 **Export Composition as FBX…**。FBX 包含基础动作、上半身覆盖及已求解的手部 IK。武器网格随 Blender 资产单独管理。

实际 X 的 CoshaRig 在隔离 Blender 中播放原有 Walk Right FK。覆盖生效期间核对 11 帧，9 根下半身骨骼（含 Hips 和双腿）的采样矩阵最大差为 0。单独的原生测试还覆盖三种文件导入、写入失败回滚、保存及重开、连续叠加和循环，GUI 检查覆盖挂载和合成的 Undo/Redo。Unity 6000.5.9f1 私有项目导入并采样了最终 FBX。当前打开的正式 X 窗口保留原运行会话，等待下次正常加载插件。

[交付回执](<D:/Blender/Projects/Character/X/validation/weapon_aware_20261127/delivery266.json>) · [完整投影](<D:/Blender/Projects/Character/X/validation/weapon_aware_20261127/stage266_04/projection.json>) · [CoshaRig 核对](<D:/Blender/Projects/Character/X/validation/weapon_aware_20261127/formal266_full.json>) · [Unity 日志](<D:/Blender/Projects/Character/X/validation/weapon_aware_20261127/unity266_final.log>)
