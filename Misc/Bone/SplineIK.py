import bpy

# Variables#
FK_bone_label = "FK"
RigName = "Dress"
Collection = "IK_Spline"
custom_shape = bpy.data.objects.get("SPIK_CTRL_SHAPE")
theList = []

# Collection Manage
# bpy.ops.object.mode_set(mode="EDIT")
# bpy.ops.armature.select_all(action='SELECT')
theList = [bone.name for bone in bpy.context.selected_editable_bones]
Collections = bpy.context.object.data.collections
for col_name in ["FK_Dress", "SPIK", "SPIK_CTRL"]:
    if col_name not in Collections:
        Collections.new(name=col_name)
Collections.active = Collections["FK_Dress"]
bpy.ops.armature.collection_assign()

# SPIK Bone Setup
bpy.ops.armature.duplicate()
bpy.ops.armature.collection_unassign()
bone = bpy.context.selected_editable_bones
for b in bone:
    b.name = b.basename.replace(FK_bone_label, "SPIK") + ".001"
bpy.ops.object.mode_set(mode="POSE")
Collections.active = Collections["SPIK"]
bpy.ops.armature.collection_assign()
Collections["SPIK"].is_solo = True

# Ctrl Bone Standby
selection_names = bpy.context.selected_pose_bones
startingPt = selection_names[0].head
endingPt = selection_names[-1].tail
midPt = (startingPt + endingPt) / 2
diff = midPt - startingPt
vertices = [startingPt, midPt, endingPt]
edges = [(0, 1), (1, 2)]
for i in selection_names:
    for c in i.constraints:
        i.constraints.remove(c)

# Curve Generate
objectName = selection_names[0].basename.replace("SPIK", "Spline")
new_mesh = bpy.data.meshes.new(objectName)
new_mesh.from_pydata(vertices, edges, [])
new_object = bpy.data.objects.new(objectName, new_mesh)
bpy.data.collections[Collection].objects.link(new_object)
bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.object.select_all(action="DESELECT")
bpy.data.objects[objectName].select_set(state=True)
bpy.context.view_layer.objects.active = bpy.data.objects[objectName]
bpy.ops.object.convert(target="CURVE")
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.curve.spline_type_set(type="NURBS")
bpy.context.object.data.splines[0].order_u = 3
bpy.context.object.data.splines[0].use_endpoint_u = True
bpy.ops.object.mode_set(mode="OBJECT")

# Cons:Spline IK
bpy.ops.object.select_all(action="DESELECT")
bpy.data.objects[RigName].select_set(state=True)
bpy.context.view_layer.objects.active = bpy.data.objects[RigName]
bpy.ops.object.mode_set(mode="POSE")
lastBone = selection_names[-1]
bpy.data.objects[RigName].data.bones.active = (
    bpy.data.objects[RigName].pose.bones[lastBone.name].bone
)
bpy.ops.pose.constraint_add(type="SPLINE_IK")
bpy.context.object.pose.bones[lastBone.name].constraints["Spline IK"].chain_count = len(
    selection_names
)
bpy.context.object.pose.bones[lastBone.name].constraints["Spline IK"].target = (
    bpy.data.objects[objectName]
)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.armature.select_all(action="DESELECT")
Collections["SPIK"].is_solo = False

# Ctrl Bone Setup
Collections["SPIK_CTRL"].is_solo = True
newBoneName = objectName.replace("Spline", "SPIK_CTRL") + ".001"
theBone = bpy.data.objects[RigName].data.edit_bones.new(newBoneName)
theBone.head = startingPt
theBone.tail = midPt
bpy.ops.object.mode_set(mode="OBJECT")
bpy.data.objects[RigName].data.bones.active = (
    bpy.data.objects[RigName].pose.bones[newBoneName].bone
)
bpy.ops.object.mode_set(mode="EDIT")
bpy.context.active_bone.parent = bpy.data.objects[RigName].data.edit_bones[
    bpy.context.active_bone.basename.replace("SPIK_CTRL", "Parent")
]
bpy.context.active_bone.length = bpy.context.active_bone.length / 4
bpy.ops.armature.duplicate_move(TRANSFORM_OT_translate={"value": diff})
bpy.ops.armature.duplicate_move(TRANSFORM_OT_translate={"value": diff})
bpy.ops.armature.select_all(action="SELECT")
Collections.active = Collections["SPIK_CTRL"]
bpy.ops.armature.collection_assign()
bpy.ops.object.mode_set(mode="POSE")
for bone in bpy.context.selected_pose_bones:
    bone.custom_shape = custom_shape
    bone.color.palette = "THEME09"
bpy.ops.object.mode_set(mode="EDIT")
for bone in bpy.context.selected_editable_bones:
    bone.color.palette = "THEME09"
bpy.ops.object.mode_set(mode="POSE")


bpy.ops.pose.select_all(action="DESELECT")
Collections["SPIK_CTRL"].is_solo = False

# Cons:Copy Transforms
Collections["FK_Dress"].is_solo = True
for n in theList:
    bpy.data.objects[RigName].data.bones.active = (
        bpy.data.objects[RigName].pose.bones[n].bone
    )
    bpy.ops.pose.constraint_add(type="COPY_TRANSFORMS")
    Constraint_name = "CTRL:Copy Transforms-SPIK"
    bpy.context.object.pose.bones[n].constraints[
        "Copy Transforms"
    ].name = Constraint_name
    theCons = bpy.context.object.pose.bones[n].constraints[Constraint_name]
    theCons.target = bpy.data.objects[RigName]
    theCons.subtarget = n.replace("FK", "SPIK")
    theCons.mix_mode = "BEFORE"
    theCons.target_space = "LOCAL"
    theCons.owner_space = "LOCAL"
Collections["FK_Dress"].is_solo = False
bpy.ops.object.mode_set(mode="OBJECT")

# Mod:Hooks
bpy.ops.object.select_all(action="DESELECT")
bpy.data.objects[objectName].select_set(True)
bpy.context.view_layer.objects.active = bpy.data.objects[objectName]
for x in range(3):
    bpy.ops.object.modifier_add(type="HOOK")
    hookName = f"Hook.00{x+1}"
    bpy.context.object.modifiers["Hook"].name = hookName
    if x == 0:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.curve.select_all(action="DESELECT")
        bpy.ops.curve.de_select_first()
    if x == 1:
        bpy.ops.curve.select_next()
        bpy.ops.curve.de_select_first()
    if x == 2:
        bpy.ops.curve.select_all(action="DESELECT")
        bpy.ops.curve.de_select_last()
    bpy.ops.object.hook_assign(modifier=hookName)
    bpy.context.object.modifiers[hookName].object = bpy.data.objects[RigName]
    boneName = (
        bpy.data.objects[objectName].name.replace("Spline", "SPIK_CTRL")
        + ".00"
        + str(x + 1)
    )
    bpy.context.object.modifiers[hookName].subtarget = boneName
bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.object.select_all(action="DESELECT")
bpy.data.objects[RigName].select_set(True)
bpy.context.view_layer.objects.active = bpy.data.objects[RigName]
bpy.ops.object.mode_set(mode="POSE")
