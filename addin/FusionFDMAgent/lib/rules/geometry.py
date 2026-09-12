"""Pure geometry for the rules. No ``adsk`` import, on purpose.

Everything here works on plain tuples and floats, which is what makes the
interesting half of the rules testable outside Fusion. The thin layer that
actually touches Fusion objects lives in ``fusion_geom.py`` and calls into
this.

Units: functions take and return whatever they are given, except where a name
says otherwise. Fusion is centimetres internally and the tool boundary is
millimetres, so the conversions are here rather than scattered.
"""

import math

MM_PER_CM = 10.0

# Angles closer than this are treated as the same direction. Fusion's own
# geometry is exact enough that a tighter value would only make us brittle
# against floating point, and a looser one starts accepting real slopes as
# perpendicular.
DEFAULT_ANGLE_TOLERANCE_DEG = 1.0


# -- units -----------------------------------------------------------------


def to_mm(centimetres):
    return centimetres * MM_PER_CM


def to_cm(millimetres):
    return millimetres / MM_PER_CM


def area_to_mm2(square_centimetres):
    return square_centimetres * MM_PER_CM * MM_PER_CM


# -- vectors ---------------------------------------------------------------


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def subtract(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a, factor):
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def negate(a):
    return (-a[0], -a[1], -a[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def length(a):
    return math.sqrt(dot(a, a))


def distance(a, b):
    return length(subtract(a, b))


def normalise(a):
    magnitude = length(a)
    if magnitude == 0.0:
        raise ValueError("cannot normalise a zero-length vector")
    return scale(a, 1.0 / magnitude)


def angle_between_deg(a, b):
    """Angle between two vectors, 0-180 degrees."""
    magnitude = length(a) * length(b)
    if magnitude == 0.0:
        raise ValueError("cannot measure the angle of a zero-length vector")
    # Clamped because round-off can push the cosine just outside [-1, 1] for
    # near-parallel vectors, and acos raises rather than saturating.
    cosine = max(-1.0, min(1.0, dot(a, b) / magnitude))
    return math.degrees(math.acos(cosine))


def is_parallel(a, b, tolerance_deg=DEFAULT_ANGLE_TOLERANCE_DEG):
    """True for the same *or* opposite direction: an axis has no sign."""
    angle = angle_between_deg(a, b)
    return angle <= tolerance_deg or angle >= 180.0 - tolerance_deg


def is_same_direction(a, b, tolerance_deg=DEFAULT_ANGLE_TOLERANCE_DEG):
    return angle_between_deg(a, b) <= tolerance_deg


def is_perpendicular(a, b, tolerance_deg=DEFAULT_ANGLE_TOLERANCE_DEG):
    return abs(angle_between_deg(a, b) - 90.0) <= tolerance_deg


def project_onto_plane(vector, normal):
    """Component of ``vector`` lying in the plane with the given normal."""
    unit = normalise(normal)
    return subtract(vector, scale(unit, dot(vector, unit)))


def point_to_axis_distance(point, axis_origin, axis_direction):
    """Perpendicular distance from a point to an infinite line."""
    unit = normalise(axis_direction)
    offset = subtract(point, axis_origin)
    along = scale(unit, dot(offset, unit))
    return length(subtract(offset, along))


# -- teardrops -------------------------------------------------------------
#
# A teardrop replaces the unprintable flat roof of a horizontal bore with two
# sloped faces. The slope is measured from the build direction, because that
# is the angle an FDM printer actually cares about: a face within ~45 degrees
# of vertical bridges without support.
#
# In 2D, with the bore circle centred at the origin and +y along the build
# direction, each roof line is tangent to the circle and meets its partner at
# an apex directly above the centre. Tangency is the whole point -- it is what
# "preserving the original circular clearance" means, because a tangent line
# never crosses into the bore.


def teardrop_tangent_points(radius, roof_angle_deg=45.0):
    """The two points where the roof lines touch the bore circle.

    Returned left-first, in the 2D frame described above.
    """
    _check_teardrop(radius, roof_angle_deg)
    theta = math.radians(roof_angle_deg)
    x = radius * math.cos(theta)
    y = radius * math.sin(theta)
    return (-x, y), (x, y)


def teardrop_apex_height(radius, roof_angle_deg=45.0):
    """Height of the roof apex above the bore centre.

    ``radius / sin(theta)`` -- which is ``radius * sqrt(2)`` at the usual 45
    degrees.
    """
    _check_teardrop(radius, roof_angle_deg)
    return radius / math.sin(math.radians(roof_angle_deg))


def teardrop_added_height(radius, roof_angle_deg=45.0):
    """How much taller the teardrop is than the plain bore."""
    return teardrop_apex_height(radius, roof_angle_deg) - radius


def _check_teardrop(radius, roof_angle_deg):
    if radius <= 0.0:
        raise ValueError("radius must be positive")
    if not 0.0 < roof_angle_deg < 90.0:
        raise ValueError("roof angle must be between 0 and 90 degrees exclusive")


# -- distances between sampled curves --------------------------------------
#
# The thin-feature guard asks "how close does this boundary edge come to any
# other part of the same footprint?". Every edge involved is coplanar, and
# Fusion can sample a curve into points cheaply, so the question reduces to
# distance between two polylines -- which is plain arithmetic, and testable
# without a CAD kernel in the room.


def point_segment_distance(point, start, end):
    """Shortest distance from a point to a finite segment."""
    span = subtract(end, start)
    span_length_squared = dot(span, span)
    if span_length_squared == 0.0:
        return distance(point, start)
    t = dot(subtract(point, start), span) / span_length_squared
    t = clamp(t, 0.0, 1.0)
    return distance(point, add(start, scale(span, t)))


def polyline_min_distance(first, second):
    """Shortest distance between two sampled curves.

    Both directions are checked: the closest approach can fall in the middle
    of one polyline's segment while every sample of the other is further away.
    """
    if len(first) < 1 or len(second) < 1:
        return None
    shortest = None
    for points, segments in ((first, second), (second, first)):
        for point in points:
            if len(segments) == 1:
                candidate = distance(point, segments[0])
                shortest = candidate if shortest is None else min(shortest, candidate)
                continue
            for index in range(len(segments) - 1):
                candidate = point_segment_distance(
                    point, segments[index], segments[index + 1]
                )
                shortest = candidate if shortest is None else min(shortest, candidate)
    return shortest


def shares_endpoint(first, second, tolerance):
    """True when two sampled curves meet at an end, as neighbours in a loop do.

    Neighbouring edges touch, so their distance is zero and would fail every
    proximity test. They are excluded by this rather than by a distance floor,
    which would also excuse the thin walls the guard exists to catch.
    """
    ends_first = (first[0], first[-1])
    ends_second = (second[0], second[-1])
    for one in ends_first:
        for two in ends_second:
            if distance(one, two) <= tolerance:
                return True
    return False


# -- misc ------------------------------------------------------------------


def clamp(value, minimum=None, maximum=None):
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def round_mm(value, places=3):
    """Round a millimetre figure for display, without trailing -0.0."""
    result = round(value, places)
    return 0.0 if result == 0.0 else result
