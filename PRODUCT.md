# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Vite + React + TypeScript (pnpm), Tailwind CSS, MapLibre GL JS + OpenFreeMap vector tiles (OpenStreetMap data; routes still draw when the basemap does not load), deck.gl (optional flow animation), Recharts, zustand, Vitest. Events and predictions come from a small Python server (`ml/olpark_demand/server.py`) that calls the park's public calendar API and runs the demand model; all data access goes through `src/api/`, and the mock JSON in `src/data/` is the fallback when the server is unreachable (labelled 시연용 on screen). The user asked to produce design files first, before code.

## Users

Primary: venue operations staff at Olympic Park (Seoul) running crowd egress after a performance, watching the dashboard on an operations-center monitor for long stretches. Secondary: judges at the KSPODO (한국체육산업개발) public-data competition, where the product is demonstrated.

## Product Purpose

After a performance ends, audiences leave venue exits for subway entrances (Olympic Park, Hanseong Baekje, Mongchontoseong stations) and parking lots for vehicle exits. OLPARK FLOW computes, minute by minute, where queues form, shows them on a map, and compares the current operating plan against a dispersal plan (guidance direction, exit assignment, staff placement). The walking simulation is the centrepiece. The operator picks a show in a popup; the map then shows every walkway of the park coloured by how congested it gets. Pressing a walkway opens a small box where the operator closes it, places an obstacle (keeps part of the width) or puts safety staff there, and the results show the change against the plan with nothing placed. Below the map: the briefing for the chosen show (who leaves by which mode, when the station is busy), a time-ordered action list, and how earlier day-before forecasts compared with the counts. Success: an operator can try where to place staff and barriers for an evening, see which walkways jam, and tell how far to trust the numbers.

## Positioning

The numbers come from a real, tested 1-minute simulation (gamma egress curve, bottleneck capacity, queue spillback, pedestrian/vehicle crossing sharing), not illustrative charts. Every figure is labeled by origin: predicted, input, assumed, or observed.

## Operating Context

Decision made shortly before and during egress (0–60 min after the show ends). Outputs: field guidance sheet (copyable), saved plan records, exported JSON/PNG, auto-play demo of 0→60 min.

## Capabilities and Constraints

- Every on-screen number comes from engine output; no hard-coded figures.
- Assumed and mock values are labeled "가정" / "시연용" on screen or in tooltips.
- Staff effect is modeled only through guidance compliance rate; it never increases path capacity.
- All UI copy in Korean. Target 1440px width, must hold at 1280px.
- Status colors always paired with an icon and label.

## Brand Commitments

Name "OLPARK FLOW", subtitle "운영 판단 플랫폼". Binding visual reference: `reference.png` (layout, spacing, color, components to be followed closely). Tokens specified by the user: Pretendard; sidebar #1E3B36; accent #2C6B5F; route colors A #2F6E60, B #C9A26B, C #8EA2C0; status 여유 #3E8E6E / 보통 #D9B44A / 병목 #E07B39 / 위험 #C8423B; cards radius 12px, border-led, tabular numerals.

## Evidence on Hand

- `reference.png`: screen reference.
- Events and forecasts come from the server; the bundled JSON in `src/data/` is mock. Performances are named "공연 A/B/C"; no real artist names, logos, or photos. Network geometry is labeled "시연용 가정값".
- Data sources to credit: KSPODO performance info, vehicle entry/exit link, real-time parking, parking congestion; Seoul real-time population and pedestrian network; Seoul Metro boardings.

## Product Principles

1. Calculation first: a figure the engine did not produce does not appear.
2. Honest provenance: show where each value comes from and how uncertain it is.
3. Actionable over decorative: every view ends in something an operator can do in the field.
4. Readable at a glance on a monitor, during a time-pressured window.

## Accessibility & Inclusion

Status is never conveyed by color alone. Long-session monitor viewing: favor legible sizes and contrast.
