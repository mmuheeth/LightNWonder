# LightNWonder — Backend

FastAPI service. Runs on **port 8001**.

## Setup

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash); .venv\Scripts\Activate.ps1 on PowerShell
# source .venv/bin/activate        # macOS / Linux
pip install -r requirements-dev.txt
cp .env.example .env
```

`requirements.txt` holds runtime dependencies; `requirements-dev.txt` pulls those
in and adds the test and lint tooling. For reproducible deploys, freeze exact
versions with `pip freeze > requirements.lock.txt`.

> Run every command below from the `backend/` directory — the `app` package is
> imported from the working directory rather than installed into site-packages.

## Run

```bash
python -m app                      # reads PORT from .env (8001)
# or
uvicorn app.main:app --reload --port 8001
```

| URL                            | What                        |
| ------------------------------ | --------------------------- |
| `http://localhost:8001/`       | Service info                |
| `http://localhost:8001/health` | Health report               |
| `http://localhost:8001/docs`   | Swagger UI (hidden in prod) |
| `http://localhost:8001/redoc`  | ReDoc                       |
| `http://localhost:8001/api/…`  | API endpoints               |

## Checks

```bash
pytest                 # tests
pytest --cov           # tests with coverage
ruff check .           # lint
ruff format .          # format
mypy                   # types (strict)
```

Tool configuration lives in `ruff.toml`, `pytest.ini`, `mypy.ini` and `.coveragerc`.

## Layout

```
backend/
├── requirements.txt         runtime dependencies
├── requirements-dev.txt     + test and lint tooling
├── ruff.toml / pytest.ini / mypy.ini / .coveragerc
├── obs-captured-files/             default screenshot/recording root (gitignored)
└── app/
    ├── main.py              create_app() factory, middleware wiring, lifespan
    ├── server.py            uvicorn entrypoint (python -m app)
    ├── core/
    │   ├── config.py        compatibility import for runtime settings
    │   ├── context.py       request-id ContextVar + ASGI scope key
    │   └── logging.py       dictConfig; console or JSON, request id on every line
    ├── config/              runtime settings plus shipped game data/readers
    │   ├── runtime.py       general settings and feature-settings composition
    │   ├── agents.py        provider-neutral agent/workflow settings
    │   ├── obs.py           OBS runtime settings
    │   ├── ideck.py         i-deck runtime settings and path resolution
    │   ├── event_capture.py Event Based Capture runtime settings
    │   ├── ocr.py           Tesseract OCR settings, and finding the engine
    │   ├── paylines.py      match threshold, default line set, overlay size
    │   ├── paytable.py      how far back to read the log for the loaded paytable
    │   ├── analyze_spin.py  which keys drive one spin, and how long each stage may take
    │   ├── image_classifier.py  where the training images and the trained models
    │                         live, which network to fit, the confidence floor,
    │                         and torch's thread cap
    │   └── game_config/     active selection plus per-game process, logs,
    │                         ROIs, reel bounds, paylines, targets, the game's
    │                         installed GameConfig directory and symbol names
    ├── utils/
    │   ├── win32.py         the only ctypes: posts messages to another window
    │   ├── panel_xml.py     reads the i-deck layout the panel service renders from
    │   ├── panel_log.py     matches the lines that panel service writes
    │   ├── game_log.py      parses the game's log and names its events
    │   ├── log_tail.py      rotation-aware cursor over a growing file, and the
    │                         follower that polls one for new lines
    │   ├── log_search.py    reads a log backwards for the last line matching a
    │                         pattern -- the opposite question to log_tail's
    │   ├── game_math.py     reads a paytable folder's math.xml (symbols, reel
    │                         strips, combos) and its gameConfig.cfg identity
    │   ├── reel_stops.py    turns a spin's logged reel stops plus the strips into
    │                         the symbols that were on screen. Unread since Analyze
    │                         Spin started naming tiles from the picture instead --
    │                         a symbol named from the log agrees with the game
    │   ├── win_geometry.py  reads winGeometry.xml: where each payline runs, and
    │                         converts a line to a game config's [row, column]
    │   ├── image_roi.py     crops a config's named region out of a frame, by
    │                         fractions, so it survives a resolution change
    │   ├── click_target.py   reads a config's named click targets, by fractions,
    │                         so they survive a window resize
    │   ├── reel_grid.py     reads a config's reel bounds and hands back the
    │                         tiles of the matrix, by fractions of the crop,
    │                         border trim included
    │   ├── paylines.py     reads a config's winning patterns, per bet
    │                         configuration, as 1-indexed grid positions
    │   ├── similarity.py    cosine similarity between two pictures, which is
    │                         how a glowing symbol still matches itself
    │   ├── symbol_dataset.py turns a folder of symbol artwork into training
    │                         pictures, putting back the reel background the
    │                         cut-outs ship without -- no torch, deliberately
    │   ├── symbol_model.py  the only module that imports torch: EfficientNet-B0,
    │                         its transforms, the training loop and a checkpoint
    │   ├── symbol_overlay.py rings each cell the classifier named, over the
    │                         reels -- no text; the codes are a table beside it
    │   ├── payline_overlay.py draws evaluated lines over the reels, one colour
    │                         each, and owns the palette they are reported with
    │   ├── ocr.py           runs Tesseract over an image and reads its answer
    │   └── paths.py         resolves an untrusted filename inside a directory
    ├── api/
    │   ├── router.py        aggregates endpoint modules  → mounted at /api
    │   ├── health.py        /health, /health/live, /health/ready  ← root, not /api
    │   └── endpoints/       one module per resource
    ├── schemas/
    │   ├── response.py      ApiResponse / PaginatedResponse envelope  ← the contract
    │   ├── health.py        health payloads
    │   ├── system.py        service info payload
    │   ├── games.py         game catalog and runtime-selection payloads
    │   ├── obs.py           OBS status, screenshot and recording payloads
    │   ├── event_capture.py capture runs and the events inside them
    │   ├── game_input.py     game window state, click targets and click results
    │   ├── ocr.py           engine status, options and what was read
    │   ├── roi.py           region catalog, and one extracted crop
    │   ├── grid.py          reel grid shape, and one split into tiles
    │   ├── image_classifier.py engine state, the dataset, a training run and
    │                         every tile of a split named
    │   ├── paylines.py      line sets, one line's verdict and its evidence
    │   ├── paytable.py      the loaded paytable, its maths and its geometry
    │   └── analyze_spin.py  one driven spin: its steps, and the two validations
    ├── exceptions/
    │   ├── base.py          AppException hierarchy
    │   └── handlers.py      the only place error responses are built
    ├── middleware/
    │   └── request_context.py   request id, timing, access log
    ├── agents/               LangChain model, agent, workflow, and checkpoint factories
    └── services/            business logic; endpoints stay thin
        ├── games.py          game catalog and active-game switching
        ├── obs.py            the single long-lived obs-websocket session
        ├── event_capture.py  follows the game log and screenshots its events
        ├── game_input.py     clicks the game's own window, proven by its log
        ├── ocr.py            reads the game's meters off a frame
        ├── grid.py           splits the reels of a screenshot into tiles,
        │                      and reads one of its own splits back
        ├── paylines.py       matches a split's tiles against the patterns that pay
        ├── image_classifier.py names each tile's symbol from the picture, and
        │                      owns the one training run the process may have
        ├── meter.py          turns a cash-meter crop into its five values
        ├── paytable.py       joins the game's log to the maths it installed
        ├── roi.py            cuts one configured region out of a screenshot
        └── analyze_spin.py   drives one spin through all of the above, then
                              validates the meter and the paylines over it
```

Adding a resource is three files plus one line:

1. `app/schemas/<thing>.py` — request/response models
2. `app/services/<thing>.py` — business logic
3. `app/api/endpoints/<thing>.py` — thin route handlers
4. register it in `app/api/router.py`

## The response contract

Every response — success or failure — has the same five top-level keys. `data`
is `null` on failure, `error` is `null` on success, so clients never branch on
key existence.

**Success**

```json
{
  "success": true,
  "message": "Game selected successfully",
  "data": { "game": "FortuneOx" },
  "error": null,
  "meta": {
    "request_id": "3f9a1c8e4b7d4f0e9a2c5b8d1e4f7a0c",
    "timestamp": "2026-08-18T09:12:44.512Z"
  }
}
```

**Failure**

```json
{
  "success": false,
  "message": "Request validation failed",
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "details": [
      { "field": "body.game", "message": "Field required", "type": "missing" }
    ]
  },
  "meta": {
    "request_id": "3f9a1c8e4b7d4f0e9a2c5b8d1e4f7a0c",
    "timestamp": "2026-08-18T09:12:44.512Z"
  }
}
```

**List endpoints** use `PaginatedResponse`, which keeps the same shape but adds
pagination fields to `meta`:

```json
"meta": {
  "request_id": "…", "timestamp": "…",
  "page": 2, "page_size": 20, "total_items": 47,
  "total_pages": 3, "has_next": true, "has_previous": true
}
```

`success` always mirrors the HTTP status: `true` for 2xx, `false` otherwise.

### Writing an endpoint

Declare the envelope as the `response_model` so OpenAPI advertises the real
shape, and return `ApiResponse.ok(...)`:

```python
@router.get("/", response_model=ApiResponse[GameCatalog])
async def list_games() -> ApiResponse[GameCatalog]:
    return ApiResponse[GameCatalog].ok(data=games_service.catalog())
```

Wrapping is explicit rather than done by a catch-all middleware. A middleware
would have to re-serialise every body, and OpenAPI would advertise the inner
payload instead of the envelope — so generated clients would be wrong.

### Raising an error

Never build an error response. Raise an `AppException` subclass; the registered
handlers render it.

```python
from app.exceptions import ConflictError, NotFoundError

raise NotFoundError("The requested resource was not found")

raise ConflictError(
    "Editing is restricted to owners",
    error_code="NOT_RESOURCE_OWNER",  # override the default code
    details=[ErrorDetail(field="owner_id", message="Must match the caller")],
)
```

| Exception                  | Status | Default code           |
| -------------------------- | -----: | ---------------------- |
| `BadRequestError`          |    400 | `BAD_REQUEST`          |
| `UnauthorizedError`        |    401 | `UNAUTHORIZED`         |
| `ForbiddenError`           |    403 | `FORBIDDEN`            |
| `NotFoundError`            |    404 | `NOT_FOUND`            |
| `ConflictError`            |    409 | `CONFLICT`             |
| `UnprocessableEntityError` |    422 | `UNPROCESSABLE_ENTITY` |
| `RateLimitError`           |    429 | `RATE_LIMIT_EXCEEDED`  |
| `ServiceUnavailableError`  |    503 | `SERVICE_UNAVAILABLE`  |
| `ObsNotConnectedError`     |    409 | `OBS_NOT_CONNECTED`    |
| `ObsConnectionError`       |    502 | `OBS_CONNECTION_FAILED`|
| `ObsRequestError`          |    502 | `OBS_REQUEST_FAILED`   |
| `IDeckWindowNotFoundError` |    409 | `IDECK_WINDOW_NOT_FOUND` |
| `IDeckAccessDeniedError`   |    409 | `IDECK_ACCESS_DENIED`  |
| `IDeckButtonNotFoundError` |    404 | `IDECK_BUTTON_NOT_FOUND` |
| `IDeckPressNotConfirmedError` | 502 | `IDECK_PRESS_NOT_CONFIRMED` |
| `IDeckConfigError`         |    500 | `IDECK_CONFIG_INVALID` |
| `EventCaptureAlreadyRunningError` | 409 | `EVENT_CAPTURE_ALREADY_RUNNING` |
| `EventCaptureNotRunningError` | 409 | `EVENT_CAPTURE_NOT_RUNNING` |
| `EventCaptureLogUnavailableError` | 409 | `EVENT_CAPTURE_LOG_UNAVAILABLE` |
| `EventCaptureRunNotFoundError` | 404 | `EVENT_CAPTURE_RUN_NOT_FOUND` |
| `SpinAnalysisAlreadyRunningError` | 409 | `SPIN_ANALYSIS_ALREADY_RUNNING` |
| `SpinAnalysisNotRunningError` | 409 | `SPIN_ANALYSIS_NOT_RUNNING` |
| `SpinAnalysisUnavailableError` | 409 | `SPIN_ANALYSIS_UNAVAILABLE` |

Add your own by subclassing:

```python
class PaymentRequiredError(AppException):
    status_code = 402
    error_code = "PAYMENT_REQUIRED"
    message = "Payment is required to continue"
```

Request validation failures (422), framework `HTTPException`s (404 on unknown
routes, 405, …) and completely unhandled exceptions are wrapped automatically.
Unhandled exceptions log a full traceback and return an opaque
`INTERNAL_SERVER_ERROR`; the exception text is echoed in `error.details` only
when `DEBUG=true`.

## Health checks

Mounted at the root, **not** under `/api`, so probes never depend on the API's
base path.

| Endpoint        | Purpose   | Behaviour                                               |
| --------------- | --------- | ------------------------------------------------------- |
| `/health`       | report    | Always 200; verdict in `data.status`                     |
| `/health/live`  | liveness  | Always 200; touches no dependencies                      |
| `/health/ready` | readiness | 200 when ready, **503** when any dependency probe fails |

Liveness is deliberately dependency-free: a database outage should not make the
orchestrator restart otherwise-healthy pods. `/health` and `/health/live` are
*reports*, so they stay 200 and put the verdict in the payload; only
`/health/ready` fails the request, because that is the signal orchestrators act
on — and it fails through the normal exception path, so its error envelope looks
like every other error in the service.

Register a probe for anything the service cannot work without — it feeds both
`/health` and `/health/ready`:

```python
# in the lifespan startup block in app/main.py
from app.schemas.health import DependencyCheck
from app.services.health import probe, register_probe


@register_probe
async def postgres() -> DependencyCheck:
    return await probe("postgres", lambda: pool.execute("SELECT 1"))
```

Probes run concurrently, are individually timed, and each is capped at 3s. A
probe that raises or times out is reported as unhealthy rather than breaking
the endpoint.

## Configuration

All settings come from the environment (or `.env` locally) — see
[.env.example](.env.example). Access them with `get_settings()`, which is
`lru_cache`d so the environment is parsed once per process.

`ENVIRONMENT=production` automatically hides `/docs`, `/redoc` and
`/openapi.json`, and disables reload.

## Agentic workflows (LangChain + LangGraph)

The backend includes LangChain v1 and LangGraph v1 as a provider-neutral agent
runtime. Set `AGENT_MODEL` with LangChain's `provider:model` notation, such as
`openai:gpt-4o-mini` or `anthropic:claude-sonnet-4-6`. The OpenAI and Anthropic
adapters are installed; add the corresponding `langchain-<provider>` package
for another provider. `AGENT_API_KEY` is the generic credential override, and
`AGENT_API_KEY_PARAM` supports providers whose constructor uses a different
keyword.

Agent execution is disabled by default. The checked-in `.env.example` contains
obvious dummy values so the variable names are present without looking like
usable credentials. Replace the selected provider key and set
`AGENT_ENABLED=true` before invoking a model. Model construction itself never
makes a network request.

The reusable factories keep request code small while leaving graph topology in
your feature/service layer:

```python
from langchain.tools import tool

from app.agents import build_agent, checkpoint_context, default_run_config
from app.config.runtime import settings


@tool
def describe_game(game: str) -> str:
    """Return a short description of a configured game."""
    return f"Configured game: {game}"


with checkpoint_context(settings, database_url=settings.DATABASE_URL) as saver:
    agent = build_agent(settings, tools=[describe_game], checkpointer=saver)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Describe FortuneOx."}]},
        default_run_config(settings, thread_id="demo-thread"),
    )
```

`AGENT_CHECKPOINT_BACKEND=memory` is the safe local default. Select `postgres`
to use `AGENT_CHECKPOINT_URL` (or `DATABASE_URL`) with the included Postgres
checkpointer package; set `AGENT_CHECKPOINT_SETUP=true` for the one-time table
initialisation. `new_workflow()` and `compile_workflow()` expose the lower-level
LangGraph `StateGraph` path for routers, human-in-the-loop steps, and
multi-agent compositions. `LANGCHAIN_TRACING_V2=true` enables optional
LangSmith tracing after a real `LANGCHAIN_API_KEY` is supplied.

## OBS Studio

Optional, and off by default. Enable the WebSocket server in OBS under *Tools >
WebSocket Server Settings*, then set `OBS_PASSWORD` in `.env`. OBS Studio 28+
bundles the obs-websocket v5 plugin; older versions need it installed.

```
GET  /api/obs/status             connection state; always 200
POST /api/obs/connect            open a session (idempotent)
POST /api/obs/disconnect         close it (idempotent, never fails)
POST /api/obs/select-game-window point the active scene's source at the active game
POST /api/obs/screenshot         base64 data URI, or a file in the screenshot root
GET  /api/obs/recording          recording state
POST /api/obs/recording/start    start; accepts optional output_dir
POST /api/obs/recording/stop
POST /api/obs/recording/pause
POST /api/obs/recording/resume
```

Things worth knowing:

- **The app boots fine without OBS.** `OBS_AUTO_CONNECT=true` connects at
  startup, but a failure is logged, not raised. OBS is deliberately *not*
  registered as a health probe: a closed screen recorder should not make
  `/health/ready` report the whole service unavailable.
- **Reconnect is lazy.** A dropped socket is re-identified on the next request,
  so quitting and reopening OBS needs no explicit `/connect`.
- **The game window is config-driven.** On connect, and through
  `/api/obs/select-game-window`, the active program scene's window-capture source
  is pointed at the `process` in the selected
  `app/config/game_config/games/<game>.json`. If a scene has several such
  sources, set its name in that file as
  `{ "obs": { "window_source": "Game Window" } }`.
- **The game has to be running to be selected.** The window is matched against
  the list OBS enumerates itself, so the identifier written to the source carries
  a real window class. An identifier built from the process name alone
  (`::game.exe`) matches nothing, and OBS reports no error for it -- the source
  just renders nothing, which shows up much later as a blank screenshot. When OBS
  lists no window for the configured process, selection fails with 502
  `OBS_REQUEST_FAILED` and names the executables it did see.
- **Capture roots are configurable.** `OBS_CAPTURE_DIR` defaults to
  `backend/obs-captured-files`. `OBS_SCREENSHOT_DIR` and `OBS_RECORDING_DIR` can
  override the two roots independently; when omitted, both fall back to
  `OBS_CAPTURE_DIR`.
- **Use-case folders are request-configurable.** A screenshot request can send
  `{"file_name": "frame", "output_dir": "ir-inspection/session-01"}`;
  recording start accepts the same `output_dir` field. The value must be a
  relative subdirectory below the relevant configured root, so use cases stay
  separated without turning the API into an arbitrary-file-write primitive.
- **`file_name` remains a bare filename.** It is resolved inside the selected
  screenshot directory and rejected if it contains a path separator or `..`.
- **A screenshot always comes back as base64.** `file_name` additionally writes
  the file and adds `file_path` to the response; without one, only the inline
  data URI is returned. So the dashboard shows the same preview either way, and
  saving is a separate decision from previewing.
- **`OBS_SCREENSHOT_SUBDIR` is where the dashboard writes.** The Screenshot
  button sends `output_dir` set to it -- `screenshots` by default, so
  `obs-captured-files/screenshots` -- and it is the directory
  `GET /api/roi/regions` reads its newest frame from. Named in configuration
  rather than spelled out in both features.
- **`OBS_SET_RECORD_DIRECTORY=true` applies the configured recording root** on
  connect and on starts without an explicit `output_dir`. OBS persists that
  profile setting after the app exits. An explicit `output_dir` is always
  applied; set the option to `false` if starts without a custom directory
  should leave OBS's existing profile directory untouched.

Start and stop wait briefly for OBS to actually flip the record output before
answering — OBS applies it asynchronously, so an immediate read reports the old
value.

## Event Based Capture

Follows the active game's own log and takes an OBS screenshot the moment it
recognises a gameplay event -- a spin, the reels landing, a bet or denomination
change, a win, a gamble offer. One Start/Stop is one run.

```
GET  /api/event-capture/status                                run in progress; always 200
POST /api/event-capture/start                                 begin a run
POST /api/event-capture/stop                                  end it and seal the record
GET  /api/event-capture/runs                                  every run, newest first
GET  /api/event-capture/runs/{run_id}                         one run and its events
GET  /api/event-capture/runs/{run_id}/screenshots/{file_name} one image
```

A run is a folder under the OBS screenshot root, named for when it started:

```
obs-captured-files/event-capture/2026-08-19_14-32-07/
├── run.json                            the record: every captured event, in order
├── 001_spin-started_14-32-11.png
├── 002_reels-stopped_14-32-14.png
└── 003_bet-changed_14-32-30.png
```

Things worth knowing:

- **Following a log is not part of this feature.** Two reusable pieces sit under
  it, and neither knows about OBS or runs. `app/utils/log_tail.py` does the
  reading: `LogTail` is one read from a cursor you hold (which is how an i-deck
  press is confirmed), and `LogFollower` holds the cursor itself and polls for
  new lines, which is what a run does. `app/utils/game_log.py` does the meaning,
  turning those lines into named events -- the sibling of `panel_log.py`, one
  module per foreign log format. What is left in
  `app/services/event_capture.py` is only which recognised events are worth a
  frame, and how a run is recorded.
- **Only visually distinct events are shipped rules.** The game logs plenty of
  internal bookkeeping between one visible change and the next -- the server
  round trip behind a spin, the raw reel-stop data before the animation plays,
  a bet confirmation a few hundred milliseconds after the bet that caused it,
  the engine settling back to idle after a round that already showed everything
  worth seeing. None of that gets a rule. `DEFAULT_RULES` in
  `app/utils/game_log.py` is the list of things a person watching the screen
  would see change: the spin cycle (`spin-started`, `reels-stopped`,
  `free-spin-reels-stopped`, `win-collected`), what the player set
  (`bet-changed`, `denomination-changed`, `paytable-changed`), gamble
  (`gamble-offered`, `gamble-accepted`, `gamble-declined`, `gamble-picked`,
  `gamble-result`, `gamble-ended`), features (`bonus-triggered`,
  `free-spins-entered`, `free-spins-ended`, `progressive-awarded`,
  `feature-scene-shown`, `mystery-symbols-revealed`, `help-opened`,
  `help-closed`) and the machine around the game (`attract-started`,
  `attract-ended`, `attract-looped`, `service-requested`, `service-cleared`,
  `locked-up`, `lockup-cleared`, `credits-changed`, `screen-covered`,
  `game-suspended`, `game-resumed`, `demo-menu-shown`, `demo-menu-hidden`,
  `game-started`).
- **Being visual is not the same as being worth a screenshot.** Every rule
  carries `capture`, and a run is made only of the ones set to true. The rest
  happen constantly (the credit meter after every win, mystery symbols on most
  spins, attract cycling to its next scene) or land on a frame another event
  already captured (`gamble-ended` shows the result `gamble-result` took), so
  they stay recognised for anything else reading the log while the run stays a
  strip of the moments it was opened for. Replayed over the real logs this is
  the difference between 247 recognised events and 131 screenshots on one
  FortuneOx session, and 1,729 against 802 on a long HuffNPuffLink one.
- **`only_on_change` is for lines a game re-logs unchanged.** HuffNPuffLink
  writes `[BetManager.UpdateCurrentBet]` several times a round with the bet it
  already had -- 154 lines for 17 real changes on one session. The debounce
  cannot help, because those lines are seconds apart and genuinely separate;
  what makes them a non-event is that none of the values moved. A rule with
  `only_on_change` fires only when the values it extracted differ from the last
  time it fired.
- **Rules anchor on the publish line, not the message name.** Every published
  message is echoed by a `StateMachine[...] [Non-queued] [<Msg>] not handled by
  state [...]` line per state machine that ignored it. Matching the bare name
  turns one spin into five screenshots. Client logs publish through
  `MessageQueue`, theme logs through `SyncMessagePublisher` and sometimes only as
  the state transition, so the shipped rules match all three shapes and let the
  debounce collapse the duplicates.
- **A game can add or drop rules.** The shipped set covers both games; anything
  game-specific goes in `app/config/game_config/games/<game>.json`:

  ```json
  "events": {
    "rules": [
      { "event": "jackpot-hit", "pattern": "MoneyLinkOutroSM.*stateStarted",
        "summary": "Jackpot sequence started", "delay_ms": 1200 }
    ],
    "disable": ["win-collected"]
  }
  ```

  Named groups in `pattern` become the event's `fields` and can be referenced
  from `summary` as `{name}`. `capture` (default true) and `only_on_change`
  (default false) are available on a declared rule too. A game's rules are tried
  before the shipped ones, so declaring one with an existing name overrides it.
  Patterns are compiled when the config is read, so a bad regex fails then
  rather than mid-run. FortuneOx needs no rules of its own; HuffNPuffLink
  declares one, because its theme log never publishes `BetChangeMsg` and the
  only record of a bet there is the `[BetManager.UpdateCurrentBet]` line
  described above.
- **`delay_ms` exists because the screen lags the log.** `SpinDoneMsg` is written
  when the server result arrives, a beat before the reels visibly settle, so
  `reels-stopped` waits 800ms and the screenshot shows the finished frame.
- **Starting requires OBS.** `/start` connects first and fails with a 409/502 if
  it cannot, because a run that takes no screenshots is a folder of text. A
  screenshot that fails *during* a run is recorded on the event as
  `capture_error` and the run carries on -- a dropped socket re-identifies on the
  next request.
- **The record is written as the run goes**, atomically, after every event. A run
  can last hours; losing all of it to a crash at minute 58 would be worse than
  rewriting a small JSON file. A backend shut down mid-run seals the record as
  `interrupted` rather than `completed`.
- **Only lines written after Start count.** The cursor begins at the log's
  current end, so a run covers this session and not the game's whole history. A
  rotation mid-run is followed into the new file.
- **The image route is the one endpoint that does not return the envelope.** An
  `<img>` src cannot unwrap JSON. Both path segments come off the wire, so both
  go through `app/utils/paths.py` before anything is opened.
- **One run at a time, for the whole process.** Two browsers pointed at the same
  backend share it. A run keeps the game and rules it started with, so switching
  the active game mid-run does not repoint it.

## Regions of interest

A game config names the parts of the screen worth reading, as fractions of the
**game window**:

```json
"roi": { "cash_meter": [0.000000, 0.844468, 1.000000, 0.884554] }
```

Left, top, right, bottom, each `0.0..1.0`. `app/utils/image_roi.py` turns those
into pixels for whatever size the frame turned out to be:

```python
from app.utils.image_roi import crop_file, named_roi

region = named_roi(game.roi, "cash_meter")
meter = crop_file(screenshot, region, destination=out / "cash_meter.png")
```

- **Fractions, so a resolution change costs nothing.** The same four numbers
  crop the same part of the picture at 854x480 and at 3840x2160; each edge is
  rounded to the nearest pixel rather than truncated, so the region scales
  instead of creeping inwards. `tests/test_image_roi.py` checks this against the
  event-capture screenshots themselves, rescaled up and down.
- **Fractions of the game, not of the canvas.** A portrait game inside a
  landscape canvas has black down both sides, and the width of those bars is a
  property of the *game window's shape*, not of the capture — so a region
  measured against the whole frame follows a resolution change but comes unaimed
  the moment the simulator is resized. `Roi.to_box_within(box)` resolves the same
  four numbers against a rectangle inside the frame and hands back pixels of the
  frame, and `app/utils/letterbox.py` is what finds that rectangle. Every
  region-resolving service goes through it, so one config works at every window
  size.
- **Measured in pixels, stored as fractions.** `Roi.from_pixels(box,
  width=..., height=...)` converts a reading taken off one screenshot in an
  image editor, so the conversion happens once rather than at every use.
- **A bad region is rejected where it is read.** A missing name, a wrong count,
  a fraction outside `0..1` or edges the wrong way round all raise `RoiError`
  naming the region — instead of surfacing later as a blank crop.

### The content box

OBS writes every frame at its **canvas** size — 1280x720 here — and fits the
window capture inside it with the source's aspect ratio preserved. A portrait
simulator in a landscape canvas therefore arrives with black bars down both
sides, and the width of those bars is a property of the game window's shape at
that moment, nothing else:

```
1280x720 canvas, 632x1080 window      1280x720 canvas, same window widened
+--------+-----------+--------+       +-----+-----------------+-----+
|        |           |        |       |     |                 |     |
| bars   |   game    |  bars  |       |bars |      game       |bars |
|        | 421 wide  |        |       |     |    643 wide     |     |
+--------+-----------+--------+       +-----+-----------------+-----+
         ^ x=429     ^ x=850                ^ x=318           ^ x=961
```

Same file size, same everything a filename shows, different picture in a
different place. A region measured as fractions of the canvas lands correctly on
one and misses on the other — which is why regions are fractions of the box
inside the bars instead.

`app/utils/letterbox.py` finds it. The frame is thresholded and the bounding box
of what survives is taken, so everything outside the box is dark by
construction. Two cases are refused rather than believed, because both look like
a very letterboxed frame: nothing above the threshold at all (the black frame
OBS writes between scenes), and a box under a quarter of the frame on either
axis (a dark game screen). Both come back as the whole frame — a fade to black
is a normal thing for a screenshot to catch, and one bad crop beats an error on
the frame after it.

```
FRAME_LETTERBOX_TRIM=true       off puts every region back on the whole canvas
FRAME_LETTERBOX_THRESHOLD=8     luminance a pixel must exceed to count as game
FRAME_LETTERBOX_MIN_FRACTION=0.25   smallest box that will be believed, per axis
```

`app/services/roi.py` owns the question for every feature that resolves a
region: `content_box(frame)` finds it, `resolve_box(roi, frame)` is the
single-region shorthand, and OCR finds it once per frame rather than once per
region. Every response reports it beside the region's own pixel box —
`content_box` and `letterboxed` on an ROI extraction, a grid split and an OCR
read alike — because a crop of the wrong thing is either a badly measured region
or a misdetected box, and only the pair tells them apart.

> **Measuring a new region.** Read the pixel box off a screenshot as usual, then
> subtract the content box's origin and divide by its size rather than by the
> frame's. `POST /api/roi/extract` prints the content box of whatever frame it
> just used, so one extraction of any existing region gives you the numbers to
> divide by. A region measured against the canvas by mistake shows up as a crop
> that is right on the frame it was measured on and wrong on every other window
> size.

> **`button_targets` are already in this space.** A click target is fractions of
> the window client area, which `win32.py` asks Windows for directly — and the
> content box is that same rectangle as the canvas received it. So the two
> conventions now agree, and a number measured for one reads correctly for the
> other.

## Extracting a region (ROI)

Cuts one configured region out of a screenshot and hands back the picture. This
is the crop step of OCR, stopping before the engine — worth having on its own,
because *is this region aimed at the right rectangle* and *does the engine read
it correctly* fail independently, and only the first is visible in the crop.
It needs no Tesseract install.

```
GET  /api/roi/regions         the active game's regions, and the frame in hand
POST /api/roi/extract         crop one region and return it as a data URI
```

```bash
# what the dropdown lists, plus the screenshot an extraction would use
curl -s localhost:8001/api/roi/regions
# cut the cash meter out of the newest screenshot
curl -s -X POST localhost:8001/api/roi/extract -H 'content-type: application/json' -d '{
  "region": "cash_meter"
}'
# or out of a particular one
curl -s -X POST localhost:8001/api/roi/extract -H 'content-type: application/json' -d '{
  "region": "cash_meter",
  "file_name": "screenshot-1787192384798.png"
}'
```

**The frame comes off disk, newest first.** The dashboard's Screenshot button
writes into `OBS_SCREENSHOT_SUBDIR` below the screenshot root —
`obs-captured-files/screenshots` by default — and an extraction with no
`file_name` uses the newest image there, by modification time rather than by
name. Nothing here asks OBS for a frame: a region is checked against evidence
still on disk to be looked at again, and a live capture would make the same crop
mean two different pictures on two extractions. `latest_frame` is null until a
screenshot has been taken, which is the panel's empty state rather than an error.

**Almost nothing is written.** The crop comes back as a PNG data URI, like OCR's
own `include_crop`. PNG regardless of the source format, because a crop of a
meter is about to be looked at closely and JPEG would add exactly the artefacts
that make a region look badly aimed. The one exception is `cash_meter`, whose
crop is also kept at `obs-captured-files/cash-meter/<frame>.png` — a running
record of what the meter read over time, rather than only the latest crop the API
returned. Splitting the reels writes everything, for the same reason inverted:
see [Splitting the reels into a grid](#splitting-the-reels-into-a-grid).

**The response carries the pixel box.** `box` is what the fractions resolved to
on that frame, which is the thing to read when a crop looks off by a few pixels —
the fractions are right and the frame is a size nobody measured against, or the
fractions are wrong.

A region declared with unusable numbers is listed in `GET /api/roi/regions` with
its `error` set rather than omitted, so a typo in the config is visible in the
dropdown instead of only on extraction. Extracting it is a 500
`GAME_CONFIG_INVALID`, naming the file — the config is wrong, not the request.
A region the game does not declare is a 404 `ROI_REGION_NOT_FOUND`, and a frame
that is not there is a 404 `ROI_FRAME_NOT_FOUND`; a `.png` that is not one, which
is what a shot caught mid-write looks like, is a 502 `ROI_EXTRACT_FAILED`.

## Splitting the reels into a grid

One step past ROI: crops the reels out of a screenshot and then divides that crop
into the individual symbol positions, as a matrix.

```
GET  /api/grid/layout         the shape of the active game's grid, and the frame in hand
POST /api/grid/split          split one frame into tiles, and write them out
```

```bash
# how many reels, how many rows, and which screenshot would be used
curl -s localhost:8001/api/grid/layout
# split the newest screenshot
curl -s -X POST localhost:8001/api/grid/split -H 'content-type: application/json' -d '{}'
# a particular frame, files only -- no base64 in the response
curl -s -X POST localhost:8001/api/grid/split -H 'content-type: application/json' -d '{
  "file_name": "screenshot-1787192384798.png",
  "include_images": false
}'
```

**One measurement and two counts.** This is the one thing to get right:

```json
"roi": { "reels": [0.35, 0.559722, 0.649219, 0.822222] },
"reel_bounds": { "rows": 3, "columns": 5, "inset": 0.03 }
```

`roi.reels` is fractions of the **frame**, like every other region and click
target here, and it is the only part anyone has to measure. `reel_bounds` is not
fractions of anything — it says how many equal shares the **reels crop** divides
into. The reels fill their own crop by definition, so reel 3 is its third fifth.
Keeping the two apart is what makes them independent: move the reel window on
screen and only `roi.reels` changes; add a sixth reel and only `columns` does.

The gaps between the reel strips need no describing — a boundary falls in the
background between two symbols, and `inset` trims what is left. Earlier configs
listed a `[start, end]` pair per reel in `col_bounds`/`row_bounds`; those keys
are now **rejected with a message rather than ignored**, since a config still
carrying them would split evenly anyway and look entirely correct. The cost of
the change is that a deck with unequal reel widths can no longer be described.

**Positions are 1-indexed and row-major.** `r1c1` is the top symbol of reel 1,
the way a paytable reads. Every tile carries its own `row` and `column` rather
than relying on its place in the list, and `positions` is the same names already
laid out as a matrix, so nothing downstream has to chunk anything:

```
positions: [["r1c1", "r1c2", "r1c3", "r1c4", "r1c5"],
            ["r2c1", "r2c2", "r2c3", "r2c4", "r2c5"],
            ["r3c1", "r3c2", "r3c3", "r3c4", "r3c5"]]
```

**`inset` trims the border the division cannot.** An even split gives a tile
everything up to its neighbour — the gap between the reel strips, and the frame
the game draws *inside* a reel when it highlights a win, which is a line of gold
lying across the symbol's own edge. An inset shrinks every tile towards its
centre:

```json
"inset": 0.03                    // a fraction off all four edges
"inset": [0.04, 0.02]            // [horizontal, vertical]
"inset": [0.04, 0.02, 0.0, 0.0]  // [left, top, right, bottom] — an roi's order
```

Each number is a fraction **of the tile**, not of the crop, so one value means
the same trim for a wide reel and a narrow one and survives a resolution change
like everything else here. It is optional and defaults to nothing.

Finding the right number is a loop, so a request can override the config for one
split — the same shape as OCR's per-request options, and for the same reason:

```bash
# try a trim against a frame that does not move, then write the winner in
curl -s -X POST localhost:8001/api/grid/split -H 'content-type: application/json' -d '{
  "file_name": "screenshot-1787224299454.png",
  "inset": 0.03
}'
```

The effective trim comes back as `inset` on both endpoints, and every tile's
`roi` and `box` already have it applied — a tile's size is never a number
someone has to go and look up. An unusable value is a 500 `GAME_CONFIG_INVALID`
in the config and a 400 `BAD_REQUEST` in a request: the same numbers, but only
one of the two is something the caller can fix by asking differently. A trim
that would leave no tile is rejected rather than clamped, because a one-pixel
sliver of the middle of a symbol is not a smaller mistake than a crash.

**Every tile comes out the same number of pixels.** Rounding each tile's edges
on its own is right for a single region and wrong for a grid: five reels spanning
76.6 pixels each round to 74, 74, 73, 74, 74 purely from where the boundaries
fall, and tiles that differ by a row or a column cannot be stacked, diffed, or
fed to anything that expects one input size. So each tile keeps its **own**
rounded left and top — nothing drifts off the symbol it is aimed at — and they
all take one shared width and height, the smallest that fits every span. The
pixel a wide tile gives up comes off its right or bottom edge, inside the gap
between reels. `tile_width` and `tile_height` on the result say what that size
is, once, instead of leaving fifteen equal numbers to be compared.

**The bounds have to be in reading order**, and `app/utils/reel_grid.py` rejects
them if they are not. A shuffled list still splits into the right number of
tiles and labels every one of them wrong, which nothing downstream could notice.
Overlap between neighbours is allowed — that is a judgement about where a symbol
ends, not a mistake.

**Unlike an ROI extraction, the split is written down.** Fifteen tiles are the
input to whatever looks at symbols next, not something to glance at and discard:

```
obs-captured-files/grid/screenshot-1787192384798/
├── reels.png            the whole crop, so the tiles have something to be checked against
└── tiles/
    ├── r1c1.png  r1c2.png  r1c3.png  r1c4.png  r1c5.png
    ├── r2c1.png  r2c2.png  r2c3.png  r2c4.png  r2c5.png
    └── r3c1.png  r3c2.png  r3c3.png  r3c4.png  r3c5.png
```

One directory per source frame, named after it — so splitting two frames leaves
two records, and splitting the *same* frame twice replaces its own, which is what
makes it safe to re-run after editing the bounds. A stale tile from a
differently-shaped grid is removed rather than left looking like part of the
current answer, and only names matching a tile's own pattern are touched.
`include_images: false` still writes everything; it only drops the base64 from
the response.

**The frame is ROI's frame.** Same directory, same newest-first rule, and the
same `ROI_FRAME_NOT_FOUND` / `ROI_EXTRACT_FAILED` when it is missing or is not
readable — `app/services/grid.py` calls `app/services/roi.py` for it rather than
growing a second reading of "the latest screenshot".

A game that declares no `roi.reels` or no `reel_bounds` is a 404
`GRID_NOT_CONFIGURED` on a split, but a **200 with `error` set** on
`GET /api/grid/layout`: only some games have reels, and selecting one of the
others is a state the dashboard renders rather than a request that went wrong.
Numbers that are unusable are a 500 `GAME_CONFIG_INVALID` naming the file, and a
crop that could not be written is a 502 `GRID_SPLIT_FAILED`.

> **A note on capture geometry.** The reel bounds are fractions of the crop and
> `roi.reels` is fractions of the game, so between them they survive both a
> resolution change and a resize of the simulator — see
> [The content box](#the-content-box) for how the second one is found.
> `reels.png` in the output is what makes a mis-aimed region obvious.
>
> FortuneOx's shipped `roi.reels` was measured against a letterboxed 1280x720
> capture where the portrait window occupied x [429, 850), and then expressed as
> fractions of that window rather than of the canvas — which is why it reads
> `[0.045131, …, 0.954870, …]`, nearly the full width of a game that fills its
> own window. Its five reels were checked against the same frame: an even fifth
> of that crop falls in the background between two strips every time, so only
> the region needed converting — which is the separation the two blocks exist
> for.

## Checking the paylines

One step past the reel grid, and the reason the grid writes its tiles down: it
reads a written split off disk, compares the tiles a payline runs through, and
says which lines pay.

```
GET  /api/paylines/layout     the sets the active game declares, and the split in hand
POST /api/paylines/check      evaluate one set against one split, and draw the result
```

```bash
# which line sets exist, which one is the default, and which split would be read
curl -s localhost:8001/api/paylines/layout
# check the default set against the newest split
curl -s -X POST localhost:8001/api/paylines/check -H 'content-type: application/json' -d '{}'
# a particular split, a particular set, a threshold of your own, numbers only
curl -s -X POST localhost:8001/api/paylines/check -H 'content-type: application/json' -d '{
  "split": "screenshot-1787384342702",
  "set": "5",
  "threshold": 0.93,
  "include_images": false
}'
```

**The patterns are a third block in the game config, keyed by bet
configuration.** A cabinet playable for five lines, twenty or forty ships all
three, so a set is looked up by name and evaluated on its own:

```json
"paylines": {
  "5": {
    "1": [[2,1],[2,2],[2,3],[2,4],[2,5]],
    "4": [[1,1],[2,2],[3,3],[2,4],[1,5]]
  },
  "20": { "1": [[2,1],[2,2],[2,3],[2,4],[2,5]], "...": [] },
  "40": { "1": [[2,1],[2,2],[2,3],[2,4],[2,5]], "...": [] }
}
```

A position is `[row, column]`, both 1-indexed, in exactly the numbering a tile's
name is written in — `[2,1]` is `r2c1`, the middle symbol of the leftmost reel.
Row first, like the matrix and like the filenames; on a 3x5 grid a transposed
coordinate is often still in range, so it is worth being deliberate about.
**Columns must strictly increase along a line**, and `app/utils/paylines.py`
rejects one that does not: a line that backtracks a reel would compare a tile
against itself and score a perfect match, which is a typo that would otherwise
read as a win.

**Two tiles are "the same symbol" by cosine similarity.** The same pot of gold on
two reels is never the same pixels — a slot game glows, pulses and scales its
symbols continuously — so an exact comparison says "different" for every pair and
a naive difference says "different" for a bright frame. Cosine similarity treats
brightness as vector *length* rather than direction, which is exactly the
invariance wanted: a uniformly brighter copy of a symbol scores 1.0.

> **The threshold is much higher than it sounds, and this is the one number worth
> tuning.** Pixel channels are non-negative, so the measure does not start at
> zero for unrelated pictures. Measured on FortuneOx's own captures: two
> *different* symbols sharing one purple reel background score **0.60 to 0.89**,
> and two crops of the *same* symbol score **0.967 and up**. The gap is wide and
> clean, but it sits far above where "0.7 means similar" would put it — at 0.7
> almost every pair is a match and every line pays five.
>
> Nothing has to be guessed. Every result reports the distribution it measured —
> `matched_min` and `rejected_max` are the two numbers a working threshold sits
> between. Tune `PAYLINE_MATCH_THRESHOLD` against a split that does not move
> rather than by intuition.

**A line is read from the left, one adjacent pair at a time, and stops at the
first pair that does not match.** That is how a slot pays: three pots on reels
1-3 pay what a pot pays for three, and a fourth pot on reel 5 behind something
else on reel 4 pays nothing extra. So `pays` is the length of the **leading** run
— 0 when reels 1 and 2 differ, otherwise 2 or more — and never a count of
matches anywhere on the line. The comparison chains tile to tile rather than
every tile back to the first, so `A~B` and `B~C` is taken as `A~B~C`.

**Every adjacent pair is scored anyway, including the ones after the run broke.**
They cost nothing — a pair is compared once and cached across the lines that
share it, which is how forty lines of four steps becomes at most 105 distinct
comparisons — and each step reports two flags rather than one:

| field | question it answers |
| --- | --- |
| `matched` | are these two tiles the same symbol? |
| `counted` | did the left-to-right read get this far? |

A step that is `matched: true, counted: false` is the interesting one: a real
pair of like symbols on a line that pays nothing, because the break was to its
left. Collapsing the two into one flag is what makes a payline checker look
wrong.

**The picture is part of the answer, and there are two of them**, because a pay
count cannot be checked by reading it. A combined overlay draws every *paying*
line over the reels in its own colour, each confirmed tile ringed **green**, and
the unmatched remainder trailing off thin and dimmed, written to disk beside the
tiles it was computed from:

```
obs-captured-files/grid/screenshot-1787384342702/
├── reels.png
├── tiles/ …
└── paylines/
    ├── 5.png          the five-line set, as it was last checked
    └── 40.png
```

One file per (split, set), so re-checking at a different threshold replaces its
own record — which is the loop the threshold needs. The combined picture never
shows *where* a paying line stopped short of the full width, on purpose: several
lines share it, and "here is where this one broke" is a question about one line,
not about all of them together.

**Every line also gets its own picture, whether it pays or not**, back as
`image_data` on that line's own entry rather than written to disk. A line that
pays nothing is exactly the one a combined overlay leaves out, and it is
usually the more interesting question: not "did it pay" but "how far did it get
before it didn't". A line's own picture is the one place the break shows: the
tile that ended its run is ringed **red**, distinct from every line's own colour
so it reads the same on every line, and `pays`'s counterpart `break_position` is
the tile name that ring sits on, or `null` when the whole line paid and nothing
broke. Neither ring colour is a member of the line palette, precisely so a ring
is never mistaken for a second line.

The palette lives in `app/utils/payline_overlay.py` and each line's colour comes
back on the result, so a swatch in the dashboard and a stroke in either picture
cannot drift apart. It avoids purple and deep red on purpose: these games are
drawn in purple, gold and red, and a violet payline over a violet reel is an
invisible payline. A forty-line set drawn all at once on the combined overlay is
busy whatever the palette does — the labels are what disambiguate it, and the
five-line set is the one to look at first; the individual pictures have no such
limit, since each is one line alone. A label reads just ``Line 4`` — the pay
count is already numeric data on the response, and printing it on the picture
too would be the same number twice.

**Nothing here touches a screenshot.** The input is a directory the grid already
wrote, so the same split checked twice at the same threshold gives the same
answer and re-checking after moving the threshold does not depend on the
simulator still showing the same spin. `app/services/grid.py` owns reading a
split back (`latest_split`, `resolve_split`, `read_split`) for the same reason it
delegates "the latest screenshot" to ROI: it named the directory, the crop and
the tiles, so a second module spelling those the same way is a second module to
change when one of them moves.

The failures say which of the three inputs is missing:

| status | code | means |
| --- | --- | --- |
| 404 | `PAYLINES_NOT_CONFIGURED` | no `paylines` block, or no set by that name |
| 404 | `GRID_NOT_CONFIGURED` | no `reel_bounds` to place the coordinates on |
| 404 | `PAYLINE_SOURCE_NOT_FOUND` | nothing split yet, or the split is missing tiles |
| 409 | `PAYLINE_SOURCE_STALE` | the split's shape is not the grid the game now declares |
| 502 | `PAYLINE_CHECK_FAILED` | a line runs through a tile the split lacks, or the picture could not be written |
| 500 | `GAME_CONFIG_INVALID` | the block is declared with unusable numbers |

`GET /api/paylines/layout` reports the first three as `error` on a **200**
instead: only some games declare paylines, and a checkout that has split nothing
is a state the dashboard renders rather than a request that went wrong. A stale
split is a 409 rather than a 500 because the config may well be right and the
split merely old — split the frame again.

## Naming the symbols (image classifier)

The payline check answers *do these two tiles match each other*. It never learns
what either one **is** -- and `utils/reel_stops.py`, which does name symbols, reads
the answer out of the game's own log, so it agrees with the game by construction.
This is the third reading: a network over the tiles themselves, and the only one
that can disagree. Two are available -- EfficientNet-B0 and ResNet34 -- and both
can be trained and kept at once.

It is also no longer only a page of its own. [Analyze Spin](#analyze-spin) reads
a spin's reels through this, in place of both of the other two: the codes it
returns are what each payline's run is counted from *and* what the award is priced
by. Whatever moves here -- the artwork, the transforms, the confidence floor --
moves a spin's verdict, so read
[the confidence floor is now the one tunable](#the-confidence-floor-is-now-the-one-tunable-and-it-costs-something)
before changing any of them.

```bash
# what there is to train on, and what is wrong with it
curl localhost:8001/api/image-classifier/dataset
# which splits could be classified, and which one is newest
curl localhost:8001/api/image-classifier/splits
# fit a model; returns as soon as the run is under way, ~4 minutes on CPU
curl -X POST localhost:8001/api/image-classifier/train -d '{}'
# follow it -- stages, epochs, loss, accuracy
curl localhost:8001/api/image-classifier/status
# stop it; whatever model was already saved is untouched
curl -X POST localhost:8001/api/image-classifier/train/cancel
# name the tiles of one split, with the other engine and a floor of your own
curl -X POST localhost:8001/api/image-classifier/classify   -d '{"architecture": "resnet34", "min_confidence": 0.8}'
```

Four files, one per concern: `utils/symbol_dataset.py` turns artwork into training
pictures (Pillow and numpy only -- **no torch**, so the part most likely to be
wrong is testable on a machine with no ML stack), `utils/symbol_model.py` is the
only module that imports torch, `utils/symbol_overlay.py` draws the answer on the
reels, and `services/image_classifier.py` is the order they go in plus the one
training run the process may have.

### torch is optional, exactly like Tesseract

`requirements.txt` installs torch and torchvision, but every import of them is
*inside* a function. So a venv without them starts the service normally and
`GET /api/image-classifier/status` reports `not_installed` with the pip command
that fixes it; only training and classifying raise. Confirmed rather than assumed:
importing `app.main` and building the app leaves `torch` absent from
`sys.modules`.

The state machine mirrors `OcrEngineState`: `disabled`, `not_installed`,
`untrained`, `training`, `stale`, `error`, `ready`. None of them is an HTTP error --
a page cannot explain a missing engine if the request that would tell it about one
fails.

### The artwork is not what the classifier sees

This is the thing to understand before changing anything here, and it is why an
earlier cosine-similarity attempt at the same problem stalled. That attempt scored
each grid tile against every art file and reached only 0.35-0.44 on the four
picture symbols while matching the card symbols not at all.

The reason is measurable. Of the classes shipped so far **exactly one** (`AA`, the
Ox) carries the game's field and frame drawn on it -- it is a framed portrait,
99.4% opaque inside its own alpha box. Every other one, including `BB`/`CC`/`DD`
which look like premium symbols, is a transparent cut-out at 0.45-0.71 opaque, and
the game composites them over the reel background at runtime. Compare a cut-out on
transparency against a tile whose symbol sits on dark purple and the background is
most of the difference.

Which is why the routing below keys on a *measured property of each file* rather
than a list of class names: the artwork grows, and a rule written against nine
specific codes would quietly stop applying to the tenth.

So `symbol_dataset.compose()` puts it back, routing on a **measured property of
the file** rather than a hardcoded class list -- a game whose whole set ships
pre-composited needs no special case, and one that mixes the two kinds gets each
of them right:

- opaque box >= `DENSE_OPACITY` (0.90): already a finished cell, used as-is.
- anything else: alpha-composited onto a synthesised plate at 0.72-0.95 of the
  plate's shorter side, centred with a nudge.

Every constant in the plate was measured off the 600 tiles the grid had already
written, not chosen:

| | |
|---|---|
| `FIELD_RGB` | `rgb(35, 0, 56)` -- the modal tile corner; two thirds sit within a couple of levels of it |
| `GOLD_RGB` / `GOLD_PROBABILITY` | `rgb(201, 121, 37)`, drawn 40% of the time -- 238 of 600 tiles keep a sliver of the reel divider the `inset` crop did not trim |
| `BLACK_PROBABILITY` | 0.02 -- frames caught mid-fade |

One composed sample per class is written to `CLASSIFIER_MODEL_DIR/samples/` on
every run. **That directory is the first thing to look at if accuracy
disappoints**: whether the background went back correctly is answerable by
looking, and reasoning about it instead is how this goes wrong.

### Two transforms, and they are a pair

Training crops (`RandomResizedCrop`, scale 0.65-1.0, ratio 0.85-1.6) cover the
measured 1.03-1.45 aspect of real tiles and the cell boundary that clips a tall
symbol. Evaluation resizes past the input size and centre-crops back
(`_EVAL_CROP = 0.90`), which is torchvision's own ImageNet recipe and is here for a
measured reason rather than for symmetry.

Without it, evaluation showed the network the *whole* picture while training had
only ever shown it zoomed crops -- so at inference every symbol appeared smaller
than anything in training. The sparsest symbol (`DD`, opaque over only ~0.22 of its
own box) scored **0.42 on pictures it had been trained on, and 1.00 on the same
pictures with a 10% centre zoom**; `CC` went 0.92 to 1.00. Holdout accuracy over
the whole set went 0.781 to 1.000.

`TRANSFORM_VERSION` travels on the checkpoint for exactly this reason. Change
either transform, bump it, and an older model reports `stale` instead of quietly
predicting differently than it was measured.

Two more that are not the stock recipe:

- **No horizontal flip.** It is the reflex first augmentation and it is wrong
  here: five of the nine symbols are the characters A, K, Q, J and 10, and a
  mirrored J is a picture the game never draws.
- **Resolution is an augmentation.** Every training picture is knocked down to a
  random 52-140px and back up. The artwork is 380-600px and real tiles are
  61-128px, so without this the network trains on detail that is simply absent at
  inference.

### Two engines, and why both are kept

`CLASSIFIER_ARCHITECTURE` picks between **ResNet34** (21.8M parameters, the
default) and **EfficientNet-B0** (5.3M). Both take the same 224px
ImageNet-normalised input through
the same transforms, so the sample synthesis, augmentation, schedule, evaluation
and checkpoint format are all shared -- only the backbone differs, which is why
adding a third is one entry in `_ARCHITECTURES` rather than a second code path.

They are not a mode. **Each keeps its own checkpoint** —
`model-<architecture>.pt` and `metrics-<architecture>.json` — so training one
leaves the other untouched and both can answer. `POST /train` and
`POST /classify` each take an optional `architecture`, `GET /status` lists every
engine with whether it is trained and what it scored, and the dashboard has a
picker on both cards. The point is comparison: two independently-fitted networks
agreeing about a tile is worth more than one of them being confident, and when
they disagree that is a signal about the tile rather than about either model.

On CPU the larger network is not simply the slower one -- ResNet is plain
convolutions while EfficientNet's depthwise separable ones are poorly served by
CPU kernels -- but measured here ResNet34 does cost roughly twice as long
(~8 minutes against ~4.3 for the same schedule). Both reach 1.00 on held-back
frames, so pick on behaviour against real splits rather than on either number --
and that is what settled the default: ResNet34 names all fifteen tiles of the
reference split correctly, so it is what `CLASSIFIER_ARCHITECTURE` ships as and
what grades a spin unless the request says otherwise. Re-measure before trusting
that; it is a statement about one split and one set of artwork.

### The confidence floor

`CLASSIFIER_MIN_CONFIDENCE` is **0.90**, and it is deliberately far stricter than
where the classes actually separate. Measured over 210 real tiles, the widest
empty band in the distribution sits around 0.56-0.68, so a floor anywhere in
there would name every symbol on the reference split correctly. At 0.90 a
correct reading is rejected whenever the model is merely fairly sure: on that
split ResNet34 reads all fifteen tiles right, and five of them -- the two Arm
Bands at 88.5% and 89.3%, and the three Orbs at 43-58% -- still come back blank.

That is the intended trade, and nothing is hidden by it. A tile below the floor
keeps its ranked candidates, and the dashboard shows the leading one and its
percentage in the per-tile table, greyed with the figure in red. So the two grids
mean *the model was sure* and the table means *this is what it thought* -- two
different questions, answered separately rather than blended into one number.

Numbers like these move whenever the artwork or the transforms change, so treat
any figure quoted here as an example rather than a constant, and re-measure
against written splits. `GET /api/image-classifier/dataset` reports what is
actually on disk now, and `missing_symbols` reports the codes the active game
declares that still have no artwork -- which is the list that decides how often
the floor has to do this work at all.

### Accuracy is two numbers, and averaging them would answer nothing

A class here is an animation **loop** of 48 near-identical frames, not 48
independent samples: measured against `AA_00000`, frame 1 differs by 0.45/255 and
frame 47 by 0.02 -- the loop closes. So no split of this dataset is honestly
unseen, and the response says so rather than implying otherwise.

- `frame_holdout_accuracy` -- over frames held out of the **middle** of each loop.
  Not the end: because the loop closes, the last frames of `AA` and `DD` sit
  0.02/255 from a retained frame, which is the same picture. A middle block is
  measurably better and is what `symbol_dataset.frame_split()` returns.
- `frame_holdout_leakage` -- each held-back frame's distance to the nearest
  retained one, over the mean distance between any two frames of that class. 0
  means duplicates and the accuracy above means nothing; 1 is the most this dataset
  can offer. Currently 0.57.
- `augmented_accuracy` -- all nine classes, but it re-augments the training
  pictures. Leaky by construction; it measures robustness to the augmentation.

The five single-picture classes contribute to the holdout figure **not at all**,
and `single_image_classes` names them. Overstating this would be the one genuinely
misleading thing this feature could do.

### A run, and stopping one

One training run process-wide, on the `analyze_spin` pattern: module-global record,
lazy lock, and all five stages (`prepare`, `head`, `finetune`, `evaluate`, `save`)
created `pending` **before** the run starts, so a failure at stage two leaves
stages three onward visibly unreached rather than absent. Cancellation is a flag
the training thread checks between batches -- never `task.cancel()` -- so a
cancelled run unwinds through its own code, writes no checkpoint, and leaves the
previous model exactly as it was.

There is **no WebSocket**. The spin progress stream is the only one, and epoch
ticks are twenty seconds apart; the page polls `/status` every two seconds while a
run is live and not at all when it is idle.

`CLASSIFIER_TORCH_THREADS` defaults to half the machine's cores and is the setting
that keeps the rest of the cabinet usable: torch takes every core by default, and
this process also holds the OBS socket, presses the i-deck and clicks the game
window.

### What it deliberately does not depend on

Unlike the payline check, which raises `PAYLINE_SOURCE_STALE` when a split's shape
disagrees with `reel_bounds` -- it must, because only the config says where a line
runs -- the classifier names each tile independently and **does not validate the
split against the config at all**. Rows and columns are only how the answers get
arranged. So an older split still classifies, and the game config is read for
exactly one thing: the `symbols` block, for display names. A game declaring none
falls back to bare codes.

Which means a classification works on any split, on any machine, without the game
installed -- the same property the hand-copied `paylines` block exists to preserve.

### Where the output goes

`CLASSIFIER_MODEL_DIR/` holds `model-<architecture>.pt` (weights plus classes,
input size, the architecture itself, normalisation, `transform_version` and a
dataset fingerprint), a matching `metrics-<architecture>.json`, and one shared
`samples/`. The architecture is on the checkpoint because without it `load` would
build the default backbone and the state dict simply would not fit -- an obscure
shape error instead of "this is a ResNet checkpoint". The checkpoint is written beside and moved over, so a run interrupted
mid-write cannot leave a truncated file where a working one was, and it is cached
on the file's mtime and size the way `services/paytable.py` caches `math.xml`.

An annotated picture goes into the split's own directory as
`grid/<split>/classifier/symbols.png` -- a green ring around a named cell, amber
around one below the floor -- one file per split, replacing its own record, the
convention the payline check follows with `paylines/<set>.png`.

Deliberately no text on it. The codes and confidences are on the response and
rendered as a table beside the picture, so drawing them here as well would
duplicate data at the one size where it is least readable: over busy artwork, at a
tile's own scale. What the picture is uniquely good for is *where* a verdict
landed, which is what makes a transposed matrix obvious at a glance.

For the same reason `include_images` (the fifteen per-tile data URIs) and
`include_overlay` are separate knobs, and the dashboard asks for the second
only -- the tile pictures were most of the payload and nothing renders them.

`GET /status` also caches its dataset summary on the dataset fingerprint. Not a
micro-optimisation: building it opens all 197 images to read size and opacity,
which measures at about twelve seconds, and the page polls this every two seconds
while training.

## Reading the meter (values)

Extracting `roi.cash_meter` also reads the numbers on it. There is no
`/api/meter`: cropping the meter and reading it are one action from the panel's
point of view, so `POST /api/roi/extract` returns a `meter` object beside the
crop, and every other region leaves it null.

```jsonc
"meter": {
  "mode": "cash",              // or "credits" -- the same cell, different quantity
  "currency": "$",             // "?" when a symbol is drawn that Tesseract won't name
  "cash": 995.60,              // null in credits mode
  "credits": null,             // null in cash mode
  "win": 0.75,                 // null is normal: the WIN cell is empty between spins
  "bet": 0.88,
  "fields": { "cash": { "value": 995.6, "text": "$995.60", "confidence": 96.0 } },
  "unmapped": [],              // non-empty means the layout is not what was expected
  "band": [3, 20], "engine_calls": 17, "duration_ms": 3989, "error": null
}
```

`app/utils/meter.py` does the image work and knows nothing about game configs;
`app/services/meter.py` supplies the engine and the per-skin band and field
windows. Reading never
fails the crop -- a missing Tesseract or an unreadable strip populates
`meter.error` and the picture still comes back, because checking that a region is
aimed correctly must not require anything to be legible inside it.

### The values are found, not looked up

Fixed pixel boxes do not work. A hand-tuned box table reads 15 of the 20 saved
crops and mangles the rest -- truncating `$996.10` to `$996.1`, welding a cell
border onto `,$999.12`. The strip is also not one size: the 20 saved frames catch
the game window at 412, 421, 459 and 501 px wide, so a table would need one entry
per window size a developer happens to drag to.

So the digits are located per image: they are the brightest thing in the strip, so
a lit column is one whose brightest pixel is near the strip's own maximum, and
neighbouring lit columns group into one number. Relative to each image, so a
change of skin, palette or background costs nothing.

### Which number is which comes from position

**The labels cannot be read.** `CASH`, `WIN` and `BET` are drawn 8px tall and
letter-spaced over artwork; tight crops at 10x return `'LA'`, `'C'`, `'CREOS'` at
confidence 0 to 45, against 79 to 97 for the values. This is not a tuning problem,
so it is not attempted. Each field owns a span of the strip's width instead:

```json
"meter": { "windows": { "cash": [0.27, 0.37], "win": [0.46, 0.56], "bet": [0.64, 0.74] } }
```

Fractions of the strip, and the strip is a crop of the **content box** -- so a
cell centre no longer moves when the simulator is resized, which is what makes a
measured window worth keeping. Across the four window widths in the saved frames
FortuneOx's centres hold at cash 0.315-0.343, win 0.500-0.529, bet 0.685-0.702.

`DEFAULT_WINDOWS` in `app/utils/meter.py` is the union of the two measured skins
and is deliberately loose -- a fallback for a game nobody has measured. **Both
shipped games declare their own `windows`**, because the union is too wide for
either: HuffNPuffLink has a fourth cell at 0.644, inside the default `bet` span
and nowhere near its real bet cell. Measure a new skin rather than inheriting.

Taking the cells left to right instead would need no windows, but **11 of the 20
saved crops have an empty WIN cell**, so counting would report the bet as the win.
Detecting the cells rather than the values does not rescue it either: bright digits
split a full cell into fragments while an empty cell stays whole.

**A number that lands in no window is reported in `unmapped`, never nudged into
the nearest field.** A strip from a differently-ordered skin reads perfectly and
means something else, and a bet quietly filed as a balance is worse than one that
arrives asking to be looked at.

### The row band is per skin, so declare it

Which rows hold the values differs by skin -- FortuneOx prints its labels *below*
the cells, HuffNPuffLink *inside* them. Read the full height of the first and its
values collapse into `4000.07` and `30.88`.

```json
"meter": { "band": [0.213333, 0.600000] }
```

`[top, bottom]` as fractions of the **strip's** height, so the same pair holds at
any capture resolution — but only the *fraction* does, and everything measured in
pixels around it has to keep up. FortuneOx's band used to be
`[0.103448, 0.689655]`, which overshot into the label rows by about 3 of a 29-row
strip and did no harm at all until the OBS canvas was set to the game's own
1080x1920. The same 3 rows became 8 legible ones on a 75-row strip, `BET` read
`10` as `410`, and the cell separations moved out from under
`GAP_SHARE`. **Re-measure a band when the canvas changes**, and see
`tests/test_meter.py::REAL_STRIPS`, which now pins both canvases for that reason.

### What survives a canvas or window change, and what does not

The game's meter layout is **proportionally identical at every capture size** --
measured, not assumed: the value rows sit at 0.276-0.533 of the strip's height on
a 421-wide capture and 0.280-0.533 on a 1080-wide one. So the region, the band and
the windows are all genuinely scale-free, and a canvas change alone never
invalidates them.

What a canvas change does invalidate is the constants still expressed in pixels.
Those are now shares wherever a share works (`GLYPH_MARGIN_SHARE` scales the glyph
clearance with the band; four fixed rows were ample at 29 rows and thin at 43, and
at a 1.5x canvas the cell border welded a leading `1` onto `$1,250.00` and dragged
a correct reading below `FLOOR`). `MIN_GAP` is the one that resisted: the gap
inside a value and the gap between two cells measure 9 and 17 on a 1080x75 strip
and converge as the strip shrinks, so no share separates them everywhere.

Measured range, swept in `test_the_meter_reads_across_canvas_sizes`: **0.35x to 2x**
of the 1080x1920 canvas reads every field correctly. Below ~0.35x the CASH and WIN
cells weld and both come back null. Nothing is expected to break above 2x.

**Resizing the game *window* is the case this cannot promise**, and the distinction
matters. Rescaling a capture only changes resolution; resizing the window lets the
game re-lay-out its own UI, and a region measured against the old layout would then
be aimed at the wrong rows. Two things to check after a window resize: that
`content_box` still reports an aspect close to the game's own (a 1280x720 capture
in this repo's own history has one at 0.6375 against the game's 0.584 -- letterbox
trimming left bars in, which shifts every fraction), and that `roi.cash_meter` still
brackets the meter row. `POST /api/roi/extract` returns the crop, which answers both
by eye in one call.

Without one, `fit_band` works it out by reading: the
row-ink profile offers two or three candidates and the one that reads with the
highest mean confidence wins, cached per game and size. That is what lets a new
game work with no setup, but it can only judge from the frame in front of it --
measured over the saved crops, fitting from each strip independently reads 15 of
20 correctly where a declared band reads 19. **Measure it once per game.**

### One reading is usually enough, and the confident one wins

`psm 7` reads `49531` as `49331`; `psm 8` gets that right but turns `75` into
`75.`; and HuffNPuffLink's orange WIN value is read by **`psm 13` alone** -- every
other mode returns nothing at all for it. So a field is read at `psm 8` first and
escalated only while it is unconvincing, and the reading the engine was *surest*
of is taken rather than the most popular one: where those two rules disagreed,
confident was right and popular was wrong every time (`0.88` over `0.83`, `75`
over `5`, `99371` over `99374`).

Escalating keeps a strip near six engine calls instead of twenty-seven, and the
reads run concurrently, which matters because each is a subprocess at roughly
200ms. Expect **3-6s** for a strip.

**Except that the engine does not always give a confidence.** Under
`tessedit_char_whitelist` — which this module always sets, because without it a
cell border welds onto the value and `$2` arrives as `s2` — Tesseract 5's LSTM
reports confidence **exactly 0** for a word it read perfectly. It hits the
correct readings hardest: `$2,959.44` comes back verbatim from six of the seven
rungs and every one is scored 0. So 0 means *unmeasured*, never *wrong*:
`MeterField.rank` puts an unscored reading below any scored one but above nothing
at all, `FLOOR` only judges a reading that has a score, and `unmapped` still
requires one — knowing which cell a value came out of is what makes an unscored
transcription worth trusting, and a stray has no such backing. Ranking on
confidence alone starts at 0, and `0 > 0` is false, which discarded the only
transcription there was and returned the field empty.

### Every value gets a margin, because one band serves three cells

The three cells are not the same height — FortuneOx's WIN digits stand 3 rows
taller than CASH's and BET's — so a band measured to clear the shorter cells'
borders leaves the tall one's glyphs touching both edges of their crop. Tesseract
reads a stroke flush against the edge as one with an extra stroke: `105` came back
as `4105` at confidence 83, and `$1,250.00` as `$4.7250.00`, both from crops that
are perfectly legible by eye.

So each value's crop is grown back over the strip while the rows it gains are not
the cell's own border (a row lit right across is a rule; a row of glyphs lights
only its strokes), and a synthesised border makes up whatever margin is left. Both
steps are conditional on measurement — a band *fitted* from the frame already pads
its cores, and padding a crop that has room costs a digit rather than saving one.

### Accuracy

Measured by running the real extractor against every saved crop and fitting a
band per *file* -- deliberately harsher than the dashboard, where one band is
reused per skin. Against the 20 saved crops the declared bands give **59 of 60
values correct, with no wrong ones**; the single failure is a missing `bet` on the
yen strip, which reports as `null` rather than as a number.

Two limits worth knowing. Both skins are **bright digits on a dark strip**, so a
dark-on-light meter would invert the brightness assumption -- untested rather than
known-broken, and `invert` is already an `OcrOptions` knob. And **cash-vs-credits
is inferred**, not read: a currency symbol or a fractional amount is money, a bare
whole number is credits. Correct on all 20, but a judgement about meaning. The
game log carries the real bet and win events, and is the way to make it certain.

## Reading text (OCR)

Turns a region into the number on it. Tesseract does the recognition; everything
here is about giving it something it can read and saying how sure it was.

```
GET  /api/ocr/status          engine, version, languages, and the default options
GET  /api/ocr/regions         the active game's readable regions and their options
POST /api/ocr/read            read regions off a live OBS frame, or off a capture
```

```bash
curl -s localhost:8001/api/ocr/status
# read every region the active game declares, off the screen right now
curl -s -X POST localhost:8001/api/ocr/read -H 'content-type: application/json' -d '{}'
# read one region off a frame a capture run already took, and see what the engine saw
curl -s -X POST localhost:8001/api/ocr/read -H 'content-type: application/json' -d '{
  "run_id": "2026-08-19_04-01-02",
  "file_name": "041_spin-result-received_04-01-24.png",
  "regions": ["cash_meter"],
  "options": {"psm": 11},
  "include_crop": true
}'
```

### Installing the engine

Tesseract is an external program, not a pip package — `requirements.txt` does not
and cannot carry it. Install it once per machine:

```powershell
winget install --id UB-Mannheim.TesseractOCR
# or the installer from https://github.com/UB-Mannheim/tesseract/wiki
```

**The installer does not put it on `PATH`**, so the backend looks where the
installers actually put it: `%LOCALAPPDATA%\Tesseract-OCR`,
`%LOCALAPPDATA%\Programs\Tesseract-OCR`, `%PROGRAMFILES%\Tesseract-OCR` and the
x86 form, after checking `PATH` first. Set `OCR_TESSERACT_CMD` only for an install
somewhere else; it accepts the folder as well as the `.exe`, because the folder is
the form the installer shows. `GET /api/ocr/status` reports which one it found, its
version and its languages — or, when there is none, every path it looked in.

Nothing fails without an engine. OCR is optional in the same way OBS is: the
service starts, `/health/ready` stays green, `status` answers `not_installed`, and
only a read is refused (409 `OCR_ENGINE_UNAVAILABLE`).

### Options come from three places

Later wins, and every reading reports what it ended up with:

1. **The environment** (`OCR_*` in `.env`) — the defaults for every region.
2. **The game config's `ocr` block** — per region, because the right
   page-segmentation mode is a property of the region, not of the machine.
3. **The request's `options`** — for one read, which is how a region gets tuned.

```json
"roi": { "cash_meter": [0.000000, 0.844468, 1.000000, 0.884554] },
"ocr": { "cash_meter": { "psm": 11, "char_whitelist": "0123456789.,$" } }
```

The keys are `language`, `psm`, `oem`, `char_whitelist`, `upscale`, `grayscale`,
`autocontrast`, `invert`, `threshold` and `dpi`. They are validated when the config
is read, so a misspelled option or a `psm` Tesseract would refuse is an error at
load time naming the file and the region — not a silent misread months later.

### The defaults are aimed at a meter, not a document

A region cut out of a game frame is one short line of large glyphs, a few hundred
pixels wide, drawn over artwork and usually light on dark. So `OCR_PSM` defaults to
7 (*a single text line*) rather than Tesseract's own 3 (*a whole page*), the crop is
greyscaled, contrast-stretched and enlarged 3x before it is handed over, and the
engine is told the image is 300 DPI instead of being left to guess from a small
picture. Measured on this project's own captures: at native size the cash meter
reads as nothing at all, and at 3x it reads `$842.94 $1.20 176`.

Two things worth knowing when a reading is wrong:

- **`char_whitelist` is a hint, not a rule.** Under the LSTM engine (`oem` 3)
  Tesseract may still return a character outside it, and on this project's meters a
  digits-only whitelist sometimes joins two values into one. Try it, keep it if it
  helps that region.
- **`include_crop` returns what the engine actually saw**, preprocessing and all,
  as a data URI. That picture answers "is this a bad region or a bad option" in one
  look, which reading the text again never does.

### Tuning a region

Read the same frame from a capture run over and over, changing one option per
request, then write the winner into the game config:

```
POST /api/ocr/read  {"run_id": ..., "file_name": ..., "options": {"psm": 6}}
POST /api/ocr/read  {"run_id": ..., "file_name": ..., "options": {"psm": 11}}
```

A live read cannot be repeated — the screen has moved on — which is exactly why a
run's screenshots are the frames to tune against.

### What a reading says

`text` is what was recognised; `value` is the first number in it and `values` is
all of them, because a meter panel often holds credit, bet and win in one region.
`confidence` is the engine's own mean, 0–100, and is the difference between *the
meter says 842.94* and *the engine produced 842.94 out of a crop of noise*. `words`
carries each word's box in crop pixels, for drawing an overlay.

One region failing does not fail the read: ask for four and the broken one comes
back with `error` set beside the three that worked. A region name the game does not
declare is different — that is a 404, because a typo and an unreadable meter should
not arrive looking the same.

## The loaded paytable (game config)

```
GET /api/paytable/                       the maths the running game has loaded
GET /api/paytable/?paytable_id=<id>      another paytable of the same game
```

One request answers the whole Game Config tab: which paytable is loaded, the
symbols and what each one does, every reel strip, the combos that pay, and the
payline set in play.

### The join is the point

Three things have to agree, and each is owned by someone else:

- **the game's log** names the paytable it loaded — `[WagerGameApp.UpdatePayTable]
  ... current paytableId[FortuneOx-1101YX-1c-90]`, written on every denomination
  change;
- **the game's install** has one directory per paytable under its `GameConfig`
  directory, named *byte-identically* to that id, holding `math.xml` and
  `gameConfig.cfg`, with a single `winGeometry.xml` beside them shared by all of
  them;
- **the game config in this repo** points at that directory (`game_config`) and
  is the only place a two-letter symbol code gets a readable name (`symbols`).

`app/services/paytable.py` is the only module that knows all three, and it
**reports how the join was made** rather than presenting the result as fact.
`source.origin` is `log` (the game said so), `requested` (a caller named an id)
or `only` (the game ships exactly one paytable and the log has not spoken), and
`source.log_line` / `logged_at` carry the line itself. That line also names the
denomination in play and every denomination the cabinet accepts
(`denomination` / `supported_denominations`) — each one loads a *different*
paytable folder, so together they are the set of maths a session can move
between without restarting the game. A page showing the wrong
maths is a stale log or a hand-typed id, and only saying which lets a reader
tell them apart.

The log is read **backwards** (`app/utils/log_search.py`), not forwards.
`log_tail.py` answers *what happened after this cursor*, which is the shape for
confirming a button press; *what did this six-megabyte file last say* is the
opposite question, so `last_match` walks the file a window at a time from the
end and stops at the first hit. `PAYTABLE_LOG_SCAN_BYTES` bounds how far back it
goes (0 scans the whole file).

### Failures name the half that is wrong

- **409 `PAYTABLE_UNAVAILABLE`** — the game config declares no `game_config`
  directory, or it is not on this machine. Nothing to retry: every other
  dashboard slice works without the game installed, and this one cannot.
- **404 `PAYTABLE_NOT_FOUND`** — the log named a paytable that the install has
  no folder for, or the requested id is not one of `available`. The message
  names both halves, because "not found" alone would leave you unable to tell
  whether the log or the install is behind.
- **502 `PAYTABLE_INVALID`** — the files are there and unreadable as maths.

An unreadable `winGeometry.xml` is deliberately **not** any of these: it comes
back as `win_geometry.error` on a 200, because the symbols, strips and combos
are all still true and a page that 404s over one of four tables is worse than
one that says so.

### What comes out of math.xml

`app/utils/game_math.py` reads it by *local* element name — the file is
namespaced (`http://scientificgames.com/slotMathXMLSchema.xsd`) and BOM-prefixed,
and pinning the namespace would make a schema-version bump look like a corrupt
file. Only what a reader of the maths needs: `BonusInfo`'s weight tables and
`MysteryReplacementInfo` are left on disk.

- **Names are configured; everything else about a symbol is read.** There is no
  display text in `math.xml` anywhere — not in `SymbolSetList`, not in
  `ReelStripList`, nowhere — so `name` comes from the game config's `symbols`
  block, keyed by code (`{"WC": "WILD", "AA": "Ox"}`). A code with no entry falls
  back to `Wild` for a member of `WildSymbolList` or `Scatter` for one a
  `CountScatterCombo` counts, and is `null` otherwise, so a game nobody has named
  yet still reads. `role` carries the same distinction as a lowercase enum and is
  always derived; `top_pay` is the best line pay for a combo of that code alone,
  and rows come back best-paying first with unpaid feature symbols last.
- **Each code is counted twice, off `ReelStripList`.** `reel_stops` is stops on
  the base game's own reels; `total_stops` and `strips` are across every strip in
  the file. 0 beside a non-zero total is what tells a feature-only symbol from
  one the game does not have at all. A code the symbol set merely declares comes
  back with `on_reels: false` and zeros rather than being dropped — FortuneOx
  declares eighteen and its strips carry seventeen, and the eighteenth is a named
  feature symbol with an award.
- **A strip is its stop order**, so it travels as an ordered list, not a tally.
  `weights` runs parallel to it; a uniform column is noise and an uneven one is
  the whole reason the column is worth reading. Strips come back with the
  default set's reels first, in reel order.
- **`ANY` is not a symbol.** It is a combo's trailing wildcard, so
  `[X, X, X, ANY, ANY]` is three of a kind on reels 1–3 and says nothing about
  reels 4 and 5 — the same *leading run* rule `services/paylines.py` reads a
  split by. `match_length` is that run's length.
- **The line pays come back twice: as declared, and pivoted.** Because a combo
  is one symbol repeated with an `ANY` tail, every pay is really a
  (symbol, run length, value) triple — so `pay_lengths` + `pay_table` is that
  grid, a row per symbol and a column per run, which is how every paytable a
  player has seen is laid out. Symbols with an identical row are merged (a game
  gives its card ranks one profile, and five identical rows say less than one
  row naming five symbols), and the merge is on the values alone, so two symbols
  share a row only when they pay the same at *every* length. `payline_combos`
  stays as the faithful reading, and a combo mixing two symbols lives only there
  — it has no per-symbol row to sit in.
- **Line combos and scatter combos are never one list.** They answer different
  questions — a run along a line, versus a count anywhere on screen.

### Which payline set is in play

`winGeometry.xml` declares every set the *game* has (40, 20 and 5 for FortuneOx)
and sits at the `GameConfig` root, shared by all 53 paytable folders. Which one
this paytable plays comes from **`gameConfig.cfg`'s `NumberOfLines`**, not from
`math.xml`'s `DefaultConfiguration/PaylineSetID` — the same `math.xml` ships in
folders that play 5, 20 and 40 lines, so its own default cannot be the answer.
`win_geometry.resolved_from` says which authority answered (`game_config`,
`math_default` or `unresolved`).

Each line comes back in both forms: `elements` is `[reel, position]` 0-indexed,
which is what the file writes, and `grid` is `[row, column]` 1-indexed, which is
what a game config's `paylines` block uses. Sending both is what makes the two
comparable — the hand-copied JSON block exists because
`services/paylines.py` checks a screenshot on a machine that may not have the
game installed, while this file only exists on one that does.

### Configuration

```
game_config     the game's installed GameConfig directory
win_geometry    its winGeometry.xml (defaults to <game_config>/winGeometry.xml)
symbols         display name per symbol code; blank means "not named yet"
```

Neither path is checked at config-load time: the game's install is not part of
this repo, and a config that names it stays valid on a machine without it. The
service reading the file is where a missing one becomes an error. `symbols` keys
are upper-cased on load, since that is how the maths writes them.

## Game selection

```
GET  /api/games/              list game configs and the active selection
PUT  /api/games/active        switch the active game for this backend process
```

The dashboard uses these endpoints to select a game without editing `.env` or
restarting the backend. The initial selection is stored in
`app/config/game_config/active_game.json`, and the selection endpoint updates
that file. Every service caching per-game metadata drops it, and OBS retargets
its window source when OBS is connected; if OBS is closed, it will retarget on
the next connection. i-deck needs no reload: it addresses the deck by layout
key, which belongs to the cabinet rather than to the game.

## Virtual OLED i-deck

Presses the emulated button deck of a game running in a simulator. The deck is
an SDL window (`Virtual OLED`, class `SDL_app`) owned by `OledPanelSvc.exe`,
which exposes no API — it listens on no port and speaks CORBA internally.

```
GET  /api/ideck/status           panel state; always 200
GET  /api/ideck/buttons          every key, in layout order
POST /api/ideck/press            press one key {"button": "<layout key>"}
POST /api/ideck/sequence         press several in order
POST /api/ideck/probe            capability check, no side effects
```

Five things worth knowing:

- **Nothing moves your cursor.** A press is `PostMessage`d straight to the
  window as `WM_MOUSEMOVE` + `WM_LBUTTONDOWN` + `WM_LBUTTONUP`. The move first
  is not optional: SDL takes a click's position from the last motion event, not
  from the button message.

- **Geometry and names are read, not guessed.** `IDECK_PANEL_XML` points at the
  layout file the panel service itself renders from, so key positions *and* key
  names have one source of truth. A key is addressed by its layout id —
  `Rebet`, `Maxbet`, `Collect`, `Service`, `Line1`..`Line5`, `Hold1`..`Hold5` —
  matched case-insensitively. Nothing per-game has to be kept in step with the
  deck, and `GET /api/ideck/buttons` lists exactly what is pressable.

- **A key on the deck is not always a key the game uses.** The layout belongs to
  the cabinet, not the theme, so a key the panel confirms may be one the running
  game binds nothing to: the press lands, the panel confirms it, and the game
  does nothing. FortuneOx binds only its bottom row — `Hold1`..`Hold5`, which
  publish `BetsPerUnitSelectButtonMsg` — and the large key, `Rebet`, which
  publishes `SpinButtonMsg`. Its top row (`Line1`..`Line5`) and `Maxbet` stay
  inert even in `PanelStateIdleWithCredits` with the bet below maximum. Only the
  game's own log separates the two cases, because a confirmed press proves the
  panel saw it, not that the game acted on it.

- **Presses are proven, not assumed.** Every press the panel accepts writes
  `Button Pressed ID=<hex>` to `IDECK_LOG_PATH`. Each press records the log size
  first and then reads only what was appended, so a press that never landed
  returns 502 instead of a false success. Set `IDECK_VERIFY_PRESSES=false` to
  skip this; the result is then honestly marked `confirmed: false`.

- **Integrity levels matter.** Windows (UIPI) drops input sent from a lower
  integrity level to a higher one. The panel is launched elevated (High
  integrity), so this backend must run elevated too — otherwise every press is
  refused, and the symptoms look like unrelated bugs. `GET /api/ideck/status`
  reports `state: access_denied` when the backend cannot drive the panel. On a
  managed workstation where you are a standard user, "Run as administrator" is not
  available — instead a child
  process inherits its launcher's integrity, and BeyondTrust/Avecto silently
  elevates VS Code, so **launching the backend from the VS Code integrated
  terminal** makes it inherit High with no prompt (`Start-Process`/`start.ps1`
  inherit; a `wt` tab does not, being hosted by the separate Medium broker). See
  [`docs/elevation.md`](../docs/elevation.md).

Start with `POST /api/ideck/probe`: it posts a mouse move and nothing else, so
it proves input reaches the panel without touching game state.

## Game window input

Clicks the on-screen buttons the button deck does not carry. Take win and gamble
are the reason this exists: they are touched on the glass, not pressed on the
i-deck, so `/api/ideck/press` cannot reach them however it is configured. The
target is the simulator's own Unity window (title = the active game's name,
class `UnityWndClass`).

```
GET  /api/game-input/status      game window state; always 200
GET  /api/game-input/targets     every configured target, in both coordinate spaces
POST /api/game-input/click       click one target {"target": "take_win"}
```

Only names the active game declares are clickable — there is no endpoint that
takes a raw pixel, so a caller can reach the buttons a game configures and no
other point on the screen.

### Why coordinates, and what makes them safe

There is no API to ask instead, and not for want of looking:

- Unity implements neither UI Automation nor Microsoft Active Accessibility, so
  the window exposes no tree of controls to address by name.
- The deck cannot do it. `virtual_oled.xml` declares fourteen keys — Service,
  Line1‑5, Rebet, Collect, Hold1‑5, Maxbet — and gamble is not among them. The
  games' own logs settle it: every gamble decision arrives as a `TouchMsg` from
  the glass, never as an OLED button id.
- The game's CUDLR console (an HTTP server it really does run) exposes only
  gaffing commands — `gaff`, `gaffq`, `gaffinfo`, `RepeatGame`, `weights` — and
  nothing that presses a button.
- The game *does* ship a proper automation service: GDK's **GAF**, a Thrift
  server whose documented surface includes `SimulateTouch(gameObject)`,
  `GetSelectableObjects`, `GetCurrentState` and `GetMeterInfo`, with wrappers
  literally named `PressTakeWin` and `PressGamble`
  (`<game>/TestCode/GAF.XML`, handlers present in the game's own
  `Assembly-CSharp.dll`). It is **not started by a normal simulator launch** —
  nothing listens on the ports its client defaults to. If it is ever switched
  on, it belongs here as a second way to deliver a click and would make this
  section obsolete.

So the coordinate stays, and two things keep it honest:

- **Geometry is not hardcoded.** Aim points live in the active game's config as
  fractions of the window, the same convention `image_roi.py` crops regions
  with, so a resized simulator needs no re-measurement:

  ```json
  "button_targets": {
    "take_win": [0.0713, 0.9724],
    "gamble":   [0.0694, 0.9383]
  }
  ```

  A target may also declare how a click on it is proven. Omit it and `take_win`
  and `gamble` pick up their defaults:

  ```json
  "button_targets": {
    "gamble_red": { "point": [0.30, 0.50], "confirm": "gamble-picked" }
  }
  ```

  `confirm` names an event from `app/utils/game_log.py`, resolved through the
  active game's own rules — so a game that overrides `gamble-accepted` gets its
  own pattern here too.

- **Clicks are proven, not assumed.** This is what earns the coordinate its
  keep: a click that misses is *silent*, so silence has to be a failure. Each
  click records the game log's size first and then reads only what was appended.
  `data.confirmed_by` says how strong the proof was:

  | value | meaning |
  | --- | --- |
  | `target-event` | the game published this target's own event (`double_up_offer_accept` for gamble, `double_up_offer_decline` for take win) — it names the button that was hit, so the coordinate was right |
  | `touch` | the game registered a touch but nothing said which button — all that is available for a target declaring no event |

  A 502 distinguishes the two causes rather than blaming the window for a
  coordinate: a touch with no target event means the click reached the game and
  missed, so re-measure against `GET /api/game-input/targets`; no touch at all
  means the input never arrived. Set `GAME_INPUT_VERIFY_CLICKS=false` to skip
  this; the result is then honestly marked `confirmed: false`.

Nothing moves your cursor, and the same **integrity level** rule as the i-deck
applies — the game runs elevated on these machines, so the backend must be too.
`GET /api/game-input/status` reports `access_denied` rather than leaving you to
guess. Elevate the backend by launching it from the VS Code terminal — see
[`docs/elevation.md`](../docs/elevation.md).

## Analyze Spin

Everything above, in order, from one button. Records, screenshots, spins, follows
the game's log to the result, collects a win if there is one, and then runs the
cash meter and payline validations over the frames it took.

```
POST /api/analyze-spin/start          spin once, and validate it
                                      ?record=, ?architecture=
GET  /api/analyze-spin/status         the run in progress, or the last one
POST /api/analyze-spin/cancel         ask the run in progress to stop
GET  /api/analyze-spin/frames/{file}  one screenshot the run took
WS   /api/analyze-spin/stream         progress, one snapshot per change
```

```bash
# spin, and return as soon as it is under way
curl -s -X POST localhost:8001/api/analyze-spin/start
# where it got to
curl -s localhost:8001/api/analyze-spin/status
# the same, plus the meter crops, the annotated reels and the paying lines
curl -s 'localhost:8001/api/analyze-spin/status?include_images=true'
```

`app/services/analyze_spin.py` is **orchestration and nothing else** — it owns no
image handling, no XML, no win32. Every piece already existed; this is the order
they go in:

| #   | Step              | What it uses                                             |
| --- | ----------------- | -------------------------------------------------------- |
| 1   | `prepare`         | OBS connect + window select, and the paytable the game loaded |
| 2   | `record-start`    | `POST /api/obs/recording/start`, into `analyze-spin/<run>` |
| 3   | `frame-initial`   | a screenshot, before anything moves                      |
| 4   | `spin`            | the i-deck key, *and* the game's own `SpinButtonMsg`     |
| 5   | `reels-stop`      | the log's `stateSpinWithStops` → `stateReelSpinDone`     |
| 6   | `win-detect`      | the win meter count-up, or its absence                   |
| 7   | `frame-outcome`   | a screenshot of the result                               |
| 8   | `take-win`        | a click into the game's window (`take_win`) — win only   |
| 9   | `frame-collected` | a screenshot once the win is on the balance — win only   |
| 10  | `record-stop`     | `POST /api/obs/recording/stop`                           |
| 11  | `meter`           | `roi.cash_meter` on every frame, then the arithmetic between them |
| 12  | `classify`        | the reels split, then every tile named by the image classifier |
| 13  | `paylines`        | those codes lined up against the *running game's* own lines |

So a losing spin takes two screenshots and a winning one takes three, and
`take-win`/`frame-collected` come back `skipped` rather than absent — a different
fact from never having got there.

### Six things worth knowing

- **A losing spin is proven by silence.** The game logs the win meter counting up
  (`[WinBangDone]`) when there is something to collect and logs nothing at all
  when there is not, so "no win" is the absence of that line within
  `ANALYZE_SPIN_WIN_WAIT_SECONDS`. That wait is paid in full on every losing
  spin, and it is the one setting here that can produce a confidently wrong
  answer rather than a timeout: set below the game's longest count-up, a win is
  reported as a loss. Everything else fails loudly.

- **A confirmed press is not a spin.** `/api/ideck/press` proves the *panel*
  registered the key, and the deck's layout belongs to the cabinet rather than
  the theme — so a key the panel confirms may be one this game binds nothing to.
  Step 4 therefore also waits for the game to publish `SpinButtonMsg`, and says
  which half failed.

- **Screenshots go where the validations read.** They are written into the
  dashboard's own screenshot directory (`OBS_SCREENSHOT_SUBDIR`), not a per-run
  one, because that is where `services/roi.py` and `services/grid.py` open a
  frame *by name*. `run.frames[].file_name` is that name;
  `GET /api/analyze-spin/frames/{file}` serves it.

- **An empty frame is caught rather than accepted.** OBS renders nothing for a
  moment after its window-capture source is re-pointed, and it reports a
  perfectly successful write of the black frame that comes out — an error
  nowhere, and every reading taken off it is meaningless rather than merely
  dark. So `prepare` waits `ANALYZE_SPIN_SOURCE_SETTLE_SECONDS` after
  retargeting, and every capture is *read back*
  (`roi.is_blank`, the same threshold regions are resolved against) and retried
  up to `ANALYZE_SPIN_BLANK_RETRIES` times. One that stays blank sets `blank` on
  the frame and fails its step without ending the run — the spin and the eight
  steps after it are still worth having.

- **Cancelling is cooperative.** Every wait polls and every poll checks the flag,
  so the run stops itself — tidying up its recording and sealing its `run.json`
  on the way — rather than being torn down mid-call. The cost is that a cancel
  lands only once whatever call is in flight returns.

- **The readings at the end cannot fail each other.** A machine with no
  Tesseract still gets its reel reading and its payline check; one with no torch
  still gets its meter arithmetic; one without the game installed still gets
  both. A validation that *runs* and reports `failed` is a completed step — only
  one that could not run at all fails. The single dependency is that `paylines`
  reads what `classify` produced, and says so when there is nothing to read.

### The stream is the one thing outside the envelope

`WS /api/analyze-spin/stream` sends a whole `{active, run}` state on connect and
one per change after that. Not deltas: a client that joins mid-spin or drops a
frame is still correct. It carries **no images** — a snapshot goes out on every
step transition and every recognised log line, and forty line pictures per push
would make the stream the slowest part of a spin. `GET /status` with
`include_images=true` is the report.

A WebSocket frame is not a response: it has no status code and no request id, so
it is the payload itself rather than `{success, message, data, error, meta}`.
That and the two file-serving routes are the only exceptions in this API.

> In development the Vite proxy needs `ws: true` to forward the upgrade, which
> `frontend/vite.config.js` sets. Without it the handshake is answered with the
> HTML index.

### The cash meter validation is arithmetic, not a rule

`roi.cash_meter` is read off each frame — which is
[Reading the meter](#reading-the-meter-values), so it needs Tesseract — and each
check is stated as a relation between two of those readings, with the sum that
decided it:

| Check                          | Relation                                          |
| ------------------------------ | ------------------------------------------------- |
| `bet-stable`                   | the bet reads the same before and after           |
| `bet-deducted`                 | balance after = balance before − bet              |
| `win-registered` / `win-empty` | the WIN cell agrees with what the log said        |
| `win-collected`                | balance after collecting = balance at the result + the win |

There is deliberately **no "the win meter cleared" check**: these games leave the
last win showing on the WIN cell after it has been collected, so an empty one
would be the surprise. The balance moving is the proof of collection, and that is
`win-collected` above.

Every check carries `expected`, `actual` and the arithmetic as a sentence,
because a failed check is nearly always one misread digit and the numbers are the
answer — "failed" is a consequence. `indeterminate` is a third verdict, not a
soft failure: an unreadable meter and a wrong balance need different fixes. Two
amounts count as equal within `ANALYZE_SPIN_METER_TOLERANCE`, which absorbs the
OCR of the last decimal and nothing larger.

**The validation also says what units it read in.** `meter.mode` is `cash`,
`credits` or `unknown`, and in cash mode `meter.currency` is the symbol drawn on
the values (`"?"` when money was read but its symbol was not — the yen glyph
these games draw reads as nothing at every mode and scale). Without it every
figure above is ambiguous: the same `1250` is 1250 credits or 1250 of some
currency, and the arithmetic is only checkable against the glass once that is
settled. It is also what makes the `bet_credits` conversion in the expected award
legible — dividing the meter's bet by the denomination reaches credits **only on
a cash meter**.

One answer for the whole run rather than one per frame, since a cabinet does not
change denomination between the screenshots of one spin. `services/meter.combine()`
folds the frames' own classifications, and it is not a vote: **cash wins a
disagreement**, because the two modes are not symmetric evidence. Money is read
positively (a currency symbol, or an amount with a fractional part) and credits is
what `_classify` concludes from the absence of both — so a frame whose three cells
all happened to be whole numbers and whose symbol did not OCR reads as credits on
a cash machine, and one frame that saw money settles it. Each frame's own reading
stays on `readings[].values.mode` for a run where they differed.

### The payline validation: two judgements, one reading

**The picture decides what landed, and the image classifier is what reads it.**
Step 12 splits the result frame's reels and hands the tiles to
[the image classifier](#naming-the-symbols-image-classifier), which returns a symbol
code per tile —
or nothing for a tile below `CLASSIFIER_MIN_CONFIDENCE`. Step 13 reads each line
as the leading run of *equal codes*, via `services/paylines.check_symbols()`, and
the paytable prices that run. So a run is **named** as well as counted, and an
award is one combo and one number.

Two earlier readings were replaced by that one, and why matters more than what:

- **cosine similarity between the split's tiles** measured the run without ever
  naming it. It says these two pictures are alike, so an award could only be
  narrowed to *every* paytable row paying at that run length. It is still there —
  it is what `POST /api/paylines/check` and the dashboard's payline panel use, and
  it is the right tool for "is this crop aimed at a symbol at all". Nothing in
  Analyze Spin calls it.
- **the reel stops in the game's own log** (`ReelSet.SetStops(ReelsStopData)` plus
  `math.xml`'s strips) named the symbols by *agreeing with the game*. A reading
  taken out of the log cannot catch a reel drawing the wrong symbol, because it
  never looked at the reel. `app/utils/reel_stops.py` is still in the tree and no
  longer read here, as is `ANALYZE_SPIN_REEL_STOP_ANCHOR` — the whole
  top/middle/bottom question disappears when the tile itself is what gets named.

**The paytable decides whether it pays, and nothing else does.** A run of two of
a symbol whose row starts at three is a *real run and no win* — so it comes back
`awarded: false` with a `note` saying what it would have needed ("Jack pays from
3 on, so this run of 2 awards nothing"), contributes nothing to the expected
award, and is flagged rather than shown as a paying line.
`app/services/paylines.py` deliberately stops at "two or more", because what pays
what is not its business; this is the module that has the paytable, so this is
where that call belongs.

So per line the response carries both readings, never collapsed:

| field | from | meaning |
| --- | --- | --- |
| `pays` | the picture | leading run of positions the classifier named with one code |
| `symbols` | the picture | the code at every position of the line, null where none was read |
| `steps` | the picture | every adjacent pair with both codes, `matched` and `counted` |
| `paying` | the picture | whether that run is two or more — **evidence, not a win** |
| `symbol` / `symbol_name` | the picture | the code the run is made of |
| `min_pay_length` | the paytable | shortest run that symbol pays at |
| `awarded` | the paytable | **whether this line earns anything** |
| `combo_id` / `combo_symbols` | `math.xml` | the combo the run matched |
| `credits` | `math.xml` | what that combo pays, per line at one credit |

`steps[].similarity` is `null` and the result's `threshold` is `null`, because
these tiles were compared by name: two codes are equal or they are not. The four
score figures on `stats` are null for the same reason — there is no distribution
to separate, so there is nothing for `matched_min`/`rejected_max` to bracket.
`method` on the result says which comparison ran (`similarity` or `symbol`), so a
reader never has to guess which set of fields is meaningful.

`runs_found` and `awarded_lines` report the two counts apart, and `summary` is
written from the awards rather than reused from the payline check — a summary
reading "Line 2 pays 2" for a run the maths awards nothing for is the exact
confusion that split exists to remove. **"Pays" is credits throughout, never the
run length**: a line *matches* five symbols and *pays* twenty-five, and one word
for both is how a run of five gets read as five credits.

A cancelled run is not presented as a result anywhere. Its own tracking picture
is dropped from the payload, and the combined overlay is **redrawn** over the
awarded lines only — `services/paylines.redraw()` rebuilds it from the finished
result rather than re-evaluating, going back through the same `_line_drawing` so
a redrawn line is identical to a first-pass one (colour included, since the index
stays the line's place in the whole set). It replaces the file the check wrote:
one picture per (split, set) is the convention, and a superseded overlay left
beside the current one is how a reader ends up looking at the wrong evidence. The
redraw is skipped entirely when nothing was cancelled. What survives on a
cancelled line is its `steps` — the codes are still the evidence that the run was
real.

### The confidence floor is now the one tunable, and it costs something

`ANALYZE_SPIN_CLASSIFIER_MIN_CONFIDENCE` defaults to **0.85**, against the Image
Classifier page's own `CLASSIFIER_MIN_CONFIDENCE` of `0.90`. Both sit above where
the classes separate, so a correct reading is rejected whenever the model is only
*fairly* sure. That is safe on a page which shows the ranked candidates beside
every rejected tile. It is not free here.

**Two unnamed tiles are never a match.** A classifier below its floor said "I
could not tell", and twice over that is not evidence of a run — so a payline
through an unnamed tile **stops there**, and the line comes back short or paying
nothing at all. A spin that plainly paid and reports no run is a floor question
first, which is why the validation carries `unnamed_positions` and the reel
reading carries every tile's leading candidate and its probability whether or not
it cleared the floor.

Setting it *blank* rather than leaving it out opts back into
`CLASSIFIER_MIN_CONFIDENCE`, which is the escape hatch for grading a spin exactly
as the page would.

**Which network grades a spin is a per-run choice**, the same shape of choice
`record` is: `POST /start?architecture=resnet34`, falling back to
`ANALYZE_SPIN_CLASSIFIER_ARCHITECTURE` and then to `CLASSIFIER_ARCHITECTURE`
(ResNet34). Both networks stay trained at once and they do not read a
split equally well, so running one spin through each and comparing is worth more
than either alone -- which is why the dashboard offers it as a dropdown beside the
spin button rather than burying it in `.env`. The name is resolved in `start()`
before the game config is even read, so an unknown one is a **400 on the request**
rather than a failed step twelve steps in, and `run.reels.architecture` records
which checkpoint answered.

There is deliberately **no fallback to cosine similarity** when the reading fails:
a run measured by likeness and then priced as though it had been named is worse
than a run reported short.

### What landed, as its own block

`run.reels` is the reading, beside `run.meter` and `run.paylines` rather than
inside either:

| field | meaning |
| --- | --- |
| `symbol_grid` / `label_grid` | the codes and their display names, row-major, null where unnamed |
| `tiles[]` | per tile: `symbol`, `known`, `leading` and `confidence` |
| `architecture` / `label` / `trained_at` | which checkpoint answered |
| `min_confidence` | the floor those decisions were made at |
| `overlay_file` | what the ringed reels were written as, beside the tiles |

`symbol_grid` is deliberately the same field name and shape as
`ClassifyResult.symbol_grid`, which is where it comes from. It carries no `error`,
unlike the two validations: it exists only when the reading succeeded, and a
failure is on the `classify` step and again on the payline validation's `error`.

It also carries **no picture**. The ringed reels are still written to
`<split>/classifier/symbols.png` -- a ring over a cell is the cheapest way to
check a split was named the way it looks -- but the rings only ever said "the
model was sure", which the grid already says in codes, so a data URI of them on
every report was weight for something nothing draws.

**Which lines exist still comes from the game's own geometry**, not the
`paylines` block of `app/config/game_config/games/<Game>.json`. That block is a
hand-copy kept so a screenshot can be checked on a machine without the game
installed; here the game *is* installed, so:

1. the game's log names the paytable it loaded;
2. that paytable's `gameConfig.cfg` says `NumberOfLines`;
3. that selects a `paylineSetID` in the shared `winGeometry.xml`;
4. its lines convert to the `[row, column]` form a split is read in.

Read once, in step 1, so a denomination change mid-run cannot have the validation
grading the spin against the wrong maths. The set is handed to
`services/paylines.check_symbols()` — the same `_evaluate_set` joinery
`/api/paylines/check` runs, taking a set and the codes to read it by rather than
reading either — and written as `paylines/geometry-<set>.png` beside the tiles, so
a geometry-sourced check never overwrites a config-sourced one. It goes back
through `app/utils/paylines.read_set()` rather than building the dataclasses
directly, because that is where a line is checked to run left to right, and a
backtracking line would otherwise compare a tile with itself and score a perfect
match.

**The total is one number, not a range.** Every awarded line names its symbol, so
it resolves to one paytable value; `expected.credits` and `expected.cash` are
single figures where they used to be `credits_min`/`credits_max` spans. Those
spans were never a claim about the maths — they were the width of what the picture
had failed to identify. An unreadable input still makes the verdict
`indeterminate` rather than a guess.

The money conversion is spelled out field by field on `expected`, because it runs
through the denomination and the line count and a wrong verdict is nearly always
one of those:

```
bet_credits      = total_bet (off the meter) / denomination (off the log)
credits_per_line = bet_credits / line_count
cash             = credits × credits_per_line × denomination
```

It rests on one assumption, stated on the schema and nowhere else: a line combo's
value is credits per line at one credit staked on that line.

### Failures, and what they are not

`start` refuses only what this process can check without touching the machine:
409 when a run is already going, 409 when the active game declares no `log` to
follow a spin through, 500 when its config will not parse. Everything else fails
on its own **step**, carrying the error the equivalent direct request would have
given — an unconfirmed press is still `IDECK_PRESS_NOT_CONFIRMED` on
`steps[].error_code`, not a spin error. A run whose spin completed but whose
validation could not run ends `failed` with a usable record rather than a lost
one.

Out of scope, deliberately: **features**. A bonus or a free-spin sequence between
the reels stopping and the win counting up will time out on step 6 or miss its
take-win, and the run's `events` list is what says so — the free-spin win meter's
own `WinBangDone` is not one of the shipped rules. One spin, base game.

### Configuration

Every setting is in [.env.example](.env.example) under *Analyze Spin*, with the
reasoning. `ANALYZE_SPIN_SPIN_BUTTON` (default `Rebet`) and
`ANALYZE_SPIN_TAKE_WIN_TARGET` (default `take_win`) are the two that are really
per-cabinet and per-game; `ANALYZE_SPIN_WIN_WAIT_SECONDS` is the one to tune
first, and `ANALYZE_SPIN_CLASSIFIER_MIN_CONFIDENCE` the one to reach for when
lines read short. Each run's record is written to
`obs-captured-files/analyze-spin/<run>/run.json`, and its video under the
recording root beside it.

## Logging

One line per request, correlated by request id:

```
2026-08-18 09:12:44 | INFO     | app.access | 3f9a1c8e… | GET /api/games/ -> 200 (4.12ms)
```

An inbound `X-Request-ID` is reused so a trace can span services; otherwise one
is generated. It is echoed on the response, included in `meta.request_id`, and
attached to every log record made while handling the request. Set `LOG_JSON=true`
in deployed environments for single-line JSON. Health probes log at DEBUG to
keep the access log readable.

The id is stored twice on purpose: in a `ContextVar` for loggers and response
builders, and on the ASGI scope. The context var is reset as the request
unwinds, but Starlette's outermost `ServerErrorMiddleware` runs *after* that — so
the unhandled-exception handler reads the scope instead and 500s still carry a
correlation id.

## Known limitation

Unhandled exceptions are turned into a 500 by Starlette's outermost
`ServerErrorMiddleware`, which sits *outside* `CORSMiddleware` — so that one
response lacks CORS headers and a browser reads it as a network error rather
than a 500. Handled errors (everything raised through `AppException`,
validation, `HTTPException`) are unaffected, and in development the frontend
talks through the Vite proxy on the same origin, where CORS never applies. If
you need CORS headers on unhandled 500s in a deployed cross-origin setup, catch
`Exception` in a middleware registered inside the CORS layer.
