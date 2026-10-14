"""Валидатор и исполнитель действий — сердце игрового MCP.

Выделено из приложения game-ai-player. Здесь нет ни модели, ни цикла
«посмотрел-подумал-сделал»: решает вызывающий агент, а этот модуль только
проверяет его действия по белому списку и отправляет их в окно.

Такой разрез нужен именно для MCP: сервер отдаёт наружу глаза и руки, а
рассуждение остаётся на стороне клиента.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import winapi

MAX_ACTIONS_PER_STEP = 8
MAX_HOLD_SECONDS = 10.0
MAX_WAIT_SECONDS = 10.0

ALLOWED_ACTIONS = {
    "key", "key_press", "key_down", "key_up", "type",
    "move", "click", "double_click", "mouse_down", "mouse_up",
    "mouse_delta", "scroll", "wait",
}


class AgentError(RuntimeError):
    pass


@dataclass
class StepRecord:
    index: int
    observation: str = ""
    plan: str = ""
    progress: str = ""
    actions: list = field(default_factory=list)
    report: list = field(default_factory=list)
    text: str = ""

    def history_line(self) -> str:
        acts = ", ".join(_short_action(a) for a in self.actions) or "нет"
        obs = (self.observation or "").strip().replace("\n", " ")[:160]
        return f"шаг {self.index}: действия=[{acts}] | увидел: {obs}"


def _short_action(action: dict) -> str:
    kind = action.get("type", "?")
    if kind in ("key", "key_press", "key_down", "key_up"):
        hold = action.get("hold")
        return f"{kind}:{action.get('key')}" + (f"×{hold}s" if hold else "")
    if kind in ("move", "click", "double_click"):
        if kind == "move":
            return f"move({action.get('x')},{action.get('y')})"
        return kind
    if kind == "wait":
        return f"wait {action.get('seconds')}s"
    if kind == "type":
        return f"type:{str(action.get('text'))[:20]!r}"
    if kind == "scroll":
        return f"scroll {action.get('amount')}"
    if kind == "mouse_delta":
        return f"look({action.get('dx')},{action.get('dy')})"
    return kind


#: ``move(0.05,0.05)`` / ``click`` / ``wait 1.0s`` shorthands small vision models
#: emit when they ignore the "one JSON object per action" rule. ``None`` means
#: "not shorthand". The space form is accepted too: measured output included
#: ``wait 1.0s`` and ``key w``, which the parenthesis-only pattern rejected.
_SHORTHAND_RE = re.compile(
    r"^(?P<name>[a-z_]+)(?:\s*\((?P<args>[^)]*)\)|\s+(?P<spaced>.+))?$")


def _action_from_shorthand(text: str) -> dict | None:
    """Rebuild one action from a bare shorthand token.

    ``meta/llama-3.2-11b-vision-instruct`` returned
    ``"move(0.05,0.05), click"`` as a single list item — a string, not a dict —
    and the whole step's actions were silently dropped. Recognising the handful
    of shorthands it actually produces keeps the step usable; anything unclear
    still falls through to the normal "skipped" note.
    """
    match = _SHORTHAND_RE.match(text.strip().lower())
    if not match:
        return None
    name = match.group("name")
    raw_args = match.group("args")
    if raw_args is None:
        raw_args = match.group("spaced") or ""
    args = [a.strip() for a in raw_args.split(",") if a.strip()]
    if name in ("click", "double_click", "mouse_down", "mouse_up"):
        # ``click(0.5,0.42)`` means "move there, then click" — the numbers are
        # coordinates, not a button name. Only accept a real button word.
        button = args[0] if args and args[0] in ("left", "right", "middle") else "left"
        out: dict = {"type": name, "button": button}
        if args and args[0] not in ("left", "right", "middle") and len(args) >= 2:
            try:
                out["x"], out["y"] = float(args[0]), float(args[1])
            except ValueError:
                out.pop("x", None)
                out.pop("y", None)
        return out
    if name == "move":
        if len(args) < 2:
            return None
        try:
            return {"type": "move", "x": float(args[0]), "y": float(args[1])}
        except ValueError:
            return None
    if name == "wait":
        seconds = 1.0
        if args:
            try:
                seconds = float(args[0].rstrip("s"))
            except ValueError:
                return None
        return {"type": "wait", "seconds": seconds}
    if name in ("key", "key_press", "key_down", "key_up") and args:
        return {"type": name, "key": args[0]}
    if name == "scroll" and args:
        try:
            return {"type": "scroll", "amount": int(float(args[0]))}
        except ValueError:
            return None
    return None


def _expand_raw_actions(raw_actions: list, notes: list[str]) -> list:
    """Flatten the malformed shapes weak models produce into action dicts.

    Two observed cases, both from a real 960px game frame:

    * ``"move(0.05,0.05), click"`` — several actions crammed into one string
    * ``'{"type": "click", "button": "left"}'`` — a JSON object kept as a string

    Repairing them here means a step still does something useful instead of being
    discarded wholesale, while unparseable entries are still reported.
    """
    out: list = []
    for item in raw_actions:
        if isinstance(item, dict):
            out.append(item)
            continue
        if not isinstance(item, str):
            out.append(item)                    # let the caller note it
            continue

        text = item.strip()
        # A stringified JSON object or array.
        if text[:1] in ("{", "["):
            try:
                decoded = json.loads(text)
            except (ValueError, TypeError):
                decoded = None
            if isinstance(decoded, list):
                out.extend(decoded)
                notes.append(f"разобран JSON-массив из строки: {len(decoded)} действий")
                continue
            if isinstance(decoded, dict):
                out.append(decoded)
                notes.append("разобран JSON-объект из строки")
                continue

        # Several shorthand tokens separated by commas. Splitting on "," would
        # break move(0.05,0.05), so commas inside parentheses are protected
        # first and restored afterwards.
        guarded = re.sub(r"\(([^)]*)\)", lambda m: "(" + m.group(1).replace(",", "\x00") + ")", text)
        rebuilt: list[dict] = []
        for fragment in guarded.split(","):
            part = fragment.strip().replace("\x00", ",")
            if not part:
                continue
            found = _action_from_shorthand(part)
            if found is None:
                # A bare number: the second half of a split move() pair.
                if rebuilt and rebuilt[-1].get("type") == "move" and "y" not in rebuilt[-1]:
                    try:
                        rebuilt[-1]["y"] = float(part.rstrip(")"))
                    except ValueError:
                        pass
                continue
            rebuilt.append(found)
        if rebuilt:
            out.extend(rebuilt)
            notes.append(f"восстановлено из строки: {text[:40]!r} → {len(rebuilt)} действий")
            continue

        whole = _action_from_shorthand(text)
        if whole:
            out.append(whole)
            notes.append(f"восстановлено действие из строки: {text[:40]!r}")
            continue
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Executor
# --------------------------------------------------------------------------- #


class ActionExecutor:
    """Validates and performs the model's action list."""

    def __init__(self, cfg, abort: threading.Event,
                 log: Callable[[str], None] | None = None):
        self.cfg = cfg
        self.abort = abort
        self.log = log or (lambda _m: None)
        self._held_keys: set[str] = set()
        #: Where the next posted click should land, in client pixels. Posted
        #: clicks carry an explicit position, unlike SendInput ones.
        self._background_cursor: tuple[int, int] = (0, 0)

    # -- validation ------------------------------------------------------- #

    def validate(self, raw_actions) -> tuple[list[dict], list[str]]:
        if raw_actions is None:
            return [], []
        if not isinstance(raw_actions, list):
            raise AgentError("поле actions должно быть списком")
        if len(raw_actions) > MAX_ACTIONS_PER_STEP:
            raise AgentError(
                f"слишком много действий за шаг ({len(raw_actions)} > {MAX_ACTIONS_PER_STEP})"
            )
        cleaned: list[dict] = []
        notes: list[str] = []
        pressed: set[str] = set()
        for action in _expand_raw_actions(raw_actions, notes):
            if not isinstance(action, dict):
                notes.append(f"пропущено не-объект: {action!r}")
                continue
            kind = str(action.get("type", "")).strip().lower()
            if kind not in ALLOWED_ACTIONS:
                notes.append(f"неизвестное действие пропущено: {kind!r}")
                continue
            item = dict(action)
            item["type"] = kind

            if kind in ("key", "key_press", "key_down", "key_up"):
                key = str(item.get("key", "")).strip().lower()
                if key in winapi.FORBIDDEN_KEYS:
                    notes.append(f"запрещённая клавиша отклонена: {key}")
                    continue
                try:
                    winapi.resolve_key(key)
                except ValueError as exc:
                    notes.append(str(exc))
                    continue
                item["key"] = key
                if kind in ("key_down", "key"):
                    pressed.add(key)
                if kind == "key_up":
                    pressed.discard(key)
                combo = pressed | {key}
                if any(combo >= set(c) for c in winapi.FORBIDDEN_COMBOS):
                    notes.append(f"запрещённая комбинация отклонена: {sorted(combo)}")
                    continue
                if kind == "key":
                    hold = float(item.get("hold", 0.08) or 0)
                    item["hold"] = max(0.02, min(hold, MAX_HOLD_SECONDS))

            elif kind in ("move",):
                item["x"] = _clamp01(item.get("x", 0.5))
                item["y"] = _clamp01(item.get("y", 0.5))

            elif kind in ("click", "double_click", "mouse_down", "mouse_up"):
                button = str(item.get("button", "left")).strip().lower()
                if button not in ("left", "right", "middle"):
                    notes.append(f"кнопка мыши заменена на left: {button!r}")
                    button = "left"
                item["button"] = button
                # A click that carries coordinates means "move there, then click".
                # Expanding it here keeps the cursor position honest instead of
                # clicking wherever the mouse happens to be.
                if "x" in item or "y" in item:
                    cleaned.append({"type": "move",
                                    "x": _clamp01(item.get("x", 0.5)),
                                    "y": _clamp01(item.get("y", 0.5))})

            elif kind == "mouse_delta":
                item["dx"] = int(_clamp(float(item.get("dx", 0) or 0), -2000, 2000))
                item["dy"] = int(_clamp(float(item.get("dy", 0) or 0), -2000, 2000))

            elif kind == "scroll":
                item["amount"] = int(_clamp(float(item.get("amount", 0) or 0), -20, 20))

            elif kind == "wait":
                item["seconds"] = _clamp(float(item.get("seconds", 0.5) or 0), 0.05, MAX_WAIT_SECONDS)

            elif kind == "type":
                text = str(item.get("text", ""))
                if not text:
                    notes.append("пустой type пропущен")
                    continue
                item["text"] = text[:200]

            cleaned.append(item)
        return cleaned, notes

    # -- execution -------------------------------------------------------- #

    def execute(self, actions: list[dict], window: winapi.WindowInfo | None,
                dry_run: bool) -> list[str]:
        report: list[str] = []
        if not actions:
            return report
        if not dry_run and not getattr(self.cfg, "background_mode", False):
            self._ensure_focus(window)

        try:
            for action in actions:
                if self.abort.is_set():
                    report.append("⛔ прервано пользователем")
                    break
                report.append(self._do(action, window, dry_run))
                if self.cfg.action_delay:
                    self._sleep(self.cfg.action_delay)
        finally:
            self._release_keys(dry_run)
        return report

    def _ensure_focus(self, window: winapi.WindowInfo | None) -> None:
        if window is None:
            return
        live = winapi.get_window(window.hwnd) or window
        if not winapi.is_foreground(live.hwnd):
            self.log(f"окно «{live.title[:40]}» не в фокусе — активирую")
            if not winapi.focus_window(live.hwnd):
                blocker_window = winapi.get_window(winapi.foreground_window())
                blocker = winapi.foreground_blocker()
                hint = ""
                if winapi.is_shell_overlay(blocker_window):
                    hint = ("\nЭто системный оверлей Windows (переключатель Alt+Tab или "
                            "меню «Пуск»). Нажмите Esc, чтобы его закрыть, и запустите "
                            "снова.")
                raise AgentError(
                    f"Не удалось перевести окно «{live.title[:40]}» в фокус.\n"
                    f"Фокус сейчас удерживает {blocker}.{hint}\n"
                    "Агент не станет печатать в чужое окно, поэтому остановился."
                )

    def ensure_visible_for_capture(self, window: winapi.WindowInfo | None) -> None:
        """Bring the game to the front so the screenshot shows the game.

        ``capture.grab`` copies a rectangle of the *screen*, not the window's own
        pixels, so anything covering the game ends up in the frame. Measured: with
        the WorkBuddy window over Minecraft the agent received a picture of the
        editor, and described it as "a Minecraft world with trees and a HUD" —
        it invented a world that was not there and would have acted on it.

        Failing to raise is fatal for a real run (the model would be reasoning
        about the wrong picture) but only a warning in dry-run, so a dry-run
        stays usable while the user is doing something else.
        """
        if getattr(self.cfg, "background_mode", False):
            # Background mode captures with PrintWindow and posts input straight
            # to the window, so it never needs the foreground. That is the point:
            # demanding focus here would defeat the whole feature.
            return
        if window is None:
            return
        live = winapi.get_window(window.hwnd) or window
        if winapi.is_foreground(live.hwnd):
            return
        if winapi.focus_window(live.hwnd) and winapi.is_foreground(live.hwnd):
            self.log(f"поднял окно «{live.title[:40]}» на передний план для съёмки")
            return

        blocker_window = winapi.get_window(winapi.foreground_window())
        blocker = winapi.foreground_blocker()
        hint = ""
        if winapi.is_shell_overlay(blocker_window):
            hint = (" Это системный оверлей Windows (Alt+Tab или меню «Пуск»). "
                    "Нажмите Esc.")
        message = (f"Окно «{live.title[:40]}» не удалось поднять на передний план — "
                   f"его перекрывает {blocker}.{hint} Скриншот показал бы чужое окно, "
                   "поэтому агент остановился.")
        if self.cfg.dry_run:
            self.log("⚠ " + message + " Кадр может не соответствовать игре.")
            return
        raise AgentError(message)

    def _do_background(self, action: dict, window: winapi.WindowInfo | None,
                       dry_run: bool) -> str:
        """Perform an action by posting messages instead of SendInput.

        SendInput goes to whatever window owns the foreground, so an agent using
        it takes the machine over. Posted messages go straight to the game, which
        is what lets the user keep working while the agent plays. Measured on
        Minecraft 1.21.11 with the window unfocused: a posted "w" moved the
        player and a posted click opened a menu.

        Coordinates are normalised 0..1 of the client area, exactly as in the
        foreground path, so the model's output needs no translation.
        """
        kind = action["type"]
        hwnd = window.hwnd if window is not None else 0
        client_w = window.client_width if window is not None else 0
        client_h = window.client_height if window is not None else 0

        def to_client(nx: float, ny: float) -> tuple[int, int]:
            return int(round(nx * client_w)), int(round(ny * client_h))

        if kind == "key":
            key, hold = action["key"], action["hold"]
            if not dry_run:
                winapi.post_key(hwnd, key, True)
                self._held_keys.add(key)
                self._sleep(hold)
                winapi.post_key(hwnd, key, False)
                self._held_keys.discard(key)
            return f"⌨ удержание {key} {hold:.2f}s (фон)"

        if kind == "key_press":
            key = action["key"]
            if not dry_run:
                winapi.post_key_press(hwnd, key)
            return f"⌨ нажатие {key} (фон)"

        if kind == "key_down":
            key = action["key"]
            if not dry_run:
                winapi.post_key(hwnd, key, True)
                self._held_keys.add(key)
            return f"⌨ ↓ {key} (фон)"

        if kind == "key_up":
            key = action["key"]
            if not dry_run:
                winapi.post_key(hwnd, key, False)
                self._held_keys.discard(key)
            return f"⌨ ↑ {key} (фон)"

        if kind == "type":
            text = action["text"]
            if not dry_run:
                winapi.post_type_text(hwnd, text)
            return f"⌨ ввод текста {text[:30]!r} (фон)"

        if kind == "move":
            x, y = action["x"], action["y"]
            cx, cy = to_client(x, y)
            if not dry_run:
                winapi.post_mouse_move(hwnd, cx, cy)
            return f"🖱 курсор → ({x:.2f}, {y:.2f}) = ({cx}, {cy}) (фон)"

        if kind in ("click", "double_click"):
            button = action["button"]
            # A click carries no position of its own; it lands wherever the last
            # "move" put the cursor, so the target must be tracked.
            cx, cy = self._background_cursor
            if not dry_run:
                winapi.post_click(hwnd, button, cx, cy)
                if kind == "double_click":
                    winapi.post_click(hwnd, button, cx, cy)
            return f"🖱 клик {button} (фон)"

        if kind in ("mouse_down", "mouse_up"):
            button = action["button"]
            cx, cy = self._background_cursor
            if not dry_run:
                winapi.post_mouse_button(hwnd, button, kind == "mouse_down", cx, cy)
            return f"🖱 {button} {'↓' if kind == 'mouse_down' else '↑'} (фон)"

        if kind == "mouse_delta":
            dx, dy = action["dx"], action["dy"]
            if not dry_run:
                winapi.post_mouse_move(hwnd, max(0, dx), max(0, dy))
            return f"🖱 обзор (Δ{dx}, Δ{dy}) (фон)"

        if kind == "scroll":
            amount = action["amount"]
            cx, cy = self._background_cursor
            if not dry_run:
                winapi.post_scroll(hwnd, amount, cx, cy)
            return f"🖱 скролл {amount} (фон)"

        if kind == "wait":
            seconds = action["seconds"]
            if not dry_run:
                self._sleep(seconds)
            return f"⏳ пауза {seconds:.2f}s"

        return f"· {kind} (фон, не выполнено)"

    def _do(self, action: dict, window: winapi.WindowInfo | None, dry_run: bool) -> str:
        if getattr(self.cfg, "background_mode", False):
            # Track where a click should land: posted clicks need an explicit
            # position, and the model sends "move" then "click" separately.
            if action["type"] == "move" and window is not None:
                self._background_cursor = (
                    int(round(action["x"] * window.client_width)),
                    int(round(action["y"] * window.client_height)),
                )
            return self._do_background(action, window, dry_run)

        kind = action["type"]
        mode = self.cfg.input_mode

        if kind == "key":
            key, hold = action["key"], action["hold"]
            if not dry_run:
                winapi.key_down(key, mode)
                self._held_keys.add(key)
                self._sleep(hold)
                winapi.key_up(key, mode)
                self._held_keys.discard(key)
            return f"⌨ удержание {key} {hold:.2f}s"

        if kind == "key_press":
            key = action["key"]
            if not dry_run:
                winapi.key_press(key, mode)
            return f"⌨ нажатие {key}"

        if kind == "key_down":
            key = action["key"]
            if not dry_run:
                winapi.key_down(key, mode)
                self._held_keys.add(key)
            return f"⌨ ↓ {key}"

        if kind == "key_up":
            key = action["key"]
            if not dry_run:
                winapi.key_up(key, mode)
                self._held_keys.discard(key)
            return f"⌨ ↑ {key}"

        if kind == "type":
            text = action["text"]
            if not dry_run:
                if getattr(self.cfg, "type_mode", "unicode") == "scancode":
                    winapi.type_scancodes(text)
                else:
                    winapi.type_unicode(text)
            return f"⌨ ввод текста {text[:30]!r}"

        if kind == "move":
            x, y = action["x"], action["y"]
            sx, sy = self._to_screen(window, x, y)
            if not dry_run:
                winapi.move_mouse_screen(sx, sy)
            return f"🖱 курсор → ({x:.2f}, {y:.2f}) = ({sx}, {sy})"

        if kind in ("click", "double_click"):
            button = action["button"]
            if not dry_run:
                if kind == "click":
                    winapi.click(button)
                else:
                    winapi.double_click(button)
            return f"🖱 {'двойной ' if kind == 'double_click' else ''}клик {button}"

        if kind in ("mouse_down", "mouse_up"):
            button = action["button"]
            if not dry_run:
                winapi.mouse_button(button, kind == "mouse_down")
            return f"🖱 {button} {'↓' if kind == 'mouse_down' else '↑'}"

        if kind == "mouse_delta":
            dx, dy = action["dx"], action["dy"]
            if not dry_run:
                winapi.move_mouse_relative(dx, dy)
            return f"🖱 обзор (Δ{dx}, Δ{dy})"

        if kind == "scroll":
            amount = action["amount"]
            if not dry_run:
                winapi.scroll(amount)
            return f"🖱 прокрутка {amount}"

        if kind == "wait":
            seconds = action["seconds"]
            if not dry_run:
                self._sleep(seconds)
            return f"⏳ пауза {seconds:.2f}s"

        return f"? {kind}"

    def _to_screen(self, window: winapi.WindowInfo | None, x: float, y: float) -> tuple[int, int]:
        live = winapi.get_window(window.hwnd) if window else None
        if live is None or live.client_width < 8:
            vx, vy, vw, vh = winapi.virtual_screen()
            return int(vx + x * vw), int(vy + y * vh)
        return (
            int(live.client_left + x * live.client_width),
            int(live.client_top + y * live.client_height),
        )

    def _release_keys(self, dry_run: bool) -> None:
        held = sorted(self._held_keys)
        self._held_keys.clear()
        if dry_run:
            return
        for key in held:
            try:
                winapi.key_up(key, self.cfg.input_mode)
            except Exception:
                pass

    def _sleep(self, seconds: float) -> None:
        """Sleep in slices so the panic key stays responsive."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.abort.is_set():
                return
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))


def _clamp(value: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low


def _clamp01(value) -> float:
    return _clamp(value, 0.0, 1.0)
