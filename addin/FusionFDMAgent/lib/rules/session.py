"""Runs the rules against the open document and keeps the result.

One session serves both ways a fix can be asked for -- the Rules panel and the
agent's tools -- so the two cannot disagree about what was found. It owns the
things neither a rule nor a tool should have to think about: what counts as
in scope, whether the document is in a state that can accept features at all,
and what happens to a finding once the geometry beneath it has changed.

Main thread only. Every entry point here is reached either from a palette
event or through the tool server's pump, both of which already run there.
"""

import time

import adsk.fusion

from ... import config
from .. import selection as selection_module
from ..logging_util import get_logger
from . import RULES, BY_ID, orientation
from . import fusion_geom as fg
from .base import RuleContext, RuleError, describe_params, merge_params

# Caps, in the spirit of the ones in design_tools: detection runs on Fusion's
# UI thread, so an imported 50,000-face mesh must degrade into a truncated
# answer rather than a frozen application.
MAX_BODIES = 20
MAX_FACES_PER_BODY = 4000
MAX_FINDINGS_PER_RULE = 100


class RuleSession:
    """Detection results for one document, and the fixes that follow."""

    def __init__(self, app):
        self._app = app
        self._log = get_logger()
        self._findings = {}          # finding id -> Finding
        self._order = []             # finding ids, in the order found
        self._revision = 0
        self._document = None
        self._check_id = None
        self._params = {}            # rule id -> the params that check ran with
        self._notes = []
        self._build_direction = None

    # -- catalogue ---------------------------------------------------------

    def catalogue(self):
        """Every rule, with the parameter values currently in force."""
        stored = _stored_rule_settings()
        described = []
        for rule in RULES:
            block = stored.get(rule.ID, {})
            values = merge_params(rule.PARAMS, block)
            described.append({
                "id": rule.ID,
                "title": rule.TITLE,
                "description": rule.DESCRIPTION,
                "enabled": block.get("enabled", True),
                "params": describe_params(rule.PARAMS, values),
            })
        return {"rules": described}

    def save_rule_settings(self, rule_id, enabled=None, params=None):
        rule = BY_ID.get(rule_id)
        if rule is None:
            raise RuleError("No rule called {!r}.".format(rule_id))
        settings = config.load_settings()
        blocks = dict(settings.get("rules") or {})
        block = dict(blocks.get(rule_id) or {})
        if enabled is not None:
            block["enabled"] = bool(enabled)
        if params:
            block.update(merge_params(rule.PARAMS, block, params))
        blocks[rule_id] = block
        settings["rules"] = blocks
        config.save_settings(settings)
        return self.catalogue()

    # -- checking ----------------------------------------------------------

    def check(self, rule_ids=None, overrides=None):
        """Run detection and replace whatever the session held before."""
        design = _design(self._app)
        context, notes = self._build_context(design)
        wanted = self._wanted(rule_ids)

        self._findings = {}
        self._order = []
        self._params = {}
        self._notes = notes
        self._document = _document_key(self._app)
        self._revision += 1
        self._check_id = "c{}".format(self._revision)

        errors = []
        for rule in wanted:
            params = self._params_for(rule, overrides)
            self._params[rule.ID] = params
            # Notes a rule adds are attributed to it, so "4 bores under the
            # minimum diameter" says which rule had a minimum diameter.
            written = len(context.notes)
            try:
                found = rule.detect(context, params)
            except RuleError as exc:
                errors.append({"rule": rule.ID, "message": str(exc)})
                continue
            except Exception as exc:
                self._log.error("%s.detect failed", rule.ID, exc_info=True)
                errors.append({
                    "rule": rule.ID,
                    "message": "{} failed: {}".format(rule.TITLE, exc),
                })
                continue
            self._store(rule, found, errors)
            self._notes.extend(
                "{}: {}".format(rule.TITLE, text)
                for text in context.notes[written:]
            )

        return self.result(errors=errors, state=_document_state(design))

    def _store(self, rule, found, errors):
        if len(found) > MAX_FINDINGS_PER_RULE:
            errors.append({
                "rule": rule.ID,
                "message": "Stopped after {} findings; narrow the check with a "
                           "selection.".format(MAX_FINDINGS_PER_RULE),
            })
            found = found[:MAX_FINDINGS_PER_RULE]
        for finding in found:
            # Ids are hashes of geometry, so a rule that reports the same
            # feature twice would otherwise overwrite itself silently.
            if finding.id in self._findings:
                continue
            self._findings[finding.id] = finding
            self._order.append(finding.id)

    def result(self, errors=None, state=None):
        return {
            "checkId": self._check_id,
            "revision": self._revision,
            "buildDirection": (
                self._build_direction.as_dict() if self._build_direction else None
            ),
            "findings": [self._findings[key].as_dict() for key in self._order],
            "notes": list(self._notes),
            "errors": list(errors or []),
            "state": state or {},
        }

    # -- context -----------------------------------------------------------

    def _build_context(self, design):
        notes = []
        bodies, notes = _bodies_in_scope(design, notes)
        watcher = selection_module.watcher()
        selected = watcher.entities() if watcher is not None else []

        self._build_direction = orientation.resolve(bodies, selected)
        if self._build_direction.source == orientation.BuildDirection.DEFAULT:
            notes.append(
                "Could not find a flat face resting on the bed, so +Z was assumed. "
                "Select the bed face and check again to correct it."
            )

        context = RuleContext(
            app=self._app,
            design=design,
            bodies=bodies,
            selection=selected,
            build_direction=self._build_direction,
            units=_units(design),
        )
        context.resolve = self._resolve
        return context, notes

    def _wanted(self, rule_ids):
        if rule_ids:
            unknown = [name for name in rule_ids if name not in BY_ID]
            if unknown:
                raise RuleError(
                    "No rule called {}. Known rules: {}.".format(
                        ", ".join(repr(name) for name in unknown),
                        ", ".join(BY_ID),
                    )
                )
            wanted = [BY_ID[name] for name in rule_ids]
            # Keep the module order regardless of how they were asked for: it
            # is the order fixes must be applied in.
            return [rule for rule in RULES if rule in wanted]
        stored = _stored_rule_settings()
        return [
            rule for rule in RULES
            if (stored.get(rule.ID) or {}).get("enabled", True)
        ]

    def _params_for(self, rule, overrides):
        stored = _stored_rule_settings().get(rule.ID, {})
        per_call = (overrides or {}).get(rule.ID) if overrides else None
        return merge_params(rule.PARAMS, stored, per_call)

    # -- resolving stale findings -----------------------------------------

    def _resolve(self, finding, reveal=False):
        """Find a finding's geometry again after the body has regenerated.

        Because a finding's id is a hash of its geometry rather than its
        position in a list, re-running detection and looking the id up is both
        the simplest way to do this and the most honest: an id that no longer
        appears describes geometry that has genuinely gone, not geometry that
        merely moved.
        """
        rule = BY_ID.get(finding.rule)
        if rule is None:
            return []
        design = _design(self._app)
        context, _notes = self._build_context(design)
        params = self._params.get(finding.rule) or self._params_for(rule, None)
        try:
            for candidate in rule.detect(context, params):
                if candidate.id == finding.id:
                    return candidate.reveal if reveal else candidate.entities
        except Exception:
            self._log.debug("re-detect for %s failed", finding.id, exc_info=True)
        return []

    # -- applying ----------------------------------------------------------

    def apply(self, finding_ids, overrides=None):
        design = _design(self._app)
        self._guard(design)

        selected = self._select(finding_ids)
        outcomes = []
        context, _notes = self._build_context(design)

        # RULES is ordered most-destructive first, and each group re-reads the
        # document, so a rule never works from geometry an earlier one rewrote.
        for rule in RULES:
            group = [self._findings[key] for key in selected if self._findings[key].rule == rule.ID]
            group = _drop_superseded(rule, group, outcomes)
            if not group:
                continue
            params = self._params.get(rule.ID) or self._params_for(rule, overrides)
            context, _notes = self._build_context(design)
            try:
                outcomes.extend(rule.apply(context, group, params))
            except RuleError as exc:
                outcomes.extend(_all_failed(group, str(exc)))
            except Exception as exc:
                self._log.error("%s.apply failed", rule.ID, exc_info=True)
                outcomes.extend(_all_failed(group, "{}: {}".format(type(exc).__name__, exc)))

        applied = [o for o in outcomes if o.status == "applied"]
        self._log.info("applied %d of %d findings", len(applied), len(selected))

        # Every fix invalidates the findings around it, so the session is only
        # honest if it re-reads the model rather than crossing items off a list.
        refreshed = self.check(list(self._params.keys()) or None, overrides)
        refreshed["outcomes"] = [outcome.as_dict() for outcome in outcomes]
        refreshed["appliedCount"] = len(applied)
        return refreshed

    def _select(self, finding_ids):
        if not finding_ids:
            raise RuleError("No findings were selected.")
        missing = [key for key in finding_ids if key not in self._findings]
        if missing:
            raise RuleError(
                "These findings are no longer current: {}. Run the check "
                "again.".format(", ".join(missing))
            )
        return [key for key in self._order if key in set(finding_ids)]

    def _guard(self, design):
        """Refuse to modify a document that cannot safely take a feature."""
        if not _same_document(self._document, _document_key(self._app)):
            raise RuleError(
                "The active document changed since the check. Run the check again."
            )

        try:
            if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
                raise RuleError(
                    "This design is in direct modelling mode. Switch to "
                    "parametric, or apply these fixes by hand."
                )
        except AttributeError:
            pass

        try:
            timeline = design.timeline
            if timeline.markerPosition < timeline.count:
                raise RuleError(
                    "The timeline marker is not at the end. New features would "
                    "be inserted into the middle of your history; drag the "
                    "marker to the end and try again."
                )
        except AttributeError:
            pass

        try:
            active = self._app.userInterface.activeCommand
        except Exception:
            active = None
        if active and active != "SelectCommand":
            raise RuleError(
                "Fusion is busy with another command. Finish or cancel it, "
                "then try again."
            )

    # -- showing a finding -------------------------------------------------

    def reveal(self, finding_id):
        """Select a finding's geometry in the canvas so the user can see it."""
        finding = self._findings.get(finding_id)
        if finding is None:
            raise RuleError("No current finding called {!r}.".format(finding_id))
        if not finding.reveal:
            raise RuleError("That finding has no geometry to show.")
        entities = [entity for entity in finding.reveal if fg.is_valid(entity)]
        if not entities:
            entities = self._resolve(finding, reveal=True)
        if not entities:
            raise RuleError(
                "That finding's geometry is no longer in the model. Run the "
                "check again."
            )

        selections = self._app.userInterface.activeSelections
        selections.clear()
        for entity in entities:
            try:
                selections.add(entity)
            except Exception:
                continue
        return {"id": finding_id, "selected": len(entities)}

    def describe(self, finding_ids):
        """What a set of ids means, for an approval card or a log line.

        Unknown ids come back marked as such rather than omitted: a card that
        quietly drops an id it cannot explain would be describing something
        other than the request the user is being asked to approve.
        """
        described = []
        for finding_id in finding_ids or []:
            finding = self._findings.get(finding_id)
            if finding is None:
                described.append({"id": finding_id, "title": finding_id, "unknown": True})
                continue
            described.append({
                "id": finding_id,
                "rule": finding.rule,
                "title": finding.title,
                "fix": finding.fix_summary,
                "body": finding.body,
            })
        return described

    def ignore(self, finding_id, ignore=True):
        """Mark a finding's geometry so future checks pass over it.

        Stored as a Fusion attribute on the entity, so it travels with the
        document. Some exclusions -- a bearing seat, a surface that will be
        machined -- cannot be read off the geometry, and this is the only way
        for the person who knows to say so once.
        """
        finding = self._findings.get(finding_id)
        if finding is None:
            raise RuleError("No current finding called {!r}.".format(finding_id))
        marked = 0
        for entity in finding.entities:
            if fg.set_ignored(entity, finding.rule, ignore):
                marked += 1
        return {"id": finding_id, "ignored": bool(ignore), "entities": marked}


# -- helpers ---------------------------------------------------------------


def _drop_superseded(rule, group, outcomes):
    """Stop two rules fixing the same feature in one pass.

    A horizontal bore can be both a teardrop candidate and a lead-in
    candidate. Both findings hash the same face, so the shared suffix of their
    ids is what gives the overlap away. The teardrop runs first and rewrites
    the entrance ring, so the lead-in is dropped rather than left to fail.
    """
    from .base import Outcome

    if rule.ID != "hole_lead_in":
        return group
    teardropped = {
        outcome.finding_id.split(":", 1)[-1]
        for outcome in outcomes
        if outcome.status == Outcome.APPLIED and outcome.finding_id.startswith("teardrop_bore:")
    }
    kept = []
    for finding in group:
        if finding.id.split(":", 1)[-1] in teardropped:
            outcomes.append(Outcome.skipped(
                finding.id,
                "Teardropped in this pass, which reshapes the mouth; re-check "
                "if you still want a lead-in.",
            ))
            continue
        kept.append(finding)
    return kept


def _all_failed(group, message):
    from .base import Outcome

    return [Outcome.failed(finding.id, message) for finding in group]


def _design(app):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        raise RuleError(
            "No Fusion design is open. Open or create a design and try again."
        )
    return design


def _document_key(app):
    """Enough to tell one open document from another, across two calls.

    Not the document object, and not its Python ``id``: Fusion hands back a
    fresh proxy every time it is asked, so two reads of the same document look
    like two different objects. ``dataFile`` is the durable identity, but it
    exists only once a document has been saved, so the name comes along for
    the ones that have not been.
    """
    try:
        document = app.activeDocument
    except Exception:
        return None

    key = {"file": None, "name": None}
    try:
        data_file = document.dataFile
        if data_file is not None:
            key["file"] = data_file.id
    except Exception:
        pass
    try:
        key["name"] = document.name
    except Exception:
        pass
    return key if (key["file"] or key["name"]) else None


def _same_document(before, after):
    """Whether two keys describe the same document.

    Ids are compared only when both keys have one. Saving a document between
    the check and the apply gives it a ``dataFile`` it did not have before,
    and that is not a reason to throw the user's findings away.
    """
    if not before or not after:
        return False
    if before.get("file") and after.get("file"):
        return before["file"] == after["file"]
    return before.get("name") == after.get("name")


def _document_state(design):
    state = {}
    try:
        state["parametric"] = (
            design.designType == adsk.fusion.DesignTypes.ParametricDesignType
        )
    except Exception:
        pass
    try:
        timeline = design.timeline
        state["timelineAtEnd"] = timeline.markerPosition >= timeline.count
    except Exception:
        state["timelineAtEnd"] = True
    state["checkedAt"] = time.strftime("%H:%M:%S")
    return state


def _units(design):
    try:
        return design.fusionUnitsManager.distanceDisplayUnits
    except Exception:
        return None


def _bodies_in_scope(design, notes):
    """Visible solid bodies of the active component, within the caps."""
    component = design.activeComponent or design.rootComponent
    bodies, meshes, occurrences, oversized = [], 0, 0, 0

    try:
        meshes = component.meshBodies.count
    except Exception:
        meshes = 0

    collection = component.bRepBodies
    for index in range(collection.count):
        body = collection.item(index)
        try:
            if not body.isSolid or not body.isLightBulbOn:
                continue
        except Exception:
            continue
        if not fg.is_native_body(body):
            occurrences += 1
            continue
        try:
            if body.faces.count > MAX_FACES_PER_BODY:
                oversized += 1
                continue
        except Exception:
            pass
        bodies.append(body)
        if len(bodies) >= MAX_BODIES:
            break

    if collection.count > len(bodies) + occurrences + oversized:
        notes.append(
            "Checked {} of {} bodies.".format(len(bodies), collection.count)
        )
    if meshes:
        notes.append(
            "{} mesh bod{} skipped: mesh has no faces or edges to analyse.".format(
                meshes, "y" if meshes == 1 else "ies"
            )
        )
    if occurrences:
        notes.append(
            "{} bod{} reached through a component instance skipped; open that "
            "component and check it there.".format(
                occurrences, "y" if occurrences == 1 else "ies"
            )
        )
    if oversized:
        notes.append(
            "{} very large bod{} skipped to keep Fusion responsive.".format(
                oversized, "y" if oversized == 1 else "ies"
            )
        )
    if not bodies:
        notes.append("No visible solid bodies to check in the active component.")
    return bodies, notes


def _stored_rule_settings():
    settings = config.load_settings()
    blocks = settings.get("rules")
    return blocks if isinstance(blocks, dict) else {}
