"""Supervises the agent sidecar process.

The sidecar runs the Claude Agent SDK, which needs pip-installed packages and
asyncio -- neither of which belongs in Fusion's embedded interpreter. It lives
in its own process under a user-level venv and speaks newline-delimited JSON
over stdio.

This module is deliberately stdlib-only, because nothing can be installed into
the interpreter it runs in.
"""

import json
import os
import subprocess
import threading
import traceback

from .. import config
from .logging_util import get_logger


class SidecarError(Exception):
    """The sidecar could not be started or died unexpectedly."""


class SidecarProcess:
    """Owns the sidecar subprocess and both of its reader threads.

    Callbacks fire on background threads. Callers are responsible for hopping
    to the main thread before touching anything Fusion owns.
    """

    def __init__(self, on_message, on_exit):
        self._on_message = on_message
        self._on_exit = on_exit
        self._process = None
        self._stdout_thread = None
        self._stderr_thread = None
        self._write_lock = threading.Lock()
        self._stopping = False
        self._log = get_logger()

    def is_running(self):
        return self._process is not None and self._process.poll() is None

    def start(self):
        """Spawn the sidecar. Raises SidecarError if it cannot be launched."""
        if self.is_running():
            return

        if not os.path.exists(config.VENV_PYTHON):
            raise SidecarError(
                "Sidecar environment missing at {}. Run scripts/bootstrap.sh.".format(
                    config.VENV_PYTHON
                )
            )

        os.makedirs(config.WORKSPACE_DIR, exist_ok=True)
        os.makedirs(config.LOG_DIR, exist_ok=True)

        env = dict(os.environ)
        # Fusion injects its own Python into these; leaking them into the venv
        # makes the sidecar import Fusion's stdlib and fail in confusing ways.
        for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"):
            env.pop(key, None)
        env["PYTHONUNBUFFERED"] = "1"
        env["FDM_AGENT_WORKSPACE"] = config.WORKSPACE_DIR
        env["FDM_AGENT_LOG_FILE"] = config.SIDECAR_LOG_FILE

        self._stopping = False
        try:
            self._process = subprocess.Popen(
                [config.VENV_PYTHON, "-u", "-m", "fdm_sidecar"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=config.WORKSPACE_DIR,
                env=env,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise SidecarError("Could not start sidecar: {}".format(exc)) from exc

        self._log.info("sidecar started pid=%s", self._process.pid)

        self._stdout_thread = threading.Thread(
            target=self._read_stdout, name="fdm-sidecar-stdout", daemon=True
        )
        self._stdout_thread.start()
        # stderr needs its own drain: an unread pipe fills up and deadlocks the
        # child once the OS buffer is full.
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, name="fdm-sidecar-stderr", daemon=True
        )
        self._stderr_thread.start()

    def send(self, message):
        """Write one JSON message to the sidecar's stdin."""
        if not self.is_running():
            raise SidecarError("Sidecar is not running.")
        line = json.dumps(message, ensure_ascii=False) + "\n"
        with self._write_lock:
            try:
                self._process.stdin.write(line)
                self._process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise SidecarError("Sidecar stdin closed: {}".format(exc)) from exc

    def stop(self, timeout=5):
        """Terminate the sidecar, escalating to kill if it does not exit."""
        self._stopping = True
        process = self._process
        self._process = None
        if process is None:
            return

        try:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
        except OSError:
            pass

        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._log.warning("sidecar did not exit; terminating")
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._log.warning("sidecar did not terminate; killing")
                process.kill()
        self._log.info("sidecar stopped")

    def _read_stdout(self):
        process = self._process
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    self._log.warning("sidecar sent non-JSON: %s", line[:500])
                    continue
                try:
                    self._on_message(message)
                except Exception:
                    self._log.error(
                        "sidecar message handler failed\n%s", traceback.format_exc()
                    )
        except (OSError, ValueError):
            pass

        code = process.poll()
        self._log.info("sidecar stdout closed, exit=%s", code)
        if not self._stopping:
            try:
                self._on_exit(code)
            except Exception:
                self._log.error("sidecar exit handler failed\n%s", traceback.format_exc())

    def _read_stderr(self):
        process = self._process
        try:
            for line in process.stderr:
                line = line.rstrip()
                if line:
                    self._log.warning("sidecar stderr: %s", line)
        except (OSError, ValueError):
            pass
