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
│   ├── paylines/           checks a split's tiles against the patterns that pay
│   ├── paytable/           the maths the running game loaded: symbols, reel
│   │                   strips, combos, and the payline set in play
│   ├── image-classifier/   trains ResNet34 or EfficientNet-B0 on the symbol
│   │                   artwork, then names the tiles of a split: the reels as
│   │                   two matrices (codes and display names), the ringed
│   │                   overlay, and a per-tile table -- or "unknown" below the
│   │                   confidence floor
│   └── analyze-spin/       drives one spin and grades it: live progress over a
│                       WebSocket, then the meter, the symbols the classifier
│                       read off the reels, and the lines those symbols paid
├── components/
│   ├── ui/                 shadcn/ui primitives (managed by the CLI)
│   ├── layout/             app shell: header + outlet
│   ├── api-error-alert.jsx consistent error rendering
│   ├── figure.jsx          a labelled number, for a row of them across a card
│   ├── stat-row.jsx        a labelled value, for a list of them down one
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

`features/paytable/` is the one slice that is a whole page rather than a
dashboard card, and the reason is the data: four tables and a 200-row reel strip
do not survive half a row. It is also the simplest possible slice -- one
parameterised query and no mutations, since nothing on the page changes anything
in the game.

Two conventions in it are worth copying. Its query key takes the paytable id
(`queryKeys.paytable.view(id)`), because inspecting another paytable of the same
game is a *different answer* rather than a refetch of this one, so switching back
is instant. And it has no `refetchInterval` at all: the answer only moves when
the game changes denomination, and each request parses close to a megabyte of
XML, so a standing poll would cost far more than it could ever notice.

It renders four of the response's cards and not all of it: the symbol table and
the scatter awards stay on the wire and off the page. Worth knowing when reading
`api.js`, whose JSDoc documents the whole payload -- the slice is deliberately
not a one-to-one rendering of it, and adding a card is a component, not an
endpoint.

Symbol *names* are the one thing on the page not read from the game's own files:
`math.xml` has no display text in any element, so they come from the backend's
`symbols` config block and travel alongside every code. The components do not
know or care -- name and code arrive on the same object.

Its one error case is worth copying too. `win_geometry.error` arrives on a
successful response -- one file of four could not be read -- so it renders as a
plain `Alert` beside the tables that are still true, not as an `ApiErrorAlert`,
which is for a request that failed.

`features/analyze-spin/` is the one slice whose live half is **not** react-query,
and the exception is worth understanding before copying it anywhere else. A run
publishes a snapshot on every step transition and every game-log line it reads --
a dozen or more in twenty seconds -- and polling an endpoint fast enough to catch
those is worse in every way than the WebSocket the backend already offers. So the
slice is split by what each half is *for*:

- `useSpinStream` is the run as it happens, straight off the socket into
  component state. Every frame is a whole `{active, run}` state rather than a
  delta, so a dropped frame or a subscriber that joins mid-spin is still correct,
  and the socket sends the current state on connect -- there is no gap to fill on
  mount. It reconnects on its own and surfaces `connected`, because a backend
  restart under a dev server is routine and a page that silently stopped updating
  looks exactly like a spin that silently stalled.
- `useSpinReport` is the *pictures*. The stream deliberately carries none: forty
  line images per push would make it the slowest part of a spin. So they are
  fetched once, from `GET /status?include_images=true`, at the moment the stream
  says a run has finished -- which is also the moment they exist.

`useSpinView` composes the two and is where the one subtlety lives: the stream can
already be on a new run while the report still holds the last one, so pictures are
used only when both name the same `run_id`. Showing the previous spin's reels
beside this spin's verdict would be worse than showing none.

Everything else in the slice follows the conventions above -- native `<details>`
for the per-line evidence and the log events, line colours taken from the server,
`spinFrameUrl()` as the one fetch outside `apiRequest` (a screenshot cannot unwrap
the envelope). Two rendering decisions are worth naming: the whole thirteen-step
sequence renders from the first frame, `pending` rows included, so a run that dies
on the press shows the nine things that never happened; and `pending`, `skipped`,
`completed` and `failed` are four visually distinct states, because a *skipped*
take-win on a losing spin and a *broken* one are different facts.

`spin-control-card` asks for the two per-run choices in the order they matter,
between the button and the run's own report: **Classifier** (which trained network
names the tiles, ResNet34 or EfficientNet-B0) and then **Record video**. The first
changes what the run *concludes* and the second only what it captures, which is
why it sits first. Its option list is hardcoded rather than fetched from the
image-classifier slice — the backend's own architecture type is the same closed
pair and `/start` 400s an unknown name, and a feature has to delete in one
directory. The first entry is both what the dropdown opens on and what the backend
would pick for a request naming none, so the visible default and the configured
one cannot drift.

The cards read down in the order the answers are produced, and the order is
load-bearing:

- `meter-validation-card` -- one row per screenshot, each with its three numbers
  and **the meter strip at the full width of the card**. A wrong reading is
  nearly always one misread digit, and at a third of the width a 3 read as an 8
  is not something a reader can see. The relations between the frames are still
  computed and still on the payload as `checks` (and `verdict` summarises them),
  but they are not a table worth scrolling past on the way to the award. Each
  figure does carry **its counterpart in the other unit**, greyed beside it: one
  number said twice, because an award is priced in credits while the glass may be
  drawing money, and the pair sitting together is what makes them comparable
  without arithmetic in the reader's head. What the
  header carries beside the verdict is **the units** -- `Cash $ · 2c`, from
  `meter.mode`/`meter.currency`/`meter.denomination` -- because every figure below
  it is otherwise ambiguous, and they decide the format too (money takes two
  decimals, a credit count is whole). Once for the card rather than per figure and
  per frame: the units are a property of the machine, and the backend already
  resolved one answer across every frame it read. The denomination is the odd one
  of the three -- it is what *converts* between the two units the mode picks
  between, and it comes from the game's log rather than off the strip.
- `reel-reading-card` -- **what landed**, from `run.reels`: the codes the image
  classifier read off the result screenshot, as two matrices (codes and display
  names), and every tile that came back *unnamed* with the candidate it leaned
  towards and its probability. Deliberately **no picture**: the ringed reels are
  still written beside the tiles, but the rings only said "the model was sure",
  which the grid says in codes. It has its own local `Grid` rather than importing
  the image classifier's, because a slice has to delete in one directory.
- `payline-validation-card` -- the lines those codes paid, ending in the **total
  credits**, taken off `expected` rather than summed here so the figure on the
  card and the one the verdict was reached with cannot differ.
- `award-comparison-card` -- the only card that is a *check* rather than a
  reading: those credits, times what a credit is worth, against the WIN cell.
  **Two rows, because the award is one multiplication** -- a paytable value is the
  award, not a per-line rate to be scaled by the stake. Still a ladder rather than
  one figure, because a wrong verdict is nearly always one of the two inputs
  rather than the pay. The rate row shows **`money_per_credit`, not the
  denomination** -- the log reports a 2c cabinet as `2` and a credit is worth
  `0.02`, so showing the value is what once made the ladder fail to reach its own
  total. The label rides in the hint, where a reader recognises it and no
  arithmetic can pick it up. **On a credit meter that row is not rendered at all**
  -- the glass is already counting what the paytable is denominated in, so there is
  no conversion, and a `× 0.02` over a total that did not use it is the same
  ladder-does-not-add-up problem in the other direction. Every row above the total
  is a step that actually happened.

  Under the ladder it carries **the spin as a table, in both units, always both**:
  before the spin, bet value, won, after the spin -- then the paytable's own claim
  and the WIN cell under a rule, because that is where a reading becomes a check.
  A table rather than two lists because the comparison a reader makes is *across*
  the units as often as down them, and one row holding both is what makes a credit
  award checkable against a cash meter at a glance. The column heading marks which
  side was **read**: the other is converted through the denomination and so cannot
  disagree with it. "After the spin" is the collected frame when there is one and
  the result frame when there is not -- take-win is skipped on a losing spin, and
  there the result screenshot already *is* the end of it.

  There is deliberately **no difference row**. The verdict badge is the answer and
  the two figures it compared are adjacent rows, so a signed delta was a third way
  of saying the same thing; the tolerance behind the badge is *not* self-evident
  from the pair, so that says itself in the caption. The stake per line is not
  shown at all either; it is still on the payload as `credits_per_line`.

  It takes the whole `meter` block rather than just a tolerance, because the table
  is built off `meter.readings` and the tolerance to quote depends on which unit
  the verdict was reached in. `award-comparison-card.test.jsx` pins the invariants:
  no conversion row when nothing is converted, both units accounted for with one
  marked as read, no difference row, and the collected-frame fallback.

The reading sits **above** the paylines because the paylines are read from it.
The confidence floor is set above where the classifier's classes separate, so a
tile it was only fairly sure of comes back unnamed -- and a payline through an
unnamed tile stops there. A spin whose lines report no run and whose reading shows
half its tiles blank is a floor question, and that only reads in that order.

Two things the payline card no longer shows, and it is worth knowing they were
removed on purpose rather than lost:

- **the strip of cosine similarity scores per line.** Tiles are compared by code
  now, so `steps[].similarity` is null and the card prints the two codes with `=`
  or `≠` between them instead. There is deliberately no 1.00 in the scores' place:
  a number would read as a measurement that was never taken.
- **the candidate award rows, and the credit *ranges* they fed.** Similarity could
  not name a symbol, so an award was every paytable row paying at that run length
  until the game's logged reel stops narrowed it. The classifier names the symbol,
  so a line resolves to one row and one number -- `expected.credits` and
  `expected.cash` are single figures where `credits_min`/`credits_max` and
  `cash_min`/`cash_max` used to be. Those spans were never a claim about the
  maths; they were the width of what the picture had failed to identify.

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
