"""
Shared rig plumbing for the projector/camera spikes: display lookup, the
fullscreen projector window, camera open/grab, and blank-and-capture.

Built for the HP Sprout Pro (downward projector + overhead camera), but any
projector extended onto the Windows desktop plus a DirectShow camera works.

    uv run config/checkerboard.py --list     # show displays + cameras, then exit
"""

import argparse
import ctypes
import sys
from pathlib import Path

import cv2
import numpy as np

CONFIG_DIR = Path(__file__).resolve().parent
HOMOGRAPHY_PATH = CONFIG_DIR / "cam_to_proj_homography.npz"
PROJ_WIN = "projector"
PREVIEW_MAX_W = 1280               # camera previews are shrunk to fit this width


def make_dpi_aware():
    """Without this, Windows display scaling shrinks/offsets the fullscreen projector window."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor aware
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


make_dpi_aware()   # must run before any window or monitor query


# ---------------- displays ----------------

def list_displays():
    from screeninfo import get_monitors
    return get_monitors()


def pick_display(index=None):
    """Explicit index, else the first non-primary display (the Sprout's projector)."""
    mons = list_displays()
    if index is not None:
        return mons[index]
    for m in mons:
        if not m.is_primary:
            return m
    return mons[0]


class Projector:
    def __init__(self, display):
        self.w, self.h = display.width, display.height
        self.black = np.zeros((self.h, self.w, 3), np.uint8)
        cv2.namedWindow(PROJ_WIN, cv2.WINDOW_NORMAL)
        cv2.moveWindow(PROJ_WIN, display.x, display.y)
        cv2.setWindowProperty(PROJ_WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    def show(self, img, settle_ms=1):
        cv2.imshow(PROJ_WIN, img)
        cv2.waitKey(max(1, settle_ms))      # settle_ms lets the projection reach the camera

    def blank(self, settle_ms=1):
        self.show(self.black, settle_ms)


# ---------------- cameras ----------------

def list_cameras(max_index=8):
    """DirectShow names (if pygrabber is installed) next to OpenCV indices."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        names = FilterGraph().get_input_devices()
    except Exception:
        names = []
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            found.append((i, names[i] if i < len(names) else "?", w, h))
        cap.release()
    return found


def open_camera(index, resolution=None, manual_exposure=True):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise RuntimeError(f"camera {index} did not open - close LightGuide / HP apps "
                           "that may be holding it, or check --list")
    if resolution:
        # most high-res webcam modes are only offered as MJPG
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if manual_exposure:
        # lock exposure so the projection doesn't blow out the image (driver-dependent;
        # press S in the scripts to open the driver dialog if this is ignored)
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
    w, h = camera_size(cap)
    print(f"Camera {index} opened at {w}x{h}")
    if resolution and (w, h) != tuple(resolution):
        print(f"  warning: asked for {resolution[0]}x{resolution[1]}, driver gave {w}x{h}")
    return cap


def camera_size(cap):
    return int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))


def open_camera_settings(cap):
    """DirectShow property dialog: exposure, focus, white balance."""
    cap.set(cv2.CAP_PROP_SETTINGS, 1)


def grab_frame(cap, flush=5):
    # flush a few buffered frames so we see the current projection
    for _ in range(flush):
        cap.grab()
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError("camera read failed")
    return frame


def blank_and_capture(proj, cap, restore=None, settle_ms=300):
    """Projector to black, settle, grab, then put `restore` back up (if given)."""
    proj.blank(settle_ms)
    frame = grab_frame(cap)
    if restore is not None:
        proj.show(restore)
    return frame


def fit_preview(frame, max_w=PREVIEW_MAX_W):
    """Downscaled copy for on-screen display, and the scale used."""
    scale = min(1.0, max_w / frame.shape[1])
    return cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), scale


# ---------------- CLI ----------------

def parse_res(text):
    w, h = text.lower().split("x")
    return int(w), int(h)


def add_rig_args(parser: argparse.ArgumentParser):
    parser.add_argument("--camera", type=int, default=0,
                        help="OpenCV/DirectShow camera index (see --list)")
    parser.add_argument("--cam-res", type=parse_res, default=None, metavar="WxH",
                        help="camera capture resolution, e.g. 3840x2160")
    parser.add_argument("--display", type=int, default=None,
                        help="projector display index (default: first non-primary)")
    parser.add_argument("--settle-ms", type=int, default=300,
                        help="wait after changing the projection before grabbing")
    parser.add_argument("--list", action="store_true",
                        help="list displays and cameras, then exit")


def handle_list(args):
    if not args.list:
        return False
    print("Displays:")
    for i, m in enumerate(list_displays()):
        print(f"  [{i}] {m.width}x{m.height} at ({m.x},{m.y})"
              f"{'  primary' if m.is_primary else ''}  {m.name or ''}")
    print("Cameras (DirectShow):")
    for i, name, w, h in list_cameras():
        print(f"  [{i}] {name}  default {w}x{h}")
    return True


def load_homography(proj, cap):
    if not HOMOGRAPHY_PATH.exists():
        sys.exit(f"{HOMOGRAPHY_PATH.name} not found - run checkerboard.py first.")
    data = np.load(HOMOGRAPHY_PATH)
    if "proj_size" in data and tuple(data["proj_size"]) != (proj.w, proj.h):
        print(f"warning: homography was solved at projector {tuple(data['proj_size'])}, "
              f"now {proj.w}x{proj.h}")
    if "cam_size" in data and tuple(data["cam_size"]) != camera_size(cap):
        print(f"warning: homography was solved at camera {tuple(data['cam_size'])}, "
              f"now {camera_size(cap)} - recalibrate")
    return data["H"]
