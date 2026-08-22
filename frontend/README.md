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
│   ├── obs/                OBS Studio control: connect, screenshot, record
│   ├── roi/                crops a configured region out of the latest shot
│   ├── grid/               splits the reels of the latest shot into tiles
│   └── paylines/           checks a split's tiles against the patterns that pay
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

`features/roi/` is the smallest complete slice, and the one to copy for a
read-then-act panel: one query for what can be chosen, one mutation whose crop is
read from `mutation.data`, and no client state beyond which option the dropdown
is on. It also shows the one case for reaching across features -- taking a
screenshot changes which frame the ROI panel would extract from, so
`useTakeScreenshot` invalidates `queryKeys.roi.all` rather than the ROI query
polling for a file it cannot see. `features/grid/` reaches across the same way,
and `useTakeScreenshot` invalidates both subtrees.

`features/grid/` is the same read-then-act shape, and its one piece of client
state exists for a loop rather than for a preference: the trim override is typed
in, tried against a frame that does not move, and then written into the game
config, so it is held as the raw string and sent only when it is not blank. It
also shows what to do when the server already knows the layout: the tiles arrive row-major carrying their own
`row`/`column`, so the panel sets `gridTemplateColumns` from the response's
`columns` instead of chunking the list itself -- a game with six reels needs
nothing changed in the component. It also shows the other half of the ROI panel's
disabled-with-a-reason pattern: a game that declares no reels comes back as a 200
with `error` set, so the card says why Split is refused rather than rendering an
error alert for a request that succeeded.

`features/paylines/` is the full-width panel, and the slice to copy when a
result has to be *checkable* rather than merely displayed. Three things in it are
deliberate:

- **The evidence is shown next to the verdict, as boxes rather than a
  sentence.** A payline that "pays 4" reads identically whether four symbols
  really line up or the match threshold is too low, so a paying line gets its
  own `PaylineResultCard` -- colour, name, pay count -- instead of being one
  clause in `result.summary`'s comma list, and the combined overlay, the run's
  whole score distribution and every adjacent comparison sit behind a dropdown
  each rather than in running text.
- **Per-line detail is a native `<details>` each, with its own tracking
  picture inside.** No accordion primitive is vendored for the dropdown, because
  that element already is this behaviour with the keyboard and screen-reader
  support written; forty lines of four comparisons is not something to read all
  at once, so all of them start closed and open independently. Opening one shows
  `line.image_data` -- that one line drawn over the reels, its confirmed tiles
  ringed green and, on this picture only, the tile at `line.break_position`
  ringed red. The combined overlay above never shows red: several lines share
  it, and "here is where this one broke" is a question about one of them. A line
  that pays nothing still gets its own picture, because "how far did it get" is
  the more interesting question for exactly those lines. Note for tests: a
  paying line's name is shown twice -- its own box and its dropdown's header --
  so a bare `getByText("Line 1")` throws on ambiguity; scope to the `<summary>`
  to find the dropdown specifically. And a closed `<details>` keeps its content
  in the DOM, so assert with `toBeVisible()` rather than `toBeInTheDocument()`.
- **A line's colour comes from the server.** The swatch beside a line and the
  stroke drawn in both the combined overlay and that line's own picture have to
  be the same decision, and the only way to guarantee that is for the backend to
  own the palette and report each line's colour on the result -- so nothing in
  this directory picks a colour.

Its one piece of client state is the threshold override, held as a raw string for
the same reason the grid panel holds its trim that way: it is a number to be found
by trying it against a fixed split and reading `stats.matched_min` /
`stats.rejected_max` on the result.

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
