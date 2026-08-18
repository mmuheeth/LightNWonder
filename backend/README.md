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
├── OBS-capture/             default screenshot/recording root (gitignored)
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
    │   └── game_config/     active selection plus per-game aliases, process,
    │                         logs, ROIs and targets
    ├── utils/
    │   ├── win32.py         the only ctypes: posts messages to another window
    │   ├── panel_xml.py     reads the i-deck layout the panel service renders from
    │   ├── panel_log.py     matches the lines that panel service writes
    │   ├── log_tail.py      rotation-aware cursor over a file being appended to
    │   └── paths.py         resolves an untrusted filename inside a directory
    ├── api/
    │   ├── router.py        aggregates endpoint modules  → mounted at /api
    │   ├── deps.py          shared dependencies (pagination)
    │   ├── health.py        /health, /health/live, /health/ready  ← root, not /api
    │   └── endpoints/       one module per resource
    ├── schemas/
    │   ├── response.py      ApiResponse / PaginatedResponse envelope  ← the contract
    │   ├── health.py        health payloads
    │   ├── system.py        service info payload
    │   ├── games.py         game catalog and runtime-selection payloads
    │   ├── obs.py           OBS status, screenshot and recording payloads
    │   └── item.py          example resource schemas
    ├── exceptions/
    │   ├── base.py          AppException hierarchy
    │   └── handlers.py      the only place error responses are built
    ├── middleware/
    │   └── request_context.py   request id, timing, access log
    └── services/            business logic; endpoints stay thin
        ├── games.py          game catalog and active-game switching
        └── obs.py            the single long-lived obs-websocket session
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
  "message": "Item retrieved successfully",
  "data": { "id": 1, "name": "Table lamp", "quantity": 12 },
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
      { "field": "body.name", "message": "Field required", "type": "missing" }
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
@router.get("/{item_id}", response_model=ApiResponse[ItemOut])
async def get_item(item_id: int) -> ApiResponse[ItemOut]:
    return ApiResponse[ItemOut].ok(
        data=item_service.get_item(item_id),
        message="Item retrieved successfully",
    )
```

For a list:

```python
@router.get("", response_model=PaginatedResponse[ItemOut])
async def list_items(pagination: PaginationDep) -> PaginatedResponse[ItemOut]:
    items, total = item_service.list_items(
        offset=pagination.offset, limit=pagination.limit
    )
    return PaginatedResponse[ItemOut].paginate(
        items, page=pagination.page, page_size=pagination.page_size, total_items=total
    )
```

Wrapping is explicit rather than done by a catch-all middleware. A middleware
would have to re-serialise every body, and OpenAPI would advertise the inner
payload instead of the envelope — so generated clients would be wrong.

### Raising an error

Never build an error response. Raise an `AppException` subclass; the registered
handlers render it.

```python
from app.exceptions import ConflictError, NotFoundError

raise NotFoundError(f"Item {item_id} was not found")

raise ConflictError(
    "Editing is restricted to owners",
    error_code="NOT_ITEM_OWNER",  # override the default code
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
- **Capture roots are configurable.** `OBS_CAPTURE_DIR` defaults to
  `backend/OBS-capture`. `OBS_SCREENSHOT_DIR` and `OBS_RECORDING_DIR` can
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
2026-08-18 09:12:44 | INFO     | app.access | 3f9a1c8e… | GET /api/items -> 200 (4.12ms)
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
