"""The Fusion operations exposed to the agent.

Every function here touches the Fusion API and so **must run on the main
thread**. Nothing in this module does its own marshalling -- `toolserver.py`
is responsible for getting here from the socket thread.

Return values must be JSON-serialisable, and are deliberately bounded: a large
assembly could otherwise produce a design tree far bigger than the model's
context window.
"""

import contextlib
import io
import traceback

import adsk.core
import adsk.fusion

from .logging_util import get_logger

# Caps that keep a big assembly from flooding the model's context.
MAX_DEPTH = 4
MAX_CHILDREN = 40
MAX_BODIES = 40
MAX_SKETCHES = 40
MAX_SCRIPT_OUTPUT = 20000


class ToolError(Exception):
    """A tool could not run. The message is shown to the agent and the user."""


def _design():
    app = adsk.core.Application.get()
    product = app.activeProduct
    design = adsk.fusion.Design.cast(product)
    if design is None:
        raise ToolError(
            "No Fusion design is open. Open or create a design and try again."
        )
    return design


def _length_unit(design):
    try:
        return design.fusionUnitsManager.distanceDisplayUnits
    except Exception:
        return None


# -- read ------------------------------------------------------------------


def get_design_tree(max_depth: int = 3, include_sketches: bool = True) -> dict:
    """Summarise the active design's component hierarchy."""
    design = _design()
    depth = max(1, min(int(max_depth), MAX_DEPTH))
    root = design.rootComponent

    app = adsk.core.Application.get()
    document = app.activeDocument

    return {
        "document": document.name if document else None,
        "units": _length_unit(design),
        "root": _component(root, depth, include_sketches),
    }


def _component(component, depth_left: int, include_sketches: bool) -> dict:
    info = {
        "name": component.name,
        "bodies": [],
        "children": [],
    }

    bodies = component.bRepBodies
    for index in range(min(bodies.count, MAX_BODIES)):
        info["bodies"].append(_body(bodies.item(index)))
    if bodies.count > MAX_BODIES:
        info["bodiesTruncated"] = bodies.count - MAX_BODIES

    if include_sketches:
        sketches = component.sketches
        names = [sketches.item(i).name for i in range(min(sketches.count, MAX_SKETCHES))]
        if names:
            info["sketches"] = names
        if sketches.count > MAX_SKETCHES:
            info["sketchesTruncated"] = sketches.count - MAX_SKETCHES

    if depth_left <= 1:
        if component.occurrences.count:
            info["childrenTruncated"] = component.occurrences.count
        return info

    occurrences = component.occurrences
    for index in range(min(occurrences.count, MAX_CHILDREN)):
        occurrence = occurrences.item(index)
        child = _component(occurrence.component, depth_left - 1, include_sketches)
        child["occurrence"] = occurrence.name
        child["visible"] = occurrence.isLightBulbOn
        info["children"].append(child)
    if occurrences.count > MAX_CHILDREN:
        info["childrenTruncated"] = occurrences.count - MAX_CHILDREN

    return info


def _body(body) -> dict:
    info = {"name": body.name, "visible": body.isLightBulbOn, "solid": body.isSolid}
    try:
        # Fusion works in centimetres internally; mm is what anyone printing
        # a part actually thinks in.
        info["volumeMm3"] = round(body.volume * 1000.0, 3)
    except Exception:
        pass
    try:
        box = body.boundingBox
        info["boundingBoxMm"] = {
            "min": [round(v * 10.0, 3) for v in (box.minPoint.x, box.minPoint.y, box.minPoint.z)],
            "max": [round(v * 10.0, 3) for v in (box.maxPoint.x, box.maxPoint.y, box.maxPoint.z)],
        }
    except Exception:
        pass
    return info


def get_parameters() -> dict:
    """List the design's user parameters, and named model parameters."""
    design = _design()

    user = []
    parameters = design.userParameters
    for index in range(parameters.count):
        user.append(_parameter(parameters.item(index)))

    model = []
    all_parameters = design.allParameters
    user_names = {entry["name"] for entry in user}
    for index in range(all_parameters.count):
        parameter = all_parameters.item(index)
        # Model parameters are mostly auto-named noise (d1, d2...); only the
        # ones someone deliberately renamed are worth the context.
        name = parameter.name
        if name in user_names or _looks_auto_named(name):
            continue
        model.append(_parameter(parameter))

    return {"units": _length_unit(design), "userParameters": user, "modelParameters": model}


def _looks_auto_named(name: str) -> bool:
    return len(name) > 1 and name[0] == "d" and name[1:].isdigit()


def _parameter(parameter) -> dict:
    info = {"name": parameter.name, "expression": parameter.expression}
    try:
        info["value"] = parameter.value
        info["unit"] = parameter.unit
    except Exception:
        pass
    try:
        if parameter.comment:
            info["comment"] = parameter.comment
    except Exception:
        pass
    return info


# -- write -----------------------------------------------------------------


def set_parameter(name: str, expression: str) -> dict:
    """Set one parameter's expression, e.g. ``wall_thickness`` -> ``"2.4 mm"``."""
    if not name:
        raise ToolError("A parameter name is required.")
    design = _design()

    parameter = design.userParameters.itemByName(name)
    if parameter is None:
        parameter = design.allParameters.itemByName(name)
    if parameter is None:
        available = [
            design.userParameters.item(i).name
            for i in range(min(design.userParameters.count, 30))
        ]
        raise ToolError(
            "No parameter named {!r}. User parameters: {}".format(
                name, ", ".join(available) or "(none)"
            )
        )

    previous = parameter.expression
    try:
        parameter.expression = str(expression)
    except Exception as exc:
        raise ToolError(
            "Fusion rejected {!r} for {!r}: {}".format(expression, name, exc)
        ) from exc

    get_logger().info("set_parameter %s: %s -> %s", name, previous, parameter.expression)
    return {
        "name": parameter.name,
        "previousExpression": previous,
        "expression": parameter.expression,
        "value": parameter.value,
        "unit": parameter.unit,
    }


def run_fusion_script(code: str) -> dict:
    """Execute Python against the live Fusion API.

    The script runs in this add-in's own process with the full API available,
    so it can do anything the user could. That is the point, and it is why
    every call is gated on an explicit approval before reaching here.

    Anything printed is captured and returned, as is a ``result`` variable if
    the script sets one.
    """
    if not code or not code.strip():
        raise ToolError("No code supplied.")

    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)

    namespace = {
        "adsk": adsk,
        "app": app,
        "ui": app.userInterface,
        "design": design,
        "root": design.rootComponent if design else None,
        "__name__": "__fusion_script__",
    }

    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            exec(compile(code, "<agent script>", "exec"), namespace)
    except Exception:
        output = stdout.getvalue()
        get_logger().error("run_fusion_script failed\n%s", traceback.format_exc())
        raise ToolError(
            "Script raised:\n{}{}".format(
                traceback.format_exc(limit=6),
                "\nOutput before the error:\n" + output if output else "",
            )
        )

    output = stdout.getvalue()
    if len(output) > MAX_SCRIPT_OUTPUT:
        output = output[:MAX_SCRIPT_OUTPUT] + "\n... (truncated)"

    response = {"ok": True, "output": output}
    if "result" in namespace:
        try:
            response["result"] = repr(namespace["result"])[:MAX_SCRIPT_OUTPUT]
        except Exception:
            pass
    get_logger().info("run_fusion_script ok (%d chars of output)", len(output))
    return response


# Tools that change the document, and therefore need the user's approval.
MUTATING = frozenset({"set_parameter", "run_fusion_script"})

REGISTRY = {
    "get_design_tree": get_design_tree,
    "get_parameters": get_parameters,
    "set_parameter": set_parameter,
    "run_fusion_script": run_fusion_script,
}
