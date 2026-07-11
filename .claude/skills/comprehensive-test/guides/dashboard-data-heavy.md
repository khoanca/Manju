# Test: Dashboard / Data-Heavy

## VISUALIZATION
- Visual regression (screenshot diff) as primary correctness — DOM positions encode data
- Pixel tolerance 0.1% (Percy/Chromatic/ocular)
- Unit test data→visual transform separately: axis scales, labels, colors
- Interactions: tooltips/zoom/pan/drill-down/legend toggles — values match data
- Edge data: empty/single/negative/huge/NaN/null/identical
- Cross-browser: SVG/Canvas varies — Chrome+Firefox+Safari minimum

## VIRTUALIZATION
Required for 50+ rows without pagination.

| Metric | Target |
|---|---|
| DOM nodes | Constant regardless of data size |
| Initial render | <200ms |
| Scroll FPS | >55fps with 10K+ rows |
| Memory | Stable over time |

- Boundary: items entering/leaving viewport render/unmount correctly
- Rapid scroll (fling): no blank frames
- Variable-height rows: correct offsets+total height
- Prod-representative data (not uniform synthetic)
- Stack: TanStack Query+Table+Virtual

## FILTER & SEARCH
- Multiple faceted filters simultaneously — results satisfy ALL constraints
- Remove 1 filter → broadens correctly
- Persist: refresh/back-forward/URL share (encode in query params)
- Debounced search: rapid typing≠excessive API calls, only final fires
- Empty/zero-result: meaningful state, not blank
- Clear all → full dataset
- Special chars + injection: `<>&"'/\`, SQL/NoSQL, Unicode, emoji, long strings
- Perf: client <100ms on 10K; server API <500ms

## EXPORT/IMPORT
**3-layer:** 1.File integrity (downloads, non-empty, opens) 2.Structure (headers, columns, escaping) 3.Content (compare vs source row-by-row)

- CSV UTF-8 encoding (commas/newlines/quotes/non-Latin)
- Export under active filters = filtered view
- Large: 100K+ rows complete, no crash/timeout, memory OK
- Import round-trip: export→import→verify no loss
- PDF: `pdf-parse` extract text, assert content/layout/page breaks

## REAL-TIME UPDATES
- Reconnect with backoff, no data loss during disconnect
- Test SSE and WebSocket independently
- Dedup on reconnect (idempotent handling)
- Throttle renders (10fps cap) while maintaining accuracy
- Prod-like network (proxy buffering, latency, packet loss)
- Stale indicators: live→stale→reconnecting→live

## PAGINATION
**Both:** First/last/empty/single-item/exceeds-total pages

**Offset:** Data consistency under concurrent mutations (skip/dup possible) · OFFSET 100K is slow

**Cursor:** Deleted cursor→400/410 (never silently skip) · constant perf regardless of position · `hasNextPage/hasPreviousPage` accuracy · concurrent users different page sizes = no interference

## AGGREGATION
- Known-answer datasets with pre-calculated results for every aggregation
- Float precision: `0.1+0.2`≠`0.30000000000000004` in display — fixed-point/decimal
- Aggregation across filtered subsets recalculates correctly
- Cross-validate with DB (same query, compare output)
- NULL handling: avg([10,NULL,20])=15 (or defined behavior)
- Timezone: date-based aggregations consistent regardless of user TZ

## PERFORMANCE
| Metric | Target |
|---|---|
| Initial load | <3s |
| Filter apply | <500ms |
| Chart re-render | <200ms |
| Scroll 10K rows | 60fps |

- Prod-scale data (1M if prod=1M)
- Memory profiling 5min interaction: no unbounded growth
- Concurrent users: API <2x baseline
- TTI+LCP (Core Web Vitals)
- 3G: usable with loading states

## RESPONSIVE
- Breakpoints: 360/600/900/1200/1920px AND sweep between
- Visual regression at each breakpoint
- Touch on mobile: chart tooltips/filter dropdowns/date pickers
- Critical data visible at all sizes
- Orientation change: no layout break or state loss
- Component-scoped breakpoints (container queries)
