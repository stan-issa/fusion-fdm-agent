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

The profile is shaped so that stability and removability can be tuned
separately, which a plain wall does not allow. From the bed up: a wide thin
flange for bed adhesion, a body thick enough not to flex, a short taper, and
a narrow ridge under the bridge. Widening the body to resist tipping then
costs nothing at the top, where the only thing that matters is how little
surface can weld itself to the part.

Separate bodies in Fusion do *not* keep two surfaces apart in the printer, so
release is a gap rather than a hope: a vertical clearance under the ridge, and
a side clearance anywhere the rib would otherwise touch a wall. A larger top
gap releases more easily and leaves a worse underside; the defaults below are
a starting experiment for PLA on a 0.4 mm nozzle at 0.2 mm layers, not a
promise. Calibrate them and keep the result -- the settings file is the
preset.

Scope, for now: bridges with open space all the way down to the plate and a
clear path out sideways, checked for the whole rib rather than just its top.
Anything else is reported and left alone.
"""

import math

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
        "default": 15.0, "min": 2.0, "max": 200.0,
        "label": "Max span", "unit": "mm",
    },
    "thickness_mm": {
        "default": 1.8, "min": 0.4, "max": 10.0,
        "label": "Body thickness", "unit": "mm",
    },
    "ridge_mm": {
        "default": 0.9, "min": 0.2, "max": 10.0,
        "label": "Top ridge width", "unit": "mm",
    },
    "taper_deg": {
        "default": 45.0, "min": 15.0, "max": 80.0,
        "label": "Taper from vertical", "unit": "°",
    },
    "top_gap_mm": {
        "default": 0.2, "min": 0.0, "max": 1.0,
        "label": "Top gap", "unit": "mm",
    },
    "side_clearance_mm": {
        "default": 0.5, "min": 0.0, "max": 5.0,
        "label": "Side clearance", "unit": "mm",
    },
    "flange_mm": {
        "default": 4.0, "min": 0.0, "max": 30.0,
        "label": "Base flange", "unit": "mm",
    },
    "flange_thickness_mm": {
        "default": 0.6, "min": 0.0, "max": 5.0,
        "label": "Flange thickness", "unit": "mm",
    },
    "tab_mm": {
        "default": 5.0, "min": 0.0, "max": 30.0,
        "label": "Grip tab", "unit": "mm",
    },
    "buttress_above_mm": {
        "default": 15.0, "min": 1.0, "max": 300.0,
        "label": "Buttress above", "unit": "mm",
    },
    "layer_height_mm": {
        "default": 0.2, "min": 0.02, "max": 1.0,
        "label": "Layer height", "unit": "mm",
    },
    "max_tilt_deg": {
        "default": 5.0, "min": 0.1, "max": 30.0,
        "label": "Max tilt from flat", "unit": "°",
    },
}

# How far a buttress reaches either side of the rib, and how much of the rib's
# height it braces. Kept below the taper, so it never touches the part.
_BUTTRESS_REACH_MM = 4.0
_BUTTRESS_FRACTION = 0.6

# Fractions of an extension to try when something is in the way, largest
# first. A blocked flange is trimmed back rather than abandoned.
_TRIM_STEPS = (1.0, 0.5, 0.25)

# A short distance to probe with when the question is only "is anything
# there?" rather than "how much room is there?". Centimetres.
_NUDGE = 0.02

# Where to probe each block for material in the way, as fractions along it
# and up it. Clear of the ends, so a probe does not land on a boundary.
_ALONG = (0.1, 0.5, 0.9)
_UP = (0.15, 0.85)

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
    if span <= params["max_span_mm"]:
        return None  # short enough to bridge cleanly

    identifier = sig.finding_id(ID, fg.face_signature(face))
    fractions = underside.rib_positions(span, params["max_span_mm"])
    title = "{} — {} mm bridge".format(body.name, _format(span))

    plan, note = _plan(
        body, face, params, direction, bottom, near, span, fractions
    )
    if plan is None:
        return Finding(
            identifier, ID, title, detail=note, body=body.name,
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
        detail=" ".join(filter(None, [
            "Spans {} mm unsupported, past the {} mm limit. {} into {} spans "
            "of {} mm.".format(
                _format(span), _format(params["max_span_mm"]),
                "One rib divides it" if len(plan) == 1
                else "{} ribs divide it".format(len(plan)),
                len(plan) + 1, _format(span / (len(plan) + 1)),
            ),
            note,
        ])),
        body=body.name,
        fixable=True,
        fix_summary="Add {} removable support{} ({} mm ridge, {} mm gap)".format(
            len(plan), "" if len(plan) == 1 else "s",
            _format(params["ridge_mm"]), _format(params["top_gap_mm"]),
        ),
        entities=[face],
        extra={"spanMm": geo.round_mm(span), "ribs": len(plan)},
    )


class _Box:
    """One block of a rib, positioned outright. Centimetres throughout."""

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

    def samples(self, up):
        """Points spread through the block, for asking what is already there."""
        height_direction = geo.cross(self.length_direction, self.width_direction)
        if geo.dot(height_direction, up) < 0.0:
            height_direction = geo.negate(height_direction)
        for along in _ALONG:
            for upward in _UP:
                yield geo.add(
                    self.centre,
                    geo.add(
                        geo.scale(
                            self.length_direction,
                            self.length * (along - 0.5),
                        ),
                        geo.scale(height_direction, self.height * (upward - 0.5)),
                    ),
                )


class _Rib:
    """One support, as the blocks it is made of.

    Built as several boxes and merged, rather than drawn as a profile and
    swept, because a box placed by its centre and two axes needs no sketch
    plane. The taper is stepped at the layer height, which is not an
    approximation of a sloped face so much as a description of what the
    slicer would make of one.
    """

    def __init__(self, centre):
        self.centre = centre
        self.boxes = []

    def add(self, box):
        self.boxes.append(box)

    def build(self):
        solid = None
        for box in self.boxes:
            piece = box.build()
            if piece is None:
                return None
            if solid is None:
                solid = piece
            elif not fg.union(solid, piece):
                return None
        return solid

    def is_clear(self, body, up):
        """Whether every part of the rib has empty space to occupy.

        The whole rib, not just its top: a flange that runs into the part, or
        a grip tab buried in it, is as much of a failure as a ridge that does.
        """
        for box in self.boxes:
            for point in box.samples(up):
                if not fg.is_outside(body, point):
                    return False
        return True


def _shape(params, direction, span_normal, across, along, bottom, height,
           reach_low, reach_high, flange_low, flange_high,
           flange_near, flange_far, buttress_reach):
    """Lay out the blocks of one rib.

    Bottom to top: a wide thin flange for bed adhesion, a body thick enough
    not to flex, a stepped taper, and a narrow ridge. Widening the body to
    resist tipping costs nothing at the ridge, which is the only part that can
    weld itself to the model.
    """
    thickness = geo.to_cm(params["thickness_mm"])
    ridge = min(geo.to_cm(params["ridge_mm"]), thickness)
    flange_height = geo.to_cm(params["flange_thickness_mm"])
    layer = geo.to_cm(params["layer_height_mm"])

    inset = (thickness - ridge) / 2.0
    rise = inset / math.tan(math.radians(params["taper_deg"])) if inset > 0 else 0.0
    body_top = bottom + height - rise
    if body_top <= bottom + flange_height:
        return None, (
            "The gap under this bridge is too shallow for a support with a "
            "base and a tapered tip."
        )

    def place(low_across, high_across, low_span, high_span, low_up, high_up):
        centre = _point(
            direction, span_normal, across,
            (low_up + high_up) / 2.0,
            (low_span + high_span) / 2.0 + along,
            (low_across + high_across) / 2.0,
        )
        return _Box(
            centre, across, span_normal,
            high_across - low_across, high_span - low_span, high_up - low_up,
        )

    rib = _Rib(_point(
        direction, span_normal, across,
        bottom + height / 2.0, along, (reach_low + reach_high) / 2.0,
    ))

    if flange_height > 0.0 and (flange_near or flange_far or flange_low or flange_high):
        rib.add(place(
            reach_low - flange_low, reach_high + flange_high,
            -thickness / 2.0 - flange_near, thickness / 2.0 + flange_far,
            bottom, bottom + flange_height,
        ))

    rib.add(place(
        reach_low, reach_high,
        -thickness / 2.0, thickness / 2.0,
        bottom + flange_height, body_top,
    ))

    # A taper at 45 degrees steps in by one layer for every layer it rises,
    # so stepping it at the layer height is what the printer does anyway.
    # Splitting the rise evenly keeps the angle exact and lands the top on the
    # ridge width rather than near it.
    if rise > 0.0:
        steps = max(1, int(math.ceil(rise / layer)))
        for step in range(steps):
            half = thickness / 2.0 - inset * (step + 1) / steps
            rib.add(place(
                reach_low, reach_high, -half, half,
                body_top + rise * step / steps,
                body_top + rise * (step + 1) / steps,
            ))

    if buttress_reach > 0.0:
        # Braces the wall across its thin direction. Kept below the taper so
        # it can never reach the part, and it travels with the rib when the
        # rib is pulled out.
        top = min(body_top, bottom + height * _BUTTRESS_FRACTION)
        if top > bottom + flange_height:
            centre = _point(
                direction, span_normal, across,
                (bottom + flange_height + top) / 2.0, along,
                (reach_low + reach_high) / 2.0,
            )
            rib.add(_Box(
                centre, span_normal, across,
                buttress_reach * 2.0, thickness, top - bottom - flange_height,
            ))

    return rib, ""


def _plan(body, face, params, direction, bottom, near, span, fractions):
    """Work out where the ribs go.

    Returns ``(plan, note)``. With no plan the note says why; with one it may
    still carry something the user should know before ticking the box.
    """
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
    middle_up = (bridge_station + bottom) / 2.0
    clearance = geo.to_cm(params["side_clearance_mm"])
    tab = geo.to_cm(params["tab_mm"])
    flange = geo.to_cm(params["flange_mm"])
    buttress = (
        geo.to_cm(_BUTTRESS_REACH_MM)
        if geo.to_mm(height) >= params["buttress_above_mm"] else 0.0
    )

    # Nothing the rule builds may reach past the part's own footprint. A
    # support wider than the thing it supports fouls the brim and the skirt,
    # and looks like a mistake even when it prints.
    across_bounds = fg.bounding_box_extent(body, across)
    span_bounds = fg.bounding_box_extent(body, near.normal)
    half = geo.to_cm(params["thickness_mm"]) / 2.0

    plan = []
    tabbed = False
    for fraction in fractions:
        along = start + geo.to_cm(span) * fraction
        anchor = _point(direction, near.normal, across, middle_up, along, 0.0)

        # Each end either stands off a wall it would weld itself to, or
        # reaches out for a grip within whatever room the part leaves.
        reach_low, reach_high, has_tab = _ends(
            body, anchor, across, low, high, tab, clearance, across_bounds
        )
        tabbed = tabbed or has_tab

        rib, reason = _shape(
            params, direction, near.normal, across, along, bottom, height,
            reach_low, reach_high,
            _trimmed(body, anchor, across, -1.0, flange, reach_low, across_bounds[0]),
            _trimmed(body, anchor, across, 1.0, flange, reach_high, across_bounds[1]),
            _trimmed(body, anchor, near.normal, -1.0, flange, -half, span_bounds[0]),
            _trimmed(body, anchor, near.normal, 1.0, flange, half, span_bounds[1]),
            _trimmed(body, anchor, near.normal, 1.0, buttress, half, span_bounds[1])
            if buttress else 0.0,
        )
        if rib is None:
            return None, reason
        if not rib.is_clear(body, direction):
            return None, (
                "Something stands in the way beneath this bridge, so a support "
                "could not reach the build plate."
            )
        plan.append(rib)

    if not plan:
        return None, "Nothing to divide."
    return plan, ("" if tabbed else _FLUSH)


_FLUSH = (
    "The bridge runs to the edge of the part, so the supports finish flush "
    "with it rather than growing a grip tab past it. Pull them out from "
    "underneath."
)


def _trimmed(body, anchor, axis, sign, wanted, from_station, bound):
    """An extension, kept inside the part's extent and out of its material."""
    return _reach(
        body, anchor, axis, sign,
        _room_for(from_station, sign, bound, wanted),
        from_station,
    )


def _ends(body, anchor, across, low, high, tab, clearance, bounds):
    """Where the rib's two ends land, and whether either got a grip tab.

    Three cases at each end. Against material, it stands off by the side
    clearance, or the two will weld together in the print. In open air it
    reaches out for a grip -- but never past the part's own extent, because a
    support wider than the thing it supports fouls the brim, the skirt and
    anything else the slicer arranges around the part. Where the bridge
    already runs to the edge of the part there is no room for a tab at all,
    and the rib finishes flush.
    """
    part_low, part_high = bounds
    ends = {}
    tabbed = False
    for sign, edge, bound in ((-1.0, low, part_low), (1.0, high, part_high)):
        # A probe just past the end says whether there is anything there. The
        # side clearance is the natural distance to ask about, since that is
        # what would be needed to stand off it.
        if _reach(body, anchor, across, sign, max(clearance, _NUDGE), edge) <= 0.0:
            ends[sign] = edge - sign * clearance
            continue
        grip = _room_for(edge, sign, bound, tab)
        if grip > 0.0 and _reach(body, anchor, across, sign, grip, edge) < grip:
            grip = 0.0
        ends[sign] = edge + sign * grip
        tabbed = tabbed or grip > 0.0
    return ends[-1.0], ends[1.0], tabbed


def _room_for(edge, sign, bound, wanted):
    """How far a rib may reach past an edge without passing the part's own.

    The rib is scaffolding; it has no business occupying ground the part does
    not. Where the two coincide the answer is zero and the rib ends flush.
    """
    return min(wanted, max(0.0, (bound - edge) * sign))


def _reach(body, anchor, axis, sign, wanted, from_station):
    """The largest extension along an axis that stays out of the part.

    Trimmed rather than abandoned: a flange that cannot have its full four
    millimetres on one side is still worth having on the other three, and half
    a flange still resists tipping.
    """
    if wanted <= 0.0:
        return 0.0
    base = geo.subtract(anchor, geo.scale(axis, geo.dot(anchor, axis)))
    for fraction in _TRIM_STEPS:
        station = from_station + sign * wanted * fraction
        probe = geo.add(base, geo.scale(axis, station))
        if fg.is_outside(body, probe):
            return wanted * fraction
    return 0.0


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
    plan, note = _plan(
        body, face, params, direction, bottom, near, span, fractions
    )
    if plan is None:
        return Outcome.failed(finding.id, note)

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
        "Added {} support{} as separate bodies: {}. {} mm body tapering to a "
        "{} mm ridge, {} mm under the bridge.".format(
            len(bodies), "" if len(bodies) == 1 else "s",
            ", ".join(item.name for item in bodies),
            _format(params["thickness_mm"]), _format(params["ridge_mm"]),
            _format(params["top_gap_mm"]),
        ),
        feature=feature,
    )
