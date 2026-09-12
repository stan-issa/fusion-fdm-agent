"""Chamfer the edges where the part meets the print bed.

The first layer of an FDM print is squashed into the bed, so its outline
spreads a little wider than the model -- elephant's foot. A small chamfer on
the footprint gives that squashed material somewhere to go, and the part sits
flat against a mating surface instead of rocking on a lip.

The whole difficulty is knowing which edges *not* to touch. A 0.3 mm chamfer
taken off both sides of a 0.6 mm fin removes the fin. So every candidate is
measured against its neighbours first, and every rejection is reported with
its reason -- a guard that silently drops edges is indistinguishable from a
bug.
"""

from . import chamfer_op, fusion_geom as fg, geometry as geo, orientation, signature as sig
from .base import Finding, Outcome

ID = "bed_chamfer"
TITLE = "Chamfer bed-contact edges"
DESCRIPTION = (
    "Relieves elephant's foot by chamfering the outline where the part touches "
    "the bed, skipping edges too thin to survive it."
)
NEEDS_BUILD_DIRECTION = True

PARAMS = {
    "size_mm": {
        "default": 0.3, "min": 0.05, "max": 3.0,
        "label": "Chamfer", "unit": "mm",
    },
    "min_wall_mm": {
        "default": 1.0, "min": 0.1, "max": 10.0,
        "label": "Min wall", "unit": "mm",
    },
}

# Above this interior angle the edge is already chamfered, filleted or simply
# shallow, and cutting it again would do nothing useful. One threshold covers
# all three: a fillet running tangentially into the bed reads as ~180 degrees,
# an existing 45 degree chamfer as 135, a sharp corner as 90.
RELIEVED_ABOVE_DEG = 100.0

# An edge must be a few times longer than the chamfer for the result to mean
# anything, and Fusion tends to refuse below that anyway.
MIN_LENGTH_FACTOR = 2.0

# How much clear space a chamfer needs beside it, as a multiple of its size.
CLEARANCE_FACTOR = 2.5

# Tolerance for deciding two sampled edges meet end to end. Centimetres.
_JOIN_TOLERANCE = 0.002


def detect(context, params):
    size_mm = params["size_mm"]
    direction = context.build_direction.vector
    findings = []

    for body in context.bodies:
        if not fg.is_native_body(body):
            continue
        faces = orientation.bed_faces(body, direction)
        if not faces:
            continue
        edges = orientation.footprint_edges(faces, direction)
        if not edges:
            continue

        keep, skipped = _sift(context, body, edges, params, direction)
        if not keep and not skipped:
            continue

        identifier = sig.finding_id(ID, _signature(body, faces, direction))
        findings.append(_finding(identifier, body, faces, keep, skipped, size_mm))

    return findings


def _finding(identifier, body, faces, keep, skipped, size_mm):
    plural = "" if len(keep) == 1 else "s"
    where = "bottom face" if len(faces) == 1 else "bottom faces"
    return Finding(
        identifier,
        ID,
        "{} — {} bed-contact edge{}".format(body.name, len(keep), plural),
        detail="Outline of the {} where the part meets the bed.".format(where),
        body=body.name,
        fixable=bool(keep),
        fix_summary="Chamfer {} mm on {} edge{}".format(
            _format(size_mm), len(keep), plural
        ),
        entities=keep,
        # When every edge was skipped there is nothing to chamfer, but the
        # user still wants to see which face the rule was talking about before
        # deciding whether they agree with it.
        reveal=keep or faces,
        skipped=skipped,
    )


def _sift(context, body, edges, params, direction):
    """Split the footprint into edges worth chamfering and edges to leave.

    Returns ``(keep, skipped)`` where skipped is ``[(what, why)]``.
    """
    size_mm = params["size_mm"]
    clearance_mm = max(params["min_wall_mm"], size_mm * CLEARANCE_FACTOR)
    sampled = [fg.edge_points(edge) for edge in edges]

    keep, skipped = [], []
    for index, edge in enumerate(edges):
        reason = _reject(context, edge, index, edges, sampled, size_mm, clearance_mm, direction)
        if reason is None:
            keep.append(edge)
        else:
            skipped.append((_describe_edge(edge), reason))
    return keep, skipped


def _reject(context, edge, index, edges, sampled, size_mm, clearance_mm, direction):
    length_mm = geo.to_mm(fg.edge_length(edge))
    if length_mm < size_mm * MIN_LENGTH_FACTOR:
        return "only {} mm long, shorter than the chamfer needs".format(
            _format(length_mm)
        )

    angle = fg.interior_angle_deg(edge)
    if angle is not None and angle > RELIEVED_ABOVE_DEG:
        return "already relieved ({}° between the faces)".format(round(angle))

    wall_mm = _wall_height(edge, direction)
    if wall_mm is not None and wall_mm < size_mm:
        return "the wall above it is only {} mm tall".format(_format(wall_mm))

    gap_mm = _nearest_neighbour(index, sampled)
    if gap_mm is not None and gap_mm < clearance_mm:
        return "{} mm from the nearest other edge, thinner than {} mm".format(
            _format(gap_mm), _format(clearance_mm)
        )
    return None


def _wall_height(edge, direction):
    """How tall the wall standing on this edge is, along the build direction.

    A chamfer taller than the wall it is cut into consumes the whole wall, and
    Fusion reports that as an opaque failure rather than a useful one. The
    wall is whichever face at this edge is not lying in the bed plane.
    """
    down = geo.negate(direction)
    tallest = None
    for index in range(edge.faces.count):
        face = edge.faces.item(index)
        try:
            if fg.is_planar(face) and geo.is_same_direction(
                fg.outward_normal(face), down, orientation.BED_NORMAL_TOLERANCE_DEG
            ):
                continue  # this is the bed itself, not the wall on it
        except Exception:
            pass
        span = _extent_along(face, direction)
        if span is not None and (tallest is None or span > tallest):
            tallest = span
    return geo.to_mm(tallest) if tallest is not None else None


def _extent_along(face, direction):
    """A face's bounding-box extent projected onto a direction."""
    try:
        box = face.boundingBox
        low, high = box.minPoint, box.maxPoint
    except Exception:
        return None
    projections = [
        geo.dot((x, y, z), direction)
        for x in (low.x, high.x)
        for y in (low.y, high.y)
        for z in (low.z, high.z)
    ]
    return max(projections) - min(projections)


def _nearest_neighbour(index, sampled):
    """Distance in mm to the closest non-adjoining edge of the same footprint.

    Neighbouring edges share a vertex, so their distance is zero; they are
    excluded by the shared endpoint rather than by a floor on the distance,
    which would also excuse the thin walls this exists to find.
    """
    this = sampled[index]
    shortest = None
    for other_index, other in enumerate(sampled):
        if other_index == index:
            continue
        if geo.shares_endpoint(this, other, _JOIN_TOLERANCE):
            continue
        gap = geo.polyline_min_distance(this, other)
        if gap is not None and (shortest is None or gap < shortest):
            shortest = gap
    return geo.to_mm(shortest) if shortest is not None else None


def _signature(body, faces, direction):
    return sig.make(
        "bed", body.name,
        surface="plane",
        normal=geo.negate(direction),
        offset=geo.to_mm(fg.plane_offset(faces[0], direction)),
        count=len(faces),
    )


def _describe_edge(edge):
    return "{} mm edge".format(_format(geo.to_mm(fg.edge_length(edge))))


def _format(value):
    return ("{:.3f}".format(value)).rstrip("0").rstrip(".")


def apply(context, findings, params):
    jobs, outcomes = [], []
    for finding in findings:
        if not finding.entities:
            outcomes.append(Outcome.skipped(
                finding.id, "Every edge was skipped, so there is nothing to chamfer."
            ))
            continue
        jobs.append((finding, finding.entities, params["size_mm"]))
    return outcomes + chamfer_op.apply_chamfer(
        context, jobs, expect=chamfer_op.REMOVES
    )
