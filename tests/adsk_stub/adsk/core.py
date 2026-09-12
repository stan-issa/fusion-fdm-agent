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
