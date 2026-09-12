"""Stand removable ribs under a bridge that is too long to span.

FDM can bridge a gap by stringing filament between two anchors, and does it
well up to a point. Past that the strands sag before they cool, and the
underside of the bridge comes out drooping, with the first solid layers above
it printing onto a wavy surface.

The remedy here is scaffolding rather than geometry: thin walls standing on
the build plate that divide one long span into several short ones. They are
separate bodies, deliberately, because they are meant to be snapped off and
thrown away -- and because a support that had been merged into the part would
be nothing but a defect.

Two details make them removable rather than permanent. Each stops a hair
short of the bridge, so the part rests on it without fusing to it, and each
runs out past the side of the part to give a grip to pull on. A support that
cannot be gripped is a support that gets dug out with pliers.

Scope, for now: bridges with open space all the way down to the plate and a
clear path out sideways. Anything else is reported and left alone.
"""

import adsk.fusion

from . import fusion_geom as fg, geometry as geo, signature as sig, underside
from .base import Finding, Outcome

ID = "bridge_ribs"
TITLE = "Rib long bridges"
DESCRIPTION = (
    "Divides a bridge too long to span cleanly with thin removable walls "
    "standing on the build plate, each with a top gap and a grip tab."
)
NEEDS_BUILD_DIRECTION = True

PARAMS = {
    "max_span_mm": {
        "default": 20.0, "min": 2.0, "max": 200.0,
        "label": "Max span", "unit": "mm",
    },
    "thickness_mm": {
        "default": 0.8, "min": 0.2, "max": 5.0,
        "label": "Rib thickness", "unit": "mm",
    },
    "top_gap_mm": {
        "default": 0.15, "min": 0.0, "max": 1.0,
        "label": "Top gap", "unit": "mm",
    },
    "tab_mm": {
        "default": 5.0, "min": 0.0, "max": 30.0,
        "label": "Grip tab", "unit": "mm",
    },
    "max_tilt_deg": {
        "default": 5.0, "min": 0.1, "max": 30.0,
        "label": "Max tilt from flat", "unit": "°",
    },
}

# Where to probe for material in the way, as fractions across the rib and up
# its height. Clear of the ends, so a probe does not land on a boundary.
_ACROSS = (0.15, 0.5, 0.85)
_UP = (0.1, 0.5, 0.9)

# How far past the side of the part a grip tab has to be clear to be grippable.
_TAB_CLEARANCE = 0.5


def detect(context, params):
    direction = context.build_direction.vector
    findings = []

    for body in context.bodies:
        if not fg.is_native_body(body):
            continue
        bottom, _top = fg.bounding_box_extent(body, direction)
        existing = _existing_supports(body)
        for face in fg.body_faces(body):
            if not underside.is_underside(face, direction, params["max_tilt_deg"], bottom):
                continue
            finding = _examine(
                context, body, face, params, direction, bottom, existing
            )
            if finding is not None:
                findings.append(finding)
    return findings


def _examine(context, body, face, params, direction, bottom, existing):
    if fg.is_ignored(face, ID):
        return None

    pair = underside.opposed(underside.supports(face, direction))
    if pair is None:
        return None  # held at one edge or none: a ledge, not a bridge
    near, far = pair

    span = underside.span_mm(near, far)
    if span <= 0.0:
        return None
    if span <= params["max_span_mm"]:
        return None  # short enough to bridge cleanly

    identifier = sig.finding_id(ID, fg.face_signature(face))
    fractions = underside.rib_positions(span, params["max_span_mm"])
    title = "{} — {} mm bridge".format(body.name, _format(span))

    plan, reason = _plan(
        body, face, params, direction, bottom, near, span, fractions
    )
    if plan is None:
        return Finding(
            identifier, ID, title, detail=reason, body=body.name,
            fixable=False, entities=[], reveal=[face],
        )
    if _already_ribbed(existing, plan):
        return Finding(
            identifier, ID, title,
            detail="Already has supports standing under it.",
            body=body.name, fixable=False, entities=[], reveal=[face],
        )

    return Finding(
        identifier,
        ID,
        title,
        detail="Spans {} mm unsupported, past the {} mm limit. {} into {} spans of {} mm.".format(
            _format(span), _format(params["max_span_mm"]),
            "One rib divides it" if len(plan) == 1
            else "{} ribs divide it".format(len(plan)),
            len(plan) + 1, _format(span / (len(plan) + 1)),
        ),
        body=body.name,
        fixable=True,
        fix_summary="Add {} removable support{}".format(
            len(plan), "" if len(plan) == 1 else "s"
        ),
        entities=[face],
        extra={"spanMm": geo.round_mm(span), "ribs": len(plan)},
    )


class _Rib:
    """One wall, as a box waiting to be built. Centimetres throughout."""

    def __init__(self, centre, length_direction, width_direction,
                 length, width, height):
        self.centre = centre
        self.length_direction = length_direction
        self.width_direction = width_direction
        self.length = length
        self.width = width
        self.height = height

    def build(self):
        return fg.create_box(
            self.centre, self.length_direction, self.width_direction,
            self.length, self.width, self.height,
        )


def _plan(body, face, params, direction, bottom, near, span, fractions):
    """Work out where the ribs go, or say why they cannot. Returns (plan, why)."""
    across = _across_direction(direction, near.normal)
    if across is None:
        return None, "Could not work out which way the bridge runs."

    low, high = underside.extent(face, across)
    if low is None:
        return None, "Could not measure the width of the bridge."

    bridge_station = fg.plane_offset(face, direction)
    height = bridge_station - bottom - geo.to_cm(params["top_gap_mm"])
    if height <= 0.0:
        return None, "There is no room between the bridge and the build plate."

    start = fg.plane_offset(near.wall, near.normal)
    middle_along = start + geo.to_cm(span) / 2.0
    middle_up = (bridge_station + bottom) / 2.0

    tab = geo.to_cm(params["tab_mm"])
    reach_low, reach_high = _grip_reach(
        body, params, direction, near.normal, across,
        low, high, tab, middle_along, middle_up,
    )
    if tab > 0.0 and reach_low == low and reach_high == high:
        return None, (
            "Nowhere to put a grip tab: the sides of the bridge are enclosed, "
            "so a support could not be pulled out."
        )

    thickness = geo.to_cm(params["thickness_mm"])
    length = reach_high - reach_low
    centre_across = (reach_low + reach_high) / 2.0
    centre_up = bottom + height / 2.0

    plan = []
    for fraction in fractions:
        station = start + geo.to_cm(span) * fraction
        centre = _point(direction, near.normal, across, centre_up, station, centre_across)
        rib = _Rib(centre, across, near.normal, length, thickness, height)
        if not _space_is_clear(body, rib, direction, across):
            return None, (
                "Something stands in the way beneath this bridge, so a support "
                "could not reach the build plate."
            )
        plan.append(rib)
    if not plan:
        return None, "Nothing to divide."
    return plan, ""


def _point(direction, span_normal, across, up, along, sideways):
    """Rebuild a point from its three station values."""
    return geo.add(
        geo.add(geo.scale(direction, up), geo.scale(span_normal, along)),
        geo.scale(across, sideways),
    )


def _across_direction(direction, span_normal):
    try:
        return geo.normalise(geo.cross(direction, span_normal))
    except ValueError:
        return None


def _grip_reach(body, params, direction, span_normal, across,
                low, high, tab, middle_along, middle_up):
    """How far the rib may run past each side of the bridge.

    A tab is only useful where there is open air beyond the side of the part
    to reach into, so each end is probed before it is extended. Reaching into
    solid material would not be a grip; it would be a collision.
    """
    if tab <= 0.0:
        return low, high
    reach_low, reach_high = low, high
    for sign in (-1.0, 1.0):
        station = (high if sign > 0 else low) + sign * tab * (1.0 + _TAB_CLEARANCE)
        probe = _point(
            direction, span_normal, across, middle_up, middle_along, station
        )
        if fg.is_outside(body, probe):
            if sign > 0:
                reach_high = high + tab
            else:
                reach_low = low - tab
    return reach_low, reach_high


def _space_is_clear(body, rib, direction, across):
    """Whether the rib's column is empty all the way down to the plate."""
    half_length = rib.length / 2.0
    for sideways in _ACROSS:
        along = -half_length + rib.length * sideways
        for upward in _UP:
            lift = -rib.height / 2.0 + rib.height * upward
            probe = geo.add(
                rib.centre,
                geo.add(
                    geo.scale(across, along),
                    geo.scale(direction, lift),
                ),
            )
            if not fg.is_outside(body, probe):
                return False
    return True


def _existing_supports(body):
    """Bounding boxes of the supports already standing in this component."""
    component = fg.parent_component(body)
    boxes = []
    if component is None:
        return boxes
    try:
        bodies = component.bRepBodies
    except Exception:
        return boxes
    for index in range(bodies.count):
        other = bodies.item(index)
        if not fg.is_support_body(other):
            continue
        try:
            box = other.boundingBox
            boxes.append((
                fg.point_tuple(box.minPoint), fg.point_tuple(box.maxPoint)
            ))
        except Exception:
            continue
    return boxes


def _already_ribbed(existing, plan):
    """Whether a support already stands where one of these ribs would."""
    for rib in plan:
        for low, high in existing:
            if all(
                low[axis] - 0.1 <= rib.centre[axis] <= high[axis] + 0.1
                for axis in (0, 1, 2)
            ):
                return True
    return False


def _format(value):
    return ("{:.3f}".format(value)).rstrip("0").rstrip(".")


# -- applying --------------------------------------------------------------


def apply(context, findings, params):
    outcomes = []
    for finding in findings:
        if not finding.fixable or not finding.entities:
            outcomes.append(Outcome.skipped(
                finding.id, finding.detail or "No supports to add here."
            ))
            continue
        live = context.resolve(finding) if context.resolve else finding.entities
        if not live:
            outcomes.append(Outcome.skipped(
                finding.id, "The geometry changed; re-check the model."
            ))
            continue
        outcomes.append(_build(context, finding, live[0], params))
    return outcomes


def _build(context, finding, face, params):
    body = face.body
    component = fg.parent_component(body)
    if component is None:
        return Outcome.failed(finding.id, "Could not find the owning component.")

    direction = context.build_direction.vector
    bottom, _top = fg.bounding_box_extent(body, direction)
    pair = underside.opposed(underside.supports(face, direction))
    if pair is None:
        return Outcome.failed(finding.id, "This is no longer a bridge.")

    near, _far = pair
    span = underside.span_mm(near, pair[1])
    fractions = underside.rib_positions(span, params["max_span_mm"])
    plan, reason = _plan(
        body, face, params, direction, bottom, near, span, fractions
    )
    if plan is None:
        return Outcome.failed(finding.id, reason)

    number = fg.next_support_number(component)
    temporary = []
    for offset, rib in enumerate(plan):
        built = rib.build()
        if built is None:
            return Outcome.failed(finding.id, "Could not build the support solid.")
        temporary.append((built, "{} {}".format(fg.SUPPORT_PREFIX, number + offset)))

    feature, bodies, problem = fg.add_bodies(component, temporary)
    if problem is not None:
        if feature is not None:
            fg.delete_feature(feature)
        return Outcome.failed(finding.id, problem)

    return Outcome.applied(
        finding.id,
        "Added {} support{} ({} mm thick, {} mm gap) as separate bodies: {}.".format(
            len(bodies), "" if len(bodies) == 1 else "s",
            _format(params["thickness_mm"]), _format(params["top_gap_mm"]),
            ", ".join(item.name for item in bodies),
        ),
        feature=feature,
    )
