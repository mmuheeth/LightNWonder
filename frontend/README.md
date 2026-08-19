# LightNWonder — Frontend

React + Vite client. Runs on **port 3001**.

Stack: React 19, Vite, Tailwind CSS v4, shadcn/ui, react-query (TanStack Query v5),
Zustand, React Router, axios. Plain JavaScript — no TypeScript.

## Setup

```bash
cd frontend
npm install
cp .env.example .env.local     # optional; defaults work for local dev
```

## Run

```bash
npm run dev        # http://localhost:3001
```

Start the backend too (`cd backend && python -m app`) — the dashboard reads
the game catalog and controls the OBS and Virtual OLED integrations through it.

> Use **`http://localhost:3001`**, not `http://127.0.0.1:3001`. Vite binds the
> hostname `localhost`, which resolves to IPv6 `[::1]` on Windows, so the IPv4
> literal will refuse the connection.

## Checks

```bash
npm run lint          # eslint
npm run format        # prettier --write
npm run format:check  # prettier --check
npm test              # vitest
npm run build         # production build into dist/
npm run preview       # serve the built bundle on 3001
npm run check         # lint + format:check + test
```

## Layout

```
src/
├── main.jsx                entry: providers + router
├── App.jsx                 route table
├── index.css               Tailwind v4 entry + shadcn design tokens
├── app/
│   └── providers.jsx       QueryClient, theme, router, error boundary
├── config/
│   └── env.js              validated import.meta.env access + route prefixes
├── lib/
│   ├── http.js             axios instance + interceptors
│   ├── api.js              envelope-aware request helpers      ← start here
│   ├── api-error.js        ApiError: the one error type callers see
│   ├── query-client.js     react-query defaults
│   ├── query-keys.js       cache key registry
│   └── utils.js            cn() class merger
├── features/
│   ├── ideck/              presses the Virtual OLED button deck
│   ├── games/              active game catalog and selector
│   ├── event-capture/      start/stop log-driven capture, and read runs back
│   └── obs/                OBS Studio control: connect, screenshot, record
├── components/
│   ├── ui/                 shadcn/ui primitives (managed by the CLI)
│   ├── layout/             app shell: header + outlet
│   ├── api-error-alert.jsx consistent error rendering
│   ├── error-boundary.jsx  catches render-time crashes
│   ├── theme-provider.jsx  applies .dark to <html>
│   └── theme-toggle.jsx
├── pages/                  route components
├── store/
│   └── ui-store.js         Zustand: persisted theme preference
└── test/                   vitest setup + renderWithProviders
```

Feature-first: each feature owns its API calls, hooks and components, so a
feature can be deleted in one directory. Shared plumbing lives in `lib/`.

`features/event-capture/` is the same shape with one twist worth copying: its
status poll uses a functional `refetchInterval`, so a live run refreshes every
two seconds while an idle dashboard asks every ten. Its screenshots are the one
thing fetched outside `apiRequest` -- `captureImageUrl()` builds a URL for an
`<img>` src, because the backend serves those as files rather than as the
envelope.

`features/obs/` is the reference for a slice that both polls and mutates: a
status query on a short `refetchInterval`, mutations that invalidate
`queryKeys.obs.all`, and one mutation (`useTakeScreenshot`) whose result is read
straight from `mutation.data` rather than the cache, so the preview needs no
component state.

## Talking to the API

The backend wraps every response in a fixed envelope
(`{ success, message, data, error, meta }`). `lib/api.js` peels that off, so
feature code deals in plain domain objects:

```js
import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

// returns envelope.data
export function getGameCatalog({ signal } = {}) {
  return apiRequest({
    method: "GET",
    url: `${routes.API}/games/`,
    signal,
  });
}
```

Then wrap it in a hook, keying the cache through `queryKeys`:

```js
export function useGameCatalog() {
  return useQuery({
    queryKey: queryKeys.games.catalog(),
    queryFn: ({ signal }) => getGameCatalog({ signal }),
  });
}
```

## Error handling

Every rejection — HTTP error, network failure, timeout, or a non-envelope
response from a proxy — is normalised to a single `ApiError`:

| Property        | Meaning                                            |
| --------------- | -------------------------------------------------- |
| `message`       | Backend's human-readable message; safe to display  |
| `code`          | Stable code, e.g. `NOT_FOUND`, `VALIDATION_ERROR`  |
| `details`       | Field-level problems from the backend              |
| `status`        | HTTP status, or `null` if the request never landed |
| `requestId`     | Correlation id — matches the server log line       |
| `fieldErrors`   | `details` as `{ inputName: message }` for forms    |
| `isClientError` | 4xx                                                |
| `isRetryable`   | 5xx, network blips, timeouts                       |
| `isAuthError`   | 401 or 403                                         |
| `isNotFound`    | 404                                                |

`fieldErrors` strips the backend's location prefix (`body.name` → `name`) so
keys line up with input names:

```jsx
function ResourceForm({ error }) {
  const nameError = error?.fieldErrors.name;

  return (
    <>
      <Input name="name" aria-invalid={Boolean(nameError)} />
      {nameError ? <p className="text-destructive text-sm">{nameError}</p> : null}
    </>
  );
}
```

For anything not field-scoped, render `<ApiErrorAlert error={error} />` — it
shows the message, the details list, the code and the request id.

`query-client.js` uses `isRetryable` so 4xx failures surface immediately instead
of burning through the retry backoff. Mutations never retry.

## Configuration

See [.env.example](.env.example). Only `VITE_`-prefixed variables reach the
browser bundle — never put secrets in them.

In development leave `VITE_API_BASE_URL` empty: requests stay relative and Vite
proxies `/api` to `http://127.0.0.1:8001`. That keeps dev same-origin, so
cookies work and CORS never applies. Point
`BACKEND_PROXY_TARGET` elsewhere to develop against a deployed backend.

For production, set `VITE_API_BASE_URL` to the API origin at build time.

## Styling

Tailwind CSS v4 via the `@tailwindcss/vite` plugin — there is no
`tailwind.config.js`. Configuration is CSS-first in `src/index.css`: the
`@theme inline` block maps shadcn's design tokens onto Tailwind colour
utilities, and `:root` / `.dark` hold the actual oklch values. Change the theme
by editing those variables.

Add more shadcn components with:

```bash
npx shadcn@latest add dialog dropdown-menu table
```

`components.json` is already configured for this project (`tsx: false`, `@`
alias, empty `tailwind.config` as Tailwind v4 requires). Files land in
`src/components/ui/` — they're vendored, so they're excluded from Prettier and
from the `react-refresh/only-export-components` lint rule.

## State

Two stores, deliberately separate:

- **react-query** owns server data — fetching, caching, invalidation.
- **Zustand** (`store/ui-store.js`) owns the client-only theme preference.

Don't copy server data into Zustand; that creates a second source of truth and
stale reads.
