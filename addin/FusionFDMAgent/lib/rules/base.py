"""The vocabulary every rule shares: findings, context, parameters, results.

Nothing here touches the Fusion API. A rule module is expected to expose::

    ID, TITLE, DESCRIPTION, PARAMS, NEEDS_BUILD_DIRECTION
    detect(context, params) -> list[Finding]
    apply(context, findings, params) -> list[Outcome]

``detect`` must never modify the document, and ``apply`` is only ever reached
after the user has agreed to it -- either by ticking a box in the Rules panel
or by allowing an approval card.
"""


class RuleError(Exception):
    """A rule could not run, with a message meant for the user and the agent.

    ``design_tools`` re-raises this as its own ``ToolError``, which is the
    exception ``toolserver`` recognises (by class name) as "report this, do not
    log a traceback".
    """


# A finding is one *fixable unit*, not one entity: a whole bed face rather than
# each of its forty edges. That is what makes the panel a list a person can
# read, and it is why the skipped-entity reasons live inside the finding.
class Finding:
    def __init__(
        self,
        identifier,
        rule,
        title,
        detail="",
        body=None,
        fixable=True,
        fix_summary="",
        entities=None,
        tokens=None,
        skipped=None,
        extra=None,
        reveal=None,
    ):
        self.id = identifier
        self.rule = rule
        self.title = title
        self.detail = detail
        self.body = body
        self.fixable = fixable
        self.fix_summary = fix_summary
        # Live Fusion entities, valid only until the next feature regenerates
        # the body. `tokens` is how a finding survives that; see session.py.
        self.entities = list(entities or [])
        self.tokens = list(tokens or [])
        # What "Show" selects in the canvas. Usually the geometry the fix acts
        # on, but a finding with nothing to fix still has something to point
        # at -- and that is exactly when the user most needs to see what the
        # rule is talking about.
        self.reveal = list(reveal) if reveal else list(self.entities)
        # [(description, reason)] for anything detect() deliberately left out.
        # Shown to the user: a silent skip is the worst thing this could do.
        self.skipped = list(skipped or [])
        self.extra = dict(extra or {})

    def as_dict(self):
        """The JSON shape sent to the palette and returned to the agent.

        Entities and tokens are deliberately absent: one is not serialisable
        and the other is a long opaque string the model can do nothing with.
        """
        payload = {
            "id": self.id,
            "rule": self.rule,
            "title": self.title,
            "fixable": self.fixable,
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.body:
            payload["body"] = self.body
        if self.fix_summary:
            payload["fix"] = self.fix_summary
        if self.skipped:
            payload["skipped"] = [
                {"what": what, "why": why} for what, why in self.skipped
            ]
        return payload


class Outcome:
    """What happened to one finding during apply."""

    APPLIED = "applied"
    FAILED = "failed"
    SKIPPED = "skipped"

    def __init__(self, finding_id, status, message="", feature=None):
        self.finding_id = finding_id
        self.status = status
        self.message = message
        self.feature = feature

    @classmethod
    def applied(cls, finding_id, message="", feature=None):
        return cls(finding_id, cls.APPLIED, message, feature)

    @classmethod
    def failed(cls, finding_id, message):
        return cls(finding_id, cls.FAILED, message)

    @classmethod
    def skipped(cls, finding_id, message):
        return cls(finding_id, cls.SKIPPED, message)

    def as_dict(self):
        payload = {"id": self.finding_id, "status": self.status}
        if self.message:
            payload["message"] = self.message
        return payload


class RuleContext:
    """Everything a rule needs about the document, resolved once per check.

    Built by ``session.py`` on Fusion's main thread. Rules read it; they never
    build one.
    """

    def __init__(
        self,
        app=None,
        design=None,
        bodies=None,
        selection=None,
        build_direction=None,
        units=None,
    ):
        self.app = app
        self.design = design
        self.bodies = list(bodies or [])
        # A `Selection` from orientation.py, or None.
        self.selection = selection
        # A `BuildDirection` from orientation.py, or None for rules that do not
        # need one (NEEDS_BUILD_DIRECTION is False).
        self.build_direction = build_direction
        self.units = units
        # Installed by the session: given a finding, return its geometry as it
        # is *now*. Every feature regenerates the body, so a rule applying a
        # second fix must ask again rather than trust what detection handed it.
        self.resolve = None
        # Things a rule wants to say that are not findings -- above all, why
        # it found nothing. A rule that examines geometry and rejects it must
        # be able to say so; silence is indistinguishable from not looking.
        self.notes = []
        # Populated lazily and cached, because computing entity tokens for
        # every face in a model is not free.
        self._thread_tokens = None

    def note(self, text):
        """Record something the user should know about this check.

        Deduplicated, because rules report per-body and the same sentence
        would otherwise arrive once per body.
        """
        if text and text not in self.notes:
            self.notes.append(text)

    def is_thread_face(self, face):
        """True if this face belongs to a thread feature.

        Asks for the token only when the document actually has threads in it:
        computing an entity token is not free, and most models have none.
        """
        tokens = self.thread_face_tokens()
        if not tokens:
            return False
        from . import fusion_geom

        return fusion_geom.entity_token(face) in tokens

    def thread_face_tokens(self):
        """Entity tokens of faces belonging to any thread feature.

        Modelled threads are not plain cylinders and so never reach the bore
        tests, but *cosmetic* threads leave the cylinder untouched and are
        invisible without asking the feature. Chamfering or teardropping a
        threaded hole would be wrong in both cases.
        """
        if self._thread_tokens is None:
            self._thread_tokens = _collect_thread_tokens(self.design)
        return self._thread_tokens


def _collect_thread_tokens(design):
    tokens = set()
    if design is None:
        return tokens
    try:
        components = design.allComponents
    except Exception:
        return tokens
    for index in range(components.count):
        try:
            threads = components.item(index).features.threadFeatures
        except Exception:
            continue
        for thread_index in range(threads.count):
            try:
                faces = threads.item(thread_index).threadFaces
            except Exception:
                continue
            for face_index in range(faces.count):
                try:
                    tokens.add(faces.item(face_index).entityToken)
                except Exception:
                    pass
    return tokens


# -- parameters ------------------------------------------------------------
#
# A rule declares PARAMS as {name: {"default":, "min":, "max":, "label":,
# "unit":}}. Values arrive from three places, each overriding the last:
# the declaration, the user's settings file, and one call's arguments.


def merge_params(spec, *layers):
    """Resolve a rule's parameters, coercing and clamping to the declaration.

    Out-of-range values are clamped rather than rejected: a rule that refuses
    to run because a stored setting drifted is worse than one that runs at the
    nearest sane value, and the resolved value is reported back either way.
    """
    resolved = {}
    for name, declaration in spec.items():
        value = declaration.get("default")
        for layer in layers:
            if isinstance(layer, dict) and name in layer and layer[name] is not None:
                value = layer[name]
        resolved[name] = _coerce(name, value, declaration)
    return resolved


def _coerce(name, value, declaration):
    default = declaration.get("default")
    if isinstance(default, bool):
        return bool(value)
    choices = declaration.get("choices")
    if choices is not None:
        text = str(value)
        if text not in choices:
            raise RuleError(
                "{!r} must be one of {}, got {!r}.".format(
                    name, ", ".join(repr(choice) for choice in choices), value
                )
            )
        return text
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise RuleError(
            "{!r} must be a number, got {!r}.".format(name, value)
        ) from None
    if number != number:  # NaN, which would clamp to itself and poison the rule
        raise RuleError("{!r} must be a number, got NaN.".format(name))
    minimum = declaration.get("min")
    maximum = declaration.get("max")
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return int(number) if isinstance(default, int) else number


def describe_params(spec, values):
    """Parameter declarations plus their resolved values, for the panel."""
    described = []
    for name, declaration in spec.items():
        described.append({
            "name": name,
            "label": declaration.get("label", name),
            "unit": declaration.get("unit", ""),
            "default": declaration.get("default"),
            "min": declaration.get("min"),
            "max": declaration.get("max"),
            "choices": declaration.get("choices"),
            "value": values.get(name, declaration.get("default")),
        })
    return described
