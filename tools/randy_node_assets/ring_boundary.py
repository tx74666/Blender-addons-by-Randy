"""Native boundary bundles shared by Arc Mask and Extend Mask.

The bundle contains three vectors rather than a lossy scalar mask:
Position = normalized UV XY, Bounds = (inner, outer, softness), and
Arc = (start degrees, sweep degrees, active). All saved computation is made
from native Shader nodes; this module is needed only while building assets.
"""

CONTRACT = "ring-boundary-v1: Position, Bounds and Arc vectors in a native Bundle"
FIELDS = ("Position", "Bounds", "Arc")


class Graph:
    """Small readable constructors with deterministic internal node placement."""

    def __init__(self, group, prefix="Boundary", origin=(-1600, -1800)):
        self.group = group
        self.prefix = prefix
        self.origin = origin
        self.index = 0

    def node(self, kind, name, width=190):
        result = self.group.nodes.new(kind)
        result.name = self.prefix + " / " + name
        result.label = name
        result.location = (
            self.origin[0] + (self.index % 8) * 240,
            self.origin[1] - (self.index // 8) * 260,
        )
        result.width = width
        result.select = False
        self.index += 1
        return result

    def value(self, socket, value):
        if isinstance(value, (int, float, tuple, list)):
            socket.default_value = value
        else:
            self.group.links.new(value, socket)

    def math(self, operation, *values, name=None):
        result = self.node("ShaderNodeMath", name or operation)
        result.operation = operation
        for socket, value in zip(result.inputs, values):
            self.value(socket, value)
        return result.outputs[0]

    def vector(self, operation, *values, name=None):
        result = self.node("ShaderNodeVectorMath", name or operation)
        result.operation = operation
        for socket, value in zip(result.inputs, values):
            self.value(socket, value)
        return result.outputs["Value"] if operation in {"LENGTH", "DISTANCE", "DOT_PRODUCT"} else result.outputs[0]

    def combine(self, x, y, z, name):
        result = self.node("ShaderNodeCombineXYZ", name)
        for socket, value in zip(result.inputs, (x, y, z)):
            self.value(socket, value)
        return result.outputs[0]

    def separate(self, value, name):
        result = self.node("ShaderNodeSeparateXYZ", name)
        self.value(result.inputs[0], value)
        return tuple(result.outputs[axis] for axis in ("X", "Y", "Z"))

    def clamp(self, value, minimum, maximum, name):
        positive = self.math("MAXIMUM", value, minimum, name=name + " minimum")
        return self.math("MINIMUM", positive, maximum, name=name + " maximum")

    def select(self, factor, when_false, when_true, name):
        # Native arithmetic keeps scalar branch selection independent of any
        # add-on or Python connection-state handler.
        inverse = self.math("SUBTRACT", 1.0, factor, name=name + " false weight")
        false_part = self.math("MULTIPLY", when_false, inverse, name=name + " false branch")
        true_part = self.math("MULTIPLY", when_true, factor, name=name + " true branch")
        return self.math("ADD", false_part, true_part, name=name)

    def smooth_profile(self, distance, width, softness, name):
        """Inclusive hard band, or an inward fade capped to half its width."""
        half = self.math("MULTIPLY", width, .5, name=name + " half width")
        soft = self.clamp(softness, 0.0, half, name + " softness")
        safe = self.math("MAXIMUM", soft, 1e-8, name=name + " safe softness")
        mapped = self.node("ShaderNodeMapRange", name + " inward smoothstep")
        mapped.data_type = "FLOAT"
        mapped.interpolation_type = "SMOOTHSTEP"
        mapped.clamp = True
        mapped.inputs["From Min"].default_value = 0.0
        mapped.inputs["To Min"].default_value = 0.0
        mapped.inputs["To Max"].default_value = 1.0
        self.value(mapped.inputs["Value"], distance)
        self.value(mapped.inputs["From Max"], safe)
        outside = self.math("LESS_THAN", distance, 0.0, name=name + " outside")
        hard = self.math("SUBTRACT", 1.0, outside, name=name + " hard profile")
        enabled = self.math("GREATER_THAN", soft, 0.0, name=name + " soft enabled")
        return self.select(enabled, hard, mapped.outputs["Result"], name + " edge profile"), soft


def pack(group, position, bounds, arc, *, name="Boundary data"):
    """Return one native Bundle output, without copying shared source data."""
    graph = Graph(group, prefix=name)
    result = graph.node("NodeCombineBundle", name, 240)
    result.bundle_items.clear()
    for field in FIELDS:
        result.bundle_items.new("VECTOR", field)
    for field, value in zip(FIELDS, (position, bounds, arc)):
        graph.value(result.inputs[field], value)
    return result.outputs[0]


def unpack(group, source, *, name="Source boundary"):
    """Missing or unconnected fields evaluate to zero, including active=0."""
    graph = Graph(group, prefix=name)
    result = graph.node("NodeSeparateBundle", name, 240)
    result.bundle_items.clear()
    for field in FIELDS:
        result.bundle_items.new("VECTOR", field)
    graph.value(result.inputs[0], source)
    return {field: result.outputs[field] for field in FIELDS}


def arc_data(group, input_sockets, uv_socket):
    """Append boundary metadata while leaving Arc Mask's original graph intact."""
    graph = Graph(group, prefix="Ring Data")
    centered = graph.vector("SUBTRACT", uv_socket, (.5, .5, 0), name="UV center")
    position = graph.vector("MULTIPLY", centered, (2.0, 2.0, 0), name="Normalized UV XY")
    inner = graph.clamp(input_sockets["Inner Radius"], 0.0, 1.0, "Inner radius")
    width = graph.math("MAXIMUM", input_sockets["Ring Width"], 0.0, name="Nonnegative width")
    outer = graph.math("ADD", inner, width, name="Unclipped outer radius")
    outer = graph.math("MINIMUM", outer, 1.0, name="Visible outer radius")
    visible_width = graph.math("SUBTRACT", outer, inner, name="Visible radial width")
    half = graph.math("MULTIPLY", visible_width, .5, name="Visible half width")
    soft = graph.clamp(input_sockets["Edge Softness"], 0.0, half, "Radial softness")
    sweep = graph.clamp(input_sockets["Sweep Angle"], 0.0, 360.0, "Sweep degrees")
    start = graph.math("FLOORED_MODULO", input_sockets["Start Angle"], 360.0, name="Wrapped start degrees")
    width_on = graph.math("GREATER_THAN", visible_width, 0.0, name="Visible radial band")
    sweep_on = graph.math("GREATER_THAN", sweep, 0.0, name="Visible angular span")
    active = graph.math("MULTIPLY", width_on, sweep_on, name="Active source")
    bounds = graph.combine(inner, outer, soft, "Bounds inner outer softness")
    arc = graph.combine(start, sweep, active, "Arc start sweep active")
    return pack(group, position, bounds, arc, name="Ring Data bundle")


def angular_fields(graph, position, start, sweep):
    """Reusable angular gate and the two unit endpoint directions."""
    x, y, _ = graph.separate(position, "Position XY")
    angle = graph.math("ARCTAN2", y, x, name="Position angle radians")
    degrees = graph.math("DEGREES", angle, name="Position angle degrees")
    delta = graph.math("SUBTRACT", degrees, start, name="Relative angle degrees")
    wrapped = graph.math("FLOORED_MODULO", delta, 360.0, name="Wrapped relative angle")
    outside = graph.math("GREATER_THAN", wrapped, sweep, name="Outside source arc")
    inside = graph.math("SUBTRACT", 1.0, outside, name="Inside source arc")
    not_full = graph.math("LESS_THAN", sweep, 360.0, name="Source has angular ends")
    start_radians = graph.math("RADIANS", start, name="Start radians")
    end_degrees = graph.math("ADD", start, sweep, name="End degrees")
    end_radians = graph.math("RADIANS", end_degrees, name="End radians")
    units = []
    for radians, label in ((start_radians, "Start"), (end_radians, "End")):
        cosine = graph.math("COSINE", radians, name=label + " cosine")
        sine = graph.math("SINE", radians, name=label + " sine")
        units.append(graph.combine(cosine, sine, 0.0, label + " unit direction"))
    return inside, not_full, units[0], units[1]


def sector_distance(graph, position, radius, inner, outer, angular_inside, has_ends, start_unit, end_unit):
    """Exact signed distance to an annular sector, before normalized disk crop.

    Circular boundaries use radial distance inside the angular range and
    nearest endpoint distance outside it. The two finite radial segments use
    clamped projections. Full circles have no end segments; disks have no
    inner circular boundary. This also handles reflex arcs and the 0/360 seam.
    """
    def circle_arc(boundary, label):
        radial = graph.math("SUBTRACT", radius, boundary, name=label + " radial difference")
        radial = graph.math("ABSOLUTE", radial, name=label + " radial distance")
        endpoint_distances = []
        for unit, side in ((start_unit, "start"), (end_unit, "end")):
            endpoint = graph.vector("SCALE", unit, name=label + " " + side + " endpoint")
            # SCALE stores its scalar in the dedicated fourth socket, not the
            # second vector input used by binary vector operations.
            endpoint.node.inputs["Scale"].default_value = 1.0
            graph.value(endpoint.node.inputs["Scale"], boundary)
            endpoint_distances.append(graph.vector("DISTANCE", position, endpoint, name=label + " " + side + " distance"))
        endpoints = graph.math("MINIMUM", *endpoint_distances, name=label + " nearest endpoint")
        return graph.select(angular_inside, endpoints, radial, label + " arc distance")

    outer_distance = circle_arc(outer, "Outer boundary")
    inner_distance = circle_arc(inner, "Inner boundary")
    hole = graph.math("GREATER_THAN", inner, 0.0, name="Source has inner hole")
    inner_distance = graph.select(hole, 1e6, inner_distance, "Only real inner boundary")
    circular_distance = graph.math("MINIMUM", inner_distance, outer_distance, name="Nearest circular boundary")
    cuts = []
    for unit, label in ((start_unit, "Start"), (end_unit, "End")):
        projection = graph.vector("DOT_PRODUCT", position, unit, name=label + " segment projection")
        projection = graph.clamp(projection, inner, outer, label + " finite segment")
        closest = graph.vector("SCALE", unit, name=label + " closest cut point")
        graph.value(closest.node.inputs["Scale"], projection)
        cuts.append(graph.vector("DISTANCE", position, closest, name=label + " cut distance"))
    cut_distance = graph.math("MINIMUM", *cuts, name="Nearest angular end")
    cut_distance = graph.select(has_ends, 1e6, cut_distance, "Only real angular ends")
    distance = graph.math("MINIMUM", circular_distance, cut_distance, name="Nearest real source contour")
    below = graph.math("LESS_THAN", radius, inner, name="Inside source hole")
    above = graph.math("GREATER_THAN", radius, outer, name="Outside source outer edge")
    radial_outside = graph.math("MAXIMUM", below, above, name="Outside source radial band")
    radial_inside = graph.math("SUBTRACT", 1.0, radial_outside, name="Inside source radial band")
    inside = graph.math("MULTIPLY", radial_inside, angular_inside, name="Inside source sector")
    twice = graph.math("MULTIPLY", inside, 2.0, name="Inside sign contribution")
    sign = graph.math("SUBTRACT", 1.0, twice, name="Signed distance orientation")
    return graph.math("MULTIPLY", distance, sign, name="Exact signed sector distance")


def radial_band(graph, radius, inner, outer, angular_inside, active, softness, label):
    """Return a mask and the effective inward softness of an exact radial band."""
    width = graph.math("SUBTRACT", outer, inner, name=label + " visible width")
    inner_distance = graph.math("SUBTRACT", radius, inner, name=label + " inner distance")
    outer_distance = graph.math("SUBTRACT", outer, radius, name=label + " outer distance")
    has_hole = graph.math("GREATER_THAN", inner, 0.0, name=label + " has inner hole")
    inner_distance = graph.select(has_hole, outer, inner_distance, label + " disk center bypass")
    nearest = graph.math("MINIMUM", inner_distance, outer_distance, name=label + " nearest radial edge")
    profile, soft = graph.smooth_profile(nearest, width, softness, label)
    enabled = graph.math("MULTIPLY", active, angular_inside, name=label + " active arc")
    return graph.math("MULTIPLY", profile, enabled, name=label + " mask"), soft
