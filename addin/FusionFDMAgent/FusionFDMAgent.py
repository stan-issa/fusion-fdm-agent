"""Fusion FDM Agent add-in entry point.

Fusion calls ``run`` when the add-in starts and ``stop`` when it is stopped or
Fusion quits. Everything else lives under ``lib/``.
"""

import traceback

import adsk.core

from .lib.logging_util import get_logger
from .lib.ui import AddInUI

_addin_ui = None


def run(context):
    global _addin_ui
    app = adsk.core.Application.get()
    ui = app.userInterface
    log = get_logger()
    try:
        log.info("--- add-in starting ---")
        _addin_ui = AddInUI(app, ui)
        _addin_ui.start()
    except Exception:
        message = "Fusion FDM Agent failed to start:\n{}".format(traceback.format_exc())
        log.error(message)
        if ui:
            ui.messageBox(message)


def stop(context):
    global _addin_ui
    app = adsk.core.Application.get()
    ui = app.userInterface
    log = get_logger()
    try:
        log.info("--- add-in stopping ---")
        if _addin_ui is not None:
            _addin_ui.stop()
    except Exception:
        message = "Fusion FDM Agent failed to stop cleanly:\n{}".format(
            traceback.format_exc()
        )
        log.error(message)
        if ui:
            ui.messageBox(message)
    finally:
        _addin_ui = None
