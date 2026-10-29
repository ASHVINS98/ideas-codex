"""Local Docker desktop, or direct CLI commands without a desktop."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def desktop():
    password = os.environ.pop("VNC_PASSWORD", "")
    if len(password) != 8 or not password.isascii() or any(character.isspace() or ord(character) < 33 or ord(character) > 126 for character in password):
        raise ValueError("Set VNC_PASSWORD in .env to exactly 8 printable ASCII characters, without spaces")
    os.environ.setdefault("DISPLAY", ":99")
    children = []
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with tempfile.TemporaryDirectory(prefix="naukri-desktop-") as temporary:
        password_file = Path(temporary) / "vnc-password"
        # Never print the password or expose the VNC TCP port on the host.
        result = subprocess.run(["x11vnc", "-storepasswd", password, str(password_file)], capture_output=True)
        password = ""
        if result.returncode:
            raise RuntimeError("Could not initialize the local viewer password")
        password_file.chmod(0o600)
        try:
            children.append(subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1440x900x24", "-nolisten", "tcp", "-ac"]))
            deadline = time.monotonic() + 10
            while not Path("/tmp/.X11-unix/X99").exists():
                if children[0].poll() is not None or time.monotonic() >= deadline or stopping:
                    raise RuntimeError("Virtual display did not start")
                time.sleep(0.1)
            children.append(subprocess.Popen(["openbox", "--sm-disable"]))
            children.append(subprocess.Popen([
                "x11vnc", "-display", ":99", "-rfbauth", str(password_file),
                "-listen", "127.0.0.1", "-rfbport", "5900", "-forever", "-shared", "-noxdamage",
            ]))
            children.append(subprocess.Popen([
                "websockify", "--web", "/usr/share/novnc", "0.0.0.0:6080", "127.0.0.1:5900",
            ]))
            print("Local browser viewer ready on port 6080. Run the login command in a second terminal.", flush=True)
            while not stopping:
                for child in children:
                    if child.poll() is not None:
                        raise RuntimeError("A local desktop service exited; inspect container logs")
                time.sleep(0.25)
            return 0
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
            deadline = time.monotonic() + 8
            for child in reversed(children):
                try:
                    child.wait(timeout=max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


def main():
    os.umask(0o077)
    args = sys.argv[1:] or ["desktop"]
    if args == ["desktop"]:
        try:
            return desktop()
        except (ValueError, RuntimeError, OSError) as error:
            print(f"Desktop setup stopped: {error}", file=sys.stderr)
            return 1
    os.execv(sys.executable, [sys.executable, "-m", "naukri_assistant", *args])


if __name__ == "__main__":
    raise SystemExit(main())
