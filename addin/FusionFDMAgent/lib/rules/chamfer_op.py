"""Creating chamfer features, shared by the two rules that need them.

Two rules chamfer edges, and the interesting part is not the API call but what
happens around it: batching, verifying, and recovering when Fusion refuses.

**Batching is a correctness measure, not tidiness.** Every feature added to a
body regenerates it and invalidates every other edge reference held from
detection. Chamfering forty footprint edges as forty features would mean
thirty-nine of them pointing at edges that no longer exist. One feature over
all of them sidesteps the problem entirely, and leaves one timeline node and
one undo step as a bonus.

**A feature that was created is not a feature that worked.** Fusion returns an
errored feature rather than raising when a chamfer cannot be built, leaving a
red mark in the browser. Anything this module creates is checked and rolled
back if it is broken, because reporting "applied" for a failed fix is the one
outcome worse than reporting the failure.

**Which way material moved is worth checking too.** A chamfer on a convex edge
cuts the corner off; on a concave one it fills the corner in. A rule that
meant to add a gusset and instead pared the corner away would produce a
perfectly healthy feature and a worse part, which no health check would catch
-- so a rule can state which it expects and have it verified.
"""

from . import fusion_geom as fg
from .base import Outcome


#: The volume a rule expects its chamfer to move, if it cares.
ADDS = "adds"
REMOVES = "removes"


def apply_chamfer(ctx, jobs, description="chamfer", expect=None):
    """Chamfer the edges of each job.

    ``jobs`` is a list of ``(finding, [edge], size_mm)`` -- the size is per
    job, because a gusset is sized by the ledge it sits under rather than by a
    setting. ``expect`` is ADDS or REMOVES when the rule knows which way the
    material should move. Returns one Outcome per job.
    """
    jobs = [(finding, edges, size) for finding, edges, size in jobs if edges and size > 0]
    if not jobs:
        return []

    outcomes = {}
    for component, group in _by_component(jobs):
        outcomes.update(_apply_group(ctx, component, group, description, expect))
    return [outcomes[finding.id] for finding, _edges, _size in jobs if finding.id in outcomes]


def _by_component(jobs):
    """Group jobs by the component that owns the geometry.

    A chamfer feature belongs to a component, so edges from two components
    cannot share one. Grouping by component is therefore the largest batch
    that is actually legal.

    Keyed by `component_key` rather than by the component: Fusion's objects
    are unhashable, so the obvious dictionary raises a TypeError instead of
    grouping. The component itself is carried alongside, because that is what
    the feature has to be created on.
    """
    grouped = {}
    order = []
    for finding, edges, size in jobs:
        component = fg.parent_component(edges[0].body)
        key = fg.component_key(component)
        if key not in grouped:
            grouped[key] = (component, [])
            order.append(key)
        grouped[key][1].append((finding, edges, size))
    return [grouped[key] for key in order]


def _apply_group(ctx, component, jobs, description, expect):
    if component is None:
        return {
            finding.id: Outcome.failed(finding.id, "Could not find the owning component.")
            for finding, _edges, _size in jobs
        }

    edge_sets = _by_size(jobs)
    start = _timeline_position(ctx)

    feature, problem = _create(component, edge_sets, expect)
    if feature is not None and problem is None:
        return {
            finding.id: Outcome.applied(
                finding.id,
                "{} {} mm on {} edge{}.".format(
                    description.capitalize(), _format(size), len(group),
                    "" if len(group) == 1 else "s",
                ),
                feature=feature,
            )
            for finding, group, size in jobs
        }

    # The batch failed, and Fusion will not say which edge caused it. Retrying
    # one finding at a time costs more timeline nodes but lets the fixable ones
    # land and names the one that cannot be.
    if feature is not None:
        fg.delete_feature(feature)
    return _apply_individually(ctx, component, jobs, description, expect, start, problem)


def _by_size(jobs):
    """One chamfer edge set per distinct distance.

    A single feature can hold several edge sets, so findings that want
    different distances still batch into one timeline node -- which matters,
    because each feature regenerates the body and invalidates the edges the
    rest of them were holding.
    """
    sets = {}
    order = []
    for _finding, edges, size in jobs:
        if size not in sets:
            sets[size] = []
            order.append(size)
        sets[size].extend(edges)
    return [(sets[size], size) for size in order]


def _apply_individually(ctx, component, jobs, description, expect, start, batch_problem):
    outcomes = {}
    created = 0
    for finding, edges, size_mm in jobs:
        # Each successful chamfer regenerates the body, so the edges captured
        # at detection time are stale by the second iteration.
        live = ctx.resolve(finding) if ctx.resolve else edges
        if not live:
            outcomes[finding.id] = Outcome.skipped(
                finding.id, "The geometry changed; re-check the model."
            )
            continue
        feature, problem = _create(component, [(live, size_mm)], expect)
        if feature is not None and problem is None:
            created += 1
            outcomes[finding.id] = Outcome.applied(
                finding.id,
                "{} {} mm on {} edge{}.".format(
                    description.capitalize(), _format(size_mm), len(live),
                    "" if len(live) == 1 else "s",
                ),
                feature=feature,
            )
            continue
        if feature is not None:
            fg.delete_feature(feature)
        outcomes[finding.id] = Outcome.failed(
            finding.id,
            problem or batch_problem or "Fusion rejected the chamfer.",
        )

    if created > 1:
        _group_timeline(ctx, start, description)
    return outcomes


def _create(component, edge_sets, expect):
    """Add one chamfer feature. Returns ``(feature, problem)``."""
    before = _volumes(component, edge_sets)
    try:
        chamfers = component.features.chamferFeatures
        chamfer_input = chamfers.createInput2()
        for edges, size_mm in edge_sets:
            chamfer_input.chamferEdgeSets.addEqualDistanceChamferEdgeSet(
                fg.object_collection(edges),
                fg.value_input_mm(size_mm),
                # Never chain tangent edges. Chaining silently re-admits the
                # very edges detection excluded as thin-feature risks, which
                # would make the guard decorative.
                False,
            )
        feature = chamfers.add(chamfer_input)
    except Exception as exc:
        return None, _describe(exc)

    problem = fg.feature_problem(feature)
    if problem is None:
        problem = _wrong_direction(component, before, expect)
    return feature, problem


def _volumes(component, edge_sets):
    """Volume of every body the chamfer will touch, keyed by name."""
    before = {}
    for edges, _size in edge_sets:
        for edge in edges:
            try:
                body = edge.body
                name = body.name
            except Exception:
                continue
            if name not in before:
                volume = fg.body_volume(body)
                if volume is not None:
                    before[name] = volume
    return before


def _wrong_direction(component, before, expect):
    """Whether the chamfer moved material the way the rule intended.

    The bodies are looked up again by name rather than reused: creating the
    feature regenerated them, so the references taken beforehand describe a
    state that no longer exists.
    """
    if not expect or not before:
        return None
    for name, was in before.items():
        body = fg.find_body(component, name)
        now = fg.body_volume(body) if body is not None else None
        if now is None:
            continue
        if expect == ADDS and now <= was:
            return (
                "That chamfer pared the corner away instead of filling it in, "
                "so it was undone. The edge is not the internal corner the "
                "rule took it for."
            )
        if expect == REMOVES and now >= was:
            return (
                "That chamfer added material instead of removing it, so it "
                "was undone."
            )
    return None


def _timeline_position(ctx):
    try:
        return ctx.design.timeline.count
    except Exception:
        return None


def _group_timeline(ctx, start, name):
    """Collapse the features just created into one timeline node."""
    if start is None:
        return
    try:
        timeline = ctx.design.timeline
        if timeline.count - start < 2:
            return
        group = timeline.timelineGroups.add(start, timeline.count - 1)
        group.name = name
    except Exception:
        # Direct-mode designs have no timeline, and a grouping that fails
        # costs nothing but tidiness.
        pass


def _describe(exc):
    message = str(exc).strip()
    return message or type(exc).__name__


def _format(value):
    return ("{:.3f}".format(value)).rstrip("0").rstrip(".")
