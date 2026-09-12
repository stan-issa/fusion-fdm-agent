"""Builds the part the rules are tested against. Run this inside Fusion.

    Utilities -> Scripts and Add-Ins -> Scripts -> + -> pick this file -> Run

Committing a `.f3d` would be committing a binary nobody can read a diff of.
This is the same part as a few dozen lines of geometry, and it doubles as the
specification: every feature here exists because some rule is supposed to
react to it in a particular way.

What each rule should say about the result:

  bed_chamfer    Both bodies rest on z=0, so both have a footprint to chamfer.
                 The 0.8 mm fin on the plate's right-hand edge is *narrower*
                 than the default 1 mm minimum wall, so its two long edges
                 must be skipped -- with that reason given, not silently.

  hole_lead_in   The two plain 5 mm holes are candidates. The threaded one is
                 not: chamfering the mouth of a thread is wrong.

  teardrop_bore  The 8 mm bore through the plate is a clean candidate. The one
                 through the second body is crossed by a 4 mm hole, so it is
                 no longer a complete cylinder and must be reported as found
                 but not automatically fixable.
"""

import traceback

import adsk.core
import adsk.fusion

PLATE = (60.0, 40.0, 20.0)     # mm
FIN_THICKNESS = 0.8            # deliberately under the 1 mm default min wall
BORE_DIAMETER = 8.0
HOLE_DIAMETER = 5.0
CROSS_DIAMETER = 4.0
BLOCK_ORIGIN_X = 80.0
BLOCK = (30.0, 40.0, 20.0)


def mm(value):
    """Fusion works in centimetres internally; this part is described in mm."""
    return value / 10.0


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        document = app.documents.add(
            adsk.core.DocumentTypes.FusionDesignDocumentType
        )
        design = adsk.fusion.Design.cast(document.products.itemByProductType("DesignProductType"))
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        root = design.rootComponent

        plate = _plate(root)
        _fin(root, plate)
        _vertical_holes(root, plate)
        _horizontal_bore(root, plate, x=45.0)

        block = _block(root)
        _horizontal_bore(root, block, x=BLOCK_ORIGIN_X + BLOCK[0] / 2.0)
        _cross_hole(root, block)

        threaded = _threaded_hole(root, plate)

        ui.messageBox(
            "Test part built.\n\n"
            "Two bodies on the bed: a plate with a 0.8 mm fin, two plain 5 mm "
            "holes, {}, and a clean 8 mm horizontal bore; and a block whose "
            "8 mm bore is cross-drilled.\n\n"
            "Open the FDM Agent panel and press Check model.".format(
                "a threaded 5 mm hole" if threaded
                else "a 5 mm hole the thread could not be added to"
            )
        )
    except Exception:
        if ui:
            ui.messageBox("Failed:\n{}".format(traceback.format_exc()))


# -- solids ----------------------------------------------------------------


def _plate(root):
    return _box(root, 0.0, 0.0, PLATE[0], PLATE[1], PLATE[2], "Plate")


def _block(root):
    return _box(
        root, BLOCK_ORIGIN_X, 0.0,
        BLOCK_ORIGIN_X + BLOCK[0], BLOCK[1], BLOCK[2], "CrossDrilled",
    )


def _box(root, x0, y0, x1, y1, height, name):
    sketch = root.sketches.add(root.xYConstructionPlane)
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(mm(x0), mm(y0), 0),
        adsk.core.Point3D.create(mm(x1), mm(y1), 0),
    )
    extrude = root.features.extrudeFeatures.addSimple(
        sketch.profiles.item(0),
        adsk.core.ValueInput.createByReal(mm(height)),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
    )
    body = extrude.bodies.item(0)
    body.name = name
    return body


def _fin(root, plate):
    """A wall thinner than the chamfer guard's minimum, standing on the bed.

    This is the feature bed_chamfer must refuse to chamfer: 0.3 mm taken off
    each side of a 0.8 mm fin leaves 0.2 mm, which is less than one extrusion
    width and simply would not print.
    """
    sketch = root.sketches.add(root.xYConstructionPlane)
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(mm(PLATE[0]), mm(15.0), 0),
        adsk.core.Point3D.create(mm(PLATE[0] + FIN_THICKNESS), mm(25.0), 0),
    )
    extrude_input = root.features.extrudeFeatures.createInput(
        sketch.profiles.item(0),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
    )
    extrude_input.participantBodies = [plate]
    extrude_input.setDistanceExtent(
        False, adsk.core.ValueInput.createByReal(mm(PLATE[2]))
    )
    root.features.extrudeFeatures.add(extrude_input)


def _vertical_holes(root, body):
    for x, y in ((12.0, 10.0), (12.0, 30.0)):
        _drill_z(root, body, x, y, HOLE_DIAMETER)


def _threaded_hole(root, plate):
    face = _drill_z(root, plate, 30.0, 10.0, HOLE_DIAMETER)
    if face is None:
        return False
    # A *cosmetic* thread on purpose: a modelled one is not a plain cylinder
    # and the rules would never mistake it for a bore. The cosmetic kind
    # leaves the cylinder untouched, which is exactly the case the thread
    # exclusion has to catch.
    try:
        threads = root.features.threadFeatures
        query = threads.threadDataQuery
        thread_type = query.defaultMetricThreadType
        ok, designation, thread_class = query.recommendThreadData(
            mm(HOLE_DIAMETER), True, thread_type
        )
        if not ok:
            return False
        info = threads.createThreadInfo(False, thread_type, designation, thread_class)
        faces = adsk.core.ObjectCollection.create()
        faces.add(face)
        threads.add(threads.createInput(faces, info))
        return True
    except Exception:
        # Thread data varies between Fusion builds; the rest of the part is
        # still worth having, so say nothing broke and carry on.
        return False


def _drill_z(root, body, x, y, diameter):
    """A through hole down the build direction. Returns its cylinder face."""
    sketch = root.sketches.add(root.xYConstructionPlane)
    sketch.sketchCurves.sketchCircles.addByCenterRadius(
        adsk.core.Point3D.create(mm(x), mm(y), 0), mm(diameter / 2.0)
    )
    return _cut_through(root, body, sketch)


def _horizontal_bore(root, body, x):
    """A bore across the build direction: the teardrop candidate."""
    sketch = root.sketches.add(root.xZConstructionPlane)
    # On the XZ plane the sketch's Y runs along the model's Z, and its normal
    # runs along the model's Y -- which is the direction the bore needs.
    sketch.sketchCurves.sketchCircles.addByCenterRadius(
        adsk.core.Point3D.create(mm(x), mm(PLATE[2] / 2.0), 0),
        mm(BORE_DIAMETER / 2.0),
    )
    return _cut_through(root, body, sketch)


def _cross_hole(root, block):
    """A hole crossing the block's bore, so the bore is no longer a cylinder."""
    sketch = root.sketches.add(root.yZConstructionPlane)
    sketch.sketchCurves.sketchCircles.addByCenterRadius(
        adsk.core.Point3D.create(mm(BLOCK[1] / 2.0), mm(BLOCK[2] / 2.0), 0),
        mm(CROSS_DIAMETER / 2.0),
    )
    return _cut_through(root, block, sketch)


def _cut_through(root, body, sketch):
    extrudes = root.features.extrudeFeatures
    extrude_input = extrudes.createInput(
        sketch.profiles.item(0), adsk.fusion.FeatureOperations.CutFeatureOperation
    )
    extrude_input.participantBodies = [body]
    # Symmetric "through all" saves working out which way each sketch plane
    # happens to face.
    extrude_input.setAllExtent(adsk.fusion.ExtentDirections.SymmetricExtentDirection)
    feature = extrudes.add(extrude_input)

    for index in range(feature.sideFaces.count):
        face = feature.sideFaces.item(index)
        if face.geometry.surfaceType == adsk.core.SurfaceTypes.CylinderSurfaceType:
            return face
    return None
