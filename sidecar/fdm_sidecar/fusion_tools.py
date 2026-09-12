"""The Fusion design tools, as in-process SDK tools for the model.

These are the payoff of running the agent in its own process: because the SDK
lives here, the tools are ordinary Python functions registered with
``create_sdk_mcp_server`` -- no separate MCP server process to launch,
supervise or authenticate. Each one forwards to the add-in over the loopback
connection, which executes it on Fusion's main thread.
"""

import json
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import fusion_client

SERVER_NAME = "fusion"

# What the model is allowed to call, in `mcp__<server>__<tool>` form.
TOOL_NAMES = [
    "get_design_tree",
    "get_parameters",
    "set_parameter",
    "run_fusion_script",
]

ALLOWED_TOOLS = ["mcp__{}__{}".format(SERVER_NAME, name) for name in TOOL_NAMES]

# Tools that change the document. The approval callback gates exactly these.
MUTATING_TOOLS = {
    "mcp__{}__set_parameter".format(SERVER_NAME),
    "mcp__{}__run_fusion_script".format(SERVER_NAME),
}


def _ok(payload: Any) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}]}


def _failed(message: str) -> dict:
    # isError lets the model see the failure and adapt, rather than the turn
    # dying with an exception it never learns about.
    return {"content": [{"type": "text", "text": message}], "isError": True}


async def _forward(name: str, payload: dict) -> dict:
    try:
        return _ok(await fusion_client.call(name, payload))
    except fusion_client.FusionCallFailed as exc:
        return _failed(str(exc))
    except fusion_client.FusionUnavailable as exc:
        return _failed(str(exc))


@tool(
    "get_design_tree",
    "Inspect the open Fusion design: components, bodies (with bounding boxes "
    "and volumes in mm), and sketch names. Call this before reasoning about "
    "the user's model.",
    {
        "type": "object",
        "properties": {
            "max_depth": {
                "type": "integer",
                "description": "How deep to walk the component hierarchy (1-4).",
                "default": 3,
            },
            "include_sketches": {"type": "boolean", "default": True},
        },
    },
)
async def get_design_tree(args: dict) -> dict:
    return await _forward("get_design_tree", {
        "max_depth": args.get("max_depth", 3),
        "include_sketches": args.get("include_sketches", True),
    })


@tool(
    "get_parameters",
    "List the design's user parameters and named model parameters, with their "
    "expressions, values and units.",
    {"type": "object", "properties": {}},
)
async def get_parameters(args: dict) -> dict:
    return await _forward("get_parameters", {})


@tool(
    "set_parameter",
    "Change one parameter's expression, e.g. wall_thickness -> '2.4 mm'. "
    "Prefer this over a script when a parameter already exists. Requires the "
    "user's approval.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact parameter name."},
            "expression": {
                "type": "string",
                "description": "Fusion expression including units, e.g. '2.4 mm'.",
            },
        },
        "required": ["name", "expression"],
    },
)
async def set_parameter(args: dict) -> dict:
    return await _forward("set_parameter", {
        "name": args.get("name"),
        "expression": args.get("expression"),
    })


@tool(
    "run_fusion_script",
    "Execute Python against the live Fusion API inside the running "
    "application. `adsk`, `app`, `ui`, `design` and `root` are predefined. "
    "Anything printed is returned, as is a `result` variable if you set one. "
    "Requires the user's approval, so keep scripts short and readable, and "
    "prefer set_parameter for simple parameter changes.",
    {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source to execute."}
        },
        "required": ["code"],
    },
)
async def run_fusion_script(args: dict) -> dict:
    return await _forward("run_fusion_script", {"code": args.get("code", "")})


def build_server():
    """Create the in-process MCP server holding the tools above."""
    return create_sdk_mcp_server(
        name=SERVER_NAME,
        version="0.1.0",
        tools=[get_design_tree, get_parameters, set_parameter, run_fusion_script],
    )
