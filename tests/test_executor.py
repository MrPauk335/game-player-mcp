"""Тесты валидатора — он и есть граница безопасности между агентом и системой.

Агент присылает произвольный JSON. Всё, что не прошло проверку, до машины не
доходит, поэтому проверять надо именно здесь, а не в интеграции.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from game_player_mcp import ActionExecutor, AgentError, Config   # noqa: E402

PASSED = FAILED = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"PASS  {name}" + (f"  — {detail}" if detail else ""))
    else:
        FAILED += 1
        print(f"FAIL  {name}  — {detail}")


def executor(**kwargs) -> ActionExecutor:
    return ActionExecutor(Config(**kwargs), threading.Event())


def main() -> int:
    ex = executor()

    print("=== запрещённые клавиши ===")
    for key in ("win", "alt+f4", "ctrl+shift+esc", "cmd", "meta"):
        cleaned, _ = ex.validate([{"type": "key_press", "key": key}])
        check(f"отклонена {key!r}", cleaned == [], str(cleaned))

    print("\n=== белый список действий ===")
    cleaned, _ = ex.validate([{"type": "eval", "code": "os.system('calc')"}])
    check("неизвестное действие отброшено", cleaned == [], str(cleaned))
    cleaned, _ = ex.validate([{"type": "key", "key": "w", "hold": 1.0}])
    check("обычная ходьба разрешена", len(cleaned) == 1, str(cleaned))

    print("\n=== ограничения величин ===")
    cleaned, _ = ex.validate([{"type": "key", "key": "w", "hold": 9999}])
    check("удержание клавиши ограничено", cleaned and cleaned[0]["hold"] <= 10.0,
          str(cleaned[0]["hold"]) if cleaned else "нет")
    cleaned, _ = ex.validate([{"type": "wait", "seconds": 9999}])
    check("пауза ограничена", cleaned and cleaned[0]["seconds"] <= 10.0,
          str(cleaned[0]["seconds"]) if cleaned else "нет")
    cleaned, _ = ex.validate([{"type": "move", "x": 5, "y": -2}])
    check("координаты зажаты в 0..1",
          cleaned and cleaned[0]["x"] == 1.0 and cleaned[0]["y"] == 0.0,
          str(cleaned[0]) if cleaned else "нет")
    cleaned, _ = ex.validate([{"type": "mouse_delta", "dx": 10**9, "dy": -10**9}])
    check("обзор ограничен по величине", cleaned and abs(cleaned[0]["dx"]) <= 4000,
          str(cleaned[0]) if cleaned else "нет")

    print("\n=== лимит на число действий ===")
    many = [{"type": "key_press", "key": "w"} for _ in range(50)]
    try:
        cleaned, _ = ex.validate(many)
        check("за шаг не больше 8 действий", len(cleaned) <= 8, f"{len(cleaned)} шт.")
    except AgentError as exc:
        # Валидатор не обрезает список, а отказывается его принимать целиком —
        # так агент не решает, что часть действий всё-таки выполнилась.
        check("превышение лимита отклонено целиком", "много действий" in str(exc),
              str(exc))
    cleaned, _ = ex.validate([{"type": "key_press", "key": "w"}] * 8)
    check("ровно восемь действий принимаются", len(cleaned) == 8, f"{len(cleaned)} шт.")

    print("\n=== шорткаты и пробельные формы ===")
    cleaned, _ = ex.validate([{"type": "key", "key": "w", "hold": "2"}])
    check("hold строкой разобран", cleaned and cleaned[0]["hold"] == 2.0,
          str(cleaned) if cleaned else "нет")
    cleaned, _ = ex.validate([{"type": "wait", "seconds": "1.5"}])
    check("seconds строкой разобран", cleaned and cleaned[0]["seconds"] == 1.5,
          str(cleaned) if cleaned else "нет")

    print("\n=== фоновый режим не берёт фокус ===")
    bg = executor(background_mode=True)
    check("background_mode доходит до исполнителя", bg.cfg.background_mode is True)

    print("\n=== одновременное зажатие нескольких клавиш ===")
    cleaned, _ = ex.validate([{"type": "keys_hold", "keys": ["shift", "w"],
                               "hold": 3.0}])
    check("shift+w принимается", cleaned and cleaned[0]["keys"] == ["shift", "w"],
          str(cleaned) if cleaned else "нет")
    for label, keys in (("shift+w", "shift+w"), ("shift w", "shift w"),
                        ("shift,w", "shift,w"), ("список", ["shift", "w"])):
        cleaned, _ = ex.validate([{"type": "keys_hold", "keys": keys, "hold": 1}])
        check(f"строка {label!r} разобрана",
              cleaned and cleaned[0]["keys"] == ["shift", "w"],
              str(cleaned[0]["keys"]) if cleaned else "нет")

    cleaned, notes = ex.validate([{"type": "keys_hold", "keys": ["win", "w"],
                                   "hold": 1}])
    check("запрещённая клавиша выброшена из комбинации",
          cleaned and cleaned[0]["keys"] == ["w"] and bool(notes),
          str(cleaned) if cleaned else "нет")
    cleaned, _ = ex.validate([{"type": "keys_hold",
                               "keys": ["a", "b", "c", "d", "e", "f"], "hold": 1}])
    check("число зажатых клавиш ограничено", cleaned and len(cleaned[0]["keys"]) <= 4,
          str(cleaned[0]["keys"]) if cleaned else "нет")
    cleaned, _ = ex.validate([{"type": "keys_hold", "keys": [], "hold": 1}])
    check("пустая комбинация отклонена", cleaned == [], str(cleaned))
    cleaned, _ = ex.validate([{"type": "keys_hold", "keys": ["shift", "w"],
                               "hold": 9999}])
    check("удержание комбинации ограничено",
          cleaned and cleaned[0]["hold"] <= 10.0,
          str(cleaned[0]["hold"]) if cleaned else "нет")

    print("\n=== авто-кликер ===")
    cleaned, _ = ex.validate([{"type": "autoclick", "button": "left",
                               "clicks": 50, "interval": 0.04}])
    check("кликер по числу кликов",
          cleaned and cleaned[0]["clicks"] == 50, str(cleaned) if cleaned else "нет")
    check("оценка длительности посчитана",
          cleaned and cleaned[0].get("estimated_seconds") == 2.0,
          str(cleaned[0].get("estimated_seconds")) if cleaned else "нет")
    cleaned, _ = ex.validate([{"type": "autoclick", "button": "left",
                               "seconds": 5}])
    check("кликер по длительности",
          cleaned and cleaned[0]["seconds"] == 5.0, str(cleaned) if cleaned else "нет")
    cleaned, _ = ex.validate([{"type": "autoclick", "button": "left"}])
    check("кликер без лимитов получает безопасный дефолт",
          cleaned and cleaned[0]["clicks"] == 10, str(cleaned) if cleaned else "нет")
    cleaned, _ = ex.validate([{"type": "autoclick", "button": "left",
                               "clicks": 999999}])
    check("число кликов ограничено", cleaned and cleaned[0]["clicks"] <= 500,
          str(cleaned[0]["clicks"]) if cleaned else "нет")
    cleaned, _ = ex.validate([{"type": "autoclick", "button": "left",
                               "seconds": 9999}])
    check("длительность кликера ограничена",
          cleaned and cleaned[0]["seconds"] <= 30.0,
          str(cleaned[0]["seconds"]) if cleaned else "нет")
    cleaned, _ = ex.validate([{"type": "autoclick", "button": "left",
                               "clicks": 10, "interval": 0}])
    check("интервал не может быть нулевым",
          cleaned and cleaned[0]["interval"] >= 0.01,
          str(cleaned[0]["interval"]) if cleaned else "нет")
    cleaned, notes = ex.validate([{"type": "autoclick", "button": "back",
                                   "clicks": 5}])
    check("неизвестная кнопка заменена на left",
          cleaned and cleaned[0]["button"] == "left", str(cleaned) if cleaned else "нет")

    print("\n=== шорткаты новых действий ===")
    cleaned, _ = ex.validate(["keys_hold(shift+w, 2.5)"])
    check("шорткат keys_hold разобран",
          cleaned and cleaned[0]["keys"] == ["shift", "w"]
          and cleaned[0]["hold"] == 2.5, str(cleaned) if cleaned else "нет")
    cleaned, _ = ex.validate(["autoclick(left,30)"])
    check("шорткат autoclick по числу",
          cleaned and cleaned[0]["clicks"] == 30, str(cleaned) if cleaned else "нет")
    cleaned, _ = ex.validate(["autoclick(left,3s)"])
    check("шорткат autoclick по времени",
          cleaned and cleaned[0].get("seconds") == 3.0,
          str(cleaned) if cleaned else "нет")

    print("\n=== порядок вызовов Win32 (без реального ввода) ===")
    # Подменяем winapi, чтобы увидеть последовательность, а не трогать систему.
    from game_player_mcp import executor as ex_mod, winapi as real_winapi

    calls: list[str] = []
    saved = {name: getattr(real_winapi, name) for name in
             ("key_down", "key_up", "click", "move_mouse_screen")}
    real_winapi.key_down = lambda k, m: calls.append(f"down:{k}")
    real_winapi.key_up = lambda k, m: calls.append(f"up:{k}")
    real_winapi.click = lambda b: calls.append(f"click:{b}")
    real_winapi.move_mouse_screen = lambda x, y: calls.append("move")
    try:
        live = ActionExecutor(Config(), threading.Event())

        cleaned, _ = live.validate([{"type": "keys_hold", "keys": ["shift", "w"],
                                     "hold": 0.05}])
        live.execute(cleaned, None, dry_run=False)
        check("keys_hold зажимает обе клавиши до отпускания",
              calls[:2] == ["down:shift", "down:w"], str(calls))
        check("keys_hold отпускает обе клавиши",
              set(calls[2:]) == {"up:shift", "up:w"}, str(calls))
        check("после keys_hold ничего не зажато", live._held_keys == set(),
              str(live._held_keys))

        calls.clear()
        cleaned, _ = live.validate([{"type": "autoclick", "button": "left",
                                     "clicks": 5, "interval": 0.01}])
        live.execute(cleaned, None, dry_run=False)
        check("autoclick нажал ровно 5 раз",
              calls.count("click:left") == 5, str(calls))

        calls.clear()
        cleaned, _ = live.validate([{"type": "autoclick", "button": "right",
                                     "clicks": 3, "interval": 0.01}])
        live.execute(cleaned, None, dry_run=False)
        check("autoclick уважает кнопку мыши",
              calls == ["click:right"] * 3, str(calls))

        calls.clear()
        live.abort.set()
        cleaned, _ = live.validate([{"type": "autoclick", "button": "left",
                                     "clicks": 50, "interval": 0.01}])
        live.execute(cleaned, None, dry_run=False)
        check("аварийный стоп прерывает кликер", len(calls) == 0, str(calls))
        live.abort.clear()
    finally:
        for name, fn in saved.items():
            setattr(real_winapi, name, fn)

    print("\n" + "=" * 60)
    print(f"ПРОЙДЕНО: {PASSED}   ПРОВАЛЕНО: {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
