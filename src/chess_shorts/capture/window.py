from __future__ import annotations

import ctypes
import math
import sys
from dataclasses import dataclass

from PIL import Image

from ..control import CaptureControl, Cancelled
from ..session import Session


@dataclass(frozen=True)
class Window:
    handle: int
    title: str
    process_id: int
    browser: str


def enable_dpi_awareness():
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass


class WindowsCapture:
    """Capture the selected HWND client using PrintWindow; never grab the desktop.

    Chromium GPU surfaces may be black. A foreground-only client-DC fallback is
    available explicitly. Neither method promises protected/occluded capture.
    """
    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("Browser window capture requires Windows 11. Local video import works on this platform.")
        from ctypes import wintypes as wt
        self.wt = wt
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        # Explicit pointer-sized signatures are essential on 64-bit Windows.
        for name, result, args in [
            ("IsWindow", wt.BOOL, [wt.HWND]), ("IsWindowVisible", wt.BOOL, [wt.HWND]),
            ("IsIconic", wt.BOOL, [wt.HWND]), ("IsHungAppWindow", wt.BOOL, [wt.HWND]),
            ("GetWindowTextLengthW", ctypes.c_int, [wt.HWND]),
            ("GetWindowTextW", ctypes.c_int, [wt.HWND, wt.LPWSTR, ctypes.c_int]),
            ("GetWindowThreadProcessId", wt.DWORD, [wt.HWND, ctypes.POINTER(wt.DWORD)]),
            ("GetClientRect", wt.BOOL, [wt.HWND, ctypes.POINTER(wt.RECT)]),
            ("GetDC", wt.HDC, [wt.HWND]), ("ReleaseDC", ctypes.c_int, [wt.HWND, wt.HDC]),
            ("PrintWindow", wt.BOOL, [wt.HWND, wt.HDC, wt.UINT]),
            ("GetForegroundWindow", wt.HWND, []),
        ]:
            function = getattr(self.user, name)
            function.restype, function.argtypes = result, args
        for name, result, args in [
            ("CreateCompatibleDC", wt.HDC, [wt.HDC]),
            ("CreateCompatibleBitmap", wt.HBITMAP, [wt.HDC, ctypes.c_int, ctypes.c_int]),
            ("SelectObject", wt.HANDLE, [wt.HDC, wt.HANDLE]),
            ("DeleteObject", wt.BOOL, [wt.HANDLE]), ("DeleteDC", wt.BOOL, [wt.HDC]),
            ("BitBlt", wt.BOOL, [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]),
            ("GetDIBits", ctypes.c_int, [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                                        ctypes.c_void_p, ctypes.c_void_p, wt.UINT]),
        ]:
            function = getattr(self.gdi, name)
            function.restype, function.argtypes = result, args
        self.kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.kernel.OpenProcess.restype = wt.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = wt.BOOL
        self.kernel.CloseHandle.argtypes = [wt.HANDLE]
        self.kernel.CloseHandle.restype = wt.BOOL

    def windows(self):
        found = []
        wt = self.wt
        callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def visit(hwnd, _):
            if not self.user.IsWindowVisible(hwnd):
                return True
            count = self.user.GetWindowTextLengthW(hwnd)
            if not count:
                return True
            title = ctypes.create_unicode_buffer(count + 1)
            self.user.GetWindowTextW(hwnd, title, count + 1)
            pid = wt.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = self.kernel.OpenProcess(0x1000, False, pid.value)
            if process:
                try:
                    path = ctypes.create_unicode_buffer(32768)
                    size = wt.DWORD(len(path))
                    if self.kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                        browser = path.value.replace("\\", "/").rsplit("/", 1)[-1].lower()
                        if browser in ("brave.exe", "chrome.exe", "msedge.exe"):
                            found.append(Window(hwnd, title.value, pid.value, browser))
                finally:
                    self.kernel.CloseHandle(process)
            return True
        callback = callback_type(visit)
        self.user.EnumWindows.argtypes = [callback_type, wt.LPARAM]
        self.user.EnumWindows.restype = wt.BOOL
        if not self.user.EnumWindows(callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return found

    def grab(self, window: Window, fallback=False):
        wt, u, g = self.wt, self.user, self.gdi
        hwnd = window.handle
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not u.IsWindow(hwnd) or pid.value != window.process_id:
            raise RuntimeError("The selected browser window closed. Refresh the window list and select it again.")
        if u.IsIconic(hwnd):
            raise RuntimeError("The browser is minimized. Restore it before capturing.")
        if u.IsHungAppWindow(hwnd):
            raise RuntimeError("The browser is not responding. Wait for it to recover before capturing.")
        rectangle = wt.RECT()
        if not u.GetClientRect(hwnd, ctypes.byref(rectangle)):
            raise ctypes.WinError(ctypes.get_last_error())
        width, height = rectangle.right, rectangle.bottom
        if width <= 0 or height <= 0:
            raise RuntimeError("The selected browser has no visible client area.")
        source = u.GetDC(hwnd)
        dc = g.CreateCompatibleDC(source)
        bitmap = g.CreateCompatibleBitmap(source, width, height)
        if not source or not dc or not bitmap:
            if bitmap: g.DeleteObject(bitmap)
            if dc: g.DeleteDC(dc)
            if source: u.ReleaseDC(hwnd, source)
            raise RuntimeError("Windows could not allocate the capture surface.")
        old = g.SelectObject(dc, bitmap)
        try:
            if fallback:
                if u.GetForegroundWindow() != hwnd:
                    raise RuntimeError("Foreground fallback requires the selected browser to stay in front. Switch to it after starting capture.")
                success = g.BitBlt(dc, 0, 0, width, height, source, 0, 0, 0x00CC0020)
            else:
                success = u.PrintWindow(hwnd, dc, 0x00000001 | 0x00000002)
            if not success:
                raise RuntimeError("Windows could not capture this window. Restore it or try foreground fallback.")

            class Header(ctypes.Structure):
                _fields_ = [("size", wt.DWORD), ("width", wt.LONG), ("height", wt.LONG),
                            ("planes", wt.WORD), ("bits", wt.WORD), ("compression", wt.DWORD),
                            ("image_size", wt.DWORD), ("x", wt.LONG), ("y", wt.LONG),
                            ("used", wt.DWORD), ("important", wt.DWORD)]
            header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
            buffer = ctypes.create_string_buffer(width * height * 4)
            # A DIB must not be selected into a DC during GetDIBits.
            g.SelectObject(dc, old)
            if g.GetDIBits(dc, bitmap, 0, height, buffer, ctypes.byref(header), 0) != height:
                raise RuntimeError("Windows failed to read the capture pixels.")
            return Image.frombytes("RGB", (width, height), buffer.raw, "raw", "BGRX")
        finally:
            g.SelectObject(dc, old)
            g.DeleteObject(bitmap)
            g.DeleteDC(dc)
            u.ReleaseDC(hwnd, source)


def crop_image(image, crop):
    if len(crop) != 4 or not all(math.isfinite(v) for v in crop):
        raise ValueError("Invalid crop rectangle.")
    left, top, right, bottom = crop
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError("Select a nonempty video region inside the preview.")
    box = (round(left * image.width), round(top * image.height),
           round(right * image.width), round(bottom * image.height))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("The crop is too small at the current window size.")
    return image.crop(box)


def capture_window(backend, window, crop, session: Session, control: CaptureControl,
                   progress=None, duration=None, fallback=False):
    interval = session.data["requested_interval_seconds"]
    session.data.update({"window_title": window.title, "browser": window.browser,
                         "crop_normalized": list(crop),
                         "capture_backend": "foreground client BitBlt" if fallback else "PrintWindow client",
                         "timing_note": "Monotonic elapsed time excluding pauses, not video time; scheduling can drop samples."})
    session.save()
    control.begin()
    next_at = control.elapsed()
    try:
        while not control.stopped:
            control.wait_until(next_at)
            if duration is not None and control.elapsed() >= duration:
                break
            with backend.grab(window, fallback=fallback) as image:
                elapsed = control.elapsed()
                with crop_image(image, crop) as cropped:
                    session.record(cropped, elapsed=elapsed)
            if progress:
                progress(len(session.data["frames"]), 0)
            # Skip missed deadlines; never generate artificial catch-up duplicates.
            next_at = (math.floor(control.elapsed() / interval) + 1) * interval
    except Cancelled:
        pass
    except Exception as exc:
        session.data['interruptions'] = control.history()
        session.finish("error", exc)
        raise
    session.data['interruptions'] = control.history()
    session.finish("stopped" if control.stopped else "complete")
    return session
