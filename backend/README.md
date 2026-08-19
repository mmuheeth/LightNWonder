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
    │   ├── obs.py           OBS runtime settings
    │   ├── ideck.py         i-deck runtime settings and path resolution
    │   ├── event_capture.py Event Based Capture runtime settings
    │   └── game_config/     active selection plus per-game aliases, process,
    │                         logs, ROIs and targets
    ├── utils/
    │   ├── win32.py         the only ctypes: posts messages to another window
    │   ├── panel_xml.py     reads the i-deck layout the panel service renders from
    │   ├── panel_log.py     matches the lines that panel service writes
    │   ├── game_log.py      parses the game's log and names its events
    │   ├── log_tail.py      rotation-aware cursor over a file being appended to
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
    │   └── event_capture.py capture runs and the events inside them
    ├── exceptions/
    │   ├── base.py          AppException hierarchy
    │   └── handlers.py      the only place error responses are built
    ├── middleware/
    │   └── request_context.py   request id, timing, access log
    └── services/            business logic; endpoints stay thin
        ├── games.py          game catalog and active-game switching
        ├── obs.py            the single long-lived obs-websocket session
        └── event_capture.py  follows the game log and screenshots its events
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
  Omit it to get base64 back instead.
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

- **The log reader is separate from the capture.** `app/utils/game_log.py` turns
  log lines into named events and knows nothing about OBS or runs, so anything
  else that wants to react to gameplay can use it. It is the sibling of
  `panel_log.py`: one module per foreign log format.
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

## Game selection

```
GET  /api/games/              list game configs and the active selection
PUT  /api/games/active        switch the active game for this backend process
```

The dashboard uses these endpoints to select a game without editing `.env` or
restarting the backend. The initial selection is stored in
`app/config/game_config/active_game.json`, and the selection endpoint updates
that file. The selected config is reloaded for i-deck aliases and OBS retargets
its window source when OBS is connected; if OBS is closed, it will retarget on
the next connection.

## Virtual OLED i-deck

Presses the emulated button deck of a game running in a simulator. The deck is
an SDL window (`Virtual OLED`, class `SDL_app`) owned by `OledPanelSvc.exe`,
which exposes no API — it listens on no port and speaks CORBA internally.

```
GET  /api/ideck/status           panel state; always 200
GET  /api/ideck/buttons          every key, in layout order
POST /api/ideck/press            press one configured key {"button": "<alias>"}
POST /api/ideck/sequence         press several in order
POST /api/ideck/probe            capability check, no side effects
```

Four things worth knowing:

- **Nothing moves your cursor.** A press is `PostMessage`d straight to the
  window as `WM_MOUSEMOVE` + `WM_LBUTTONDOWN` + `WM_LBUTTONUP`. The move first
  is not optional: SDL takes a click's position from the last motion event, not
  from the button message.

- **Geometry is read, not guessed.** `IDECK_PANEL_XML` points at the layout file
  the panel service itself renders from, so key positions have one source of
  truth. Only the friendly names come from
  `app/config/game_config/games/<game>.json`:

  ```json
  "ideck": { "aliases": { "friendly_name": "LayoutKey" } }
  ```

  The initial game is selected with
  `app/config/game_config/active_game.json`, or changed from the dashboard.
  After that, aliases, process metadata, game log path, screen regions and
  in-game targets all come from
  `app/config/game_config/games/<game>.json`. The API accepts any alias in that
  file or any key name from the panel layout.

- **Presses are proven, not assumed.** Every press the panel accepts writes
  `Button Pressed ID=<hex>` to `IDECK_LOG_PATH`. Each press records the log size
  first and then reads only what was appended, so a press that never landed
  returns 502 instead of a false success. Set `IDECK_VERIFY_PRESSES=false` to
  skip this; the result is then honestly marked `confirmed: false`.

- **Integrity levels matter.** Windows (UIPI) drops input sent from a lower
  integrity level to a higher one. If the panel was launched elevated, this
  backend must be started elevated too — otherwise every press is refused, and
  the symptoms look like unrelated bugs. `GET /api/ideck/status` reports
  `access_denied` in that case rather than leaving you to guess.

Start with `POST /api/ideck/probe`: it posts a mouse move and nothing else, so
it proves input reaches the panel without touching game state.

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
