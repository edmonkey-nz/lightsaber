"""`run.py --open nvidia|default` — open the editor in its own Chrome once the
server is answering, so a show needs one command instead of two terminals.

**`nvidia` falls back to the default GPU in a Wayland session**, because
there it breaks video (measured 2026-11, ARCHITECTURE.md §12 "Correction"):
Chrome on the NVIDIA card through PRIME offload either draws video white/black
(OpenGL via GLX) or drops to software compositing (EGL, Vulkan). It is still
launched on the NVIDIA card in an X11 session — the untested route that may
work ("Ubuntu on Xorg" at login, with `prime-select nvidia`) — or when
LIGHTSABER_FORCE_NVIDIA=1 is set, e.g. for a visualiser-only show, which does
render correctly on the card under Wayland.

Why a SEPARATE Chrome profile (`--user-data-dir`), not just a new window:
Chrome runs one process per profile, so launching it while your everyday
Chrome is open only opens a window in that existing process — on whichever
GPU it already has. A profile of its own starts a separate Chrome that can sit
on a different GPU from everyday browsing. Output windows are opened from the
editor, so they inherit its GPU.

Why `nvidia` needs `--ozone-platform=x11`: in a Wayland session Chrome draws
natively through EGL and ignores the PRIME offload variables; under XWayland
it goes through GLX, where `__NV_PRIME_RENDER_OFFLOAD=1` +
`__GLX_VENDOR_LIBRARY_NAME=nvidia` select the card. (`prime-select nvidia`
does not help on Wayland: the desktop runs on the iGPU and Chrome follows it.)

Why Chrome is started DETACHED (its own session, output to a log file):
- Ctrl+C in the terminal goes to the whole foreground process group. Attached,
  stopping the server would close the editor and every output window with
  it — dark projectors. Detached, they show "disconnected", keep playing
  video, and reconnect by themselves when the server comes back, which is
  also what makes restarting the server after a Python change painless.
- Chrome prints a stream of harmless noise (sync registration, fontconfig,
  a sandboxed driver probe) that would bury the server's own messages. It
  goes to a log in the profile directory instead, so it's still there when
  something really is wrong.

If that Chrome is already running, nothing is launched — re-running the
command after a server restart must not stack up duplicate editor windows.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time

PROFILE_DIR = os.path.expanduser("~/.config/lightsaber-chrome")
_CHROMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def _find_chrome() -> str | None:
    for name in _CHROMES:
        path = shutil.which(name)
        if path:
            return path
    return None


def _profile_running(profile: str) -> bool:
    """Chrome marks a profile in use with a `SingletonLock` symlink pointing
    at "<hostname>-<pid>". A stale lock (Chrome crashed) points at a dead
    pid, which must not block a launch."""
    lock = os.path.join(profile, "SingletonLock")
    try:
        target = os.readlink(lock)
    except OSError:
        return False
    host, _, pid = target.rpartition("-")
    if host and host != socket.gethostname():
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def _wait_for_port(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def build_command(mode: str, url: str, chrome: str, profile: str = PROFILE_DIR):
    """(argv, env) for launching Chrome in `mode`. Split out so it can be
    checked without starting a browser."""
    env = dict(os.environ)
    argv = [chrome, f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check"]
    if mode == "nvidia":
        env["__NV_PRIME_RENDER_OFFLOAD"] = "1"
        env["__GLX_VENDOR_LIBRARY_NAME"] = "nvidia"
        argv.append("--ozone-platform=x11")
    argv.append(url)
    return argv, env


def open_editor(mode: str, port: int) -> None:
    """Start a background thread that opens the editor once the server is
    up. Never raises: a browser that can't be opened must not stop the
    server, which is still reachable by hand."""
    def run():
        url = f"http://localhost:{port}"
        mode_ = mode
        if mode == "nvidia" and not sys.platform.startswith("linux"):
            print("[lightsaber] --open nvidia uses Linux PRIME offload; opening with the "
                  "default GPU instead")
            mode_ = "default"
        elif (mode == "nvidia" and os.environ.get("XDG_SESSION_TYPE") == "wayland"
              and os.environ.get("LIGHTSABER_FORCE_NVIDIA") != "1"):
            print("[lightsaber] --open nvidia: in a Wayland session Chrome can't show VIDEO on the "
                  "NVIDIA card (it draws white/black), so opening on the default GPU instead. "
                  "Log in to 'Ubuntu on Xorg' to try the NVIDIA card, or set "
                  "LIGHTSABER_FORCE_NVIDIA=1 for a show with no video (visualisers are fine).")
            mode_ = "default"
        if _profile_running(PROFILE_DIR) and mode_ != mode:
            print("[lightsaber] (an already-open lightsaber Chrome keeps whichever GPU it "
                  "started on — close it first to switch)")
        chrome = _find_chrome()
        if not chrome:
            print(f"[lightsaber] --open: no Chrome/Chromium found on PATH — open {url} by hand")
            return
        if _profile_running(PROFILE_DIR):
            print("[lightsaber] the lightsaber Chrome is already open — it reconnects by "
                  "itself, so not opening another")
            return
        if not _wait_for_port(port):
            print(f"[lightsaber] --open: server didn't answer on port {port}; not opening Chrome")
            return
        os.makedirs(PROFILE_DIR, exist_ok=True)
        log_path = os.path.join(PROFILE_DIR, "launch.log")
        argv, env = build_command(mode_, url, chrome)
        try:
            with open(log_path, "ab") as log:
                log.write(f"\n--- {time.ctime()} {' '.join(argv)}\n".encode())
                log.flush()
                subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            print(f"[lightsaber] --open: could not start Chrome ({e}) — open {url} by hand")
            return
        gpu = "the NVIDIA GPU" if mode_ == "nvidia" else "the default GPU"
        print(f"[lightsaber] opened the editor in Chrome on {gpu} — check the GPU dot in "
              f"its header (green = dedicated). Chrome's own log: {log_path}")
    threading.Thread(target=run, name="open-editor", daemon=True).start()
