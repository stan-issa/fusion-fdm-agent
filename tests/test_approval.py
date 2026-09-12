"""Checks the approval round trip in the sidecar.

Approval is the safety boundary for a tool that can run arbitrary code against
the user's document, so the failure modes matter as much as the happy path: a
prompt nobody answers, and a turn cancelled mid-prompt, must both come back
as a refusal rather than parking the coroutine forever.

Needs the sidecar venv (it imports the SDK through the backends package).
Run with ./scripts/test.sh
"""

import asyncio
import sys

from fdm_sidecar.__main__ import ApprovalBroker

failures = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s %s" % (label, detail))
        failures.append(label)


async def run() -> None:
    sent = []
    broker = ApprovalBroker(sent.append)

    # -- allowed ----------------------------------------------------------
    task = asyncio.create_task(broker.request("mcp__fusion__set_parameter", {"name": "w"}))
    await asyncio.sleep(0.05)
    check("request is emitted", len(sent) == 1 and sent[0]["action"] == "approvalRequest", repr(sent))
    check("request carries tool and input",
          sent[0].get("tool") == "mcp__fusion__set_parameter" and sent[0]["input"] == {"name": "w"},
          repr(sent[0]))
    broker.resolve(sent[0]["id"], True)
    check("allow resolves true", await task is True)

    # -- denied -----------------------------------------------------------
    sent.clear()
    task = asyncio.create_task(broker.request("mcp__fusion__run_fusion_script", {"code": "pass"}))
    await asyncio.sleep(0.05)
    broker.resolve(sent[0]["id"], False)
    check("deny resolves false", await task is False)

    # -- cancelled turn ---------------------------------------------------
    sent.clear()
    task = asyncio.create_task(broker.request("mcp__fusion__run_fusion_script", {"code": "pass"}))
    await asyncio.sleep(0.05)
    broker.cancel_all()
    check("cancel_all denies outstanding prompts", await task is False)

    # -- nobody answers ---------------------------------------------------
    sent.clear()
    broker.TIMEOUT_SECONDS = 0.2
    check("timeout denies", await broker.request("mcp__fusion__run_fusion_script", {}) is False)

    # -- a stale answer must not explode ----------------------------------
    broker.resolve("nosuch", True)
    check("unknown id is ignored", True)


def main() -> int:
    asyncio.run(run())
    if failures:
        print("\n%d check(s) failed" % len(failures))
        return 1
    print("\nOK: approval requests, denials, cancellation and timeout all behave")
    return 0


if __name__ == "__main__":
    sys.exit(main())
