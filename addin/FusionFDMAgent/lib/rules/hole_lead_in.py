"""Put a lead-in chamfer on the mouth of a bore.

A printed hole is rarely as round or as clean at its entrance as it is
further down: the first layer over the void droops, and the bed side spreads.
A small chamfer at the mouth gives a screw, dowel or pin something to find its
way into, and costs nothing in fit because it removes material only from the
top of the bore. The diameter below the chamfer is untouched, which is the
whole reason to chamfer rather than to open the hole out.

Which holes are assembly holes is not something geometry can answer, so the
rule does not try: it reports every plain bore it finds and leaves the choice
to the person ticking boxes in the panel -- or to a Fusion selection, which
narrows detection when one is active.
"""

import adsk.fusion

from . import chamfer_op, fusion_geom as fg, geometry as geo, signature as sig
from .base import Finding, Outcome

ID = "hole_lead_in"
TITLE = "Add lead-ins to holes"
DESCRIPTION = (
    "Chamfers the mouth of plain cylindrical bores so a fastener starts "
    "cleanly, leaving the bore diameter below the chamfer unchanged."
)
NEEDS_BUILD_DIRECTION = True

PARAMS = {
    "size_mm": {
        "default": 0.5, "min": 0.05, "max": 3.0,
        "label": "Lead-in", "unit": "mm",
    },
    "min_depth_mm": {
        "default": 2.0, "min": 0.2, "max": 50.0,
        "label": "Min bore depth", "unit": "mm",
    },
    "which_ends": {
        "default": "both", "choices": ("both", "top", "bed"),
        "label": "Ends",
    },
}

# A bore must be several times deeper than the chamfer, or the chamfer eats
# the whole thing and stops being a lead-in.
DEPTH_FACTOR = 3.0


def detect(context, params):
    size_mm = params["size_mm"]
    direction = context.build_direction.vector
    restrict = _selected_tokens(context)
    findings = []
    shallow = [0]

    for body in context.bodies:
        if not fg.is_native_body(body):
            continue
        for face in fg.body_faces(body):
            finding = _examine(
                context, body, face, params, direction, restrict, size_mm, shallow
            )
            if finding is not None:
                findings.append(finding)

    if shallow[0]:
        # Counted rather than dropped: a bore excluded for being shallow is a
        # decision the user may disagree with, and they can only disagree with
        # a decision they can see.
        context.note(
            "{} bore{} shallower than {} mm passed over.".format(
                shallow[0], "" if shallow[0] == 1 else "s",
                _format(params["min_depth_mm"]),
            )
        )
    if not findings and not shallow[0] and restrict:
        context.note("Nothing selected in Fusion is a plain cylindrical bore.")
    return findings


def _examine(context, body, face, params, direction, restrict, size_mm, shallow):
    if not fg.is_cylindrical(face) or not fg.is_bore(face):
        return None
    if restrict is not None and fg.entity_token(face) not in restrict:
        return None
    if context.is_thread_face(face):
        return None  # a threaded hole's mouth belongs to the thread, not to us

    _origin, _axis, radius = fg.cylinder_axis(face)
    depth = fg.cylinder_axial_length(face)
    if depth is None:
        return None
    depth_mm = geo.to_mm(depth)
    diameter_mm = geo.to_mm(radius * 2.0)

    identifier = sig.finding_id(ID, fg.face_signature(face))
    title = "{} — ⌀{} mm bore, {} mm deep".format(
        body.name, _format(diameter_mm), _format(depth_mm)
    )

    if depth_mm < max(params["min_depth_mm"], size_mm * DEPTH_FACTOR):
        shallow[0] += 1
        return None  # too shallow to lead into

    rings, note = _entrance_rings(face, direction, params["which_ends"])
    if not rings:
        if note:
            return Finding(
                identifier, ID, title, detail=note, body=body.name,
                fixable=False, entities=[], reveal=[face],
            )
        return None

    plural = "" if len(rings) == 1 else "s"
    return Finding(
        identifier,
        ID,
        title,
        detail="{} open end{} with no lead-in.".format(len(rings), plural),
        body=body.name,
        fixable=True,
        fix_summary="Chamfer {} mm at {} end{}".format(
            _format(size_mm), len(rings), plural
        ),
        entities=rings,
        extra={"diameterMm": geo.round_mm(diameter_mm), "depthMm": geo.round_mm(depth_mm)},
    )


def _entrance_rings(face, direction, which_ends):
    """Circular edges at the bore's mouth that could take a chamfer.

    An entrance that already opens into a cone has a lead-in, or a
    countersink; either way it is finished, and the bore is reported as such
    rather than chamfered twice.
    """
    candidates = []
    for edge in fg.face_edges(face):
        if not fg.is_circular(edge):
            continue
        neighbour = fg.other_face(edge, face)
        if neighbour is None:
            continue
        if fg.surface_type(neighbour) == _cone_type():
            return [], "Already has a lead-in or countersink."
        if not fg.is_planar(neighbour):
            continue
        candidates.append(edge)

    if which_ends == "both" or len(candidates) < 2:
        return candidates, ""
    return _pick_end(candidates, direction, which_ends), ""


def _pick_end(edges, direction, which_ends):
    """Keep only the highest or lowest ring along the build direction."""
    heights = []
    for edge in edges:
        centre = fg.circle_centre(edge)
        heights.append(geo.dot(centre, direction) if centre else 0.0)
    if max(heights) - min(heights) < 1e-6:
        # A horizontal bore has no top or bottom end; both are the same
        # height, so narrowing by end is meaningless and would silently drop
        # one at random.
        return edges
    wanted = max(heights) if which_ends == "top" else min(heights)
    return [
        edge for edge, height in zip(edges, heights)
        if abs(height - wanted) < 1e-6
    ]


def _cone_type():
    import adsk.core

    return adsk.core.SurfaceTypes.ConeSurfaceType


def _selected_tokens(context):
    """Tokens of cylindrical faces the user has selected, or None for all.

    A selection narrows the search to the holes the user pointed at, which is
    how "these two are assembly holes" gets expressed without a dialog.
    """
    entities = (context.selection or [])
    tokens = set()
    for entity in entities:
        face = adsk.fusion.BRepFace.cast(entity)
        if face is not None and fg.is_cylindrical(face):
            token = fg.entity_token(face)
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
                finding.id, finding.detail or "Nothing to chamfer on this bore."
            ))
            continue
        jobs.append((finding, finding.entities, params["size_mm"]))
    return outcomes + chamfer_op.apply_chamfer(
        context, jobs, description="lead-in", expect=chamfer_op.REMOVES
    )
