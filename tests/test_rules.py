"""Exercises the rules engine's arithmetic and bookkeeping without Fusion.

The rules split deliberately down one line: `geometry.py` and `signature.py`
do the reasoning on plain numbers, `fusion_geom.py` does nothing but translate
Fusion objects into those numbers. That split is what makes this file
possible, and it is also where the subtle mistakes live -- a teardrop apex at
the wrong height, a thin-wall measurement that quietly ignores its own
neighbours, an id that changes when the finding did not.

What is *not* tested here is anything that needs real topology. A stub
elaborate enough to fake a B-Rep would only ever test itself; those paths are
verified by using the add-in against the part scripts/make_test_part.py
builds.

Run with ./scripts/test.sh
"""

import ast
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "adsk_stub"))
sys.path.insert(0, os.path.join(ROOT, "addin"))

import adsk.core                                                # noqa: E402
import adsk.fusion                                              # noqa: E402

from FusionFDMAgent.lib import design_tools                     # noqa: E402
from FusionFDMAgent.lib.rules import geometry as geo            # noqa: E402
from FusionFDMAgent.lib.rules import signature as sig           # noqa: E402
from FusionFDMAgent.lib.rules import RULES, BY_ID               # noqa: E402
from FusionFDMAgent.lib.rules import chamfer_op, teardrop_bore   # noqa: E402
from FusionFDMAgent.lib.rules.session import _same_document     # noqa: E402
from FusionFDMAgent.lib.rules.base import (                     # noqa: E402
    Finding, Outcome, RuleContext, RuleError, describe_params, merge_params,
)

failures = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   {}".format(label))
    else:
        print("  FAIL {} {}".format(label, detail))
        failures.append(label)


def close(first, second, tolerance=1e-9):
    return abs(first - second) <= tolerance


# -- teardrop geometry -----------------------------------------------------


def test_teardrop():
    print("teardrop geometry")

    # The classic 45 degree teardrop peaks at r*sqrt(2). If this is wrong the
    # roof is either unprintable or needlessly tall, and nothing else notices.
    check("45 degree apex is r*sqrt(2)",
          close(geo.teardrop_apex_height(5.0, 45.0), 5.0 * math.sqrt(2)))
    check("apex is r/sin(theta) in general",
          close(geo.teardrop_apex_height(3.0, 30.0), 3.0 / math.sin(math.radians(30.0))))
    check("added height is apex minus radius",
          close(geo.teardrop_added_height(5.0, 45.0), 5.0 * math.sqrt(2) - 5.0))

    left, right = geo.teardrop_tangent_points(5.0, 45.0)
    check("tangent points are mirrored", close(left[0], -right[0]) and close(left[1], right[1]))
    check("tangent points lie on the circle",
          close(math.hypot(*right), 5.0))

    # Tangency is what "preserves the original clearance" means: the roof line
    # touches the bore and never crosses into it.
    apex = (0.0, geo.teardrop_apex_height(5.0, 45.0))
    to_apex = (apex[0] - right[0], apex[1] - right[1])
    radial = right
    check("roof line is tangent to the bore",
          close(radial[0] * to_apex[0] + radial[1] * to_apex[1], 0.0, 1e-9))

    for bad, why in ((0.0, "zero radius"), (-1.0, "negative radius")):
        try:
            geo.teardrop_apex_height(bad, 45.0)
            check("rejects " + why, False)
        except ValueError:
            check("rejects " + why, True)
    for angle in (0.0, 90.0, 120.0):
        try:
            geo.teardrop_apex_height(5.0, angle)
            check("rejects a {} degree roof".format(angle), False)
        except ValueError:
            check("rejects a {} degree roof".format(angle), True)


# -- vectors and angles ----------------------------------------------------


def test_vectors():
    print("vectors and angles")

    check("perpendicular", geo.is_perpendicular((1, 0, 0), (0, 1, 0)))
    check("not perpendicular at 80 degrees",
          not geo.is_perpendicular((1, 0, 0), (math.cos(math.radians(80)), math.sin(math.radians(80)), 0)))
    # An axis has no sign, so antiparallel must count as parallel or every
    # bore would be classified by which way the modeller happened to draw it.
    check("antiparallel counts as parallel", geo.is_parallel((0, 0, 1), (0, 0, -1)))
    check("antiparallel is not the same direction",
          not geo.is_same_direction((0, 0, 1), (0, 0, -1)))

    # Round-off pushes the cosine outside [-1, 1] for near-parallel vectors,
    # and acos raises rather than saturating.
    check("near-parallel does not raise", close(geo.angle_between_deg((1, 0, 0), (1, 0, 0)), 0.0, 1e-6))
    check("opposite is 180", close(geo.angle_between_deg((1, 0, 0), (-1, 0, 0)), 180.0, 1e-6))

    flattened = geo.project_onto_plane((1.0, 0.0, 5.0), (0.0, 0.0, 1.0))
    check("projection drops the normal component", close(flattened[2], 0.0))

    check("point to axis distance",
          close(geo.point_to_axis_distance((3.0, 4.0, 9.0), (0, 0, 0), (0, 0, 1)), 5.0))

    for bad in ((0, 0, 0),):
        try:
            geo.normalise(bad)
            check("rejects a zero vector", False)
        except ValueError:
            check("rejects a zero vector", True)


def test_units():
    print("units")
    check("cm to mm", close(geo.to_mm(2.54), 25.4))
    check("mm to cm", close(geo.to_cm(25.4), 2.54))
    check("area cm2 to mm2", close(geo.area_to_mm2(1.0), 100.0))
    check("round_mm kills negative zero", str(geo.round_mm(-0.0000001)) == "0.0")


# -- the thin-feature measurement -----------------------------------------


def test_distances():
    print("polyline distances")

    left = [(0.0, 0.0, 0.0), (0.0, 10.0, 0.0)]
    right = [(0.6, 0.0, 0.0), (0.6, 10.0, 0.0)]
    check("parallel walls measure their gap",
          close(geo.polyline_min_distance(left, right), 0.6))

    # The closest approach can fall in the middle of a segment, where neither
    # polyline has a sample. Comparing sampled points alone would miss it.
    across = [(-5.0, 5.0, 0.0), (-0.3, 5.0, 0.0)]
    check("closest approach mid-segment is found",
          close(geo.polyline_min_distance(left, across), 0.3))

    check("point to segment clamps to the ends",
          close(geo.point_segment_distance((0.0, 20.0, 0.0), *left), 10.0))

    # Neighbouring edges of a loop touch, so they must be excluded by sharing
    # an endpoint rather than by a floor on the distance -- a floor would also
    # excuse the thin walls the guard exists to find.
    neighbour = [(0.0, 10.0, 0.0), (7.0, 10.0, 0.0)]
    check("adjoining edges are recognised", geo.shares_endpoint(left, neighbour, 1e-6))
    check("separate edges are not", not geo.shares_endpoint(left, right, 1e-6))
    check("endpoint match tolerates round-off",
          geo.shares_endpoint(left, [(0.0, 10.0 + 1e-7, 0.0), (7.0, 10.0, 0.0)], 1e-6))


# -- finding identity ------------------------------------------------------


def test_signatures():
    print("finding identity")

    one = sig.make("edge", "Body1", curve="circle", centre=(1.0, 2.0, 3.0), radius=2.5)
    same = sig.make("edge", "Body1", curve="circle", centre=(1.0, 2.0, 3.0), radius=2.5)
    check("identical geometry hashes the same", sig.key(one) == sig.key(same))

    # Floating-point noise between two detections must not rename a finding,
    # or the box the user ticked silently becomes a different edge.
    noisy = sig.make(
        "edge", "Body1", curve="circle",
        centre=(1.0 + 1e-9, 2.0 - 1e-9, 3.0), radius=2.5 + 1e-9,
    )
    check("noise does not change the id", sig.key(one) == sig.key(noisy))

    moved = sig.make("edge", "Body1", curve="circle", centre=(1.0, 2.0, 4.0), radius=2.5)
    check("a moved edge gets a new id", sig.key(one) != sig.key(moved))
    other_body = sig.make("edge", "Body2", curve="circle", centre=(1.0, 2.0, 3.0), radius=2.5)
    check("the same shape on another body differs", sig.key(one) != sig.key(other_body))

    check("ids are prefixed by their rule",
          sig.finding_id("bed_chamfer", one).startswith("bed_chamfer:"))
    check("quantise snaps to the grid", close(sig.quantise(0.1234), 0.12))
    check("None fields are dropped",
          "radius" not in sig.make("edge", "Body1", radius=None))

    # Two rules describing the same face share the hash after the colon, which
    # is what lets the session notice that a teardrop and a lead-in are aimed
    # at the same bore.
    face = sig.make("face", "Body1", surface="cylinder", radius=3.0)
    teardrop = sig.finding_id("teardrop_bore", face)
    lead_in = sig.finding_id("hole_lead_in", face)
    check("rules share a suffix for the same feature",
          teardrop.split(":")[1] == lead_in.split(":")[1])


# -- parameters ------------------------------------------------------------


def test_params():
    print("parameters")

    spec = {
        "size_mm": {"default": 0.3, "min": 0.05, "max": 3.0},
        "count": {"default": 2, "min": 1, "max": 9},
        "ends": {"default": "both", "choices": ("both", "top", "bed")},
    }

    check("defaults apply", merge_params(spec)["size_mm"] == 0.3)
    check("later layers win",
          merge_params(spec, {"size_mm": 0.4}, {"size_mm": 0.5})["size_mm"] == 0.5)
    check("None does not override",
          merge_params(spec, {"size_mm": 0.4}, {"size_mm": None})["size_mm"] == 0.4)

    # Clamping rather than refusing: a rule that will not run because a stored
    # setting drifted is worse than one that runs at the nearest sane value.
    check("clamps above the maximum", merge_params(spec, {"size_mm": 99})["size_mm"] == 3.0)
    check("clamps below the minimum", merge_params(spec, {"size_mm": 0})["size_mm"] == 0.05)
    check("integers stay integers",
          isinstance(merge_params(spec, {"count": 4.7})["count"], int))
    check("strings coerce to numbers", merge_params(spec, {"size_mm": "0.6"})["size_mm"] == 0.6)
    check("choices pass through", merge_params(spec, {"ends": "top"})["ends"] == "top")

    for bad, label in (({"size_mm": "thick"}, "non-numeric"),
                       ({"size_mm": float("nan")}, "NaN"),
                       ({"ends": "sideways"}, "an unknown choice")):
        try:
            merge_params(spec, bad)
            check("rejects {}".format(label), False)
        except RuleError:
            check("rejects {}".format(label), True)

    described = {entry["name"]: entry for entry in describe_params(spec, merge_params(spec))}
    check("describes every parameter", len(described) == 3)
    check("carries the resolved value", described["size_mm"]["value"] == 0.3)
    check("carries choices for the panel", described["ends"]["choices"] == ("both", "top", "bed"))


# -- findings and outcomes -------------------------------------------------


def test_findings():
    print("findings")

    finding = Finding(
        "bed_chamfer:abc", "bed_chamfer", "Body1 — 7 edges",
        detail="Outline of the bottom face.", body="Body1",
        fix_summary="Chamfer 0.3 mm", entities=[object()],
        skipped=[("2 mm edge", "too close to its neighbour")],
    )
    payload = finding.as_dict()
    check("entities never reach the wire", "entities" not in payload)
    check("skip reasons do",
          payload["skipped"][0]["why"] == "too close to its neighbour")
    check("fix summary is carried", payload["fix"] == "Chamfer 0.3 mm")
    check("empty fields are omitted", "body" in payload and "notFixable" not in payload)

    check("outcome statuses", Outcome.applied("x").as_dict()["status"] == "applied")
    check("failure carries its message",
          Outcome.failed("x", "no").as_dict()["message"] == "no")


# -- the rule catalogue ----------------------------------------------------


def test_catalogue():
    print("rule catalogue")

    for rule in RULES:
        label = rule.ID
        check("{} declares a title".format(label), bool(rule.TITLE))
        check("{} declares a description".format(label), bool(rule.DESCRIPTION))
        check("{} has detect and apply".format(label),
              callable(rule.detect) and callable(rule.apply))
        check("{} parameters all have defaults".format(label),
              all("default" in spec for spec in rule.PARAMS.values()))
        check("{} parameters resolve".format(label),
              set(merge_params(rule.PARAMS)) == set(rule.PARAMS))

    check("ids are unique", len(BY_ID) == len(RULES))

    # RULES is applied in order, most destructive first, because each feature
    # regenerates the body and rewrites what the later rules were aiming at.
    order = [rule.ID for rule in RULES]
    check("teardrop runs before lead-in",
          order.index("teardrop_bore") < order.index("hole_lead_in"))
    check("bed chamfer runs last", order[-1] == "bed_chamfer")


class _FusionObject:
    """Stands in for a Fusion API object, which defines no __hash__.

    That is the property under test: none of these may be used as a
    dictionary key or put in a set, and code that tries raises a TypeError at
    the point of grouping rather than where the object came from.
    """

    __hash__ = None

    def __init__(self, **fields):
        self.__dict__.update(fields)


# -- what "Show" selects ---------------------------------------------------


def test_reveal():
    print("what Show selects")

    edges = [_FusionObject(name="edge")]
    finding = Finding("r:1", "bed_chamfer", "t", entities=edges)
    check("reveal defaults to the geometry the fix acts on",
          finding.reveal == edges)

    # The bug: a finding with nothing to fix was built with entities=[], so
    # Show had nothing to select and reported "that finding's geometry is no
    # longer in the model" -- about geometry sitting right there in the
    # canvas. Nothing to fix is exactly when you most want to see what the
    # rule meant.
    face = _FusionObject(name="face")
    unfixable = Finding("r:2", "teardrop_bore", "t", fixable=False, reveal=[face])
    check("a finding with no fix still has something to show",
          unfixable.entities == [] and unfixable.reveal == [face])

    # bed_chamfer skipping every edge is the same shape of problem: no edges
    # to chamfer, but a bottom face worth looking at.
    faces = [_FusionObject(name="bed")]
    nothing_kept = Finding("r:3", "bed_chamfer", "t", entities=[], reveal=faces)
    check("a fully skipped finding falls back to its face",
          nothing_kept.reveal == faces)

    check("reveal is copied, not aliased",
          Finding("r:4", "x", "t", entities=edges).reveal is not edges)


# -- where the teardrop cut runs -------------------------------------------
#
# Enough fake topology to walk, and no more: faces that know their surface
# type, their normal and their edges, and edges that know their circle and the
# two faces meeting at them. The geometry is declared rather than computed, so
# what is under test is the *walk* -- which ring leads where -- and not a
# reimplementation of Fusion.


class _Collection(list):
    @property
    def count(self):
        return len(self)

    def item(self, index):
        return self[index]


class _FakeBody(_FusionObject):
    def pointContainment(self, point):
        # Probing always lands outside, so outward_normal takes the declared
        # normal at face value instead of flipping it.
        return adsk.fusion.PointContainment.PointOutsidePointContainment


class _FakeFace(_FusionObject):
    def __init__(self, token, surface, normal=(0.0, 0.0, 1.0), origin=None):
        _FusionObject.__init__(self)
        self.entityToken = token
        self.isParamReversed = False
        self.body = _FakeBody()
        self.edges = _Collection()
        self.geometry = _FusionObject(surfaceType=surface)
        if origin is not None:
            self.geometry.origin = adsk.core.Point3D.create(*origin)
        self.pointOnFace = adsk.core.Point3D.create(*(origin or (0.0, 0.0, 0.0)))
        vector = adsk.core.Vector3D.create(*normal)
        self.evaluator = _FusionObject(
            getNormalAtPoint=lambda point, v=vector: (True, v)
        )


def _fake_ring(radius, z, first, second):
    edge = _FusionObject(
        entityToken="edge-{}-{}".format(radius, z),
        geometry=_FusionObject(
            curveType=adsk.core.Curve3DTypes.Circle3DCurveType,
            radius=radius,
            center=adsk.core.Point3D.create(0.0, 0.0, z),
        ),
        faces=_Collection([first, second]),
    )
    first.edges.append(edge)
    second.edges.append(edge)
    return edge


def _through_bore(lead_in=0.0):
    """A bore through a 1 cm plate, optionally chamfered at both ends.

    Plate faces sit at z=0 and z=1. With a lead-in the full-diameter bore is
    shorter than the plate, ending at z=lead_in and z=1-lead_in, with a cone
    between each ring and the face outside it.
    """
    planes = adsk.core.SurfaceTypes.PlaneSurfaceType
    cones = adsk.core.SurfaceTypes.ConeSurfaceType
    cylinders = adsk.core.SurfaceTypes.CylinderSurfaceType

    bore = _FakeFace("bore", cylinders)
    for index, (outer_z, ring_z) in enumerate(
        ((0.0, lead_in), (1.0, 1.0 - lead_in))
    ):
        flat = _FakeFace(
            "flat-{}".format(index), planes, (0.0, 0.0, 1.0), (0.0, 0.0, outer_z)
        )
        if lead_in:
            cone = _FakeFace("cone-{}".format(index), cones)
            _fake_ring(0.25, ring_z, bore, cone)
            _fake_ring(0.30, outer_z, cone, flat)   # the wider, outer ring
        else:
            _fake_ring(0.25, ring_z, bore, flat)
    return bore


def test_teardrop_cut_span():
    print("where the teardrop cut starts and stops")

    axis = (0.0, 0.0, 1.0)

    entrance, start, end = teardrop_bore._cut_span(_through_bore(), axis)
    check("a plain bore sketches on its flat face",
          entrance is not None and entrance.entityToken.startswith("flat"))
    check("and cuts the full thickness", close(abs(end - start), 1.0, 1e-9),
          "{} to {}".format(start, end))

    # The bug: lead-ins are applied to both ends by default, so a hole that
    # has been through the other rule opens into a cone at each end and never
    # touches a flat face directly. Teardrop first reported every such hole as
    # impossible to fix; then, once it could reach across one chamfer, it
    # measured the cut only as far as the *far end's ring* -- leaving that
    # chamfer with the flat roof the teardrop exists to remove.
    entrance, start, end = teardrop_bore._cut_span(_through_bore(0.05), axis)
    check("a bore with lead-ins finds the flat face across the chamfer",
          entrance is not None and entrance.entityToken.startswith("flat"),
          "got {}".format(entrance.entityToken if entrance else None))
    check("the cut spans flat face to flat face, not ring to ring",
          close(abs(end - start), 1.0, 1e-9),
          "{} to {} (0.95 would stop short of the far chamfer)".format(start, end))

    # The cone has two rings. Following the one shared with the bore would
    # walk straight back where it came from.
    bore = _through_bore(0.05)
    cone = bore.edges.item(0).faces.item(1)
    check("the walk crosses the cone's wider ring, not the shared one",
          teardrop_bore._flat_face_beyond(
              bore.edges.item(0), bore, axis
          ).entityToken != cone.entityToken)

    # A blind bore, or one breaking out through a curved wall, has nothing
    # flat at that end. The cut stops at the ring rather than guessing.
    blind = _FakeFace("bore", adsk.core.SurfaceTypes.CylinderSurfaceType)
    floor = _FakeFace(
        "floor", adsk.core.SurfaceTypes.PlaneSurfaceType,
        (0.0, 0.0, 1.0), (0.0, 0.0, 0.0),
    )
    dome = _FakeFace("dome", adsk.core.SurfaceTypes.SphereSurfaceType)
    _fake_ring(0.25, 0.0, blind, floor)
    _fake_ring(0.25, 0.8, blind, dome)
    entrance, start, end = teardrop_bore._cut_span(blind, axis)
    check("an end with nothing flat falls back to its own ring",
          entrance is not None and close(abs(end - start), 0.8, 1e-9),
          "{} to {}".format(start, end))

    nowhere = _FakeFace("bore", adsk.core.SurfaceTypes.CylinderSurfaceType)
    curved = _FakeFace("dome", adsk.core.SurfaceTypes.SphereSurfaceType)
    _fake_ring(0.25, 0.0, nowhere, curved)
    _fake_ring(0.25, 1.0, nowhere, curved)
    entrance, _start, _end = teardrop_bore._cut_span(nowhere, axis)
    check("no flat face anywhere means no cut", entrance is None)


# -- saying why nothing was found ------------------------------------------


def test_silence_is_explained():
    print("explaining an empty result")

    # The bug this guards against: teardrop had a min_diameter_mm that
    # defaulted to 6 mm, which excludes every fastener clearance hole in a
    # typical printed part -- and excluded them without a word. The threshold
    # is gone; how small is too small to bother with is a judgement about the
    # part, not one a default should make.
    check("no size threshold hides bores from the user",
          "min_diameter_mm" not in teardrop_bore.PARAMS,
          sorted(teardrop_bore.PARAMS))

    params = merge_params(teardrop_bore.PARAMS)

    context = RuleContext()
    passed = teardrop_bore._PassedOver()
    passed.not_horizontal = 3
    passed.report(context, params, found_any=False)
    check("vertical-only parts are told nothing runs across the build direction",
          len(context.notes) == 1 and "across the build direction" in context.notes[0])

    context = RuleContext()
    teardrop_bore._PassedOver().report(context, params, found_any=False)
    check("a part with no bores at all says so",
          context.notes == ["No cylindrical bores found to check."])

    # Silence is fine when there is something to show: the findings speak.
    context = RuleContext()
    passed = teardrop_bore._PassedOver()
    passed.not_horizontal = 2
    passed.report(context, params, found_any=True)
    check("no note when findings were produced", context.notes == [])

    # Rules report per body, so the same sentence would otherwise arrive once
    # per body.
    context = RuleContext()
    context.note("same")
    context.note("same")
    context.note("")
    check("notes are deduplicated and empties dropped", context.notes == ["same"])


# -- batching the chamfer jobs ---------------------------------------------


def test_grouping():
    print("grouping edges for one chamfer feature")

    first = _FusionObject(entityToken="comp-1", name="Plate")
    second = _FusionObject(entityToken="comp-2", name="Bracket")
    plate = _FusionObject(parentComponent=first)
    bracket = _FusionObject(parentComponent=second)

    def job(name, body):
        finding = Finding(name, "bed_chamfer", name)
        return (finding, [_FusionObject(body=body)])

    jobs = [job("a", plate), job("b", bracket), job("c", plate)]

    try:
        grouped = chamfer_op._by_component(jobs)
    except TypeError as exc:
        check("components are not used as dictionary keys", False, str(exc))
        return

    check("one group per component", len(grouped) == 2)
    check("findings from the same component batch together",
          [len(items) for _component, items in grouped] == [2, 1])
    check("the component itself is carried, not just its key",
          grouped[0][0] is first)
    # One feature over many edges is what keeps the other findings' edges
    # valid, so the order findings were reported in has to survive grouping.
    check("order is preserved",
          [finding.id for finding, _edges in grouped[0][1]] == ["a", "c"])

    # A component Fusion will not give a token for still has to group, or the
    # whole rule fails over a missing string.
    nameless = _FusionObject(name="Unnamed")
    del nameless.__dict__["name"]
    check("an unidentifiable component still yields a key",
          chamfer_op.fg.component_key(nameless) == "component:unidentified")
    check("a token is preferred when there is one",
          chamfer_op.fg.component_key(first) == "comp-1")
    check("the name is the fallback",
          chamfer_op.fg.component_key(_FusionObject(name="Plate")) == "name:Plate")


# -- document identity -----------------------------------------------------


def test_document_identity():
    print("document identity")

    # The guard exists to stop a fix landing in a document other than the one
    # that was checked. Its first version compared `id(app.activeDocument)`,
    # which can never match: Fusion builds a fresh proxy object on every read,
    # so applying always failed with "the active document changed".
    saved = {"file": "urn:adsk:1234", "name": "Bracket v3"}
    reread = {"file": "urn:adsk:1234", "name": "Bracket v3"}
    check("re-reading the same document matches", _same_document(saved, reread))

    renamed = {"file": "urn:adsk:1234", "name": "Bracket v4"}
    check("a rename does not lose the findings", _same_document(saved, renamed))

    other = {"file": "urn:adsk:9999", "name": "Bracket v3"}
    check("a different file does not match", not _same_document(saved, other))

    # Saving mid-session gives a document a dataFile it did not have before.
    # That is not a reason to throw the user's findings away.
    unsaved = {"file": None, "name": "Untitled"}
    now_saved = {"file": "urn:adsk:5555", "name": "Untitled"}
    check("saving between check and apply is tolerated",
          _same_document(unsaved, now_saved))

    check("two unsaved documents with different names differ",
          not _same_document(unsaved, {"file": None, "name": "Untitled 2"}))
    check("a missing key never matches", not _same_document(None, saved))
    check("two missing keys do not match either", not _same_document(None, None))


# -- the two processes agree ----------------------------------------------


def _literal(path, name):
    """Read a module-level literal without importing the module.

    fusion_tools imports the Agent SDK, which the add-in half of the suite
    deliberately does not have; parsing keeps this check in the same run as
    the registry it is checking against.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError("{} not found in {}".format(name, path))


def _mutating_tools(path, server):
    """MUTATING_TOOLS, whose members are built with .format rather than typed.

    Evaluating the module is not an option here, so the one call shape it uses
    is unpicked directly. A member written any other way raises instead of
    being skipped, so this cannot quietly stop checking anything.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "MUTATING_TOOLS"
                   for t in node.targets):
            continue
        names = []
        for element in node.value.elts:
            if (isinstance(element, ast.Call)
                    and isinstance(element.func, ast.Attribute)
                    and element.func.attr == "format"
                    and isinstance(element.func.value, ast.Constant)):
                names.append(element.func.value.value.format(server))
            elif isinstance(element, ast.Constant):
                names.append(element.value)
            else:
                raise AssertionError(
                    "MUTATING_TOOLS has an entry this test cannot read; "
                    "update tests/test_rules.py rather than leaving it unchecked."
                )
        return names
    raise AssertionError("MUTATING_TOOLS not found in {}".format(path))


def test_parity():
    print("tool parity across the two processes")

    tools_path = os.path.join(ROOT, "sidecar", "fdm_sidecar", "fusion_tools.py")
    exposed = _literal(tools_path, "TOOL_NAMES")

    # A tool that exists on one side only is invisible until someone tries to
    # call it, and then it fails inside the model's turn rather than here.
    check("every exposed tool exists in the add-in",
          set(exposed) <= set(design_tools.REGISTRY),
          sorted(set(exposed) - set(design_tools.REGISTRY)))
    check("every add-in tool is exposed",
          set(design_tools.REGISTRY) <= set(exposed),
          sorted(set(design_tools.REGISTRY) - set(exposed)))

    server = _literal(tools_path, "SERVER_NAME")
    prefix = "mcp__{}__".format(server)
    gated = {
        name[len(prefix):]
        for name in _mutating_tools(tools_path, server)
        if name.startswith(prefix)
    }
    check("the same tools are gated on both sides",
          gated == set(design_tools.MUTATING),
          "{} vs {}".format(sorted(gated), sorted(design_tools.MUTATING)))
    check("apply_rule_fix needs approval", "apply_rule_fix" in design_tools.MUTATING)


for test in (test_teardrop, test_vectors, test_units, test_distances,
             test_signatures, test_params, test_findings, test_catalogue,
             test_reveal, test_teardrop_cut_span, test_silence_is_explained,
             test_grouping,
             test_document_identity, test_parity):
    test()

print()
if failures:
    print("{} failed: {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("rules: all checks passed")
