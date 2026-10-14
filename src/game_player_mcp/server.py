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
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent

from . import capture as capture_mod
from . import winapi
from .config import Config
from .executor import ActionExecutor, AgentError

server = MCPServer(
    name="game-player",
    instructions=(
        "Control a game running in a window. Call list_windows first, then look, "
        "then act. Coordinates are normalised 0..1 of the window's client area. "
        "Walk with a held key ({\"type\":\"key\",\"key\":\"w\",\"hold\":2.0}), "
        "click with move+click in one step. After every act, look again before "
        "deciding the next move. If an action returns an error, do not repeat it "
        "— look again and try something different."
    ),
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
    frame = capture_mod.capture(win, "client", max_width, 70,
                                grid=show_grid, background=background)
    if not background:
        win = winapi.get_window(win.hwnd) or win
    info = (f"Window: {win.title}\n"
            f"Frame: {frame.width}x{frame.height} | client "
            f"{win.client_width}x{win.client_height} | focused: "
            f"{'yes' if winapi.is_foreground(win.hwnd) else 'no'}\n"
            f"Capture: {frame.source}\n"
            f"Action coordinates are normalised 0..1 of the client area.")
    return [TextContent(type="text", text=info),
            ImageContent(type="image", data=base64.b64encode(frame.jpeg).decode(),
                         mime_type="image/jpeg")]


@server.tool(description=(
    "Perform actions in the window. Pass a list of objects, e.g. "
    '[{"type":"key","key":"w","hold":2.0},{"type":"wait","seconds":0.5}]. '
    "Supported: key, key_press, key_down, key_up, type, move, click, "
    "double_click, mouse_down, mouse_up, mouse_delta, scroll, wait."))
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
        report = executor.execute(cleaned, win, dry_run=dry_run)
    except AgentError as exc:
        return f"Stopped: {exc}"
    lines = [f"{'[dry-run] ' if dry_run else ''}{len(report)} action(s) done:"]
    lines.extend(f"  {line}" for line in report)
    if notes:
        lines.append("Validator notes:")
        lines.extend(f"  {note}" for note in notes)
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
