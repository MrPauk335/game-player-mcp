"""MCP-сервер: отдаёт агенту глаза и руки для управления игрой в окне.

Разрез намеренно узкий. Обычный computer-use MCP даёт агенту весь рабочий стол;
здесь только два действия — **посмотреть в одно окно** и **подействовать в нём**,
а решение принимает вызывающий агент. Отсюда три отличия:

* **Прицельно по окну.** Кадр берётся с клиентской области игры, координаты
  нормализованы 0..1, поэтому модель не зависит от разрешения и позиции окна.
* **Фоновый режим.** ``background=True`` снимает через ``PrintWindow`` и шлёт
  ввод через ``PostMessage``: игра не забирает фокус, пользователь может
  работать за компьютером. Требует в настройках игры ``pauseOnLostFocus:false``
  и ``rawMouseInput:false``.
* **Белый список действий.** Всё, что приходит от агента, проходит через
  ``ActionExecutor``: запрещённые клавиши отклоняются, удержания ограничены,
  за вызов не больше 8 действий. Агент не может случайно выйти в систему.

Запуск:

    python -m game_player_mcp
"""

from __future__ import annotations

import base64
import threading
import time
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent

from . import capture as capture_mod
from . import winapi
from .config import Config
from .executor import ActionExecutor, AgentError

server = MCPServer(
    name="game-player",
    instructions="""\
Control a game running in a window. You supply the judgement; this server only
supplies eyes and hands.

LOOK BEFORE EVERY ACTION
1. Call look() and read the frame before deciding anything. Never act on a frame
   you already used — the game moved on while you were thinking.
2. Say what you see before you choose: menu or gameplay? Which counters, health,
   ammo, hotbar slots? Where is the cursor? Is anything animating?
3. A menu on screen swallows movement keys. If a menu is open, close it first
   (escape, or a click on the resume button) and only then play.
4. Check that your previous action actually did something. If the frame is
   unchanged, it did not land.

TIMING
The screenshot is already stale when you receive it: a model round-trip costs
2-20 s, so the game has advanced. Budget for that instead of assuming the frame
is live.
- Estimate your action's own duration, then the game's reaction time, and put a
  wait between them.
- Rough magnitudes: a click ~0.05 s, a menu transition ~0.3 s, a level or game
  load 10-40 s, a block break in a voxel game 1-3 s.
- After anything slow, look() again before deciding the next step.
- Prefer a smaller number of longer, deliberate actions over a burst of tiny
  ones you cannot observe.

CLICK BURSTS
Use autoclick rather than a long list of click actions. It holds a steady rate
and stops by itself on whichever limit you give first:
  {"type":"autoclick","button":"left","clicks":50,"interval":0.04}
  {"type":"autoclick","button":"left","seconds":10,"interval":0.05}
Give both when you are unsure: clicks bounds the burst, seconds bounds the time.

HOLDING KEYS
- {"type":"key","key":"w","hold":2.0} — one key for a fixed time.
- {"type":"keys_hold","keys":["shift","w"],"hold":3.0} — several keys at once:
  sprint, sneak while walking, turn while moving. Keys accept a list or a string
  ("shift+w").
- key_down / key_up hold across several calls; keys_hold is safer when you
  already know the duration. release_all drops everything if you lose track.

READ THE STATE FROM THE FRAME
- menus versus gameplay
- health, hunger, ammo, score, timers — if a number changes, something happened
- inventory or hotbar — did the last action yield an item?
- your own position relative to what you are trying to reach

IF NOTHING CHANGED
Do not repeat the same action. Doing it twice means the first did not land:
look again, then change something — different coordinates, an extra wait, or a
different key.
""",
)

#: Состояние живёт между вызовами: окно и исполнитель с зажатыми клавишами.
_state: dict[str, Any] = {"window": None, "executor": None, "background": False}


def _config() -> Config:
    return Config()


def _executor() -> ActionExecutor:
    if _state["executor"] is None:
        _state["executor"] = ActionExecutor(_config(), threading.Event())
    return _state["executor"]


def _find(needle: str) -> winapi.WindowInfo | None:
    """Окно по части заголовка; при равенстве — самое похожее на игру."""
    needle = (needle or "").lower()
    matches = [w for w in winapi.list_windows(min_size=0)
               if needle in w.title.lower() and not w.is_own]
    if not matches:
        return None
    matches.sort(key=lambda w: (not w.looks_like_game, -w.width * w.height))
    return matches[0]


def _resolve(window: str | None) -> winapi.WindowInfo:
    if window:
        found = _find(window)
        if found is None:
            raise AgentError(f"no window matching '{window}'")
        _state["window"] = found
        return found
    if _state["window"] is None:
        raise AgentError("no window selected — call list_windows, then look")
    live = winapi.get_window(_state["window"].hwnd)
    if live is None:
        raise AgentError("the selected window has closed")
    _state["window"] = live
    return live


@server.tool(description="List windows: title, process, size, and whether it looks like a game")
def list_windows() -> str:
    rows = []
    for w in winapi.list_windows(min_size=0):
        if w.is_own:
            continue
        mark = "*" if w.looks_like_game else " "
        state = " (minimised)" if w.minimized else ""
        rows.append(f"{mark} {w.title[:52]:<52} {w.process[:16]:<16} "
                    f"{w.display_size[0]}x{w.display_size[1]}{state}")
    return "\n".join(rows) or "no windows found"


@server.tool(description="Capture the game window and return it as an image")
def look(window: str | None = None, background: bool = False,
         show_grid: bool = True, max_width: int = 1024) -> list[Any]:
    """Возвращает текст с геометрией и сам кадр в JPEG.

    ``show_grid=True`` рисует сетку 0..1 с подписями: замерено, что модели
    читают позицию с сетки заметно лучше, чем угадывают координату — на живом
    Minecraft обе модели целились в (0.76, 0.71) для кнопки на (0.62, 0.56).
    """
    try:
        win = _resolve(window)
    except AgentError as exc:
        return [TextContent(type="text", text=f"Cannot look: {exc}")]
    if not background:
        # Экранный захват берёт то, что видно, поэтому игру надо поднять.
        if not winapi.is_foreground(win.hwnd):
            winapi.focus_window(win.hwnd)
    _state["background"] = background
    started = time.monotonic()
    frame = capture_mod.capture(win, "client", max_width, 70,
                                grid=show_grid, background=background)
    capture_ms = int((time.monotonic() - started) * 1000)
    if not background:
        win = winapi.get_window(win.hwnd) or win
    # Время в кадре — не украшение: пока агент думает, игра уходит вперёд, и без
    # этой метки он не может оценить, насколько кадр уже устарел.
    info = (f"Window: {win.title}\n"
            f"Frame: {frame.width}x{frame.height} | client "
            f"{win.client_width}x{win.client_height} | focused: "
            f"{'yes' if winapi.is_foreground(win.hwnd) else 'no'}\n"
            f"Capture: {frame.source} in {capture_ms} ms at "
            f"{time.strftime('%H:%M:%S')}\n"
            f"Action coordinates are normalised 0..1 of the client area.")
    return [TextContent(type="text", text=info),
            ImageContent(type="image", data=base64.b64encode(frame.jpeg).decode(),
                         mime_type="image/jpeg")]


@server.tool(description=(
    "Perform actions in the window. Pass a list of objects, e.g. "
    '[{"type":"key","key":"w","hold":2.0},{"type":"wait","seconds":0.5}]. '
    "Supported: key, key_press, key_down, key_up, keys_hold (several keys at "
    "once), type, move, click, double_click, mouse_down, mouse_up, mouse_delta, "
    "scroll, wait, autoclick (count or duration)."))
def act(window: str | None = None, actions: list[dict] | None = None,
        background: bool = False, dry_run: bool = False) -> str:
    """Действия проходят тот же валидатор, что и в приложении.

    Ошибки возвращаются текстом, а не исключением: агент должен прочитать их и
    исправиться, а не упасть.
    """
    try:
        win = _resolve(window)
    except AgentError as exc:
        # Агент должен прочитать причину и вызвать look, а не получить трейсбек.
        return f"Cannot act: {exc}"
    executor = _executor()
    executor.cfg.background_mode = background
    try:
        # validate() отказывается принимать список целиком, если он слишком
        # длинный, — агент должен прочитать причину, а не получить исключение.
        cleaned, notes = executor.validate(actions or [])
    except AgentError as exc:
        return f"Rejected: {exc}"
    if not cleaned:
        return "No action passed validation.\n" + "\n".join(notes)
    if not background and not dry_run and not winapi.is_foreground(win.hwnd):
        winapi.focus_window(win.hwnd)
    try:
        started = time.monotonic()
        report = executor.execute(cleaned, win, dry_run=dry_run)
        elapsed = time.monotonic() - started
    except AgentError as exc:
        return f"Stopped: {exc}"
    lines = [f"{'[dry-run] ' if dry_run else ''}{len(report)} action(s) done "
             f"in {elapsed:.2f}s:"]
    lines.extend(f"  {line}" for line in report)
    if notes:
        lines.append("Validator notes:")
        lines.extend(f"  {note}" for note in notes)
    # Агенту нужна фактическая длительность, а не заказанная: по ней он считает,
    # сколько времени прошло в игре, пока он не смотрел.
    lines.append(f"Elapsed: {elapsed:.2f}s. Look again before deciding the next "
                 f"step — the game kept running.")
    return "\n".join(lines)


@server.tool(description="Press and release a key (hold in seconds)")
def press(key: str, hold: float = 0.08, window: str | None = None,
          background: bool = False) -> str:
    action = ({"type": "key_press", "key": key} if hold <= 0.1
              else {"type": "key", "key": key, "hold": hold})
    return act(window=window, background=background, actions=[action])


@server.tool(description="Click at normalised coordinates 0..1")
def click_at(x: float, y: float, button: str = "left", window: str | None = None,
             background: bool = False) -> str:
    return act(window=window, background=background, actions=[
        {"type": "move", "x": x, "y": y},
        {"type": "click", "button": button},
    ])


@server.tool(description="Release every held key — emergency stop")
def release_all() -> str:
    executor = _executor()
    held = sorted(executor._held_keys)
    if not held:
        return "No keys are held."
    executor._release_keys(dry_run=False)
    return f"Released: {', '.join(held)}"


@server.tool(description="What is held right now and which window is selected")
def status() -> str:
    executor = _executor()
    window = _state["window"]
    return (f"Window: {window.title if window else 'none selected'}\n"
            f"Background: {_state['background']}\n"
            f"Held: {', '.join(sorted(executor._held_keys)) or 'nothing'}")


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
