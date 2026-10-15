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
{"type": "key_down",   "key": "shift"}
{"type": "key_up",     "key": "shift"}
{"type": "keys_hold",  "keys": ["shift", "w"], "hold": 3.0}
{"type": "move",       "x": 0.5, "y": 0.3}
{"type": "click",      "button": "left"}
{"type": "double_click","button": "left"}
{"type": "mouse_down", "button": "left"}
{"type": "mouse_up",   "button": "left"}
{"type": "mouse_delta","dx": 300, "dy": -100}
{"type": "scroll",     "amount": -1}
{"type": "wait",       "seconds": 1.5}
{"type": "autoclick",  "button": "left", "clicks": 50, "interval": 0.04}
{"type": "autoclick",  "button": "left", "seconds": 10, "interval": 0.05}
```

### Holding several keys at once

`key` holds exactly one key and releases it before the next action. `keys_hold`
holds a set together, which is what sprinting, sneaking while walking, or
turning while moving need:

```json
{"type": "keys_hold", "keys": ["shift", "w"], "hold": 3.0}
```

`keys` accepts a list or a string — `"shift+w"`, `"shift w"` and `"shift,w"` all
parse the same. Before pressing, everything left over from earlier actions is
released, so a combination never stacks on a stale key. Forbidden keys are
dropped from the combination with a note, and at most 4 keys are held at once.

### Auto-clicker

`autoclick` exists because a list of 50 `click` actions is both slow and
imprecise. It takes either limit, or both:

```json
{"type": "autoclick", "button": "left", "clicks": 50, "interval": 0.04}
{"type": "autoclick", "button": "left", "seconds": 10, "interval": 0.05}
```

`clicks` bounds the burst, `seconds` bounds the time; whichever comes first
stops it. Give both when unsure — a model that miscounts still stops on time.
Measured rate is exact: 40 clicks at `interval=0.05` finished in 2.00 s
(20.0 clicks/s), 1.5 s of clicking produced 30 clicks. The pacing subtracts the
click's own duration instead of sleeping a fixed gap, which is why the rate holds
rather than drifting slower.

Limits: 500 clicks, 30 s, minimum interval 0.01 s. The abort event is checked
every iteration, so `release_all` and F12 land mid-burst. `validate` also returns
an `estimated_seconds` so the agent knows how long the step will take before
committing to it.

### Telling the agent how to play

The server ships with an `instructions` block the MCP client hands to the model
on connect. It is not decoration — the failure modes it addresses were all
observed on live runs:

- **Look before every action.** Never act on a frame you already used. The
  round-trip costs 2-20 s, so the game has moved on.
- **Read the state, not just the picture.** Menu or gameplay, counters, health,
  hotbar — and whether the previous action changed anything at all.
- **A menu swallows movement keys.** Close it before trying to walk.
- **Budget the timings.** A click is ~0.05 s, a menu transition ~0.3 s, a game
  load 10-40 s. `look` reports when the frame was taken and how long capture
  took; `act` reports the real elapsed time, so the agent can account for the
  gap it did not watch.
- **If nothing changed, do not repeat.** Doing it twice means the first did not
  land — look again and change something.

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

42 checks over the validator and the execution path: forbidden keys, unknown
actions, clamping, the per-call limit, string-form parsing, `keys_hold`
combinations, the auto-clicker's two limits, and the exact Win32 call order
(with `winapi` stubbed out, so the suite never touches your machine).

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

**Несколько клавиш одновременно.** `keys_hold` держит набор клавиш вместе —
бег с ускорением (`shift+w`), приседание на ходу (`ctrl+w`). Перед нажатием
сбрасываются остатки от прошлых действий, поэтому комбинация не накладывается
на чужую зажатую клавишу. Одновременно разрешено до 4 клавиш.

**Авто-кликер.** `autoclick` принимает лимит по числу кликов или по времени,
что сработает раньше — то и остановит:

```json
{"type": "autoclick", "button": "left", "clicks": 50, "interval": 0.04}
{"type": "autoclick", "button": "left", "seconds": 10, "interval": 0.05}
```

Темп точный: 40 кликов с интервалом 0.05 с отработали ровно за 2.00 с
(20.0 клик/с). Пауза считается как «начало + номер клика × интервал», а не как
фиксированный промежуток, поэтому частота не сползает вниз.

**Инструкции для агента** встроены в сервер (`instructions`) и доходят до
модели при подключении: осматривать кадр перед каждым действием, читать
состояние игры (меню, счётчики, хотбар), оценивать тайминги и не повторять
действие, которое не дало изменений. `look` возвращает время съёмки и
длительность захвата, `act` — фактическую длительность шага: по ним агент
считает, сколько времени прошло, пока он не смотрел.

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
