"""Says why the ledge rule passes over a face. Run this inside Fusion.

    Utilities -> Scripts and Add-Ins -> Scripts -> + -> pick this file -> Run

A rule that finds nothing looks exactly like a rule that did not look, and on
a real part the difference matters. This walks the same gates ledge_gusset
walks, in the same order, and prints the first one each face or edge falls at.
It imports the rules from *this checkout*, not from the copy installed in
Fusion, so it reports on the code in the repository.

Nothing here modifies the model.
"""

import importlib
import os
import sys
import traceback
import types

import adsk.core
import adsk.fusion

# Fusion has already imported the *installed* add-in under the name
# `FusionFDMAgent`, so importing the checkout by that name would either
# collide with it or quietly read the installed copy -- which is the one copy
# this script exists to bypass. Giving the checkout's rules folder a package
# name of its own keeps the two apart; the rules only import each other, so a
# package rooted at that folder is all they need.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PACKAGE = "fdm_agent_checkout"
if _PACKAGE not in sys.modules:
    _package = types.ModuleType(_PACKAGE)
    _package.__path__ = [
        os.path.join(ROOT, "addin", "FusionFDMAgent", "lib", "rules")
    ]
    sys.modules[_PACKAGE] = _package

fg = importlib.import_module(_PACKAGE + ".fusion_geom")
geo = importlib.import_module(_PACKAGE + ".geometry")
underside = importlib.import_module(_PACKAGE + ".underside")
ledge_gusset = importlib.import_module(_PACKAGE + ".ledge_gusset")

# The build direction the add-in would use for a part sitting on the bed.
UP = (0.0, 0.0, 1.0)
MAX_TILT_DEG = ledge_gusset.PARAMS["max_tilt_deg"]["default"]


def run(_context):
    app = adsk.core.Application.get()
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        if design is None:
            app.userInterface.messageBox("Open a design first.")
            return
        lines = []
        component = design.activeComponent or design.rootComponent
        lines.append("active component: {}".format(component.name))
        lines.append("build direction: +Z")
        bodies = component.bRepBodies
        if bodies.count == 0:
            lines.append("no bodies in the active component -- "
                         "the add-in would see none either")
        for index in range(bodies.count):
            _report_body(bodies.item(index), lines)
        text = "\n".join(lines)
        print(text)
        path = os.path.join(os.path.expanduser("~"), "ledge-report.txt")
        with open(path, "w") as handle:
            handle.write(text + "\n")
        app.userInterface.messageBox(
            "Written to {}\n\n{}".format(path, text[:1200])
        )
    except Exception:
        app.userInterface.messageBox("Failed:\n{}".format(traceback.format_exc()))


def _report_body(body, lines):
    lines.append("")
    lines.append("body {}: {} faces".format(body.name, body.faces.count))
    if not fg.is_native_body(body):
        lines.append("  SKIPPED: reached through an occurrence, not analysed")
        return
    try:
        if not body.isSolid:
            lines.append("  SKIPPED: not a solid")
            return
        if not body.isLightBulbOn:
            lines.append("  SKIPPED: hidden")
            return
    except Exception:
        pass
    if fg.is_support_body(body):
        lines.append("  SKIPPED: named as a printed support")
        return

    bottom, _top = fg.bounding_box_extent(body, UP)
    downward = 0
    for index, face in enumerate(fg.body_faces(body)):
        if not fg.is_planar(face):
            continue
        try:
            normal = fg.outward_normal(face)
        except Exception:
            continue
        tilt = geo.angle_between_deg(normal, geo.negate(UP))
        if tilt > 90.0:
            continue  # not facing downwards at all
        downward += 1
        lines.append("  face {}: faces down, {:.1f}° off flat, {:.2f} mm "
                     "above the lowest point".format(
                         index, tilt, geo.to_mm(fg.plane_offset(face, UP) - bottom)))
        if tilt > MAX_TILT_DEG:
            lines.append("    not flat enough (limit {:.1f}°)".format(MAX_TILT_DEG))
            continue
        if fg.plane_offset(face, UP) <= bottom + underside.BED_TOLERANCE:
            lines.append("    resting on the bed, not an overhang")
            continue
        _report_face(body, face, lines)
    if downward == 0:
        lines.append("  no downward-facing planar faces at all")


def _report_face(body, face, lines):
    found = underside.supports(face, UP)
    lines.append("    {} supporting edge(s) of {}".format(
        len(found), len(fg.face_edges(face))))
    for support in found:
        lines.append("      wall normal ({:.2f}, {:.2f}, {:.2f}), runs {:.2f} mm "
                     "below".format(support.normal[0], support.normal[1],
                                    support.normal[2], support.depth_mm))
    for first_index, first in enumerate(found):
        for second in found[first_index + 1:]:
            angle = geo.angle_between_deg(first.normal, second.normal)
            if angle < 180.0 - underside.OPPOSED_TOLERANCE_DEG:
                continue
            lines.append("      opposed pair at {:.1f}°, span {:.2f} mm "
                         "({})".format(angle, underside.span_mm(first, second),
                                       "across a gap: a bridge"
                                       if underside.span_mm(first, second) > 0.0
                                       else "back to back: still a ledge"))
    if underside.opposed(found) is not None:
        lines.append("    CLAIMED BY bridge_ribs, so the ledge rule passes over it")
        return
    if fg.is_ignored(face, ledge_gusset.ID):
        lines.append("    marked ignored for this rule")
        return

    for edge_index, edge in enumerate(fg.face_edges(face)):
        lines.append("      edge {}: {}".format(
            edge_index, _edge_verdict(body, face, edge)))


def _edge_verdict(body, face, edge):
    """The first gate in ledge_gusset._examine that this edge falls at."""
    if not fg.is_straight(edge):
        return "curved, so no 45° chamfer to give it"
    if fg.is_ignored(edge, ledge_gusset.ID):
        return "marked ignored for this rule"
    wall = fg.other_face(edge, face)
    if wall is None:
        return "no face on the other side"
    if not fg.is_planar(wall):
        return "the face on the other side is not planar"
    try:
        wall_normal = fg.outward_normal(wall)
    except Exception:
        return "could not read the other face's normal"
    off = 90.0 - geo.angle_between_deg(wall_normal, UP)
    if not geo.is_perpendicular(wall_normal, UP, underside.WALL_TOLERANCE_DEG):
        return "the face on the other side leans {:.1f}° off vertical".format(abs(off))
    below_mm = underside.depth_below(wall, edge, UP)
    if below_mm <= ledge_gusset._MIN_GUSSET_MM:
        return "the wall does not run below this edge ({:.3f} mm)".format(below_mm)
    projection_mm = ledge_gusset._projection_mm(face, wall, wall_normal)
    if projection_mm <= ledge_gusset._MIN_GUSSET_MM:
        return "nothing projects past this wall ({:.3f} mm)".format(projection_mm)
    gusset_mm = min(projection_mm, below_mm)
    if not ledge_gusset._space_is_clear(body, edge, wall_normal, UP, gusset_mm):
        return "FOUND but not fixable: the corner is already occupied"
    return "FOUND: {:.2f} mm ledge, wall {:.2f} mm below, {:.2f} mm gusset".format(
        projection_mm, below_mm, gusset_mm)
