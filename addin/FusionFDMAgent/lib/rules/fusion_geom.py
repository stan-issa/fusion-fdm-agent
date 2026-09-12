"""The thin layer where the rules meet the Fusion API.

Every function here takes Fusion objects and returns plain tuples and floats,
so the reasoning in ``geometry.py`` and in the rules themselves stays free of
``adsk``. Lengths are Fusion's own centimetres unless a name says ``_mm``.

Main thread only, like everything else that touches the API.
"""

import math

import adsk.core
import adsk.fusion

from . import geometry as geo

# How far outside a face to probe when asking which side of the solid a point
# is on. Comfortably larger than Fusion's modelling tolerance, comfortably
# smaller than any feature a 3D printer can resolve. Centimetres.
_PROBE_DISTANCE = 0.01


# -- conversions -----------------------------------------------------------


def point_tuple(point):
    return (point.x, point.y, point.z)


def vector_tuple(vector):
    return (vector.x, vector.y, vector.z)


def to_point(values):
    return adsk.core.Point3D.create(values[0], values[1], values[2])


def to_vector(values):
    return adsk.core.Vector3D.create(values[0], values[1], values[2])


def entity_token(entity):
    """An entity's persistent token, or None if Fusion will not give one."""
    try:
        return entity.entityToken
    except Exception:
        return None


def resolve_token(design, token):
    """Find an entity again after the body has been regenerated."""
    if not token:
        return None
    try:
        found = design.findEntityByToken(token)
    except Exception:
        return None
    if not found:
        return None
    # findEntityByToken returns a list; a BRep token resolves to exactly one.
    return found[0]


def is_valid(entity):
    try:
        return bool(entity) and entity.isValid
    except Exception:
        return False


# -- faces -----------------------------------------------------------------


def surface_type(face):
    try:
        return face.geometry.surfaceType
    except Exception:
        return None


def is_planar(face):
    return surface_type(face) == adsk.core.SurfaceTypes.PlaneSurfaceType


def is_cylindrical(face):
    return surface_type(face) == adsk.core.SurfaceTypes.CylinderSurfaceType


def face_area(face):
    try:
        return face.area
    except Exception:
        return 0.0


def outward_normal(face, point=None):
    """The normal pointing *out of* the solid at a point on the face.

    Neither ``face.geometry.normal`` nor the evaluator answers this on its own:
    both describe the underlying *surface*, whose parameterisation may run
    opposite to the face that uses it. ``isParamReversed`` is the flag that
    reconciles them, and point containment is the ground truth that confirms
    it -- cheap enough to run always, and it removes a whole class of
    silently-inverted geometry.
    """
    if point is None:
        point = face.pointOnFace
    ok, normal = face.evaluator.getNormalAtPoint(point)
    if not ok:
        raise rule_error("Could not evaluate the normal of a face.")
    vector = geo.normalise(vector_tuple(normal))
    if face.isParamReversed:
        vector = geo.negate(vector)

    # Confirm against the solid itself. If stepping along the normal lands
    # inside the material, the flag lied and the truth is the other way.
    position = point_tuple(point)
    outside = geo.add(position, geo.scale(vector, _PROBE_DISTANCE))
    inside = geo.subtract(position, geo.scale(vector, _PROBE_DISTANCE))
    try:
        body = face.body
    except Exception:
        return vector
    if is_inside(body, outside) and not is_inside(body, inside):
        return geo.negate(vector)
    return vector


def rule_error(message):
    from .base import RuleError

    return RuleError(message)


def outer_loop_edges(face):
    """Edges of the face's outer boundary, ignoring holes in it."""
    edges = []
    loops = face.loops
    for index in range(loops.count):
        loop = loops.item(index)
        if not loop.isOuter:
            continue
        loop_edges = loop.edges
        for edge_index in range(loop_edges.count):
            edges.append(loop_edges.item(edge_index))
    return edges


def all_loop_edges(face):
    edges = []
    loops = face.loops
    for index in range(loops.count):
        loop_edges = loops.item(index).edges
        for edge_index in range(loop_edges.count):
            edges.append(loop_edges.item(edge_index))
    return edges


def face_edges(face):
    edges = []
    collection = face.edges
    for index in range(collection.count):
        edges.append(collection.item(index))
    return edges


def body_faces(body):
    faces = []
    collection = body.faces
    for index in range(collection.count):
        faces.append(collection.item(index))
    return faces


# -- cylinders -------------------------------------------------------------


def cylinder_axis(face):
    """``(origin, axis, radius)`` for a cylindrical face, in centimetres."""
    cylinder = face.geometry
    return (
        point_tuple(cylinder.origin),
        geo.normalise(vector_tuple(cylinder.axis)),
        cylinder.radius,
    )


def is_bore(face):
    """True for the inside of a hole, false for the outside of a boss.

    Containment is the primary test and needs no notion of orientation: the
    axis of a bore runs through empty space, the axis of a boss runs through
    metal. The normal test is kept as a cross-check for the case where
    containment cannot answer -- an axis that happens to pass through a
    neighbouring feature, for instance.
    """
    origin, axis, _radius = cylinder_axis(face)
    point = face.pointOnFace
    position = point_tuple(point)

    offset = geo.subtract(position, origin)
    on_axis = geo.add(origin, geo.scale(axis, geo.dot(offset, axis)))
    try:
        body = face.body
    except Exception:
        body = None
    if body is not None:
        if is_outside(body, on_axis):
            return True
        if is_inside(body, on_axis):
            return False

    # The outward normal of a bore wall points towards the axis, because the
    # material is on the far side of it.
    radial = geo.subtract(position, on_axis)
    if geo.length(radial) == 0.0:
        return False
    return geo.dot(outward_normal(face, point), geo.normalise(radial)) < 0.0


def is_full_cylinder(face):
    """True when the face wraps a complete 360 degrees.

    A bore that something else has cut into is no longer a full cylinder, and
    guessing at what a teardrop should do to it is exactly the kind of thing
    this code should decline to attempt.
    """
    try:
        _origin, _axis, radius = cylinder_axis(face)
    except Exception:
        return False
    length = cylinder_axial_length(face)
    if length is None or length <= 0.0:
        return False
    # Compare the face's measured area against the lateral area a complete
    # cylinder of this radius and length would have. A bore something else has
    # cut into comes out smaller; the tolerance covers evaluator rounding.
    whole_cylinder = 2.0 * math.pi * radius * length
    if whole_cylinder <= 0.0:
        return False
    return face_area(face) / whole_cylinder > 0.98


def cylinder_axial_length(face):
    """Distance between the face's two circular ends, along its axis."""
    _origin, axis, _radius = cylinder_axis(face)
    circles = [edge for edge in face_edges(face) if is_circular(edge)]
    if len(circles) < 2:
        return None
    positions = []
    for edge in circles:
        centre = circle_centre(edge)
        if centre is None:
            return None
        positions.append(geo.dot(centre, axis))
    return max(positions) - min(positions)


# -- edges -----------------------------------------------------------------


def is_circular(edge):
    try:
        return edge.geometry.curveType == adsk.core.Curve3DTypes.Circle3DCurveType
    except Exception:
        return False


def circle_centre(edge):
    try:
        return point_tuple(edge.geometry.center)
    except Exception:
        return None


def circle_radius(edge):
    try:
        return edge.geometry.radius
    except Exception:
        return None


def edge_length(edge):
    try:
        return edge.length
    except Exception:
        return 0.0


def edge_midpoint(edge):
    evaluator = edge.evaluator
    ok, start, end = evaluator.getParameterExtents()
    if ok:
        ok, point = evaluator.getPointAtParameter((start + end) / 2.0)
        if ok:
            return point_tuple(point)
    # pointOnEdge is not the midpoint, but it is on the edge, which is all the
    # thin-wall probe actually needs.
    return point_tuple(edge.pointOnEdge)


def adjacent_faces(edge):
    return edge.faces.item(0), edge.faces.item(1)


def other_face(edge, face):
    """The face on the other side of an edge, or None if it has only one."""
    faces = edge.faces
    if faces.count < 2:
        return None
    first, second = faces.item(0), faces.item(1)
    first_token, face_token = entity_token(first), entity_token(face)
    if first_token is not None and face_token is not None:
        return second if first_token == face_token else first
    # Fall back on geometric identity when tokens are unavailable.
    try:
        return second if first.pointOnFace.isEqualTo(face.pointOnFace) else first
    except Exception:
        return second


def interior_angle_deg(edge):
    """The material-side angle between the two faces meeting at an edge.

    90 for a sharp box corner, 135 where a 45 degree chamfer has already been
    cut, near 180 where a fillet runs tangentially into the face. One number
    therefore answers "is this edge already relieved?" for both chamfers and
    fillets, which is why the rules ask for it rather than testing tangency
    separately.
    """
    faces = edge.faces
    if faces.count < 2:
        return None
    midpoint = edge_midpoint(edge)
    try:
        first = outward_normal(faces.item(0), nearest_point_on_face(faces.item(0), midpoint))
        second = outward_normal(faces.item(1), nearest_point_on_face(faces.item(1), midpoint))
    except Exception:
        return None

    between = geo.angle_between_deg(first, second)
    outward = geo.add(first, second)
    if geo.length(outward) == 0.0:
        return 180.0
    probe = geo.add(midpoint, geo.scale(geo.normalise(outward), _PROBE_DISTANCE))
    if is_outside(edge.body, probe):
        return 180.0 - between   # convex: a corner you could chamfer
    return 180.0 + between       # concave: an internal corner


def nearest_point_on_face(face, point):
    """A point on the face near the given one, for evaluating a normal there."""
    try:
        ok, found = face.evaluator.getPointAtParameter(
            face.evaluator.getParameterAtPoint(to_point(point))[1]
        )
        if ok:
            return found
    except Exception:
        pass
    return face.pointOnFace


def is_inside(body, point):
    """True when a point lies within the solid (boundary counts as neither)."""
    try:
        containment = body.pointContainment(to_point(point))
    except Exception:
        return False
    return containment == adsk.fusion.PointContainment.PointInsidePointContainment


def is_outside(body, point):
    try:
        containment = body.pointContainment(to_point(point))
    except Exception:
        return False
    return containment == adsk.fusion.PointContainment.PointOutsidePointContainment


# -- measurement -----------------------------------------------------------


def minimum_distance(app, first, second):
    """Shortest distance between two entities, in centimetres, or None."""
    try:
        return app.measureManager.measureMinimumDistance(first, second).value
    except Exception:
        return None


def bounding_box_extent(body, direction):
    """``(min, max)`` projection of a body's bounding box onto a direction."""
    box = body.boundingBox
    low, high = box.minPoint, box.maxPoint
    corners = [
        (x, y, z)
        for x in (low.x, high.x)
        for y in (low.y, high.y)
        for z in (low.z, high.z)
    ]
    projections = [geo.dot(corner, direction) for corner in corners]
    return min(projections), max(projections)


# -- bodies ----------------------------------------------------------------


def is_native_body(body):
    """False for a body reached through an occurrence.

    Geometry read through an occurrence is in assembly space while the feature
    that fixes it must be created on the native component, and quietly mixing
    the two puts chamfers in the wrong place. Rules report these bodies as not
    analysed rather than getting it subtly wrong.
    """
    try:
        return body.assemblyContext is None
    except Exception:
        return True


def parent_component(body):
    try:
        return body.parentComponent
    except Exception:
        return None


def component_key(component):
    """A hashable stand-in for a component.

    Fusion's API objects define no ``__hash__``, so one cannot be a dictionary
    key or go in a set. Anything that needs to group *by* component has to
    group by something derived from it instead -- and the failure only shows
    up at the moment of grouping, which is usually well away from where the
    object was obtained.
    """
    if component is None:
        return None
    token = entity_token(component)
    if token:
        return token
    try:
        return "name:" + component.name
    except Exception:
        return "component:unidentified"


# -- features --------------------------------------------------------------


def feature_problem(feature):
    """A message if a freshly created feature is unhealthy, else None.

    ``add()`` returning without raising is not proof the feature worked --
    Fusion will hand back an errored feature and leave a red mark in the
    browser. A rules engine that reports "applied" in that case is lying, so
    every feature it creates is checked and rolled back if it is broken.
    """
    try:
        state = feature.healthState
    except Exception:
        return None
    healthy = adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
    if state == healthy:
        return None
    try:
        message = feature.errorOrWarningMessage
    except Exception:
        message = ""
    return message or "Fusion created the feature in an error state."


def delete_feature(feature):
    try:
        feature.deleteMe()
    except Exception:
        pass


def value_input_mm(millimetres):
    """A ValueInput for a length the rule computed.

    ``createByReal`` takes Fusion's internal centimetres. ``createByString``
    would put a readable "0.3 mm" expression in the timeline, but it parses
    with the user's locale, so a comma-decimal machine rejects it. A machine
    generated fix does not need a pretty expression.
    """
    return adsk.core.ValueInput.createByReal(geo.to_cm(millimetres))


def object_collection(items):
    collection = adsk.core.ObjectCollection.create()
    for item in items:
        collection.add(item)
    return collection


# -- signatures ------------------------------------------------------------
#
# A signature describes an entity well enough to recognise it again after the
# body has been regenerated. It deliberately avoids anything Fusion chooses
# arbitrarily -- `pointOnFace` is *a* point on the face, not a stable one, so
# a face is described by its plane or its axis instead.


def body_name(entity):
    try:
        return entity.body.name
    except Exception:
        return ""


def plane_offset(face, normal):
    """Signed distance from the origin to a planar face, along its normal."""
    try:
        return geo.dot(point_tuple(face.geometry.origin), normal)
    except Exception:
        return geo.dot(point_tuple(face.pointOnFace), normal)


def axis_anchor(origin, axis):
    """The point on an infinite axis closest to the world origin.

    Cylinders report whichever origin the modelling operation happened to
    leave behind; this canonicalises it so the same axis always hashes the
    same way.
    """
    return geo.subtract(origin, geo.scale(axis, geo.dot(origin, axis)))


def face_signature(face):
    from . import signature as sig

    name = ""
    try:
        name = face.body.name
    except Exception:
        pass

    if is_planar(face):
        normal = outward_normal(face)
        return sig.make(
            "face", name,
            surface="plane",
            normal=normal,
            offset=geo.to_mm(plane_offset(face, normal)),
            area=geo.area_to_mm2(face_area(face)),
        )
    if is_cylindrical(face):
        origin, axis, radius = cylinder_axis(face)
        return sig.make(
            "face", name,
            surface="cylinder",
            axis=axis,
            axis_point=[geo.to_mm(value) for value in axis_anchor(origin, axis)],
            radius=geo.to_mm(radius),
        )
    return sig.make(
        "face", name,
        surface=str(surface_type(face)),
        point=[geo.to_mm(value) for value in point_tuple(face.pointOnFace)],
        area=geo.area_to_mm2(face_area(face)),
    )


def edge_signature(edge):
    from . import signature as sig

    name = ""
    try:
        name = edge.body.name
    except Exception:
        pass

    if is_circular(edge):
        centre = circle_centre(edge) or (0.0, 0.0, 0.0)
        normal = (0.0, 0.0, 1.0)
        try:
            normal = geo.normalise(vector_tuple(edge.geometry.normal))
        except Exception:
            pass
        return sig.make(
            "edge", name,
            curve="circle",
            centre=[geo.to_mm(value) for value in centre],
            normal=normal,
            radius=geo.to_mm(circle_radius(edge) or 0.0),
        )
    return sig.make(
        "edge", name,
        curve="curve",
        point=[geo.to_mm(value) for value in edge_midpoint(edge)],
        length=geo.to_mm(edge_length(edge)),
    )


def edge_points(edge, tolerance=0.005):
    """Sample an edge into a polyline, in centimetres.

    ``getStrokes`` is Fusion's own curve tessellation, so a circle comes back
    as enough points to measure against rather than three.
    """
    try:
        evaluator = edge.evaluator
        ok, start, end = evaluator.getParameterExtents()
        if ok:
            ok, points = evaluator.getStrokes(start, end, tolerance)
            if ok and points:
                return [point_tuple(point) for point in points]
    except Exception:
        pass
    try:
        return [
            point_tuple(edge.startVertex.geometry),
            point_tuple(edge.endVertex.geometry),
        ]
    except Exception:
        return [edge_midpoint(edge)]


# -- the ignore escape hatch ----------------------------------------------
#
# Some exclusions cannot be derived from geometry. A bearing seat and a
# clearance hole are the same cylinder; only the person who drew it knows
# which is which. Fusion attributes are the idiomatic way to record that, and
# they persist with the document rather than in a settings file that knows
# nothing about which model it is describing.

ATTRIBUTE_GROUP = "fdmAgent"
IGNORE_ATTRIBUTE = "ignore"


def ignored_rules(entity):
    """Rule ids this entity has been marked to skip. ``*`` means all of them."""
    try:
        attribute = entity.attributes.itemByName(ATTRIBUTE_GROUP, IGNORE_ATTRIBUTE)
    except Exception:
        return frozenset()
    if attribute is None or not attribute.value:
        return frozenset()
    return frozenset(part.strip() for part in str(attribute.value).split(",") if part.strip())


def is_ignored(entity, rule_id):
    marked = ignored_rules(entity)
    return "*" in marked or rule_id in marked


def set_ignored(entity, rule_id, ignore=True):
    """Add or remove a rule from an entity's ignore list."""
    marked = set(ignored_rules(entity))
    if ignore:
        marked.add(rule_id)
    else:
        marked.discard(rule_id)
    try:
        entity.attributes.add(
            ATTRIBUTE_GROUP, IGNORE_ATTRIBUTE, ",".join(sorted(marked))
        )
    except Exception:
        return False
    return True
