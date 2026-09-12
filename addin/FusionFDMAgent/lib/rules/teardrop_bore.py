"""Turn a horizontal bore into a teardrop so its roof prints without support.

A hole drilled across the build direction has a flat ceiling. FDM cannot
bridge a flat ceiling that starts as a tangent: the first strands over the
crown have nothing to sit on, so they droop, and the top of the hole comes out
sagged and undersized. The classic fix is to replace the crown with a peak --
two faces at 45 degrees, each of which the printer can build as an ordinary
overhang.

The roof lines are *tangent* to the bore, so they never cross into it. The
original circular clearance is preserved exactly; the hole only gains a little
attic.

Detection is deliberately timid. A teardrop cut into the wrong hole -- a
bearing seat, a threaded hole, somewhere two bores cross -- is worse than no
teardrop at all, so anything that is not an unambiguous plain bore is reported
and left alone.
"""

import adsk.core
import adsk.fusion

from . import fusion_geom as fg, geometry as geo, signature as sig
from .base import Finding, Outcome

ID = "teardrop_bore"
TITLE = "Teardrop horizontal bores"
DESCRIPTION = (
    "Replaces the unsupported flat roof of a bore running across the build "
    "direction with two 45° faces, keeping the original circular clearance. "
    "Every size is reported; how small is too small to bother with is a "
    "judgement about the part, not something a threshold should make for you."
)
NEEDS_BUILD_DIRECTION = True

PARAMS = {
    "roof_angle_deg": {
        "default": 45.0, "min": 20.0, "max": 70.0,
        "label": "Roof angle", "unit": "°",
    },
    "max_axis_tilt_deg": {
        "default": 30.0, "min": 1.0, "max": 45.0,
        "label": "Max tilt from horizontal", "unit": "°",
    },
}

# Two bores count as coaxial -- a counterbore, or a seat -- when their axes
# line up this closely. Centimetres and degrees.
_COAXIAL_DISTANCE = 0.01
_COAXIAL_ANGLE_DEG = 1.0

# How square the entrance face must be to the bore axis to sketch on it.
_ENTRANCE_TOLERANCE_DEG = 2.0


def detect(context, params):
    direction = context.build_direction.vector
    findings = []
    passed_over = _PassedOver()

    for body in context.bodies:
        if not fg.is_native_body(body):
            continue
        bores = [
            face for face in fg.body_faces(body)
            if fg.is_cylindrical(face) and fg.is_bore(face)
        ]
        for face in bores:
            finding = _examine(
                context, body, face, bores, params, direction, passed_over
            )
            if finding is not None:
                findings.append(finding)

    passed_over.report(context, params, bool(findings))
    return findings


class _PassedOver:
    """Bores this rule looked at and decided against.

    Worth counting rather than discarding. A rule that reports nothing is
    indistinguishable from a rule that is broken, and "no horizontal bores"
    and "six bores, none of them horizontal" are very different answers to the
    same button.
    """

    def __init__(self):
        self.not_horizontal = 0

    def report(self, context, params, found_any):
        if found_any:
            return
        # Nothing at all. Say what was actually examined, so the answer is
        # "there is nothing to do" rather than an unexplained blank.
        if self.not_horizontal:
            context.note(
                "No bores run across the build direction. {} other bore{} "
                "checked; a bore more than {}° off horizontal prints its own "
                "roof as an overhang.".format(
                    self.not_horizontal, "" if self.not_horizontal == 1 else "s",
                    _format(params["max_axis_tilt_deg"]),
                )
            )
        else:
            context.note("No cylindrical bores found to check.")


def _examine(context, body, face, siblings, params, direction, passed_over):
    if fg.is_ignored(face, ID):
        return None
    if context.is_thread_face(face):
        return None

    _origin, axis, radius = fg.cylinder_axis(face)
    diameter_mm = geo.to_mm(radius * 2.0)

    # Tilt is how far the axis lies from horizontal: 0 for a bore straight
    # across the build direction, 90 for one straight up it.
    tilt = abs(90.0 - geo.angle_between_deg(axis, direction))
    if tilt > params["max_axis_tilt_deg"]:
        passed_over.not_horizontal += 1
        return None  # steep enough that its roof is already a printable overhang

    identifier = sig.finding_id(ID, fg.face_signature(face))
    title = "{} — ⌀{} mm horizontal bore".format(body.name, _format(diameter_mm))

    reason = _unfixable_reason(face, siblings)
    if reason is not None:
        # No entities, because there is no fix to apply them to -- but the
        # bore is still shown when asked, since "which hole do you mean?" is
        # the first thing anyone reads an unfixable finding and wonders.
        return Finding(
            identifier, ID, title, detail=reason, body=body.name,
            fixable=False, entities=[], reveal=[face],
        )

    entrance, _start, _end = _cut_span(face, axis)
    if entrance is None:
        return Finding(
            identifier, ID, title,
            detail=(
                "No flat face square to the bore to build the cut from, so this "
                "one needs doing by hand."
            ),
            body=body.name, fixable=False, entities=[], reveal=[face],
        )

    added_mm = geo.to_mm(
        geo.teardrop_added_height(radius, params["roof_angle_deg"])
    )
    return Finding(
        identifier,
        ID,
        title,
        detail="Axis {}° from horizontal; the teardrop adds {} mm of height.".format(
            round(tilt), _format(added_mm)
        ),
        body=body.name,
        fixable=True,
        fix_summary="Cut a {}° teardrop roof".format(_format(params["roof_angle_deg"])),
        entities=[face],
        # Measurements only. Entity tokens were carried here at one point and
        # were worse than useless: by the time a fix runs they name geometry a
        # previous fix has already replaced, and apply re-derives all of it
        # from the face it is handed anyway.
        extra={"diameterMm": geo.round_mm(diameter_mm)},
    )


def _unfixable_reason(face, siblings):
    """Why this bore must be left alone, or None if it may be teardropped."""
    if not fg.is_full_cylinder(face):
        return (
            "Not a complete cylinder — something else cuts into it, and where "
            "the roof should go is ambiguous."
        )
    rings = [edge for edge in fg.face_edges(face) if fg.is_circular(edge)]
    if len(rings) != 2:
        return "Does not have two plain circular ends, so it is not a simple bore."
    if _has_coaxial_neighbour(face, siblings):
        return (
            "Shares its axis with another bore — a counterbore or a seat, where "
            "the fit matters more than the support."
        )
    return None


def _has_coaxial_neighbour(face, siblings):
    origin, axis, radius = fg.cylinder_axis(face)
    anchor = fg.axis_anchor(origin, axis)
    for other in siblings:
        if other is face:
            continue
        try:
            other_origin, other_axis, other_radius = fg.cylinder_axis(other)
        except Exception:
            continue
        if abs(other_radius - radius) < 1e-9:
            continue  # the same bore reached twice, not a neighbour
        if not geo.is_parallel(axis, other_axis, _COAXIAL_ANGLE_DEG):
            continue
        if geo.distance(anchor, fg.axis_anchor(other_origin, other_axis)) < _COAXIAL_DISTANCE:
            return True
    return False


def _cut_span(face, axis):
    """Where to sketch the teardrop, and how far along the bore to cut it.

    Returns ``(entrance face, start station, end station)`` as distances along
    the bore axis, or ``(None, None, None)`` if there is nowhere to sketch.

    *Both* ends are followed out to the flat face they open onto. Measuring
    only to the far end's ring leaves the chamfer beyond it untouched, so a
    hole with a lead-in at each end came out with a teardrop at one end and
    the original flat roof still sitting over the other.

    An end that opens onto nothing flat -- a blind bore, or one breaking out
    through a curved wall -- falls back to its own ring, which is the furthest
    the cut can go without guessing.
    """
    rings = []
    for edge in fg.face_edges(face):
        if not fg.is_circular(edge):
            continue
        centre = fg.circle_centre(edge)
        if centre is not None:
            rings.append((edge, centre))

    if len(rings) != 2:
        return None, None, None

    ends = []
    for edge, centre in rings:
        flat = _flat_face_beyond(edge, face, axis)
        station = (
            fg.plane_offset(flat, axis) if flat is not None
            else geo.dot(centre, axis)
        )
        ends.append((flat, station))

    for index, (flat, station) in enumerate(ends):
        if flat is not None:
            return flat, station, ends[1 - index][1]
    return None, None, None


def _flat_face_beyond(ring, bore, axis):
    """The flat face this end of the bore opens onto, across a chamfer if need be."""
    neighbour = fg.other_face(ring, bore)
    if neighbour is None:
        return None
    if _square_flat(neighbour, axis):
        return neighbour
    if fg.surface_type(neighbour) != _cone_type():
        return None

    # A lead-in or countersink. The face it opens onto is across the cone's
    # *other* ring -- the wider one, since a chamfer opens outwards.
    inner_radius = fg.circle_radius(ring)
    for edge in fg.face_edges(neighbour):
        if not fg.is_circular(edge):
            continue
        radius = fg.circle_radius(edge)
        if radius is None or inner_radius is None or radius <= inner_radius:
            continue
        beyond = fg.other_face(edge, neighbour)
        if beyond is not None and _square_flat(beyond, axis):
            return beyond
    return None


def _square_flat(face, axis):
    """A planar face at right angles to the bore, which a sketch can sit on."""
    if not fg.is_planar(face):
        return False
    try:
        normal = fg.outward_normal(face)
    except Exception:
        return False
    return geo.is_parallel(normal, axis, _ENTRANCE_TOLERANCE_DEG)


def _cone_type():
    return adsk.core.SurfaceTypes.ConeSurfaceType


def _format(value):
    return ("{:.3f}".format(value)).rstrip("0").rstrip(".")


# -- applying --------------------------------------------------------------


def apply(context, findings, params):
    outcomes = []
    start = _timeline_position(context)
    created = 0

    for finding in findings:
        if not finding.fixable:
            outcomes.append(Outcome.skipped(finding.id, finding.detail))
            continue
        live = context.resolve(finding) if context.resolve else finding.entities
        if not live:
            outcomes.append(Outcome.skipped(
                finding.id, "The geometry changed; re-check the model."
            ))
            continue
        outcome = _teardrop(context, finding, live[0], params)
        outcomes.append(outcome)
        if outcome.status == Outcome.APPLIED:
            created += 1

    if created > 1:
        _group_timeline(context, start, "Teardrop bores")
    return outcomes


def _teardrop(context, finding, face, params):
    component = fg.parent_component(face.body)
    if component is None:
        return Outcome.failed(finding.id, "Could not find the owning component.")

    origin, axis, radius = fg.cylinder_axis(face)
    entrance, start, end = _cut_span(face, axis)
    if entrance is None:
        return Outcome.failed(finding.id, "Lost the flat face the cut builds from.")

    direction = context.build_direction.vector

    # Everything is measured as a station along the bore axis, because neither
    # end of the cut is necessarily the bore's own end: a lead-in puts the
    # flat face a chamfer's depth further out at each end. Taking a ring's
    # centre as the circle centre would put the teardrop inside the material
    # rather than on the sketch plane.
    centre = geo.add(origin, geo.scale(axis, start - geo.dot(origin, axis)))
    depth = abs(end - start)
    if depth <= 0.0:
        return Outcome.failed(finding.id, "Could not measure how deep the bore runs.")
    into = geo.scale(axis, 1.0 if end > start else -1.0)

    sketch = None
    try:
        sketch = _draw(component, entrance, centre, radius, axis, direction, params)
        if sketch.profiles.count != 1:
            # One closed loop in an empty sketch is exactly one profile.
            # Anything else means the geometry came out wrong, and cutting a
            # profile picked by guesswork is how you ruin someone's model.
            count = sketch.profiles.count
            _delete(sketch)
            return Outcome.failed(
                finding.id,
                "The teardrop outline produced {} regions instead of 1.".format(count),
            )
        feature, problem = _cut(
            component, sketch.profiles.item(0), face.body, sketch, into, depth,
        )
    except Exception as exc:
        _delete(sketch)
        return Outcome.failed(finding.id, str(exc) or type(exc).__name__)

    if problem is not None:
        if feature is not None:
            fg.delete_feature(feature)
        _delete(sketch)
        return Outcome.failed(finding.id, problem)

    return Outcome.applied(
        finding.id,
        "Cut a {}° teardrop roof over {} mm of bore.".format(
            _format(params["roof_angle_deg"]), _format(geo.to_mm(depth))
        ),
        feature=feature,
    )


def _draw(component, entrance, centre, radius, axis, direction, params):
    """Sketch the teardrop outline: a 270° arc closed by two tangent lines."""
    # addWithoutEdges, because Fusion has a preference that auto-projects the
    # host face's edges into a new sketch. With it on, the sketch arrives
    # already full of geometry and the profile count becomes a lottery.
    sketch = component.sketches.addWithoutEdges(entrance)

    angle = params["roof_angle_deg"]
    up = geo.normalise(geo.project_onto_plane(direction, axis))
    across = geo.normalise(geo.cross(up, axis))

    (left_x, left_y), (right_x, right_y) = geo.teardrop_tangent_points(radius, angle)
    left = geo.add(centre, geo.add(geo.scale(across, left_x), geo.scale(up, left_y)))
    right = geo.add(centre, geo.add(geo.scale(across, right_x), geo.scale(up, right_y)))
    apex = geo.add(centre, geo.scale(up, geo.teardrop_apex_height(radius, angle)))
    bottom = geo.subtract(centre, geo.scale(up, radius))

    to_sketch = sketch.modelToSketchSpace
    sketch.isComputeDeferred = True
    try:
        # Three points, not a centre and a sweep: an arc built from a sweep
        # angle depends on which way round the sketch's axes happen to run,
        # and the bottom point says unambiguously which way round to go.
        sketch.sketchCurves.sketchArcs.addByThreePoints(
            to_sketch(fg.to_point(left)),
            to_sketch(fg.to_point(bottom)),
            to_sketch(fg.to_point(right)),
        )
        lines = sketch.sketchCurves.sketchLines
        lines.addByTwoPoints(to_sketch(fg.to_point(right)), to_sketch(fg.to_point(apex)))
        lines.addByTwoPoints(to_sketch(fg.to_point(apex)), to_sketch(fg.to_point(left)))
    finally:
        sketch.isComputeDeferred = False
    return sketch


def _cut(component, profile, body, sketch, into, depth):
    extrudes = component.features.extrudeFeatures
    extrude_input = extrudes.createInput(
        profile, adsk.fusion.FeatureOperations.CutFeatureOperation
    )
    # Only this body. A cut with no participants set will happily carve
    # through anything else that shares the space.
    extrude_input.participantBodies = [body]

    _set_extent(extrude_input, sketch, into, depth)
    feature = extrudes.add(extrude_input)
    return feature, fg.feature_problem(feature)


def _set_extent(extrude_input, sketch, into, depth):
    """Cut exactly the length of the bore, in the direction it runs.

    The extent is measured, never "through all": a blind bore would otherwise
    get a slot cut out of the far side of the part.
    """
    positive = _points_along_sketch_normal(sketch, into)
    distance = fg.value_input_mm(geo.to_mm(depth))
    try:
        definition = adsk.fusion.DistanceExtentDefinition.create(distance)
        extrude_input.setOneSideExtent(
            definition,
            adsk.fusion.ExtentDirections.PositiveExtentDirection if positive
            else adsk.fusion.ExtentDirections.NegativeExtentDirection,
        )
    except Exception:
        # Older extent API: the sign of the distance chooses the direction.
        signed = geo.to_mm(depth) * (1.0 if positive else -1.0)
        extrude_input.setDistanceExtent(False, fg.value_input_mm(signed))


def _points_along_sketch_normal(sketch, into):
    """Whether the bore runs along the sketch's normal or against it.

    A sketch's own Z need not agree with the face it was built on, so the
    direction is read from the sketch rather than assumed.
    """
    try:
        _origin, _x_axis, _y_axis, z_axis = sketch.transform.getAsCoordinateSystem()
        return geo.dot(into, fg.vector_tuple(z_axis)) > 0.0
    except Exception:
        return True


def _delete(sketch):
    if sketch is None:
        return
    try:
        sketch.deleteMe()
    except Exception:
        pass


def _timeline_position(context):
    try:
        return context.design.timeline.count
    except Exception:
        return None


def _group_timeline(context, start, name):
    if start is None:
        return
    try:
        timeline = context.design.timeline
        if timeline.count - start < 2:
            return
        timeline.timelineGroups.add(start, timeline.count - 1).name = name
    except Exception:
        pass
