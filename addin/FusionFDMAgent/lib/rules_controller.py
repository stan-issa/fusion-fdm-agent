"""One rules session, shared by the panel and the agent.

The Rules tab and the agent's tools are two front ends onto the same work, and
if each kept its own state they would disagree the moment either one used it:
the user would tick a finding the agent had already fixed, or the agent would
apply a fix against a list the panel had refreshed. So the session lives here
and both go through it.

That also makes the approval card readable. When the agent asks to apply a
fix, the request on the wire is a list of opaque ids; the bridge asks this
module what those ids mean so the card can say what will actually change.

Main thread only.
"""

import adsk.core

from .logging_util import get_logger
from .rules.session import RuleSession

_session = None
# Set by the bridge. Called whenever the session's findings change for a
# reason the panel did not initiate -- an agent check, or an agent fix -- so
# the Rules tab does not sit showing a stale list behind the conversation.
_on_result = None


def session():
    global _session
    if _session is None:
        _session = RuleSession(adsk.core.Application.get())
    return _session


def reset():
    global _session, _on_result
    _session = None
    _on_result = None


def set_result_listener(listener):
    global _on_result
    _on_result = listener


def _publish(result):
    if _on_result is None:
        return
    try:
        _on_result(result)
    except Exception:
        get_logger().debug("publishing rule results failed", exc_info=True)
    return result


# -- operations ------------------------------------------------------------


def list_rules():
    return session().catalogue()


def check_rules(rules=None, params=None, announce=True):
    result = session().check(rules, params)
    if announce:
        _publish(result)
    return result


def apply_rule_fix(finding_ids, params=None, announce=True):
    result = session().apply(finding_ids, params)
    if announce:
        _publish(result)
    return result


def reveal(finding_id):
    return session().reveal(finding_id)


def ignore(finding_id, ignore_it=True):
    result = session().ignore(finding_id, ignore_it)
    # Ignoring changes what a check would find, so refresh rather than leave
    # the row sitting there looking unchanged.
    refreshed = check_rules(announce=False)
    refreshed["ignored"] = result
    return _publish(refreshed) or refreshed


def save_rule_settings(rule_id, enabled=None, params=None):
    return session().save_rule_settings(rule_id, enabled, params)


# -- describing a pending fix ----------------------------------------------


def describe_findings(finding_ids):
    """What these ids mean, for an approval card."""
    return session().describe(finding_ids)
