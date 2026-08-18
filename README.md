# LightNWonder

Monorepo scaffold: FastAPI backend, React + Vite frontend.

| Part                     | Stack                                                                      | Port |
| ------------------------ | -------------------------------------------------------------------------- | ---- |
| [backend/](backend/)     | Python, FastAPI, uvicorn, pydantic v2                                      | 8001 |
| [frontend/](frontend/)   | React 19, Vite, Tailwind v4, shadcn/ui, react-query, Zustand (plain JS)     | 3001 |

Each side has its own README with the detail:
[backend/README.md](backend/README.md) · [frontend/README.md](frontend/README.md)

## Quick start

Two terminals.

**Backend**

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash); .venv\Scripts\Activate.ps1 on PowerShell
# source .venv/bin/activate        # macOS / Linux
pip install -r requirements-dev.txt
cp .env.example .env
python -m app
```

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:3001**. The dashboard shows live backend health, which
is the fastest confirmation that both halves are wired together.

> Use `localhost`, not `127.0.0.1`, for the frontend: Vite binds the hostname
> `localhost`, which resolves to IPv6 `[::1]` on Windows.

| URL                            | What                        |
| ------------------------------ | --------------------------- |
| `http://localhost:3001`        | Web app                     |
| `http://localhost:8001/health` | Health report               |
| `http://localhost:8001/docs`   | Swagger UI (hidden in prod) |
| `http://localhost:8001/api/…`  | API endpoints               |

In development the frontend makes **relative** requests and Vite proxies `/api`
and `/health` to the backend. Dev is therefore same-origin: cookies work and
CORS never applies.

## Checks

```bash
cd backend  && pytest && ruff check . && ruff format --check . && mypy
cd frontend && npm run check          # lint + format:check + test
```

## The response contract

This is the part worth reading before writing code. **Every** backend
response — success or failure — has the same five top-level keys:

```json
{
  "success": true,
  "message": "Item retrieved successfully",
  "data": { "id": 1, "name": "Table lamp" },
  "error": null,
  "meta": { "request_id": "3f9a1c8e…", "timestamp": "2026-08-18T09:12:44.512Z" }
}
```

On failure, `data` is `null` and `error` is populated:

```json
{
  "success": false,
  "message": "Request validation failed",
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "details": [{ "field": "body.name", "message": "Field required", "type": "missing" }]
  },
  "meta": { "request_id": "3f9a1c8e…", "timestamp": "2026-08-18T09:12:44.512Z" }
}
```

Both keys are always present, so clients never branch on key existence, and
`success` always mirrors the HTTP status. List endpoints keep the same shape and
add pagination to `meta`.

**Backend** — declare the envelope as the `response_model` and raise typed
exceptions; handlers render every error:

```python
@router.get("/{item_id}", response_model=ApiResponse[ItemOut])
async def get_item(item_id: int) -> ApiResponse[ItemOut]:
    return ApiResponse[ItemOut].ok(data=service.get_item(item_id))

# elsewhere — never build an error response by hand
raise NotFoundError(f"Item {item_id} was not found")
```

**Frontend** — `lib/api.js` unwraps the envelope and `lib/api-error.js`
normalises every failure into one `ApiError`, with `fieldErrors` ready to bind
to form inputs.

Every request also carries an `X-Request-ID` end to end: the browser generates
one, the backend adopts it, echoes it in `meta.request_id`, and stamps it on
every log line for that request. That id is the fastest way to connect a UI
error to its server-side cause.

## Where things live

```
backend/app/
├── api/            router.py, health.py, endpoints/   ← routes only, kept thin
├── schemas/        response.py holds the envelope     ← the contract
├── exceptions/     base.py hierarchy, handlers.py     ← the only error renderer
├── services/       business logic (incl. obs.py, ideck.py)
├── config/         game data that ships with the code, and its reader
├── utils/          win32 interop, panel layout/log formats, log tail, safe paths
├── middleware/     request id, timing, access log
└── core/           settings, logging, request context

backend/OBS-capture/    OBS screenshots and recordings (gitignored)

frontend/src/
├── features/       one directory per feature (api + hooks + components)
├── lib/            http, api, api-error, query client/keys
├── components/     ui/ (shadcn), layout/, shared pieces
└── store/          Zustand UI state
```

Both sides ship a small **`items`** example resource that exercises the whole
path: list with pagination, create with validation errors, delete with a 404.
Delete it once you have real endpoints — `backend/app/api/endpoints/items.py`
(plus its service and schema) and `frontend/src/features/items/`.

## OBS Studio

The dashboard's **OBS Studio** card drives a local OBS instance over
[obs-websocket](https://github.com/obsproject/obs-websocket) v5 — connect,
capture a screenshot, and start/stop/pause a recording. OBS Studio 28+ bundles
the plugin; enable it under *Tools → WebSocket Server Settings*.

The integration is backend-owned: the password lives in `backend/.env` and never
reaches the browser, which only ever talks to `/api/obs/*`. Screenshots and
recordings both land in `backend/OBS-capture/`.

It is **optional and off by default** — the app boots and stays healthy with OBS
closed, and a dropped connection re-establishes itself on the next request. See
[backend/README.md](backend/README.md#obs-studio) for the endpoints and the
settings.

## Virtual OLED i-deck

The dashboard's **i-deck** card presses the emulated button deck of the
currently selected game. The deck is an SDL window served by
`OledPanelSvc.exe`, which exposes no API, so presses are delivered as mouse
messages posted straight to that window. **Your cursor never moves.** Button
names and game-specific details come from the selected file in
`backend/app/config/game_config/games/`.

Key positions are read from the panel's own layout file rather than hardcoded,
and every press is confirmed against `C:\logs\OledPanelSvc.log` before the
request succeeds — a press that did not land returns a 502 rather than a
cheerful lie.

One gotcha worth knowing up front: Windows refuses input sent from a lower
integrity level to a higher one, so **if the panel was launched elevated, the
backend must be started elevated too**. The card says `access_denied` when that
is the case. See
[backend/README.md](backend/README.md#virtual-oled-i-deck) for the endpoints and
the settings.

## Notes

- Health is mounted at the backend **root** (`/health`, `/health/live`,
  `/health/ready`), deliberately outside the `/api` prefix, so probes never
  depend on the API's base path.
- `ENVIRONMENT=production` hides `/docs`, `/redoc` and `/openapi.json`.
- Backend commands must run from `backend/` — `app` is imported from the working
  directory rather than installed into site-packages.
- Tailwind v4 has no `tailwind.config.js`; theming is CSS-first in
  `frontend/src/index.css`.
