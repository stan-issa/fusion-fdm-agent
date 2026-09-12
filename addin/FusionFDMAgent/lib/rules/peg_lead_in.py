"""Chamfer the end of a peg, so it finds its hole.

The companion to the lead-in on a hole, and the half people forget. A
chamfered hole and a square-ended peg still fight each other on assembly:
the peg has to be aligned to within the chamfer before it will start, and on
a printed part -- where the peg is a little oval and the hole a little
undersized -- that is exactly the tolerance nobody has.

Chamfering both halves doubles the misalignment the pair will swallow, and
costs nothing: the chamfer takes material only from the tip, so the fit
further down is the fit that was designed.

Which end is the question worth getting right. A peg's tip and its root are
both circular edges on the same cylinder, and they are told apart by which
way the material folds at them -- the tip is a convex corner, the root a
concave one where the peg meets what it stands on. Chamfering the root would
undercut the peg instead of leading it in.
"""

import adsk.fusion

from . import chamfer_op, fusion_geom as fg, geometry as geo, orientation
from . import signature as sig
from .base import Finding, Outcome

ID = "peg_lead_in"
TITLE = "Chamfer mating pegs"
DESCRIPTION = (
    "Chamfers the free end of pegs and pins so they start into a hole, "
    "leaving the diameter below the chamfer alone."
)
NEEDS_BUILD_DIRECTION = True

PARAMS = {
    "size_mm": {
        "default": 0.5, "min": 0.05, "max": 3.0,
        "label": "Lead-in", "unit": "mm",
    },
    "min_length_mm": {
        "default": 2.0, "min": 0.2, "max": 50.0,
        "label": "Min peg length", "unit": "mm",
    },
}

# A peg must be several times longer than the chamfer, or the chamfer is most
# of the peg.
LENGTH_FACTOR = 3.0

# How much of the radius a chamfer may take before the tip stops being a tip
# and starts being a cone.
MAX_RADIUS_FRACTION = 0.8


def detect(context, params):
    size_mm = params["size_mm"]
    direction = context.build_direction.vector
    restrict = _selected_tokens(context)
    findings = []

    for body in context.bodies:
        if not fg.is_native_body(body):
            continue
        bottom, _top = fg.bounding_box_extent(body, direction)
        for face in fg.body_faces(body):
            finding = _examine(
                context, body, face, params, direction, bottom, restrict, size_mm
            )
            if finding is not None:
                findings.append(finding)
    return findings


def _examine(context, body, face, params, direction, bottom, restrict, size_mm):
    if not fg.is_cylindrical(face) or fg.is_bore(face):
        return None  # a hole; hole_lead_in has that half
    if restrict is not None and fg.entity_token(face) not in restrict:
        return None
    if context.is_thread_face(face):
        return None  # the end of a thread is the thread's business
    if fg.is_ignored(face, ID):
        return None

    _origin, _axis, radius = fg.cylinder_axis(face)
    length = fg.cylinder_axial_length(face)
    if length is None:
        return None
    length_mm = geo.to_mm(length)
    radius_mm = geo.to_mm(radius)
    diameter_mm = radius_mm * 2.0

    identifier = sig.finding_id(ID, fg.face_signature(face))
    title = "{} — ⌀{} mm peg, {} mm long".format(
        body.name, _format(diameter_mm), _format(length_mm)
    )

    if length_mm < max(params["min_length_mm"], size_mm * LENGTH_FACTOR):
        return None  # too stubby to lead anything in

    if size_mm > radius_mm * MAX_RADIUS_FRACTION:
        return Finding(
            identifier, ID, title,
            detail=(
                "A {} mm chamfer would take most of a {} mm radius and turn "
                "the tip into a cone.".format(_format(size_mm), _format(radius_mm))
            ),
            body=body.name, fixable=False, entities=[], reveal=[face],
        )

    tips, note = _free_ends(face, direction, bottom)
    if not tips:
        if note:
            return Finding(
                identifier, ID, title, detail=note, body=body.name,
                fixable=False, entities=[], reveal=[face],
            )
        return None

    plural = "" if len(tips) == 1 else "s"
    return Finding(
        identifier,
        ID,
        title,
        detail="{} free end{} with a square corner.".format(len(tips), plural),
        body=body.name,
        fixable=True,
        fix_summary="Chamfer {} mm at {} end{}".format(
            _format(size_mm), len(tips), plural
        ),
        entities=tips,
        extra={"diameterMm": geo.round_mm(diameter_mm),
               "lengthMm": geo.round_mm(length_mm)},
    )


def _free_ends(face, direction, bottom):
    """The circular edges at a peg's free end, skipping its root.

    A peg's two ends look alike -- a circle where the cylinder meets a flat
    face -- and differ in which way the solid folds at them. The tip is a
    convex corner; the root is concave, where the peg rises out of whatever
    carries it. Chamfering the root would undercut the peg rather than lead
    anything into anything.
    """
    ends = []
    for edge in fg.face_edges(face):
        if not fg.is_circular(edge):
            continue
        neighbour = fg.other_face(edge, face)
        if neighbour is None:
            continue
        if fg.surface_type(neighbour) == _cone_type():
            return [], "Already chamfered."
        if not fg.is_planar(neighbour):
            continue

        angle = fg.interior_angle_deg(edge)
        if angle is None or angle >= 180.0:
            continue  # concave: this is where the peg joins its base

        # A peg standing on its tip has that face chamfered by bed_chamfer,
        # at the size elephant's foot wants rather than the size assembly
        # wants. Two chamfers on one edge is one too many.
        if orientation.is_bed_face(neighbour, direction, bottom):
            return [], (
                "Its end sits on the build plate, where the bed-contact rule "
                "has it."
            )
        ends.append(edge)
    return ends, ""


def _cone_type():
    import adsk.core

    return adsk.core.SurfaceTypes.ConeSurfaceType


def _selected_tokens(context):
    """Tokens of cylindrical faces the user has selected, or None for all.

    Which pegs actually mate with something is not a question geometry can
    answer, so a selection is how the user says.
    """
    tokens = set()
    for entity in (context.selection or []):
        candidate = adsk.fusion.BRepFace.cast(entity)
        if candidate is not None and fg.is_cylindrical(candidate):
            token = fg.entity_token(candidate)
            if token:
                tokens.add(token)
            continue
        edge = adsk.fusion.BRepEdge.cast(entity)
        if edge is not None:
            for index in range(edge.faces.count):
                neighbour = edge.faces.item(index)
                if fg.is_cylindrical(neighbour):
                    token = fg.entity_token(neighbour)
                    if token:
                        tokens.add(token)
    return tokens or None


def _format(value):
    return ("{:.3f}".format(value)).rstrip("0").rstrip(".")


def apply(context, findings, params):
    jobs, outcomes = [], []
    for finding in findings:
        if not finding.fixable or not finding.entities:
            outcomes.append(Outcome.skipped(
                finding.id, finding.detail or "Nothing to chamfer on this peg."
            ))
            continue
        jobs.append((finding, finding.entities, params["size_mm"]))
    return outcomes + chamfer_op.apply_chamfer(
        context, jobs, description="lead-in", expect=chamfer_op.REMOVES
    )
