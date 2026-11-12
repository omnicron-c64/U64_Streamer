"""Copy an RGB frame to the desktop clipboard."""

import ctypes
from ctypes import wintypes
import io
import os
import shutil
import struct
import subprocess
import sys

import pygame


def bitmap_data(frame):
    height, width, _ = frame.shape
    padding = bytes((-width * 3) % 4)
    pixels = b"".join(row.tobytes() + padding for row in frame[::-1, :, ::-1])
    header = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 0, 0, 0, 0)
    return header + pixels


def copy_windows(frame):
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user.SetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalFree.restype = wintypes.HGLOBAL

    data = bitmap_data(frame)
    handle = kernel.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        address = kernel.GlobalLock(handle)
        if not address:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(address, data, len(data))
        finally:
            kernel.GlobalUnlock(handle)
        if not user.OpenClipboard(pygame.display.get_wm_info()["window"]):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not user.EmptyClipboard() or not user.SetClipboardData(8, handle):  # CF_DIB
                raise ctypes.WinError(ctypes.get_last_error())
            handle = None  # Windows now owns the memory.
        finally:
            user.CloseClipboard()
    finally:
        if handle:
            kernel.GlobalFree(handle)


def copy_snapshot(frame):
    if sys.platform == "win32":
        copy_windows(frame)
        return
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        command = ["wl-copy", "--type", "image/png"]
    elif os.environ.get("DISPLAY") and shutil.which("xclip"):
        command = ["xclip", "-selection", "clipboard", "-t", "image/png", "-i"]
    else:
        raise RuntimeError("Install wl-clipboard (Wayland) or xclip (X11) to copy images.")
    image = pygame.surfarray.make_surface(frame.transpose(1, 0, 2))
    png = io.BytesIO()
    pygame.image.save(image, png, "snapshot.png")
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        if "LD_LIBRARY_PATH_ORIG" in env:
            env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
        else:
            env.pop("LD_LIBRARY_PATH", None)
    subprocess.run(command, input=png.getvalue(), check=True, timeout=3,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
