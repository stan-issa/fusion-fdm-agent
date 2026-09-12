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
"""

from . import fusion_geom as fg
from .base import Outcome


def apply_chamfer(ctx, jobs, size_mm, description="chamfer"):
    """Chamfer the edges of each job.

    ``jobs`` is a list of ``(finding, [edge])``. Returns one Outcome per job.
    """
    jobs = [(finding, edges) for finding, edges in jobs if edges]
    if not jobs:
        return []

    outcomes = {}
    for component, group in _by_component(jobs):
        outcomes.update(_apply_group(ctx, component, group, size_mm, description))
    return [outcomes[finding.id] for finding, _edges in jobs if finding.id in outcomes]


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
    for finding, edges in jobs:
        component = fg.parent_component(edges[0].body)
        key = fg.component_key(component)
        if key not in grouped:
            grouped[key] = (component, [])
            order.append(key)
        grouped[key][1].append((finding, edges))
    return [grouped[key] for key in order]


def _apply_group(ctx, component, jobs, size_mm, description):
    if component is None:
        return {
            finding.id: Outcome.failed(finding.id, "Could not find the owning component.")
            for finding, _edges in jobs
        }

    edges = [edge for _finding, group in jobs for edge in group]
    start = _timeline_position(ctx)

    feature, problem = _create(component, edges, size_mm)
    if feature is not None and problem is None:
        total = sum(len(group) for _finding, group in jobs)
        return {
            finding.id: Outcome.applied(
                finding.id,
                "{} {} mm on {} edge{}.".format(
                    description.capitalize(), _format(size_mm), total,
                    "" if total == 1 else "s",
                ),
                feature=feature,
            )
            for finding, _group in jobs
        }

    # The batch failed, and Fusion will not say which edge caused it. Retrying
    # one finding at a time costs more timeline nodes but lets the fixable ones
    # land and names the one that cannot be.
    if feature is not None:
        fg.delete_feature(feature)
    return _apply_individually(ctx, component, jobs, size_mm, description, start, problem)


def _apply_individually(ctx, component, jobs, size_mm, description, start, batch_problem):
    outcomes = {}
    created = 0
    for finding, edges in jobs:
        # Each successful chamfer regenerates the body, so the edges captured
        # at detection time are stale by the second iteration.
        live = ctx.resolve(finding) if ctx.resolve else edges
        if not live:
            outcomes[finding.id] = Outcome.skipped(
                finding.id, "The geometry changed; re-check the model."
            )
            continue
        feature, problem = _create(component, live, size_mm)
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
        _group_timeline(ctx, start, "{} {} mm".format(description, _format(size_mm)))
    return outcomes


def _create(component, edges, size_mm):
    """Add one chamfer feature. Returns ``(feature, problem)``."""
    try:
        chamfers = component.features.chamferFeatures
        chamfer_input = chamfers.createInput2()
        chamfer_input.chamferEdgeSets.addEqualDistanceChamferEdgeSet(
            fg.object_collection(edges),
            fg.value_input_mm(size_mm),
            # Never chain tangent edges. Chaining silently re-admits the very
            # edges detection excluded as thin-feature risks, which would make
            # the guard decorative.
            False,
        )
        feature = chamfers.add(chamfer_input)
    except Exception as exc:
        return None, _describe(exc)
    return feature, fg.feature_problem(feature)


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
