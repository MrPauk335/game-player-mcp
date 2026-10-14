"""Screen capture of a game window, tuned for vision models.

Pillow is the only third-party dependency in the whole project and it is used
exclusively here.
"""

from __future__ import annotations

import ctypes
import io
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageGrab

from . import winapi

#: ``ImageGrab.grab`` uses a short-lived GDI copy of the screen; all_screens
#: lets us capture windows that straddle monitors.
_ALL_SCREENS = True

#: PrintWindow flag that asks the window to render its full content, including
#: hardware-composited surfaces that plain PrintWindow returns black for.
_PW_RENDERFULLCONTENT = 0x00000002

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
# HDCs are pointer-sized. Without argtypes ctypes narrows them to a C int and
# CreateCompatibleDC raises "int too long to convert" on 64-bit Windows.
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                            wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                            wintypes.UINT]


@dataclass
class Frame:
    """One captured frame plus everything needed to map it back to the screen."""

    image: Image.Image
    jpeg: bytes
    width: int
    height: int
    source_width: int
    source_height: int
    origin: tuple[int, int]
    scale: float
    source: str

    def screen_to_frame(self, x: int, y: int) -> tuple[int, int]:
        return (
            int(round((x - self.origin[0]) * self.scale)),
            int(round((y - self.origin[1]) * self.scale)),
        )


def _bbox_for(window: winapi.WindowInfo, source: str) -> tuple[int, int, int, int, str]:
    if source == "desktop":
        vx, vy, vw, vh = winapi.virtual_screen()
        return vx, vy, vx + vw, vy + vh, "весь рабочий стол"
    if source == "client":
        left, top, right, bottom = (
            window.client_left,
            window.client_top,
            window.client_right,
            window.client_bottom,
        )
        label = "клиентская область"
    else:  # "window"
        left, top, right, bottom = window.left, window.top, window.right, window.bottom
        label = "окно целиком"
    if right - left < 8 or bottom - top < 8:
        vx, vy, vw, vh = winapi.virtual_screen()
        return vx, vy, vx + vw, vy + vh, "весь рабочий стол (окно недоступно)"
    return left, top, right, bottom, label


def grab_window_content(window: winapi.WindowInfo) -> Image.Image | None:
    """Ask the window to draw itself into a bitmap — works while it is covered.

    ``ImageGrab`` copies the screen, so a window behind another one comes back as
    that other window. ``PrintWindow`` with ``PW_RENDERFULLCONTENT`` asks the
    window to render itself instead, which is what makes an unfocused run
    possible at all. Verified on Minecraft 1.21.11 with the window deliberately
    behind the editor: the pause menu, the player skin and the HUD all came
    through.

    The bitmap is allocated at *window* size because PrintWindow draws the whole
    window including its frame; the client area is cropped out afterwards, so
    frame coordinates keep matching the ones clicks use.
    """
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    win_w = max(1, window.right - window.left)
    win_h = max(1, window.bottom - window.top)
    off_x = window.client_left - window.left
    off_y = window.client_top - window.top
    cli_w = max(1, window.client_width)
    cli_h = max(1, window.client_height)

    window_dc = user32.GetDC(window.hwnd)
    if not window_dc:
        return None
    mem_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, win_w, win_h)
    if not mem_dc or not bitmap:
        user32.ReleaseDC(window.hwnd, window_dc)
        return None
    previous = gdi32.SelectObject(mem_dc, bitmap)

    try:
        ok = user32.PrintWindow(window.hwnd, mem_dc, _PW_RENDERFULLCONTENT)
        if not ok:
            return None

        class _BitmapInfo(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        info = _BitmapInfo()
        info.biSize = ctypes.sizeof(_BitmapInfo)
        info.biWidth = win_w
        info.biHeight = -win_h          # negative = top-down rows
        info.biPlanes = 1
        info.biBitCount = 32
        buffer = ctypes.create_string_buffer(win_w * win_h * 4)
        copied = gdi32.GetDIBits(mem_dc, bitmap, 0, win_h, buffer,
                                 ctypes.byref(info), 0)
        if not copied:
            return None
        image = Image.frombuffer("RGBA", (win_w, win_h), buffer,
                                 "raw", "BGRA", 0, 1).convert("RGB")
    finally:
        gdi32.SelectObject(mem_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(window.hwnd, window_dc)

    return image.crop((off_x, off_y, off_x + cli_w, off_y + cli_h))


def grab(window: winapi.WindowInfo | None, source: str = "client",
         background: bool = False) -> Frame:
    """Capture a frame from ``window`` (or the whole desktop when it is None)."""
    if window is None:
        left, top, right, bottom, label = _bbox_for(_dummy_window(), "desktop")
    else:
        left, top, right, bottom, label = _bbox_for(window, source)

    if background and window is not None and source != "desktop":
        content = grab_window_content(window)
        if content is not None and content.width >= 8 and content.height >= 8:
            return Frame(
                image=content,
                jpeg=b"",
                width=content.width,
                height=content.height,
                source_width=content.width,
                source_height=content.height,
                origin=(window.client_left, window.client_top),
                scale=1.0,
                source=f"{label} (фоновый захват)",
            )

    image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=_ALL_SCREENS)
    image = image.convert("RGB")

    return Frame(
        image=image,
        jpeg=b"",
        width=image.width,
        height=image.height,
        source_width=image.width,
        source_height=image.height,
        origin=(left, top),
        scale=1.0,
        source=label,
    )


def _dummy_window() -> winapi.WindowInfo:
    return winapi.WindowInfo(
        hwnd=0, title="", pid=0, process="", exe_path="",
        left=0, top=0, right=0, bottom=0,
        client_left=0, client_top=0, client_right=0, client_bottom=0,
        minimized=False,
    )


def draw_grid(image: Image.Image) -> Image.Image:
    """Overlay a normalised coordinate grid so the model can read positions.

    Vision models are poor at guessing absolute coordinates from a picture.
    Measured: asked to click the "Возродиться" button, which sits at
    ``(0.62, 0.56)``, ``meta/llama-3.2-11b-vision-instruct`` and
    ``gemini-3.7-flash`` both aimed at ``(0.76, 0.71)`` and hit empty space —
    five clicks in a row, no progress. Reading a labelled grid is a far easier
    task than regressing a pixel position, so the lines are drawn in.

    The grid is deliberately faint and unlabelled in the middle of the frame so
    it does not hide the game; labels sit on the edges.
    """
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = image.size
    line = (255, 255, 255, 46)
    major = (255, 255, 255, 105)
    text_colour = (255, 240, 120, 220)

    for step in range(1, 10):
        fraction = step / 10
        colour = major if step % 5 == 0 else line
        x = int(round(width * fraction))
        y = int(round(height * fraction))
        draw.line([(x, 0), (x, height)], fill=colour, width=1)
        draw.line([(0, y), (width, y)], fill=colour, width=1)

    # Label the quarter lines on both axes, and the corners. The model needs to be
    # able to read a value off the picture, not interpolate from one number.
    for fraction in (0.25, 0.5, 0.75):
        x = int(round(width * fraction))
        y = int(round(height * fraction))
        draw.text((x + 3, 3), f"x={fraction:.2f}", fill=text_colour)
        draw.text((3, y + 3), f"y={fraction:.2f}", fill=text_colour)
    draw.text((3, 14), "x=0.00", fill=text_colour)
    draw.text((width - 52, 3), "x=1.00", fill=text_colour)
    draw.text((3, height - 13), "y=1.00", fill=text_colour)

    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def encode(frame: Frame, max_width: int = 1280, quality: int = 70,
           grid: bool = False) -> Frame:
    """Downscale (keeping aspect ratio) and JPEG-encode for the API."""
    image = frame.image
    scale = 1.0
    if max_width and image.width > max_width:
        scale = max_width / image.width
        image = image.resize(
            (max_width, max(1, int(round(image.height * scale)))),
            Image.LANCZOS,
        )
    if grid:
        image = draw_grid(image)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=int(quality), optimize=True)
    frame.image = image
    frame.jpeg = buffer.getvalue()
    frame.width = image.width
    frame.height = image.height
    frame.scale = scale
    return frame


def capture(window: winapi.WindowInfo | None, source: str = "client",
            max_width: int = 1280, quality: int = 70,
            grid: bool = False, background: bool = False) -> Frame:
    return encode(grab(window, source, background=background),
                  max_width=max_width, quality=quality, grid=grid)


def vision_probe_image(seed: int) -> tuple[bytes, str]:
    """A synthetic image with a random 3-digit code.

    Used by the "vision check" button: if the model cannot read the code back,
    it will not be able to read a game HUD either.
    """
    import random

    rng = random.Random(seed)
    code = f"{rng.randint(100, 999)}"
    shape = rng.choice(["circle", "square", "triangle"])
    colour = rng.choice(["red", "green", "blue"])

    img = Image.new("RGB", (480, 240), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    palette = {"red": (210, 40, 40), "green": (30, 150, 70), "blue": (40, 80, 210)}
    fill = palette[colour]
    if shape == "circle":
        draw.ellipse((30, 60, 150, 180), fill=fill)
    elif shape == "square":
        draw.rectangle((30, 60, 150, 180), fill=fill)
    else:
        draw.polygon([(90, 55), (25, 185), (155, 185)], fill=fill)
    draw.text((200, 100), code, fill=(20, 20, 20))

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    expected = f"{code} {shape} {colour}"
    return buffer.getvalue(), expected


def self_test() -> dict:
    frame = encode(grab(None, "desktop"), max_width=640, quality=60)
    return {
        "source": frame.source,
        "size": f"{frame.width}x{frame.height}",
        "jpeg_kb": round(len(frame.jpeg) / 1024, 1),
    }
