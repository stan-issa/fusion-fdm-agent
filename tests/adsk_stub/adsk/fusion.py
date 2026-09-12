"""Stub standing in for adsk.fusion outside Fusion.

Only enough to let the add-in's modules import and to give their enumeration
comparisons something to compare. Nothing here models geometry; see the note
in core.py about why that is deliberate.
"""

from .core import _Enum


class Design:
    @staticmethod
    def cast(arg):
        return None


class _Castable:
    @staticmethod
    def cast(arg):
        return None


class BRepBody(_Castable):
    pass


class BRepFace(_Castable):
    pass


class BRepEdge(_Castable):
    pass


class Occurrence(_Castable):
    pass


class DistanceExtentDefinition:
    @staticmethod
    def create(value):
        return DistanceExtentDefinition()


DesignTypes = _Enum("DirectDesignType", "ParametricDesignType")

PointContainment = _Enum(
    "PointInsidePointContainment",
    "PointOnBoundaryPointContainment",
    "PointOutsidePointContainment",
    "UnknownPointContainment",
)

FeatureOperations = _Enum(
    "JoinFeatureOperation", "CutFeatureOperation", "IntersectFeatureOperation",
    "NewBodyFeatureOperation", "NewComponentFeatureOperation",
)

ExtentDirections = _Enum("PositiveExtentDirection", "NegativeExtentDirection")

FeatureHealthStates = _Enum(
    "HealthyFeatureHealthState", "WarningFeatureHealthState",
    "ErrorFeatureHealthState", "SuppressedFeatureHealthState",
    "RolledBackFeatureHealthState", "UnknownFeatureHealthState",
)
