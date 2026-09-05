# BMW Inventory Visualizer

A local-only React + Vite app for exploring the crawler's `results.json` —
filter, sort, map, annotate, and triage dealer inventory.

> **Local-only is not a style choice.** The annotations API (`/api/annotations`)
> and the geocoding proxy (`/api/geocode`) are Vite **dev-server** middleware
> (`vite.config.ts`, `configureServer`). They do not exist in `npm run build`
> output, and not in `npm run preview` either. Deploy the static build somewhere
> and the app silently loads with no notes, saves into a 404, and shows no
> distances or map pins. See
> [`../docs/330I_DEAL_FINDER.md`](../docs/330I_DEAL_FINDER.md) § "Hosting"
> before deploying anywhere.

![Tech: React 19, TypeScript, Vite, Tailwind, Leaflet]

## Features

- **Table / Map / Split views** — toggle from the header. Dark mode toggle too.
- **Filter panel** — full-text search (VIN, dealer, color, your notes) plus
  faceted filters for **max distance from home**, **condition** ("lease
  eligibility": all/new/not-new, since only a car titled new can be leased as
  new), **M Sport package**, **exterior color**, state, model, trim, year,
  mileage, days on lot, price, certified, platform, CarFax badge, and owner
  count. `DEFAULT_FILTERS` (`src/types.ts`) ships pre-set to this fork's hunt:
  750 miles of 47119, condition = New, 0–5,000 miles, M Sport required, red
  and white excluded. Every one of those is a single click to relax — "Reset
  all" restores exactly this state.
- **Vehicle detail panel** — opens inline on click; shows CarFax, NHTSA,
  dealer links, LLM ownership reasoning, etc.
- **Annotations** — cycle each VIN through `interesting → shortlisted →
  contacted → negotiating → purchased → pass` (plus explicit pass reasons),
  with a free-text comment and optional Leasehackr/listing links. Persisted
  to disk at `public/data/annotations.json` via a small Vite dev-server
  middleware (`/api/annotations`).
- **Sortable table** — every column including dealer and distance from your
  configured home ZIP (`ORIGIN_COORDS` in `src/utils/distance.ts`). The trim
  cell carries an M Sport pill: **M** = package found on the VDP, **–** = page
  fetched and nothing found, **?** = never fetched (unknown, check by hand).
- **Map view** — Leaflet + OpenStreetMap tiles. Dealer city/state pairs are
  geocoded via Nominatim and cached in `localStorage` so subsequent runs
  load instantly.

## Setup

```bash
cd visualizer
npm install
```

## Usage

### Normal workflow (after running the crawler)

```bash
npm run dev:sync    # copies ../crawler/results.json → public/data/results.json, then starts Vite
```

Vite will print a local URL (usually http://localhost:5173) — open it.

### Individual scripts

| Command | What it does |
| --- | --- |
| `npm run dev` | Start Vite dev server (uses whatever `public/data/results.json` is already there). |
| `npm run sync` | Copy latest `../crawler/results.json` into `public/data/`. |
| `npm run dev:sync` | `sync` then `dev`. |
| `npm run build` | Type-check and build to `dist/`. |
| `npm run preview` | Serve the production build locally. |
| `npm run lint` | Run ESLint. |

### Refreshing data while the dev server is running

Just run `npm run sync` in another terminal — Vite will hot-reload `results.json`
automatically on the next page refresh.

## How the pieces fit

```
crawler/results.json
        │   (npm run sync)
        ▼
visualizer/public/data/results.json   ← loaded by useVehicles()
visualizer/public/data/annotations.json  ← read/written by /api/annotations (dev only)
```

- `src/App.tsx` — top-level layout, view toggle, selected-vehicle state.
- `src/hooks/useVehicles.ts` — fetches `/data/results.json` and applies filters.
- `src/hooks/useAnnotations.ts` — VIN → `{tag, comment}` map, debounced save.
- `src/hooks/useGeocoder.ts` — Nominatim geocoding with `localStorage` cache.
- `src/hooks/useDarkMode.ts` — theme state (localStorage + OS preference).
- `src/components/FilterPanel.tsx` — left sidebar.
- `src/components/VehicleTable.tsx` — sortable table with inline tag/comment.
- `src/components/MapView.tsx` — Leaflet map, vehicles clustered by dealer.
- `src/components/VehicleDetail.tsx` — inline detail panel with full record.
- `vite.config.ts` — defines the `/api/annotations` GET/POST middleware that
  reads/writes `public/data/annotations.json`.

## Notes

- **Annotations only persist in `dev` / `preview`** because the `/api/annotations`
  endpoint is a Vite plugin. A static build (`npm run build` → `dist/`) will
  load existing annotations only if you serve it alongside that same endpoint.
- **Map geocoding** uses Nominatim's public endpoint, rate-limited to one
  request every 300 ms. First load of a fresh dataset may take a minute; after
  that everything is cached in `localStorage` under `bmw_dealer_geocache_v1`.
- `public/data/` is gitignored content-wise — the JSON there is derived from
  the crawler output and can always be regenerated.
