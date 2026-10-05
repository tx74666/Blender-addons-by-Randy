"""On-demand area estimates and a separate material from manually baked maps."""

import json
import math
import sys

import bpy

try:
    from .rr_bake_resolution import recommend_bake_resolution
    from . import rr_baked_material
except ImportError:
    from rr_bake_resolution import recommend_bake_resolution
    import rr_baked_material


def _main():
    return sys.modules[__package__ or "random_realm_builder_exporter"]


def selected_bake_meshes(context, *, include_active=True):
    rr = _main()
    selected = list(context.selected_objects)
    active = context.object
    if include_active and active is not None and active not in selected:
        selected.append(active)
    meshes = []
    for obj in selected:
        candidates = [obj] if obj.type == "MESH" else rr.get_asset_meshes(obj)
        for mesh in candidates:
            if mesh.type == "MESH" and mesh not in meshes:
                meshes.append(mesh)
    return meshes


def analyze_bake_selection(context, settings):
    """Sum evaluated triangle areas by material; release every temporary mesh."""
    rr = _main()
    materials = rr.pbr_framework_filtered_materials_for_context(context, settings)
    if not materials:
        raise ValueError("Select a mesh and at least one PBR material.")
    totals = {material: 0.0 for material in materials}
    missing_uv = set()
    mesh_count = 0
    depsgraph = context.evaluated_depsgraph_get()
    scale = float(context.scene.unit_settings.scale_length)
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError("Scene unit scale must be positive.")
    for obj in selected_bake_meshes(context):
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        if mesh is None:
            continue
        try:
            mesh_count += 1
            mesh.calc_loop_triangles()
            matrix = evaluated.matrix_world
            for triangle in mesh.loop_triangles:
                index = triangle.material_index
                # Object-level overrides can differ on two users of one Mesh.
                material = (evaluated.material_slots[index].material
                            if 0 <= index < len(evaluated.material_slots) else None)
                if material is not None:
                    material = material.original
                if material not in totals:
                    continue
                a, b, c = (matrix @ mesh.vertices[i].co for i in triangle.vertices)
                totals[material] += (b - a).cross(c - a).length * 0.5 * scale * scale
                if not mesh.uv_layers:
                    missing_uv.add(obj.name)
        finally:
            evaluated.to_mesh_clear()
    records = []
    for material, area in totals.items():
        if area <= 0.0:
            continue
        result = recommend_bake_resolution(
            area, settings.pbr_bake_texel_density,
            uv_utilization=settings.pbr_bake_uv_utilization,
            supported_sizes=rr.PBR_BAKE_SIZES,
        )
        records.append(dict(material=material.name, **result))
    if not records:
        raise ValueError("Selected materials have no evaluated mesh surface to bake.")
    records.sort(key=lambda row: row["required_resolution"], reverse=True)
    return dict(records=records, mesh_count=mesh_count, missing_uv=sorted(missing_uv),
                recommended_resolution=max(row["recommended_resolution"] for row in records))


def manual_baked_inputs(context, settings):
    rr = _main()
    materials = rr.pbr_framework_filtered_materials_for_context(context, settings)
    roles = rr.pbr_framework_selected_roles_in_order(settings)
    if not materials or not roles:
        raise ValueError("Choose source materials and texture maps first.")
    if not {"BaseColor", "Roughness", "Metallic"}.issubset(role["key"] for role in roles):
        raise ValueError("Enable Base Color, Roughness and Metallic for a complete opaque PBR material; Normal is optional.")
    entries = []
    for material in materials:
        images = {}
        for role in roles:
            node = rr.find_pbr_framework_target_image_node(material, role)
            image = getattr(node, "image", None)
            if image is None:
                raise ValueError(f"{material.name}: missing {role['label']}; bake and Save Images first.")
            images[role["key"]] = image
        entries.append((material, rr_baked_material.validate_baked_images(images)))
    return entries


def baked_preview_signature(context, entries):
    return (
        tuple(sorted(obj.as_pointer() for obj in context.selected_objects)),
        context.object.as_pointer() if context.object is not None else None,
        tuple(obj.as_pointer() for obj in selected_bake_meshes(context)),
        tuple((source.as_pointer(), tuple(
            (role, image.as_pointer(), tuple(image.size), image.filepath_raw, image.is_dirty)
            for role, image in sorted(images.items())
        )) for source, images in entries),
    )


def activate_temporary_pbr_targets(material_images, role):
    """Use fresh unconnected bake nodes; preserve every source node binding."""
    restores = []
    try:
        for material, images in material_images.items():
            tree = material.node_tree
            states = [(node, node.select) for node in tree.nodes]
            active = tree.nodes.active
            node = tree.nodes.new("ShaderNodeTexImage")
            def restore(tree=tree, node=node, states=states, active=active):
                if node.name in tree.nodes:
                    tree.nodes.remove(node)
                for old, selected in states:
                    old.select = selected
                tree.nodes.active = active
            restores.append(restore)
            node.name = "RR Temporary Bake Target"
            node.image = images[role["key"]][0]
            for old in tree.nodes:
                old.select = old == node
            tree.nodes.active = node
        return restores
    except Exception:
        _main().restore_actions(restores)
        raise


def _prepare_direct_emit(material, socket_name):
    rr = _main()
    tree = material.node_tree
    output = rr.active_material_output_node(material)
    surface = output.inputs.get("Surface") if output else None
    if surface is None or len(surface.links) != 1:
        raise ValueError(f"{material.name}: expected one active Principled output.")
    shader = surface.links[0].from_node
    if shader.bl_idname != "ShaderNodeBsdfPrincipled":
        raise ValueError(f"{material.name}: mixed shaders require manual Bake.")
    links = [(link.from_socket, link.to_socket) for link in surface.links]
    emission = None
    def restore():
        for link in list(surface.links):
            tree.links.remove(link)
        for source, target in links:
            tree.links.new(source, target)
        if emission is not None and emission.name in tree.nodes:
            tree.nodes.remove(emission)
    try:
        emission = tree.nodes.new("ShaderNodeEmission")
        emission.name = "RR Temporary Bake Emit"
        emission.inputs["Strength"].default_value = 1.0
        rr.connect_socket_to_emission_color(
            tree, rr.shader_role_input_socket(shader, socket_name),
            emission.inputs["Color"], socket_name,
        )
        rr.link_replace(tree, emission.outputs["Emission"], surface)
        return restore
    except Exception:
        restore()
        raise


def prepare_direct_pbr_emit(materials, socket_name):
    restores = []
    try:
        for material in materials:
            restores.append(_prepare_direct_emit(material, socket_name))
        return restores
    except Exception:
        _main().restore_actions(restores)
        raise


class RR_OT_recommend_pbr_bake_size(bpy.types.Operator):
    bl_idname = "rr_builder.recommend_pbr_bake_size"
    bl_label = "Recommend Bake Size"
    bl_description = "Estimate size per selected material from evaluated area, scene units and target pixels per meter"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        try:
            self._analysis = analyze_bake_selection(context, context.scene.rr_builder_export_settings)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=490)

    def draw(self, context):
        layout = self.layout
        data = self._analysis
        layout.label(text=f"Recommend: {data['recommended_resolution']} px per map")
        layout.label(text="Evaluated world area; separate map set per material")
        layout.label(text="UV coverage is an estimate; overlaps / detail are not verified.")
        for row in data["records"][:8]:
            box = layout.box()
            box.label(text=row["material"])
            box.label(text=f"{row['area_m2']:.2f} m² → {row['recommended_resolution']} px")
            box.label(text=f"Required {row['required_resolution']} px; achieves {row['achieved_texels_per_meter']:.0f} px/m")
            if row["capped"]:
                box.label(text="4K limit: split this surface or lower density.", icon="ERROR")
        if len(data["records"]) > 8:
            layout.label(text=f"And {len(data['records']) - 8} smaller material(s)")
        if data["missing_uv"]:
            layout.label(text="Missing UV maps: unwrap before baking.", icon="ERROR")
        layout.label(text="Applies Size only. Existing targets stay until Create Targets.")

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        try:
            data = analyze_bake_selection(context, settings)
            if hasattr(self, "_analysis") and self._analysis != data:
                raise ValueError("Surface or material selection changed. Open Recommend Size again.")
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        settings.pbr_bake_resolution = data["recommended_resolution"]
        settings.pbr_bake_size_analysis = json.dumps(data)
        settings.pbr_bake_size_summary = f"Last estimate: {data['recommended_resolution']} px / {len(data['records'])} material(s)"
        if any(row["capped"] for row in data["records"]):
            settings.pbr_bake_size_summary += "; density exceeds 4K"
        self.report({"INFO"}, settings.pbr_bake_size_summary)
        return {"FINISHED"}


class RR_OT_create_baked_pbr_material(bpy.types.Operator):
    bl_idname = "rr_builder.create_baked_pbr_material"
    bl_label = "Create Baked Material"
    bl_description = "Create separate source_baked opaque PBR materials from saved manual maps; keep the source nodes"
    bl_options = {"REGISTER", "UNDO"}

    use_on_selected: bpy.props.BoolProperty(
        name="Use on selected meshes", default=False,
        description="Assign only selected mesh objects using object-level slots; keep all other source users",
    )

    def invoke(self, context, event):
        try:
            entries = manual_baked_inputs(context, context.scene.rr_builder_export_settings)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self._preview_names = [source.name for source, _images in entries]
        self._preview_signature = baked_preview_signature(context, entries)
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        layout = self.layout
        for name in self._preview_names[:8]:
            layout.label(text=f"{name} → {name}_baked")
        layout.label(text="Original source nodes are preserved.")
        layout.label(text="Opaque PBR only; glass / emission use separate materials.")
        layout.label(text="Saved maps need a visual preview; this does not run Bake.")
        layout.label(text="Use Save Images after your last Bake before confirming.")
        layout.prop(self, "use_on_selected")

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        created = []
        assignments = []
        try:
            entries = manual_baked_inputs(context, settings)
            if (hasattr(self, "_preview_signature")
                    and self._preview_signature != baked_preview_signature(context, entries)):
                raise ValueError("Selection or baked maps changed. Open the preview again.")
            meshes = selected_bake_meshes(context, include_active=not self.use_on_selected)
            if self.use_on_selected and not meshes:
                raise ValueError("Select mesh assets before assigning baked materials.")
            if self.use_on_selected and any(not obj.is_editable for obj in meshes):
                raise ValueError("Selected mesh objects must be editable to assign baked materials.")
            materials = {m for m, _ in entries}
            uv_names = _main().bake_uv_map_names_by_material(meshes, materials)
            replacements = {}
            for source, images in entries:
                baked = rr_baked_material.create_baked_material(source, images, uv_map_name=uv_names.get(source, ""))
                created.append(baked)
                replacements[source] = baked
            if self.use_on_selected:
                for obj in meshes:
                    for slot in obj.material_slots:
                        if slot.material not in replacements:
                            continue
                        original = slot.material
                        assignments.append((slot, slot.link, original))
                        slot.link = "OBJECT"
                        slot.material = replacements[original]
            for baked in created:
                baked.use_fake_user = True
        except Exception as exc:
            for slot, link, material in reversed(assignments):
                slot.material = material
                slot.link = link
            for material in reversed(created):
                bpy.data.materials.remove(material)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        settings.pbr_framework_status = f"Created {len(created)} separate baked material(s); preview before export."
        self.report({"INFO"}, settings.pbr_framework_status)
        return {"FINISHED"}


BAKE_TOOLS_CLASSES = (RR_OT_recommend_pbr_bake_size, RR_OT_create_baked_pbr_material)
