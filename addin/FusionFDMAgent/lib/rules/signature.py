"""Stable identities for findings, and a way to find an entity again.

Two problems share one answer.

*Identity.* A finding's id must survive a re-check. Numbering findings in
discovery order fails the moment detection reorders them: the box the user
ticked now refers to a different edge. Hashing a geometric description instead
means the same edge keeps the same id across checks.

*Resolution.* Adding any feature regenerates the body and invalidates every
live ``BRepEdge`` from the previous detection. Entity tokens survive a little
longer but not past the topology change that a fix *is*. Because ids are
stable, re-resolution needs no separate mechanism: ``session`` re-runs
detection and matches on the id, and a finding whose id no longer appears has
genuinely gone rather than merely moved.

Everything here is pure: signatures are plain dicts of numbers and strings.
"""

import hashlib

# Positions are quantised before hashing so that floating-point noise between
# two detections cannot change an id. 0.01 mm is far finer than any feature a
# printer resolves and far coarser than the noise.
QUANTUM_MM = 0.01

def quantise(value, quantum=QUANTUM_MM):
    return round(float(value) / quantum) * quantum


def quantise_point(point, quantum=QUANTUM_MM):
    return tuple(quantise(axis, quantum) for axis in point)


def make(kind, body, **fields):
    """Build a signature. ``fields`` are millimetres, or plain descriptors."""
    signature = {"kind": kind, "body": body or ""}
    for name, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (tuple, list)):
            signature[name] = quantise_point(value)
        elif isinstance(value, (int, float)):
            signature[name] = quantise(value)
        else:
            signature[name] = value
    return signature


def key(signature, length=8):
    """A short, stable id for a signature."""
    return hashlib.sha1(_canonical(signature).encode("utf-8")).hexdigest()[:length]


def finding_id(rule, signature):
    return "{}:{}".format(rule, key(signature))


def _canonical(signature):
    parts = []
    for name in sorted(signature):
        value = signature[name]
        if isinstance(value, (tuple, list)):
            rendered = ",".join("{:.4f}".format(item) for item in value)
        elif isinstance(value, float):
            rendered = "{:.4f}".format(value)
        else:
            rendered = str(value)
        parts.append("{}={}".format(name, rendered))
    return "|".join(parts)
