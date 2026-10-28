# Valuation dashboard design

## Selected reference

Implement the selected vehicle valuation screenshot as a responsive desktop-first product dashboard. At a 1536px viewport, keep the content near 1320px wide, with a broad image hero, one compact search row, a two-part estimate and mileage analysis with a comparable listings table. Above 1600px, expand the shared search/results container to 94% of the viewport, capped at 140rem. Step the root type and control scale to 112.5% at 1600px, 125% at 2560px, and 150% at 3520px. Keep the estimate column at 24–32rem and give the listings the remaining width, supporting 3840×2160 without stretching explanatory text across the screen. Preserve the existing tablet and mobile layout.

## Physical scene and visual direction

A buyer checks a vehicle's value at a naturally lit home desk, so the interface is light, calm, and easy to scan. Use a cool neutral base, blue action and value accents, familiar sans-serif typography, fine dividers, restrained elevation, and compact data visualization. The supplied `public/valuation-hero.png` provides the mountain-road Toyota Camry image.

## Product behavior

Keep the form limited to vehicle and optional mileage; do not require users to specify location. Geographic valuation support remains in the API, with explicit broader-market fallback notices when a region is supplied. Keep price text green using the theme's green text token and use a larger confidence icon with hover, focus, and touch tooltip support.

Start with a blank vehicle and mileage form. One required autocomplete guides the user through year, make, then model. Suggestions come from `GET /api/makes` and the selected make's `GET /api/makes/{id}` response. Keep the selected make, model, and model-year database IDs. Cache successful responses in browser session storage for 900 seconds, deduplicate concurrent requests, and let failed requests retry.

The optional mileage field immediately follows the vehicle field and accepts a nonnegative safe integer, with or without correctly grouped commas. Blank mileage stays undefined. Blank mileage is omitted from the valuation request so the API can choose an available reference mileage.

Format mileage with grouping commas as the user types. Default to kilometers for Canada and miles for the United States, using the browser locale first and the same-origin `/api/location` country hint when available. Other regions default to miles. The user can switch units at any time; remember an explicit choice in local storage when available. Send requested mileage to the valuation API in miles; convert returned kilometer values to the selected display unit. Unit changes convert the current input and preserve the same distance and any active estimate. Location hints must never prompt for permission or override an explicit choice or an in-progress mileage entry.

Submit the selected vehicle to `GET /api/valuations/model-a`. Display its estimate, confidence, recorded listing summary, mileage chart, and positive-weight comparable listings. Paginate comparables in groups of 10, newest observations first. Distinguish insufficient evidence from unavailable services; never substitute demo valuations.

In development, Vite proxies relative `/api` requests to Flask at `127.0.0.1:5001`. Production deployment serves the frontend and API from one origin or uses an equivalent reverse proxy.

## Entry and query transitions

The initial search occupies the viewport, with a larger centered heading and form. On page load, type “What's the value of your vehicle?” once, then draw a flat, tapered blue brush-stroke underline beneath “your”. Reserve the full headline's layout throughout and expose the complete text to assistive technology immediately. A valid submission docks the search at the top. Measure the two layouts once and animate only CSS transforms over 850 ms with quintic ease-out; do not animate dimensions or add simulated network delays. Returning to vehicle selection restores the centered state without replaying the headline. Reduced-motion users see the completed headline and emphasis immediately.

Show valuation skeletons while the valuation request or docking transition is pending, then reveal the result over 220 ms. Catalog requests use the normal cache and time out after 15 seconds. Valuation requests time out after 20 seconds and expose a retry state. New vehicle input invalidates older pending results. Announce query status and disable repeat submission while busy. Reduced-motion preferences remove movement and shimmer without delaying results.
