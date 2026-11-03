# Character Designer 0.77.148：Rest Pose资产与FK直接骨形

完成记录，2026-11-02T12:55:58.948244Z。源码/磁盘、实际component认证和owned显示提交、独立post-event、调用方UI恢复已按各自范围闭合；下述事实固定于此时点，不推断之后其它对话的更新。最终stage148_04完整253files/221py，相对approved complete146为6changed/0new/247exact。显式增量为README、__init__版本、body_region._finish_edit_region、control_pose_assets、control_pose_blend及limb_fk_visuals.sync_native_display；package __init__只有版本literal改变，排除Slim147、controller-animation翻译与其它暂停ownership候选。stage148_04相对03只改README，所有Python实际字节相同。

最终投影SHA `2d85f993ef02de22cec05b4da46b8ac02a33fefc224925bfe11c3cf521034f9b`。官方ZIP SHA `bf4bf16ce560db8ad1a6a00296946891e72879c10776683d47500ded13bfc01f`，253条目/CRC全部通过、253payload/221AST精确且无额外或重复条目；双5.2/X各253files，missing/different/extra material均0，正常pycache排除且未删除。原部署12files已备份。磁盘交付不证明loaded runtime已采用；actual04的component认证/显示提交、独立post-event和最终UI/closure已完成；前两次预写拒绝及第三次foreground停止保留。

Rest Pose 会建立可编辑的 Natural Pose Test，保留原动画。此前双击 Pose Library 资产会在入口直接取消；现在已认证的测试会话可通过既有 Blend 事务应用、镜像或混合原骨 Pose。Controls 使用现有控制器匹配；Natural＋Original 直接写原骨输入，不改写 Original 的 SESSION/REFS。实际骨骼 Rest 不变。

使用方法：

- 双击 Pose 资产应用；Shift＋双击镜像到另一侧。
- 选中资产目标中的一部分骨骼时，只应用该部分；镜像沿用现有左右选区转换。无相关目标选区时，沿用完整资产的 fallback。
- Blend Pose 可调节强度，确认后保留，取消则恢复开始状态。
- 选择 Pose 返回之前保留的动画。Auto Key 开启时，新测试载体同步 TOKEN/NATURAL；旧载体及恢复记录保留。关闭时不另建写键载体。

生成控制骨通道资产在 Natural 中仍明确拒绝。此次支持原骨及通过真实捕获生成的受管理 Pose；没有将任意控制器资产重解释为原骨资产，也没有实现整段动画的双向曲线翻译。

FK手臂直接骨形属于另一个明确授权的显示改动，只处理已认证owned ARM，不改腿、其它作者骨/资源或未选侧。区域重建完成后，仅同步所选owned shape/native marker及模式派生hide/membership/recovery叶值到实际重建模式。沿用既有重建默认IK，不承诺输入FK模式或对应FK派生样式位元相同；其它作者域、非所选侧和原生hide/lock依各自完整witness精确保留。

FK组件pure10、authoring native7、regional5、genuine CurrentRig display1、私有code installer14、read-only observer2按独立原范围通过。regional真实sync写后故障经外层事务恢复；CurrentRig只证明既有资源的display-only transition，不授予旧legacy资源全面升级。旧integration mode/display mismatch、旧FK-style合同不兼容、schema/JSON/closure-reader/observer假设失败保持原值，后续通过仅按新独立receipt计数。FK NativeTag补充已实际7checks通过，另重核late12assignment rollback/native marker与menu order；原14core只按不变算法复用。不把该metadata reader proof称为产品延迟或新的功能性能测试。

Graph Editor的I针对已有可编辑曲线插键，选骨不等于该处有可编辑曲线；用户已确认Timeline能K。只读Graph01观察时FCURVES insert_poll为true，绑定正常，所选CTRL_hand_IK.L对应10条visible/editable/selected曲线且curve/group未锁；fresh Graph02及Graph04观察时selected为空、OnlySelected为true，因此visible/editable/selected曲线均0、insert_poll为false。两次frame14/Action-slot均各自前后精确，0插键/锁/pose/frame/selection写入，标准I绑定存在。不能把早先10曲线/polltrue写成当前状态，也没有复现之前16指骨的物理I事件或证明其具体原因；I/Insert/keymap/Keying Set保持未改。腕隐藏由用户确认是尚未切IK，不列新bug或额外修复。

验证与限制：

- Host73 通过：新增路由/权限/双令牌模型16、既有 Blend UI31、Pose Test19、区域 Rest7。旧 Apply mock 的9 errors/1 failure与原源码对照一致；旧 Rest fake-bpy 的10 errors保留，未改称通过。
- 原生31个不同范围：core22、authority7、full-arm2。包括真实 Apply/Flip、局部选区、固定起点混合/Cancel、已有帧 Auto Key、新载体双令牌、实际 raw/key 写入后的故障回滚和 Return。Controls 与真正进入的 Original 均有覆盖；Original `_match_original` 调用为0。
- 独立小型 witness 核对 Source/资产/完整 slot 内容、原生 Rest、几何/Keys/weights/history、旧载体以及 Original opaque SESSION/REFS。原生矩阵容差4e-4，最大观察误差约2.132e-4；不是提高容差后的补通过。
- 31来自已索引的真实通过范围：整体失败的 guards01 中6项加独立补例1项；arms01 中Controls1项加 arms02 的Original1项。smoke01/02/03、guards01、arms01原报告保持失败及各自原因，smoke04六项是子集、不再加总。
- 测试使用与私有 rig Rest 一致的真实捕获资产和本地 ID dispatcher/UI execute；不是 artist 的 Fist/Relax/Arm Flat 文件，也不是实际双击、菜单或 WM 事件。未测保存重开、Source 库加载、artist 延迟或播放 FPS。
- 旧私有组件安装器9checks/13endpoint故障及cold补例保留。新增NativeTag reader14checks通过，实际4个pre/first/all/verified fault恢复；旧13只因mutation/rollback算法相同而复用，不把两套数合成新feature范围。认证原生Menu.append产生的精确_owner属性及字典身份，同时拒foreign owner、extra attr、annotation、equal-value属性字典替换。cold Blend absence和ALREADY只读0apply也有原生例。它仍只替换10既有函数、不写metadata/register/import、不扫Actionpoints/geometry arrays。NativeTag证明安装器兼容和权限，不是产品延迟优化或新的功能性能实测。

实际actor01–04及独立post-event：

`artist_joint148_live01.json`保持failed；Natural子receipt为PREWRITE_REFUSAL，原因是旧认证器不认识原生Menu.append加入的合法_menu._owner属性。拒绝发生于apply前，code/metadata/import/Actionpoint/geometry/pose操作均0，FK步骤未进入，右Assets已恢复。不得将它改成成功或伪称本次安装了10函数。

Actual02同样保持PREWRITE_REFUSAL：preflight旧144保护reader遍历深rig.data metadata时触及bound，未进入apply/0code/0metadata/0point写读，FK未进入，右Assets已恢复；不是产品Pose执行失败。

独立只读probe已看见package metadata148、两Apply目标函数是candidate、Blend coherent absent，且原生_menu tag及_transaction.__wrapped__身份有真实记录；bounded state和右Assets还原精确。刷新时间与发起者未知，此观察不能推断完整runtime刷新或跨刷新identity。后续仅更换current-context adoption witness为scoped_natural148_04_final/entry05，产品source/package不改。Nativeopaque6实际通过：真实18层IDPropertyGroup复现旧12层拒绝；新reader不调用旧全metadata扫描、当前raw/token及opaque长度变化仍fresh-refuse；nativeTag/cold-Blend只读0apply/0writes通过。source/code/default/attrs/module/RNA/keymap守卫及apply/rollback算法保持字节/AST不变。

新的witness只保证current scene/view-layer/editor/frame/dirty、active rig raw/locks/selection/collection flags、当前Action-slot/NLA/worklist owner与nativeID roster、浅层scalar/token/ID及opaque type/count/length。深历史array/checkpoint subtree不展开；同形的深内容改变刻意不属equality范围，reader不据此获得恢复、回放或覆盖权限。不能继续宣称完整Scene作者metadata相等。

Actual03在Natural ALREADY和FK code只读ALREADY均通过后、D display尚未进入时，因foreground变化而停止，0writes；原failed保留。joint04唯一窄改是D前仍核同一app/native context而不再次要求foreground，初始foreground及Natural/FK代码guards保持原样，peer无blocker。

Actual04实际MAIN PASS，结束于2026-11-02T12:05:10.260630Z：Natural ALREADY_CANDIDATE_COMPONENTS，0apply/0code/helper/metadata/import；70已加载函数认证、Blend coherently absent保持lazy。FK code already_target、planned/actual assignments0，known endpoints/source身份通过。D01实际committed=true/complete=true：仅2个owned ARM shape pointers及native marker更新；0newwidget/build/remove/mode/pose/Action/save，原registry/resources IDs和bounded inputs精确，dirtytrue保持，右下Assets恢复。

metadata148是观察且保持，不称本轮完整runtime刷新或已写10/9函数；刷新时间/发起者未知。实际D显示完成与代码ALREADY是不同结果。source/disk旧验收保留当时尚未核对artist/Graph的历史字段，不回写它来替代后续真实观察。

独立下一事件post01于2026-11-02T12:51:43.186127Z通过，0代码或metadata写入、0产品导入、0Actionpoints/geometry arrays读取、0姿势/插键/帧修改；current raw pose/context仍按有限范围读取。Natural重新认证70已加载函数、planned0、Blend保持cold absent。该次current-context before/after hash各自相等；不宣称它与main在不同editor/user事件前后的hash相等。独立FK observer确认display complete：L仍IK，owned rings保留、三个原骨在Body外；R为FK，上/前臂及原已native手骨均无控制形状、三原骨在Body中。六ARM visibility/selection locks按原范围保留。package/metadata身份只在明确main/post记录范围连续，不跨未知刷新推断。

Root在post后独立Sky视觉观察确认右Properties及Assets恢复、Relax Pose/Arm Flat/Fist可见，POSE/frame14/0selected/dirtytrue、用户最新视角保留；left human pending Console未执行/未清。此为视觉观察，不是又一次native full-array验证，没有保存或解码截图payload。两处right Console仍保留Root已执行的过期输入buffer，今后使用Console只清这些过期Root行，左边人工pending继续保留。已交还用户键鼠，未另作artist Pose或I试验。

一次性main源码认证和显示handoff为16.235173秒，不能当Alt+R功能延迟；145已闭合native reset证据原样继承，无复测或重开FPS调查。

固定依据：

- [Natural component gate](D:/Blender/Projects/Character/X/validation/rest_pose_assets_20261102/natural_component_gate148_01.json)，SHA `7c2fe363eb432d62a87f939de378b489ab99dfb11bb9f30958786d5c590ec9ab`。只批准两文件组件，不批准完整 stage148_02。
- [Native31 summary](D:/Blender/Projects/Character/X/validation/rest_pose_assets_20261102/native_summary148_02.json)，SHA `9816e171d8724e1c3965e1c909fee43bf79bc92951f66e77e8d30e47d0799721`。
- [Source/host receipt](D:/Blender/Projects/Character/X/validation/natural_pose_assets_20261102/source_host_result01.json)，SHA `86ed8fd95ba741cba591ca15521c46b09a04c48e4ee4782bdf015256ce914853`；[independent source peer](D:/Blender/Projects/Character/X/validation/natural_pose_assets_20261102/peer_source148_01.json)，SHA `063973f4291b6a07801f12c6735917185d7abfe207007cbb7ff931881bf21487`。
- `control_pose_assets.py` SHA `a382606d5189a3e2f56dda81c40107a786326134f941c75deeda110abdd447a1`；`control_pose_blend.py` SHA `d90a0b0ed8bc06768ba3fa8e78c5a51c0fdc6bb8c045eed855b993a1b491d058`。10个既有函数变更，无新 helper/public API；neutral 专属 capability 与非 Natural Apply 其余路径保留。
- 前一NativeTag安装器`scoped_natural148_03.py` SHA `e8dd78f5bc1de79553cbe9b1481142506b1f738b67a6d11c0473c1e8c5eed9ef`；前一入口`natural_entry148_04.py` SHA `8e572c6e6124969bbd1567c0d3d110115f61861e585a344122cbba1a661147c8`；[NativeTag14 receipt](D:/Blender/Projects/Character/X/validation/rest_pose_assets_20261102/narrow_update148/native_tag01/result.json) SHA `334c23c18cd513abff2ad42df7ec261ab50030d4e4f273d07de96bf45331e9cf`。旧updater02/entry03及实际拒绝不覆盖。
- [完整source/package gate](D:/Blender/Projects/Character/X/validation/rest_pose_assets_20261102/source_package_gate148_01.json)，SHA `021d55739e3cf504e97eb99aaacd9afb559d2b912102a5d98ac81e873bd55acc`；[Source/disk acceptance](D:/Blender/Projects/Character/X/validation/rest_pose_assets_20261102/acceptance_source_disk148_01.json)，SHA `92ceeb62d4db043650ab20ce0d0e787ced3fa775a11bb22de0608e54c4a1e7c9`；[archive/dual-deploy peer](D:/Blender/Projects/Character/X/validation/rest_pose_assets_20261102/peer_package148_01.json)，SHA `6e80ac968e7b71c4035f68cd888297d4843f067646aa5f975cd581e62f40b58d`。
- [FK组件index](D:/Blender/Projects/Character/X/validation/fk_native_display_20261102/component_ready148_01.json)，SHA `35560d2f500fc2e13be109a268b183ee23ae13acee98f63ae3505958c4f012f1`；其source/disk与actual字段依各自receipt，不把index当artist成功。
- Actual01 SHA `55b0db291b3ffc08fed5d74b09bafb8e1d3e166763ad8aa083032dca24ad083b`；Natural01拒绝SHA `24b007d52fcaac3e85fb8782a2c25852abd47800ab3c62dc080716d5b9e002b0`；Actual02 SHA `f7d4886c0fd57179a3fea55e75e4f503a404280a0c4f10d19c1982d6e55dbea5`；Natural02拒绝SHA `554e2f093c282e5a2b2f30bda6cde3ca2e0bb4f96c5eb6eb4038a4876c4c7c1e`；只读probe SHA `74b002f1f7ef119c0eb6d643495fee035fcb047847f6b51bd785ce9343fef127`；Graph01/02 SHA `c5f2731fb046e2948712c1cd8262282528c344f57906efbdeeedf6b09cb5ca89` / `333cd021fe320cb64433d5392c3f9e1436ce815dea919350fb1090cef19a4049`。
- [FK NativeTag ready02](D:/Blender/Projects/Character/X/validation/fk_native_display_20261102/component_ready148_native_tag02.json) SHA `34ae427e33ae98dced75e59e8a71ffe4982d0abcc08af7af3fce7c187a8a3047`；native metadata/menu-membership/observer7范围及base14复用边界以原receipt为准。
- current-context updater`scoped_natural148_04_final.py` SHA `5a85cebd178a836d9d496870d1399e9d97e2051a13f17d984bdce699c905b9bf`；entry05 SHA `d24d79970699c168c3eaa1f167e1b42954c9b31d787c34385a818f42ea22be9f`；[Nativeopaque6](D:/Blender/Projects/Character/X/validation/rest_pose_assets_20261102/narrow_update148/native_opaque01/result.json) SHA `3ef01fdcb13d470b13cb35d91cb5627c41ad77b9dcbf9a623720f94ab5931cb1`。
- Actual03 stopped-before-D SHA `d9d15fcb848a19996bf45d190f2e5a72bd46acb9516eac08c6cc9fa78ff23a55`；[Actual04 main](D:/Blender/Projects/Character/X/validation/rest_pose_assets_20261102/artist_joint148_live04.json) SHA `85e2c5b54b45a2c98233bd0d9cf0ba5793aa2cc8b0e78c9a204ba0aee3531eb0`；Natural readonly SHA `60901c62387b0478c5524e1fff0147eecd1f5cc8ecd9034eee6d1fea424497fc`；FK code readonly SHA `b73a2670327a8a5ad740e260fdacfad4298124e050ee301bc4921240a6c3f5aa`；D committed SHA `de97d7aea419fb598efd41d91a11196b09307bb8faf72fbd0b3b0decf6134266`；Graph04 SHA `a1e8d747711960879c74405fb966d9f729377a70e3c927aba1d8ed59d09fcac0`。

最终入口：[delivery_closure148.json](D:/Blender/Projects/Character/X/validation/rest_pose_assets_20261102/delivery_closure148.json)，SHA `253b46be7d03e1025656b1fe24504d6a696613575f9afea4bc13950ae83148f9`。闭合范围为SOURCE/DISK/COMPONENTS/DISPLAY/POSTEVENT/UI；不是full runtime刷新、artist资产/物理输入试验或完整作者数组验收。

[post01](D:/Blender/Projects/Character/X/validation/rest_pose_assets_20261102/artist_joint148_post01.json) SHA `4ba4ee697a5d3b8d906a0bfee28649805e426793fc8b940a8211a6dc48e0de03`；[FK post](D:/Blender/Projects/Character/X/validation/rest_pose_assets_20261102/artist_joint148_post01.fk.json) SHA `0ebc5d4171d9c2592a833a0577cddc9f5954ac91faf78a43438dd3cbaf5fda9f`；[UI observation](D:/Blender/Projects/Character/X/validation/rest_pose_assets_20261102/artist_UI_restored148_01.json) SHA `06147be72eb4cd1cea5ecb8e96bfb9a1579a12e584ae0bde23b0d74d84a2be9b`；[independent actual peer](D:/Blender/Projects/Character/X/validation/rest_pose_assets_20261102/peer_actual148_01.json) SHA `625198239b82526591ae145993930343e389d2ebc5d0db623d11dd276771805e`。Actual04的代码ALREADY与D实际显示提交分列，不能合并成代码更新；metadata148及磁盘不代表所有模块刷新。

保留此前全部版本、失败、恢复和证据范围。本次不授权 artist Pose 试验、场景或偏好保存、重启/fullreload、Unity、Git或监控；原 X 播放 FPS 分支继续经济结案。该记录固定在上述完成时点，后续更新单独留证。
