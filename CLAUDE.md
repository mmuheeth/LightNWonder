# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows-hosted control surface for slot-game simulators. The FastAPI backend
(`backend/`, port 8001) drives five local integrations — OBS Studio over
obs-websocket v5, a Virtual OLED button deck served by `OledPanelSvc.exe`, a
log-following screenshot recorder, Tesseract OCR over the frames OBS wrote, and
posted mouse clicks into the game's own Unity window — and the React dashboard
(`frontend/`, port 3001) is the UI for them. Every one is host-machine specific:
they talk to processes, windows and log files on the developer's own PC, not to a
network service.

The dashboard covers the first three, plus `features/games` for choosing the
active game, `features/roi` for cropping a configured region out of the latest
screenshot, `features/grid` for splitting that screenshot's reels into a matrix of
tiles, `features/paylines` for checking which of the game's winning patterns
those tiles satisfy, and `features/paytable` for the maths the running game
actually loaded. `game-input` and `ocr` are backend-only so far — endpoints
and services with no `src/features/` slice — so don't go hunting for their UI.

Two slices are whole routes rather than dashboard cards, both because their
content does not survive half a row.

`features/paytable` (`/game-config`, the "Game Config" tab) renders four cards —
current paytable, win geometry, payline combos, reel strips — out of a response
that also carries the symbol table those names come from and the scatter awards;
the slice is deliberately not a one-to-one rendering of the payload.

`features/analyze-spin` (`/analyze-spin`, the "Analyze Spin" tab) is every other
integration in one press: it records, screenshots, spins the i-deck, follows the
game log to the result, clicks take-win if anything was won, screenshots each
moment, stops recording, and then validates the cash meter and the paylines over
the frames it took. It is the only slice whose live half is not react-query — a
run publishes a snapshot per step over a WebSocket — and the only one that
composes other features' services rather than wrapping one of its own.

`README.md`, `backend/README.md` and `frontend/README.md` are unusually detailed
and current; read the relevant one before changing an integration.

## Commands

`.\start.ps1` from the repo root launches the backend and frontend **each in its
own window via `Start-Process pwsh`** (not Windows Terminal tabs). The backend
always starts elevated — accept the UAC/Avecto prompt in its own window — because
it needs High integrity to drive the i-deck and click the game (UIPI drops
Medium→High input; the whole cabinet is auto-elevated to High here). `-Elevate`
and `-NoAdmin` are both deprecated no-op aliases now that elevation is
unconditional. Confirm from `GET /api/ideck/status`: `ready` means the backend
can drive the panel, `access_denied` means it cannot. `Start-Process` payloads may
contain `;`, so the launch strings use `Set-Location …; & $venvPython -m app`.

Backend commands **must run from `backend/`** — `app` is imported from the
working directory, not installed into site-packages.

```bash
cd backend
source .venv/Scripts/activate      # Git Bash; .venv\Scripts\Activate.ps1 in PowerShell
python -m app                      # run (reads PORT from .env)
pytest                             # tests
pytest tests/test_obs.py::test_disconnect_is_idempotent      # one test
pytest --cov
ruff check . && ruff format --check .
mypy                               # strict, over `app` only
```

```bash
cd frontend
npm run dev                        # http://localhost:3001
npm run check                      # lint + format:check + test
npx vitest run src/lib/api.test.js # one test file
```

Use `localhost`, not `127.0.0.1`, for the frontend — Vite binds the hostname,
which resolves to IPv6 `[::1]` on Windows.

## The response envelope is the contract

Every backend response — success *and* failure — is
`{ success, message, data, error, meta }`, with `data` null on failure and
`error` null on success. Both keys always present.

- Endpoints declare `ApiResponse[T]` as `response_model` and return
  `ApiResponse[T].ok(data=...)`. Wrapping is explicit, never middleware, so
  OpenAPI advertises the real shape.
- Errors are **never** constructed by hand. Raise an `AppException` subclass
  from `app/exceptions/base.py`; `app/exceptions/handlers.py` is the only place
  an error response is built. New error type = new subclass with
  `status_code` / `error_code` / `message`.
- `frontend/src/lib/api.js` unwraps the envelope; `api-error.js` normalises every
  failure into one `ApiError` with `fieldErrors` ready for form binding. Feature
  code never sees the envelope.
- The one exception: `GET /api/event-capture/runs/{id}/screenshots/{file}`
  returns a raw image, because an `<img>` src cannot unwrap JSON.
- `tests/asserts.py` (`assert_success`, `assert_failure`) enforces the shape;
  use it in new endpoint tests.

Adding a backend resource is three files plus one line: `app/schemas/<thing>.py`,
`app/services/<thing>.py`, `app/api/endpoints/<thing>.py`, then register in
`app/api/router.py`. Endpoints stay thin; logic lives in the service.

## Backend architecture

**Services are module-level singletons, not classes.** `obs.py`, `ideck.py`,
`event_capture.py`, `game_input.py`, `ocr.py` and `analyze_spin.py` each hold
their state (client, lock, running run, cached engine) in module globals and
expose a `reset()` that `tests/conftest.py` calls autouse before and after every
test. Callers import the namespace, not the functions: `from app.services import
obs as obs_service`. Only `event_capture.reset()` and `analyze_spin.reset()` are
async — both have a task to cancel.

**Settings are composed by inheritance.** `Settings` in `app/config/runtime.py`
inherits `AgentSettings`, `ObsSettings`, `IDeckSettings`, `EventCaptureSettings`,
`GameInputSettings`, `OcrSettings`, `FrameSettings`, `PaylineSettings`,
`PaytableSettings` and `AnalyzeSpinSettings`
(each in its own `app/config/*.py`) while env var names stay flat — a new
integration is a new mixin, not a new settings object. `get_settings()` is `lru_cache`d and a
module-level `settings` instance is imported directly by services — so tests
override behaviour with
`monkeypatch.setattr(settings, "IDECK_GAME_CONFIG_DIR", tmp_path)` rather than
by building new settings. `app/core/config.py` is a re-export shim.

**Per-game data is separate from env config.** `app/config/game_config/games/<Game>.json`
ships with the code and carries the process name, log path, OBS window source,
ROIs, reel bounds, paylines, click targets and event rules. The i-deck is not
in that list: a key is pressed by the id the panel layout gives it, so the deck
needs nothing per-game.
`active_game.json` holds the current selection and is rewritten atomically by
`PUT /api/games/active` — no restart, no `.env` edit. Adding a game is adding a
JSON file.

Two keys point *out* of the repo, at the game's own install: `game_config`
(its `GameConfig` directory) and `win_geometry` (that directory's
`winGeometry.xml`). Neither is checked at load time: the install is not part of
this repo and a config naming it stays valid on a machine without it, so the
service reading the file is where a missing one becomes an error.

A third, `symbols`, is the display name per two-letter symbol code. It is the
one thing about a game's maths that is *declared* rather than read, and only
because it cannot be read — see below.

**Foreign formats get their own `app/utils` module**, deliberately ignorant of
who consumes them: `panel_xml.py` (i-deck layout), `panel_log.py` (panel service
log), `game_log.py` (game log → named events, plus `DEFAULT_RULES`),
`log_tail.py` (rotation-aware cursor), `log_search.py` (the opposite
question — reads a log *backwards* for the last line matching a pattern),
`game_math.py` (a paytable folder's `math.xml` and `gameConfig.cfg`),
`win_geometry.py` (`winGeometry.xml`, plus the conversion between its
0-indexed reel-first lines and a config's 1-indexed `[row, column]` ones),
`reel_stops.py` (a spin's logged stops plus the strips become the symbols that
were on screen -- naming them, never deciding what paid),
`win32.py` (the only ctypes),
`ocr.py` (runs the Tesseract program and reads its TSV back),
`image_roi.py` (crops a named region out of a frame),
`letterbox.py` (finds the part of a frame the game fills),
`reel_grid.py` (reads the `reel_bounds` block into positioned tiles),
`click_target.py` (reads the `button_targets` block),
`paylines.py` (reads the `paylines` block into ordered grid positions),
`similarity.py` (cosine similarity between two pictures),
`payline_overlay.py` (draws evaluated lines over a crop, and owns their palette),
`paths.py` (resolves untrusted filenames inside a root — use it for anything
that came off the wire).

**Regions and click targets are fractions of the game window, never pixels.**
`roi` crops *out* of a captured image and `button_targets` aims a click *into* a
window, but both are `0.0..1.0` against the rectangle **the game itself fills** —
so neither a different capture resolution nor a resized simulator needs a
re-measurement. Keep that convention when adding either.

For a click target that rectangle is the window client area, which `win32.py`
asks Windows for. For a region it is the **content box**: OBS writes every frame
at its canvas size and fits the window capture inside it, so a portrait game in a
landscape canvas arrives with black bars whose width is a property of the
window's shape at that moment. `utils/letterbox.py` finds the box by trimming
near-black borders, `services/roi.py` owns the question for ROI, OCR and grid
alike (`content_box()` / `resolve_box()`), and `Roi.to_box_within()` is what
resolves fractions against it. Three things are deliberate: detection refuses to
believe a box under a quarter of the frame or a frame with nothing above the
threshold (both come back as the whole frame, because a fade to black is a normal
thing for a screenshot to catch), the box is found once per frame rather than
once per region, and every response reports it beside the region's own box —
a crop of the wrong thing is either a badly measured region or a misdetected box,
and only the pair says which. `FRAME_LETTERBOX_TRIM=false` reverts to canvas
fractions. **Region values in a game config are therefore fractions of the game,
not of the canvas** — measuring one off a letterboxed screenshot means
subtracting the bars first.

**`reel_bounds` is the one exception, deliberately.** It is not fractions of
anything: it is `rows` and `columns`, counts of how many equal shares the
**`roi.reels` crop** divides into. The reels fill their own crop by definition,
so reel 3 is its third fifth and no spans need measuring. That is what keeps the
two blocks independent: the reel window moving on screen is a change to
`roi.reels` alone, and a sixth reel a change to `columns` alone. The per-span
`col_bounds`/`row_bounds` form this replaced is **rejected**, not ignored — a
config still carrying it would split evenly anyway and look correct. An unequal
grid (a wider centre reel) can no longer be described; that was the trade.
Its optional `inset` is a rectangle
again: a fraction of **each tile**, trimmed off every edge, which is what removes
the frame a game draws inside a reel to highlight a win. A request may override
it for one split — config errors are 500, request errors 400 — and a trim that
would leave no tile is rejected rather than clamped.

**`services/roi.py`, `services/ocr.py` and `services/grid.py` are the same
joinery, one step apart each.** All three resolve the active game's `roi` block
against the content box of a frame; ROI returns the crop, OCR hands it to
Tesseract, and grid divides it by `reel_bounds`. Keep them apart — checking that a region is aimed
correctly must not require an engine install. ROI reads the newest file in
`settings.obs_dashboard_screenshot_dir` and never asks OBS for a frame, so the
same crop extracted twice is the same picture, and **`roi.py` owns that question
for everyone**: its `resolve_frame`/`open_frame`/`content_box`/`resolve_box`/
`describe`/`describe_path`/`encode_png` are public and `grid.py` and `ocr.py`
call them rather than re-deriving "the latest screenshot" or re-reading a
letterboxed frame. ROI, grid and paylines are also the three services
holding no state, so none has a `reset()` nor anything in `conftest.py`.

**Every tile is the same pixel size, deliberately.** `ReelGrid.place()` rounds
each tile's *position* on its own and then gives them all one shared width and
height (the smallest that fits every span) — because rounding both edges of every
tile independently varies them by a pixel, and a grid of unequal tiles cannot be
stacked or fed to anything expecting one input size. Don't "simplify" it back to
`tile.roi.to_box()` per tile.

**`services/paylines.py` reads a written split rather than a screenshot, and
that is the whole point of the grid writing one.** ROI asks *is this rectangle
aimed right*, grid asks *does it divide into the symbols I expect*, paylines asks
*do those symbols line up* — each reads the previous one's output instead of
redoing it, which is why a check is repeatable at a new threshold without the
simulator still showing the same spin. `grid.py` owns reading a split back
(`latest_split` / `resolve_split` / `read_split`) for the same reason `roi.py`
owns "the latest screenshot": it named the directory, the crop and the tiles.

**Two tiles are the same symbol by cosine similarity, and the threshold is not
intuitive.** Pixel channels are non-negative, so the measure does not start at
zero for unrelated pictures — on FortuneOx's captures two *different* symbols
score 0.60–0.89 and two crops of the *same* symbol score 0.967 and up. The
separation is wide but far above where "0.7 means similar" would put it, so a
threshold that sounds generous calls almost every pair a match. Do not "fix"
that by inventing a normalisation: every result already reports `matched_min`
and `rejected_max`, the two numbers a working cut sits between — tune
`PAYLINE_MATCH_THRESHOLD` (default `0.85`) against a split that does not move.

**A payline is read left to right and stops at the first pair that does not
match**, so `pays` is the length of the *leading* run — 0 when reels 1 and 2
differ, otherwise 2 or more — and never a count of matches anywhere on the line.
Every adjacent pair is scored anyway and each step carries `matched` (are these
the same symbol) *and* `counted` (did the read get this far). A matched step that
was never counted is the interesting case; collapsing the two flags into one is
what makes a payline checker look wrong. Line colours come from
`utils/payline_overlay.py` and travel on the response, so the dashboard's swatch
and the drawn stroke cannot drift — the frontend never picks one.

**Every line gets its own picture, paying or not, with a break marker where its
run stopped.** `pays`'s counterpart is `break_position` -- the tile name where
the leading run failed, or null when the whole line paid -- and the picture at
`PaylineLine.image_data` draws exactly that line, its confirmed tiles ringed
green and (on this picture only) the break tile ringed red. The combined
`overlay_image` still draws only the *paying* lines and never the red ring --
several lines share that picture, and "here is where this one broke" is a
question about one of them. Both pictures are built from the one
`payline_overlay.DrawnLine` per config line that
`services/paylines._line_drawing()` produces: the combined overlay is that list
filtered to `pays >= 2` with `dataclasses.replace(drawing, break_point=None)`,
not a second drawing pass. A line's label on either picture is just `Line 4` --
`pays` is already numeric data on the response, not something to repeat as text
on the picture.

**`services/paytable.py` is a three-way join, and it reports how it joined.**
The game's log names the paytable it loaded (`paytableId[FortuneOx-1101YX-1c-90]`,
written on every denomination change); that string is byte-identical to a
directory under the game's installed `GameConfig` folder; and the game config in
this repo is what points at that folder and names its symbol codes. This is the
only module that knows all three, so every response carries `source.origin`
(`log` / `requested` / `only`) and the log line itself — a page showing the wrong
maths is a stale log or a hand-typed id, and only saying which lets a reader
tell. Failures split by whose fault it is: 409 the machine (no install), 404 the
log or the request (that id has no folder — and the message names *both* halves),
502 the files (present but unreadable). An unreadable `winGeometry.xml` is
deliberately none of those: it comes back as `win_geometry.error` on a 200,
because the symbols, strips and combos above it are all still true.

It is also the one service that caches, keyed on each file's mtime *and* size,
because `math.xml` is close to a megabyte — hence a `reset()` and a `conftest.py`
entry, unlike `roi`/`grid`/`paylines`.

**Which payline set is in play comes from the paytable, not from the maths.**
`winGeometry.xml` sits at the `GameConfig` root and is shared by all 53 of
FortuneOx's paytable folders; the same `math.xml` ships in folders that play 5,
20 and 40 lines, so its own `DefaultConfiguration/PaylineSetID` cannot be the
answer. `gameConfig.cfg`'s `NumberOfLines` is, and `resolved_from` says which
file answered. Lines travel in both forms — `elements` as the file writes them
(`[reel, position]`, 0-indexed) and `grid` as a config's `paylines` block writes
them (`[row, column]`, 1-indexed) — because the hand-copied JSON block exists so
`services/paylines.py` can check a screenshot on a machine without the game
installed, and the two are only comparable if both forms are sent.

**A symbol row is two files joined, and the split is not arbitrary.** Which
codes exist, what they pay and how much of the reels they occupy are *read* from
`math.xml`; what to call them is *declared* in the game config's `symbols` block.
That block exists because these files carry no display text in any element — not
in `SymbolSetList`, not in `ReelStripList`, nowhere — so `WC` becomes "WILD"
there or nowhere. Underneath it `ROLE_LABELS` supplies `Wild`/`Scatter` from
what the maths' structure implies (`GameMath.roles()`: in `WildSymbolList` →
wild, counted by a `CountScatterCombo` → scatter), so a game whose config names
nothing yet is still readable.

**Counts come from `ReelStripList`, and each code is counted twice.**
`GameMath.reel_symbols()` reads the strips — what can actually land — for stops
on the base game's own reels *and* across every strip in the file, because 0
beside a non-zero total is what tells a feature-only symbol from one this game
does not have. A code the symbol set only declares is still listed
(`on_reels: false`, all zeros): FortuneOx declares eighteen and strips carry
seventeen, and the eighteenth is a named feature symbol with an award, so
dropping it would silently shorten the table the config was written against.

**`ANY` in a combo is not a symbol** — it is the trailing wildcard, so
`[X, X, X, ANY, ANY]` is the same *leading run* rule `services/paylines.py`
reads a split by. Because of that, every line pay is really a (symbol, run
length, value) triple, and the response carries both shapes: `payline_combos`
as `math.xml` declares them, and `pay_lengths`/`pay_table` pivoted into the grid
a paytable poster actually is (`GameMath.line_pays()` + `_pay_table()`), with
symbols paying identically at every length merged into one row. Don't drop the
declared list — a combo mixing two symbols has no per-symbol row to sit in and
would vanish with it.

**Grid always writes its output; ROI only writes the cash meter.** A split goes
to `obs-captured-files/grid/<frame stem>/` — `reels.png` plus `tiles/r1c1.png`,
1-indexed and row-major — one directory per source frame, so re-splitting a frame
replaces its own record. A payline check writes its annotated reels into that
same directory as `paylines/<set>.png`, one per (split, set), because a pay count
cannot be checked by reading it and re-checking at a new threshold should replace
its own record too. Stale tiles from a differently-shaped grid are cleared,
and only names matching a tile's own pattern are touched. ROI's own
`_SAVED_REGION` writes `cash-meter/<frame>.png` and every other region comes back
as a data URI only.

**`services/analyze_spin.py` is orchestration and nothing else.** It owns no
image handling, no XML, no win32 — it is the *order* the other services go in
(OBS record/screenshot, i-deck press, game log wait, game-input click, then ROI's
meter and grid+paylines), and every step carries the underlying service's own
error code. `_step` also has one convention worth knowing: a body that sets
`step.error` **without raising** is recorded as failed and the run carries on,
for the step that did its work and knows the result is unusable. Six things it
exists to get right:

- **A losing spin is proven by silence.** The game logs `[WinBangDone]` when the
  win meter counts up and logs nothing at all when there is no win, so "no win"
  is that line's absence within `ANALYZE_SPIN_WIN_WAIT_SECONDS`. Set below the
  longest count-up, a win comes back as a loss — the only setting here that
  produces a confidently wrong answer rather than a timeout.
- **The steps exist before they run.** A run is created with all twelve
  `pending`, so a failure on step four leaves the rest visibly unreached, and
  take-win on a losing spin is `skipped` — a different fact from unreached.
- **Cancellation is cooperative, never `task.cancel()`.** Every wait polls and
  every poll checks a flag, so a cancelled run unwinds through its own code and
  no `finally` runs under a pending `CancelledError` (which is also what keeps
  `filterwarnings = error` happy). `abort()` in the lifespan runs *before* OBS
  disconnects, because the one thing worth getting right at shutdown is OBS not
  being left recording.
- **Screenshots go where the validations read**: the dashboard screenshot dir
  (`OBS_SCREENSHOT_SUBDIR`), not a per-run one, because `roi.py` and `grid.py`
  open a frame *by name* there. `frame_path()` delegates to
  `roi.resolve_frame()` rather than re-deriving the guards.
- **An empty frame is read back, not trusted.** OBS renders nothing for a moment
  after its window source is re-pointed and reports a successful write of the
  black frame anyway, so `prepare` settles after retargeting and every capture is
  probed with `roi.is_blank` and retried. A frame that stays blank sets
  `blank` on itself and fails its step *without raising* — see the
  error-without-raising convention on `_step`.
- **The two validations cannot fail each other**, and each catches its own
  exceptions so the run reaches both. A validation that runs and reports `failed`
  is a *completed* step; only one that could not run at all fails.

**Payline validation is three judgements from three sources, kept strictly
apart.** This is the invariant to preserve if anything here is refactored:

- **The picture decides what landed.** `pays` is the leading run of tiles cosine
  similarity found alike, and every pair's score travels on `steps` — a run of
  two is a claim about two numbers and is not checkable without them. Nothing
  about the win comes out of the log: a checker that read the answer there would
  agree with the game by construction and could never catch a reel drawing the
  wrong symbol.
- **The paytable decides whether it pays.** `paying` (two or more tiles alike) is
  *evidence*; `awarded` is the win. A run of two of a symbol paying from three is
  cancelled — `awarded: false`, a `note` naming `min_pay_length`, no credits, and
  excluded from `expected`. `services/paylines.py` deliberately stops at "two or
  more" because what pays what is not its business; `analyze_spin` has the
  paytable, so the call belongs there — including `summary`, which is written
  from the awards rather than reused from the check.
- **A cancelled run is not a result.** Its per-line picture is dropped from the
  payload and the combined overlay is redrawn over the awarded lines only, via
  `paylines.redraw()` — which rebuilds from the finished result through the same
  `_line_drawing`, so a redrawn line cannot differ from a first-pass one, and
  replaces the file the check wrote. Skipped when nothing was cancelled. Its
  `steps` survive: the scores are the evidence, and a run the maths would have
  paid one symbol longer means the threshold is worth revisiting.
- **"Pays" means credits, everywhere.** A line *matches* five symbols and *pays*
  twenty-five. Never use the word for the run length — that is `pays` the field,
  and rendering it as "pays 5" is what made a run of five read as five credits.
- **The logged reel stops only name the symbols.**
  `ReelSet.SetStops(ReelsStopData)` (`game_log.REEL_STOPS`, deliberately *not* a
  `DEFAULT_RULES` entry — it is nothing to screenshot) plus `math.xml`'s strips
  give the code at every grid position, via `utils/reel_stops.py`. That is the
  one thing similarity cannot say about a run, and what turns "something paying
  at three" into one combo (`_combo_for`) and one credit value. Without stops the
  award stays a range (`value_min`/`value_max`, `exact: false`) rather than being
  guessed.
- Both readings are reported per line, and `agrees: false` is an *output*, not an
  error: a wild standing in, or reels drawing what the maths did not say landed.
- **A stop index does not say which row it is.** `ANALYZE_SPIN_REEL_STOP_ANCHOR`
  defaults to `auto`, which builds top/middle/bottom and keeps whichever agrees
  best with the pairs similarity already measured. `stop_anchor_decided` says
  when that was a tie, so an ambiguous spin does not look certain.

**Which lines exist still comes from the game's own geometry.**
`services/paylines.check_lines()` is the public door for a line set the caller
assembled — `analyze_spin` builds one from the paytable's applicable
`winGeometry.xml` set, so the lines are the ones the *running* game plays rather
than the hand-copy in `games/<Game>.json` (which exists for machines without the
game installed). Both entry points funnel through `_evaluate_set`, so a set from
either source is scored, drawn and written identically; the geometry set is named
`geometry-<id>` so its overlay never overwrites a config-sourced one. It is built
by handing a dict to `utils/paylines.read_set()` rather than constructing the
dataclasses, because that is where a line is checked to run left to right.

**The progress stream is the only WebSocket, and the only non-envelope JSON.**
`WS /api/analyze-spin/stream` sends a whole `SpinAnalysisState` on connect and one
per change — snapshots, not deltas, so a late or lossy subscriber is still
correct. It carries no images; `GET /status?include_images=true` is the report.
The endpoint runs a push task and a receive task and takes whichever finishes
first, because a one-way stream that never reads would only notice a closed tab
on its next send. Vite needs `ws: true` on the `/api` proxy or the upgrade gets
the HTML index.

**Request correlation.** `RequestContextMiddleware` is registered last, so it
runs outermost. The id lives both in a `ContextVar` (for loggers and envelope
builders) and on the ASGI scope, because Starlette's `ServerErrorMiddleware`
runs after the context var is reset and unhandled 500s still need an id.

**Health is at the root**, not under `/api`. `/health` and `/health/live` always
200 with the verdict in the payload; only `/health/ready` fails (503). Register
a probe with `@register_probe` in the lifespan for anything the service cannot
work without — OBS is deliberately *not* one, since a closed screen recorder
should not mark the service unavailable.

**Database.** `app/services/database.py` opens an asyncpg pool in the lifespan
and does nothing else — no schema, tables or migrations. An empty or missing
`DATABASE_URL` disables it silently so local dev and tests run without a
database; when configured, a failed connection aborts startup.

**`app/agents/` is wired to nothing yet.** LangChain v1 + LangGraph v1 building
blocks — `build_chat_model`, `build_agent`, `new_workflow`/`compile_workflow`,
and sync/async `checkpoint_context` — imported by no service and no endpoint.
Treat it as the sanctioned shape for the first real agent, not as dead code.
Three rules it exists to enforce:

- **Nothing is constructed at import time.** No model, no database connection.
  The caller composes an agent in its service layer and owns invocation, thread
  ids and the checkpointer's lifetime, which keeps request lifecycles and
  background workflows independently testable.
- **`AGENT_ENABLED` is `false` by default** and `build_agent` raises
  `AgentDisabledError` until it is set. `.env.example` ships dummy credentials,
  so `is_placeholder_secret()` in `app/config/agents.py` screens values like
  `dummy-replace-with-real-key` and refuses to forward them to a provider or to
  LangSmith. Add a marker there rather than working around it.
- **Provider-neutral.** `AGENT_MODEL` uses LangChain's `provider:model`
  notation (`openai:gpt-4o-mini`, `anthropic:claude-sonnet-4-6`); only
  `langchain-openai` and `langchain-anthropic` are installed. A new provider is
  a new `langchain-<provider>` package, plus `AGENT_API_KEY_PARAM` if its
  constructor does not take `api_key`.

`AGENT_CHECKPOINT_BACKEND` is `memory` locally; `postgres` imports the Postgres
saver lazily inside the context manager and falls back to `DATABASE_URL` when
`AGENT_CHECKPOINT_URL` is empty. `recursion_limit` is invocation config, not a
`compile()` argument — it comes from `default_run_config()`.

## Test conventions that bite

- `pytest.ini` sets `filterwarnings = error`. A task cancelled but not awaited
  becomes a pending-task warning, which becomes a test failure — that is why
  `event_capture_service.reset()` is async and why services have no background
  reconnect task.
- `asyncio_mode = auto`: async tests need no marker.
- The `client` fixture uses `httpx.ASGITransport`, which **does not run the
  lifespan**. Startup work (the DB pool, OBS auto-connect) never happens in
  tests; anything that must be exercised has to be called directly.
- `raise_app_exceptions=False` on the transport, so handler output is asserted
  rather than the exception propagating.
- `mypy.ini` has `files = app` — tests are not strictly typed. It pins
  `python_version = 3.12` because numpy's stubs (via `opencv-python`) use syntax
  mypy rejects below it; ruff still targets `py311`.
- Ruff deliberately does **not** enable `TCH`: moving Pydantic/FastAPI imports
  into `TYPE_CHECKING` breaks runtime annotation resolution.

## Frontend architecture

Plain JavaScript, no TypeScript. Feature-first: each directory under
`src/features/` owns its `api.js`, hooks and components, so a feature deletes in
one directory. Shared plumbing is in `src/lib/`.

- Cache keys come from the `queryKeys` registry in `lib/query-keys.js` — never
  inline an array literal.
- react-query owns server state; Zustand (`store/ui-store.js`) owns only the
  client-side theme. Do not copy server data into Zustand.
- **Background polling is opt-in.** `useObsStatus` and `useIDeckStatus` default
  to `refetchInterval: false` with `staleTime: 30_000`; mutations invalidate the
  subtree, so an action still refreshes status immediately. Don't reintroduce a
  standing poll for these. `useCaptureStatus` in `features/event-capture/` is the
  reference for a functional `refetchInterval` — 2s while a run is live,
  `false` when idle, so no panel polls without a reason to. That slice also
  owns `captureImageUrl()`, the one fetch that bypasses `apiRequest`.
- **`features/analyze-spin/` is the one live view that is not react-query.** A
  run pushes a snapshot per step over a WebSocket, so `useSpinStream` holds it in
  component state and `useSpinReport` fetches only the pictures (which the stream
  omits) once the run ends. `useSpinView` composes the two and uses the report's
  images only when its `run_id` matches the live one — the stream can already be
  on a new run while the report holds the last. Don't turn this into a poll, and
  don't copy the pattern into a slice whose data changes at human speed.
- `features/obs/` is still the reference slice for the query + mutate shape.
- Tailwind v4 has no `tailwind.config.js`; theming is CSS-first in
  `src/index.css` (`@theme inline` + `:root`/`.dark` oklch values).
- `src/components/ui/` is vendored shadcn output — excluded from Prettier and
  from the `react-refresh` lint rule. Add components with
  `npx shadcn@latest add <name>`.
- Dev is same-origin: leave `VITE_API_BASE_URL` empty and let the Vite proxy
  forward `/api` to the backend, so cookies work and CORS never applies.

## Windows-specific gotchas

- **i-deck presses need matching integrity levels.** Windows UIPI drops input
  sent from a lower integrity level to a higher one, so if `OledPanelSvc.exe` was
  launched elevated the backend must be too. `GET /api/ideck/status` reports
  `access_denied` instead of leaving you to guess.
- **Nothing moves the cursor.** Presses are `PostMessage`d as
  `WM_MOUSEMOVE` + `WM_LBUTTONDOWN` + `WM_LBUTTONUP`; the move first is required
  because SDL takes a click's position from the last motion event.
- **Presses are confirmed, not assumed.** Each one reads only what was appended
  to the panel log and returns 502 if the press did not land.
- **Take-win and gamble are not on the i-deck.** The panel has fourteen keys and
  neither is among them — the games' logs prove it, arriving as a `TouchMsg` from
  the glass. So `services/game_input.py` posts clicks into the simulator's Unity
  window. There is no API to ask politely with: Unity implements neither UI
  Automation nor MSAA, and GDK's GAF Thrift server (which has a literal
  `SimulateTouch`) is present in `Assembly-CSharp.dll` but not started by a
  normal launch. If it ever is, it belongs behind that module's public API.
- **A missed click is silent** — no error, no exception, just nothing. So each
  one records the game log's size first and reads only what was appended.
  A target's `confirm` key names a `game_log` event, which proves *which* button
  was hit; without one the fallback is `TOUCH_REGISTERED`, which proves only that
  input arrived. The two are reported apart, never blurred — don't collapse them.
- **OBS window selection needs the game running.** The window identifier is
  `title:class:executable` matched against the list OBS enumerates itself; one
  built from the process name alone binds to nothing and fails silently as a
  blank screenshot much later.
- **OBS state settles asynchronously.** Recording start/stop polls for the real
  state before answering rather than reading it immediately.
- **Tesseract is not on `PATH`.** Its Windows installer does not add it, so
  `app/config/ocr.py` discovers the binary where the installers put it and
  `OCR_TESSERACT_CMD` overrides that. OCR is optional like OBS: no engine means
  `GET /api/ocr/status` says `not_installed` and only reads fail.
- `obs-captured-files/` (screenshots, recordings, `event-capture/<run>/`) is
  gitignored output, not source.
