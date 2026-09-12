"""What holds a flat underside up.

Two rules care about downward-facing faces and mean different things by them.
A *ledge* is held along one edge and hangs over air for the rest of its
reach; a *bridge* is held at two opposite edges and sags in the middle. The
remedies are different -- a gusset filling one corner, or ribs standing under
the span -- so the rules have to agree on which is which, or both will act on
the same face and the part will get two fixes for one problem.

Nothing here decides anything; it measures. The rules apply their own
thresholds to what it reports.
"""

from . import fusion_geom as fg
from . import geometry as geo

# How square to the build direction a face must be to count as a wall.
WALL_TOLERANCE_DEG = 5.0

# A face this close to the bottom of the body is standing on the bed rather
# than hanging over air. Centimetres.
BED_TOLERANCE = 0.001

# Two walls count as facing each other within this much of being opposite.
OPPOSED_TOLERANCE_DEG = 5.0

# Below this a wall is not really carrying on downwards at all. Millimetres.
MIN_DEPTH_MM = 0.1


def is_underside(face, direction, max_tilt_deg, bottom):
    """A flat face hanging over air, rather than the one resting on the bed."""
    if not fg.is_planar(face):
        return False
    try:
        normal = fg.outward_normal(face)
    except Exception:
        return False
    if not geo.is_same_direction(normal, geo.negate(direction), max_tilt_deg):
        return False
    # The bottom of the part is not an overhang; it is what the part stands on.
    return fg.plane_offset(face, direction) > bottom + BED_TOLERANCE


class Support:
    """One edge of an underside that a wall carries."""

    def __init__(self, edge, wall, normal, depth_mm):
        self.edge = edge
        self.wall = wall
        # The wall's outward normal, which points away from its material and
        # therefore across the opening the underside spans.
        self.normal = normal
        self.depth_mm = depth_mm

    def station(self):
        """Where the wall's plane sits along its own normal."""
        return fg.plane_offset(self.wall, self.normal)


def supports(face, direction):
    """The edges of an underside that are actually held up.

    An edge only counts when its wall carries on *below* the underside. That
    single test is what separates the inner edge of a ledge from its outer
    one: the face at the outer edge rises from it and has nothing underneath,
    so there is nothing down there holding anything.
    """
    found = []
    for edge in fg.face_edges(face):
        if not fg.is_straight(edge):
            continue
        wall = fg.other_face(edge, face)
        if wall is None or not fg.is_planar(wall):
            continue
        try:
            normal = fg.outward_normal(wall)
        except Exception:
            continue
        if not geo.is_perpendicular(normal, direction, WALL_TOLERANCE_DEG):
            continue
        depth_mm = depth_below(wall, edge, direction)
        if depth_mm <= MIN_DEPTH_MM:
            continue
        found.append(Support(edge, wall, normal, depth_mm))
    return found


def depth_below(wall, edge, direction):
    """How far a wall carries on below an edge, in millimetres."""
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


def opposed(found, tolerance_deg=OPPOSED_TOLERANCE_DEG):
    """Two supports facing each other across the underside, or None.

    This is the test for "bridge rather than ledge", and it is what keeps the
    two rules off each other's faces.
    """
    for index, first in enumerate(found):
        for second in found[index + 1:]:
            if geo.angle_between_deg(first.normal, second.normal) >= 180.0 - tolerance_deg:
                return first, second
    return None


def span_mm(first, second):
    """The clear distance between two opposed supports, in millimetres.

    Measured along the first wall's outward normal, which points across the
    opening because it points away from its own material.
    """
    return geo.to_mm(
        fg.plane_offset(second.wall, first.normal) - fg.plane_offset(first.wall, first.normal)
    )


def extent(face, axis):
    """``(min, max)`` of a face along a direction, from its sampled edges."""
    low = high = None
    for edge in fg.face_edges(face):
        for point in fg.edge_points(edge):
            station = geo.dot(point, axis)
            if low is None or station < low:
                low = station
            if high is None or station > high:
                high = station
    return low, high


def rib_positions(span, maximum, count=None):
    """Where to stand ribs so no remaining span exceeds the maximum.

    Fractions of the span, evenly spaced. A 60 mm bridge against a 20 mm
    limit needs two, a third and two thirds of the way across -- 20 mm and
    40 mm -- because three equal spans of 20 are the fewest that fit.
    """
    if span <= 0.0 or maximum <= 0.0 or span <= maximum:
        return []
    if count is None:
        # ceil(span / maximum) pieces, so one fewer divider between them.
        pieces = int(span / maximum)
        if pieces * maximum < span:
            pieces += 1
        count = pieces - 1
    return [(index + 1) / float(count + 1) for index in range(count)]
