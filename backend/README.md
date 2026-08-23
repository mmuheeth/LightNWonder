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
    │   └── game_config/     active selection plus per-game process, logs,
    │                         ROIs, reel bounds, paylines and targets
    ├── utils/
    │   ├── win32.py         the only ctypes: posts messages to another window
    │   ├── panel_xml.py     reads the i-deck layout the panel service renders from
    │   ├── panel_log.py     matches the lines that panel service writes
    │   ├── game_log.py      parses the game's log and names its events
    │   ├── log_tail.py      rotation-aware cursor over a growing file, and the
    │                         follower that polls one for new lines
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
    │   └── paylines.py      line sets, one line's verdict and its evidence
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
        ├── meter.py          turns a cash-meter crop into its five values
        └── roi.py            cuts one configured region out of a screenshot
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
"meter": { "band": [0.103448, 0.689655] }
```

`[top, bottom]` as fractions of the **strip's** height, so the same pair holds at
any capture resolution. Without one, `fit_band` works it out by reading: the
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
