"""Slope the underside of a ledge so it prints without support.

A shelf projecting from a wall has a flat underside: a ninety degree
overhang, which FDM cannot print. The first layer of the ledge has nothing
beneath it and droops, and the usual remedy is a support structure the user
then has to break off a surface they cared about.

A gusset removes the problem instead of propping it up. Filling the corner
between the wall and the underside with a triangle turns the overhang into a
45 degree slope, which prints as an ordinary overhang, and leaves the top of
the ledge exactly as it was -- the surface that is usually there for a reason.

The fix is a chamfer, which is less surprising than it sounds. A chamfer on a
*convex* edge cuts the corner off; on a concave one it fills the corner in,
and the corner beneath a ledge is concave. An equal-distance chamfer therefore
adds precisely the triangular gusset wanted, at 45 degrees by construction.
Because the direction matters so much and a chamfer the wrong way round would
look perfectly healthy, the applier is told to expect the body to grow.
"""

from . import chamfer_op, fusion_geom as fg, geometry as geo, signature as sig
from .base import Finding, Outcome

ID = "ledge_gusset"
TITLE = "Slope ledge undersides"
DESCRIPTION = (
    "Fills the corner under a ledge with a 45° gusset, so its flat underside "
    "prints as a slope instead of needing support. The top of the ledge is "
    "untouched."
)
NEEDS_BUILD_DIRECTION = True

PARAMS = {
    "max_tilt_deg": {
        "default": 5.0, "min": 0.1, "max": 30.0,
        "label": "Max tilt from flat", "unit": "°",
    },
    "max_gusset_mm": {
        "default": 25.0, "min": 1.0, "max": 200.0,
        "label": "Max gusset", "unit": "mm",
    },
}

# How square to the build direction a face must be to count as a wall.
_WALL_TOLERANCE_DEG = 5.0

# A face this close to the bottom of the body is resting on the bed, not
# hanging over air. Centimetres.
_BED_TOLERANCE = 0.001

# Below this there is nothing worth adding, and Fusion will refuse anyway.
_MIN_GUSSET_MM = 0.1

# Where to probe for anything already occupying the gusset's space, as
# fractions of the way out along the ledge edge and into the triangle.
_ALONG_EDGE = (0.25, 0.5, 0.75)
_INTO_TRIANGLE = (0.25, 0.45)


def detect(context, params):
    direction = context.build_direction.vector
    findings = []

    for body in context.bodies:
        if not fg.is_native_body(body):
            continue
        bottom, _top = fg.bounding_box_extent(body, direction)
        for face in fg.body_faces(body):
            if not _is_underside(face, direction, params, bottom):
                continue
            for edge in fg.face_edges(face):
                finding = _examine(context, body, face, edge, params, direction)
                if finding is not None:
                    findings.append(finding)
    return findings


def _is_underside(face, direction, params, bottom):
    """A flat face hanging over air, rather than the one resting on the bed."""
    if not fg.is_planar(face):
        return False
    try:
        normal = fg.outward_normal(face)
    except Exception:
        return False
    if not geo.is_same_direction(
        normal, geo.negate(direction), params["max_tilt_deg"]
    ):
        return False
    # The bottom of the part is not an overhang; it is what the part stands on.
    return fg.plane_offset(face, direction) > bottom + _BED_TOLERANCE


def _examine(context, body, face, edge, params, direction):
    # Straight ledges only. A curved edge needs a swept gusset, and a chamfer
    # around it would not be the 45 degrees the rule promises.
    if not fg.is_straight(edge):
        return None
    if fg.is_ignored(edge, ID) or fg.is_ignored(face, ID):
        return None

    wall = fg.other_face(edge, face)
    if wall is None or not fg.is_planar(wall):
        return None
    try:
        wall_normal = fg.outward_normal(wall)
    except Exception:
        return None
    if not geo.is_perpendicular(wall_normal, direction, _WALL_TOLERANCE_DEG):
        return None

    # The wall has to carry on *below* the ledge for a gusset to sit against
    # it. This is also what tells a ledge's inner edge from its outer one:
    # the face at the outer edge is the end of the ledge and rises from it,
    # so there is nothing underneath to build against.
    below_mm = _wall_depth_mm(wall, edge, direction)
    if below_mm <= _MIN_GUSSET_MM:
        return None

    projection_mm = _projection_mm(face, wall, wall_normal)
    if projection_mm <= _MIN_GUSSET_MM:
        return None

    identifier = sig.finding_id(ID, fg.edge_signature(edge))
    length_mm = geo.to_mm(fg.edge_length(edge))
    title = "{} — {} mm ledge, {} mm wide".format(
        body.name, _format(projection_mm), _format(length_mm)
    )

    if projection_mm > params["max_gusset_mm"]:
        return Finding(
            identifier, ID, title,
            detail=(
                "Projects {} mm. A solid 45° gusset that deep adds a great "
                "deal of material; a rib or a redesign is the better answer, "
                "so this one is left alone.".format(_format(projection_mm))
            ),
            body=body.name, fixable=False, entities=[], reveal=[edge],
        )

    gusset_mm = min(projection_mm, below_mm)
    if not _space_is_clear(body, edge, wall_normal, direction, gusset_mm):
        return Finding(
            identifier, ID, title,
            detail=(
                "Something already fills the space under this ledge — another "
                "feature, or a gusset it has been given already."
            ),
            body=body.name, fixable=False, entities=[], reveal=[edge],
        )

    return Finding(
        identifier,
        ID,
        title,
        detail=_detail(projection_mm, below_mm, gusset_mm),
        body=body.name,
        fixable=True,
        fix_summary="Add a 45° gusset {} mm deep".format(_format(gusset_mm)),
        entities=[edge],
        extra={"gussetMm": gusset_mm, "projectionMm": geo.round_mm(projection_mm)},
    )


def _detail(projection_mm, below_mm, gusset_mm):
    if gusset_mm >= projection_mm:
        return "Flat underside over air; the gusset turns all of it into a 45° slope."
    # Honest about a partial fix: the rest of the underside stays flat, and
    # the user should know that before they tick the box.
    return (
        "Flat underside over air. The wall only runs {} mm below it, so the "
        "slope covers {} mm of the {} mm overhang and the rest stays flat."
    ).format(_format(below_mm), _format(gusset_mm), _format(projection_mm))


def _projection_mm(face, wall, wall_normal):
    """How far the underside reaches out past the wall it hangs off."""
    wall_station = fg.plane_offset(wall, wall_normal)
    furthest = None
    for edge in fg.face_edges(face):
        for point in fg.edge_points(edge):
            station = geo.dot(point, wall_normal)
            if furthest is None or station > furthest:
                furthest = station
    if furthest is None:
        return 0.0
    return geo.to_mm(furthest - wall_station)


def _wall_depth_mm(wall, edge, direction):
    """How far the wall carries on below the ledge."""
    here = geo.dot(fg.edge_midpoint(edge), direction)
    lowest = None
    for wall_edge in fg.face_edges(wall):
        for point in fg.edge_points(wall_edge):
            station = geo.dot(point, direction)
            if lowest is None or station < lowest:
                lowest = station
    if lowest is None:
        return 0.0
    return geo.to_mm(here - lowest)


def _space_is_clear(body, edge, wall_normal, direction, gusset_mm):
    """Whether the triangle the gusset would fill is empty at the moment.

    This is what "avoid adjoining parts" comes down to. It also settles, for
    free, whether the corner has already been gusseted: if the triangle is
    solid the rule has nothing to add, whatever the reason.
    """
    reach = geo.to_cm(gusset_mm)
    points = fg.edge_points(edge)
    if len(points) < 2:
        return True
    start, end = points[0], points[-1]
    span = geo.subtract(end, start)
    down = geo.negate(direction)

    for along in _ALONG_EDGE:
        base = geo.add(start, geo.scale(span, along))
        for into in _INTO_TRIANGLE:
            # Inside the triangle: out along the wall and down by the same
            # amount stays under the 45 degree hypotenuse for any fraction
            # below a half.
            probe = geo.add(base, geo.add(
                geo.scale(wall_normal, reach * into),
                geo.scale(down, reach * into),
            ))
            if not fg.is_outside(body, probe):
                return False
    return True


def _format(value):
    return ("{:.3f}".format(value)).rstrip("0").rstrip(".")


def apply(context, findings, params):
    jobs, outcomes = [], []
    for finding in findings:
        if not finding.fixable or not finding.entities:
            outcomes.append(Outcome.skipped(
                finding.id, finding.detail or "Nothing to add under this ledge."
            ))
            continue
        jobs.append((finding, finding.entities, finding.extra.get("gussetMm", 0.0)))
    return outcomes + chamfer_op.apply_chamfer(
        context, jobs, description="gusset", expect=chamfer_op.ADDS
    )
