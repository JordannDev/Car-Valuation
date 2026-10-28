# ValueApi frontend

React, TypeScript, Vite, and Astryx. Requires Node.js 22.13+ and pnpm.

## Source structure

- `src/App.tsx` and `src/main.tsx`: app composition and entry point.
- `src/components/`: UI components and charts.
- `src/hooks/`: React hooks.
- `src/services/`: API clients, response validation, caching, and browser preferences.
- `src/models/`: shared domain types. API valuation types stay inferred from their parser in `services/model-a.ts`.
- `src/utils/`: mileage, vehicle selection, and chart helpers.
- `src/styles/` and `src/themes/`: application styles and custom Astryx themes. The CLI-managed `src/astryx-themes.ts` stays at the source root so Astryx can discover it.
- `src/tests/`: unit tests, run with `pnpm test`.

## Run locally

```sh
cd src/frontend
pnpm install
pnpm dev
```

The Vite development server proxies relative `/api` requests to `http://127.0.0.1:5001`. Start the Flask API on that address before using the vehicle search. Deployments should serve the frontend and `/api` from the same origin, or provide an equivalent same-origin reverse proxy.

The vehicle field loads the make catalog, then fetches details only for the make being searched. Successful catalog responses are cached in browser session storage for 900 seconds. Submitting the form calls `GET /api/valuations/model-a` with the selected year, make, model, and optional mileage. Mileage is sent explicitly in miles, preserving the selected unit conversion. Estimates use USD, displayed beside the price; the mileage unit does not determine currency. Missing source currency and mileage units are interpreted as USD and miles, per the dataset assumptions. Stored explicit units remain authoritative, and source dates are required. There is no automatic CAD exchange-rate conversion.

Results identify whether the price is current, historical, or projected. Historical estimates preserve the calculated listing reference without time adjustment. Optional projections can be disabled; without configured assumptions, results stay historical. A projected range is a provisional uncertainty band, not a calibrated confidence interval. No import or audit timestamps are used as observation dates, and no demo prices or listings are rendered as valuation evidence.

## Build and checks

```sh
pnpm typecheck
pnpm build
pnpm test
pnpm exec astryx doctor --built dist
```

The mountain-road hero is `public/valuation-hero.png`.


Comparable listings appear beside the estimate, with original asking prices,
source observation dates, dealer city/region/country, and static photo skeletons
(the dataset has no vehicle photos). The list includes up to 100 positive-weight
comparables, newest first, with 10 rows per page. The displayed sample is capped
after sorting; the valuation and its charts still use the full API response.
Missing locations display “Location unavailable”. Mileage-adjusted prices
remain separate in the API.
