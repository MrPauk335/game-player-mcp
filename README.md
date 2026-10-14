# game-player-mcp

An MCP server that gives an agent **eyes and hands** to play a game running in a
window. The server only looks and acts — thinking stays with the calling agent.

```
┌─────────────┐   look()  ┌──────────────┐   act()   ┌────────────┐
│  your agent │ ────────► │ game-player  │ ────────► │ game window│
│  (any MCP)  │ ◄──────── │ MCP server   │           │            │
└─────────────┘  JPEG     └──────────────┘           └────────────┘
```

## Why not just use a computer-use MCP?

A general computer-use server hands the agent your entire desktop. This one is
deliberately narrow, and that is the point:

- **Scoped to one window.** The frame is the game's client area and coordinates
  are normalised `0..1`, so the model does not care about resolution or where the
  window sits. It cannot click into another application.
- **Background mode.** With `background=True` the frame comes from `PrintWindow`
  and input goes out as `PostMessage` — the game never takes focus, so you can
  keep working while the agent plays. Verified on a live Roblox session.
- **Action whitelist.** Every action passes a validator: forbidden keys
  (`Win`, `Alt+F4`, `Ctrl+Shift+Esc`) are rejected, holds and waits are clamped,
  at most 8 actions per call. The agent cannot walk out of the game into your OS.

## Install

```bash
pip install git+https://github.com/MrPauk335/game-player-mcp
```

Or from a clone:

```bash
git clone https://github.com/MrPauk335/game-player-mcp
cd game-player-mcp
pip install -e .
```

Windows only — it drives `SendInput`, `PrintWindow` and `PostMessage`.

## Configure

Add it to your MCP client. For a JSON-configured client:

```json
{
  "mcpServers": {
    "game-player": {
      "command": "python",
      "args": ["-m", "game_player_mcp"]
    }
  }
}
```

Do **not** rely on a `cwd` field — some loaders ignore it. `python -m
game_player_mcp` works from any directory because the package is installed.

## Tools

| Tool | What it does |
|---|---|
| `list_windows` | windows with title, process, size, game-likeness |
| `look` | capture the window → text + JPEG image |
| `act` | run a list of validated actions |
| `press` | press a key, with optional hold |
| `click_at` | click at normalised `0..1` coordinates |
| `release_all` | release every held key (emergency stop) |
| `status` | what is held and which window is selected |

### Typical loop

```
list_windows            → find the game
look(window="roblox")   → see it
act([{"type":"key","key":"w","hold":2.0}])   → walk forward
look()                  → check what changed
```

Coordinates are always `0..1` of the client area: `x=0` is the left edge,
`x=1` the right. A grid with labels is drawn on the frame by default
(`show_grid=True`) — vision models read a labelled grid far more reliably than
they guess an absolute position. Measured: asked to click a button at
`(0.62, 0.56)`, two different models aimed at `(0.76, 0.71)` and hit nothing,
five times in a row.

### Actions

```json
{"type": "key",        "key": "w", "hold": 2.0}
{"type": "key_press",  "key": "escape"}
{"type": "move",       "x": 0.5, "y": 0.3}
{"type": "click",      "button": "left"}
{"type": "double_click","button": "left"}
{"type": "mouse_down", "button": "left"}
{"type": "mouse_up",   "button": "left"}
{"type": "mouse_delta","dx": 300, "dy": -100}
{"type": "scroll",     "amount": -1}
{"type": "wait",       "seconds": 1.5}
```

## Background mode

`background=True` on `look` and `act` keeps the game unfocused. Two of the
game's own settings decide whether that is enough, both in the instance's
`options.txt`:

| Setting | Why it matters |
|---|---|
| `pauseOnLostFocus:false` | otherwise the pause screen reopens every tick and swallows movement keys |
| `rawMouseInput:false` | otherwise the camera reads raw mouse motion, which cannot be synthesised, so the view never turns |

Measured on Minecraft 1.21.11 with the window deliberately unfocused: a posted
`w` moved the player from `z=-52.700` to `z=-52.300`, and a posted click opened a
menu. A raw stdio test showed the server surviving the handshake with clean stderr.

**Camera rotation is the known limit.** Games that read raw mouse input —
Roblox, Minecraft with `rawMouseInput:true` — do not turn their view from
synthesised input. Neither relative moves, nor absolute moves, nor arrow keys
worked; frame diffs stayed at ~1.5, which is world animation, not rotation.
The agent can walk and click, but it cannot look around.

## Safety

- Forbidden keys are rejected before anything reaches the OS.
- Holds are clamped to 10 s, waits to 10 s, coordinates to `0..1`.
- A list longer than 8 actions is rejected whole, not truncated — so the agent
  never assumes part of it ran.
- `release_all` drops every held key if something goes wrong.
- Validation errors come back as text, never as a crash, so the agent can read
  the reason and correct itself.

## Tests

```bash
python tests/test_executor.py
```

16 checks over the validator: forbidden keys, unknown actions, clamping, the
per-call limit, and string-form parsing.

## License

MIT.

---

# game-player-mcp (по-русски)

MCP-сервер, который даёт агенту **глаза и руки** для игры в окне. Сервер только
смотрит и действует — думает вызывающий агент.

**Зачем, если есть обычный computer-use.** Тот отдаёт агенту весь рабочий стол.
Этот намеренно узкий: работает с одним окном, координаты нормализованы `0..1`,
и каждое действие проходит белый список — запрещённые клавиши (`Win`, `Alt+F4`,
`Ctrl+Shift+Esc`) отклоняются, удержания и паузы ограничены, за вызов не больше
8 действий. Агент не может случайно выйти в систему.

**Фоновый режим** (`background=True`) снимает кадр через `PrintWindow` и шлёт
ввод через `PostMessage`, поэтому игра не забирает фокус и вы можете работать за
компьютером. Требует в настройках игры `pauseOnLostFocus:false` и
`rawMouseInput:false` — иначе игра перехватит управление обратно.

**Что не работает:** поворот камеры в играх, читающих raw-ввод мыши (Roblox,
Minecraft с `rawMouseInput:true`). Ни относительное движение, ни абсолютное, ни
стрелки — замерено, разница кадров ~1.5, это анимация мира. Ходить и кликать
агент может, осматриваться — нет.

**Установка:** `pip install git+https://github.com/MrPauk335/game-player-mcp`,
затем прописать в клиенте команду `python -m game_player_mcp`. Только Windows.
