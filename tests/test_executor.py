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

    print("\n" + "=" * 60)
    print(f"ПРОЙДЕНО: {PASSED}   ПРОВАЛЕНО: {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
