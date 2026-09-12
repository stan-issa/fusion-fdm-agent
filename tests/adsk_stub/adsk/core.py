"""Minimal stand-in for adsk.core, enough to import the add-in outside Fusion."""


class _Handler:
    def __init__(self, *a, **k):
        pass


class CustomEventHandler(_Handler):
    pass


class HTMLEventHandler(_Handler):
    pass


class CommandEventHandler(_Handler):
    pass


class CommandCreatedEventHandler(_Handler):
    pass


class ActiveSelectionEventHandler(_Handler):
    pass


class PaletteDockingStates:
    PaletteDockStateFloating = 0
    PaletteDockStateTop = 1
    PaletteDockStateBottom = 2
    PaletteDockStateLeft = 3
    PaletteDockStateRight = 4


class Application:
    @staticmethod
    def get():
        raise RuntimeError("no Fusion application outside Fusion")


# Enough of the value types and enumerations for the add-in's modules to
# import and for their arithmetic to be exercised. Deliberately *not* a fake
# B-Rep kernel: tests that need real topology use Fusion, and a stub elaborate
# enough to fake faces and edges would only ever test itself.


class _Enum:
    """An enumeration whose members are distinct, comparable placeholders."""

    def __init__(self, *names):
        for index, name in enumerate(names):
            setattr(self, name, index)


class Point3D:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z

    @staticmethod
    def create(x=0.0, y=0.0, z=0.0):
        return Point3D(x, y, z)

    def asArray(self):
        return [self.x, self.y, self.z]


class Vector3D(Point3D):
    @staticmethod
    def create(x=0.0, y=0.0, z=0.0):
        return Vector3D(x, y, z)


class ValueInput:
    def __init__(self, value):
        self.value = value

    @staticmethod
    def createByReal(value):
        return ValueInput(value)

    @staticmethod
    def createByString(expression):
        return ValueInput(expression)


class ObjectCollection(list):
    @staticmethod
    def create():
        return ObjectCollection()

    @property
    def count(self):
        return len(self)

    def item(self, index):
        return self[index]


SurfaceTypes = _Enum(
    "PlaneSurfaceType", "CylinderSurfaceType", "ConeSurfaceType",
    "SphereSurfaceType", "TorusSurfaceType", "NurbsSurfaceType",
)

Curve3DTypes = _Enum(
    "Line3DCurveType", "Circle3DCurveType", "Arc3DCurveType",
    "EllipseCurveType", "NurbsCurve3DCurveType",
)
