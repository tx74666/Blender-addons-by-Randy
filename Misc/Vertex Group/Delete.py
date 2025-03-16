import bpy

obj = bpy.context.active_object

if obj and obj.type == "MESH":
    groups_to_remove = []

    for i, vertex_group in enumerate(obj.vertex_groups):
        max_weight = 0

        for vertex in obj.data.vertices:
            try:
                weight = vertex_group.weight(vertex.index)
                if weight > max_weight:
                    max_weight = weight
            except RuntimeError:
                continue

        print(f"顶点组 '{vertex_group.name}' 的最大权重值是 {max_weight}")

        if max_weight < 0.01:
            groups_to_remove.append(i)

    for i in reversed(groups_to_remove):
        group_name = obj.vertex_groups[i].name
        obj.vertex_groups.remove(obj.vertex_groups[i])
        print(f"已删除顶点组 '{group_name}'，因为其最大权重小于 0.01")
