"""The rule catalogue.

Rules are deliberately ordinary modules rather than classes or plugins: a rule
is a handful of constants and two functions, and anything more ceremonious
would make adding one feel like a project.

Order matters here. It is the order fixes are applied in, and it runs from the
most destructive change to the least, because each feature regenerates the
body and rewrites the edges the later rules were aiming at. Teardropping a
bore destroys the entrance ring a lead-in would have chamfered; chamfering the
footprint rewrites every edge of the bottom face. Going the other way round
would leave the second rule pointing at geometry that no longer exists.
"""

from . import bed_chamfer, hole_lead_in, teardrop_bore

RULES = (teardrop_bore, hole_lead_in, bed_chamfer)

BY_ID = {rule.ID: rule for rule in RULES}


def get(rule_id):
    return BY_ID.get(rule_id)


def ids():
    return [rule.ID for rule in RULES]
