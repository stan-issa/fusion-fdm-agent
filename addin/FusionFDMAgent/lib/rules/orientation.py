"""Which way is up, and which faces touch the bed.

Two of the three rules are meaningless without a build direction, so it is
resolved once per check and carried on the ``RuleContext``. It is never
guessed silently: the result records *how* it was decided so the panel and the
agent can both say so, and the user can correct it by selecting the bed face.
"""

import adsk.fusion

from . import fusion_geom as fg
from . import geometry as geo

# A face is "on the bed" if it is this close to the lowest point of its body.
# Centimetres, so 10 microns -- tight enough that a step is not mistaken for
# the bed, loose enough to absorb modelling round-off.
BED_PLANE_TOLERANCE = 0.001

# How far a face's normal may stray from straight down and still be the bed.
BED_NORMAL_TOLERANCE_DEG = 1.0

# The fallback when nothing is selected and nothing can be inferred: Fusion's
# Z, which is what a part modelled the obvious way prints along.
DEFAULT_DIRECTION = (0.0, 0.0, 1.0)


class BuildDirection:
    """The direction the printer builds in: away from the bed, "up"."""

    SELECTION = "selection"
    INFERRED = "inferred"
    DEFAULT = "default"

    def __init__(self, vector, source, face=None, body=None):
        self.vector = geo.normalise(vector)
        self.source = source
        self.face = face
        self.body = body

    @property
    def label(self):
        described = _axis_label(self.vector)
        if self.source == self.SELECTION:
            return "{} (from the face you selected)".format(described)
        if self.source == self.INFERRED:
            return "{} (inferred from the lowest face of {})".format(
                described, self.body or "the model"
            )
        return "{} (assumed; nothing selected and no flat bottom found)".format(described)

    def as_dict(self):
        return {
            "vector": [round(value, 6) for value in self.vector],
            "source": self.source,
            "label": self.label,
            "body": self.body,
        }


def _axis_label(vector):
    for name, axis in (
        ("+Z", (0.0, 0.0, 1.0)), ("-Z", (0.0, 0.0, -1.0)),
        ("+Y", (0.0, 1.0, 0.0)), ("-Y", (0.0, -1.0, 0.0)),
        ("+X", (1.0, 0.0, 0.0)), ("-X", (-1.0, 0.0, 0.0)),
    ):
        if geo.is_same_direction(vector, axis, 1.0):
            return name
    return "({:.3f}, {:.3f}, {:.3f})".format(*vector)


# -- resolution ------------------------------------------------------------


def resolve(bodies, selected_entities=None):
    """Decide the build direction: selection first, then inference."""
    selected = _selected_planar_face(selected_entities or [])
    if selected is not None:
        # The bed face points down and out of the part; the printer builds the
        # other way.
        return BuildDirection(
            geo.negate(fg.outward_normal(selected)),
            BuildDirection.SELECTION,
            face=selected,
            body=fg.body_name(selected),
        )

    inferred = _infer(bodies)
    if inferred is not None:
        return inferred

    return BuildDirection(DEFAULT_DIRECTION, BuildDirection.DEFAULT)


def _selected_planar_face(entities):
    for entity in entities:
        face = adsk.fusion.BRepFace.cast(entity)
        if face is not None and fg.is_planar(face):
            return face
    return None


def _infer(bodies):
    """The largest downward-facing planar face at the bottom of the model."""
    best = None
    for body in bodies:
        if not fg.is_native_body(body):
            continue
        low, _high = fg.bounding_box_extent(body, DEFAULT_DIRECTION)
        for face in fg.body_faces(body):
            if not fg.is_planar(face):
                continue
            try:
                normal = fg.outward_normal(face)
            except Exception:
                continue
            if not geo.is_same_direction(
                normal, geo.negate(DEFAULT_DIRECTION), BED_NORMAL_TOLERANCE_DEG
            ):
                continue
            offset = fg.plane_offset(face, DEFAULT_DIRECTION)
            if offset > low + BED_PLANE_TOLERANCE:
                continue
            area = fg.face_area(face)
            if best is None or area > best[0]:
                best = (area, face, body)

    if best is None:
        return None
    _area, face, body = best
    return BuildDirection(
        DEFAULT_DIRECTION, BuildDirection.INFERRED, face=face, body=body.name
    )


# -- the bed ---------------------------------------------------------------


def bed_faces(body, direction):
    """Every planar face of a body lying in its lowest plane.

    Deliberately a set rather than a single face: an earlier feature can split
    what a person sees as "the bottom" into several coplanar faces, and taking
    only one of them would treat the seams between them as footprint edges and
    chamfer lines across the middle of the part.
    """
    down = geo.negate(direction)
    low, _high = fg.bounding_box_extent(body, direction)
    faces = []
    for face in fg.body_faces(body):
        if not fg.is_planar(face):
            continue
        try:
            normal = fg.outward_normal(face)
        except Exception:
            continue
        if not geo.is_same_direction(normal, down, BED_NORMAL_TOLERANCE_DEG):
            continue
        if fg.plane_offset(face, direction) > low + BED_PLANE_TOLERANCE:
            continue
        faces.append(face)
    return faces


def is_bed_face(face, direction, bed_offset):
    """The geometric test for membership of the bed plane.

    Used instead of entity identity because Fusion hands back a fresh proxy
    object on every traversal, so two references to the same face compare
    unequal and cannot be put in a set.
    """
    if not fg.is_planar(face):
        return False
    try:
        normal = fg.outward_normal(face)
    except Exception:
        return False
    if not geo.is_same_direction(normal, geo.negate(direction), BED_NORMAL_TOLERANCE_DEG):
        return False
    return abs(fg.plane_offset(face, direction) - bed_offset) <= BED_PLANE_TOLERANCE


def footprint_edges(faces, direction):
    """The silhouette of the part where it meets the bed.

    An edge between two coplanar bed faces is an internal seam, not part of
    the footprint, so an edge only counts when exactly one of the two faces
    meeting at it is on the bed.
    """
    if not faces:
        return []
    bed_offset = fg.plane_offset(faces[0], direction)
    edges = []
    for face in faces:
        for edge in fg.outer_loop_edges(face):
            neighbour = fg.other_face(edge, face)
            if neighbour is None:
                continue
            if is_bed_face(neighbour, direction, bed_offset):
                continue  # a seam between two halves of the same flat bottom
            edges.append(edge)
    return edges


# -- describing a selection ------------------------------------------------


def describe_entity(entity):
    """A compact, JSON-safe description of one selected entity."""
    face = adsk.fusion.BRepFace.cast(entity)
    if face is not None:
        described = {
            "type": "face",
            "surface": _surface_name(face),
            "body": fg.body_name(face),
            "areaMm2": geo.round_mm(geo.area_to_mm2(fg.face_area(face))),
        }
        try:
            described["normal"] = [round(v, 4) for v in fg.outward_normal(face)]
        except Exception:
            pass
        if fg.is_cylindrical(face):
            origin, axis, radius = fg.cylinder_axis(face)
            described["diameterMm"] = geo.round_mm(geo.to_mm(radius * 2.0))
            described["axis"] = [round(v, 4) for v in axis]
            described["isBore"] = fg.is_bore(face)
        return described

    edge = adsk.fusion.BRepEdge.cast(entity)
    if edge is not None:
        described = {
            "type": "edge",
            "body": fg.body_name(edge),
            "lengthMm": geo.round_mm(geo.to_mm(fg.edge_length(edge))),
            "circular": fg.is_circular(edge),
        }
        if fg.is_circular(edge):
            described["diameterMm"] = geo.round_mm(
                geo.to_mm((fg.circle_radius(edge) or 0.0) * 2.0)
            )
        return described

    body = adsk.fusion.BRepBody.cast(entity)
    if body is not None:
        return {"type": "body", "name": body.name, "faces": body.faces.count}

    occurrence = adsk.fusion.Occurrence.cast(entity)
    if occurrence is not None:
        return {"type": "occurrence", "name": occurrence.name}

    return {"type": type(entity).__name__}


_SURFACE_NAMES = {}


def _surface_name(face):
    import adsk.core

    if not _SURFACE_NAMES:
        for name in dir(adsk.core.SurfaceTypes):
            if name.endswith("SurfaceType"):
                _SURFACE_NAMES[getattr(adsk.core.SurfaceTypes, name)] = (
                    name[: -len("SurfaceType")].lower()
                )
    return _SURFACE_NAMES.get(fg.surface_type(face), "unknown")
