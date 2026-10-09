# Screenshots and segment results

The platform running on the SellerSprite exports in `data/raw` (Amazon US, one snapshot per market), captured
2026-10-09 from the frontend-v2 command center at desktop width.

- `00-segment-results.png` / `segment-results.html`: results for all 74 segments in the three markets (observed revenue
  floor, modelled estimate with 95% range, units, top brand, HHI, rating bar, quality gap, opportunity), the analyst's
  answer on the best opportunity, and the integrity findings per market.
- `01`–`19`: every main page (mission control, markets, each market's analysis, opportunity board, intelligence map,
  competitors, product detail, analyst, launch simulator, review queues, data quality, search, economics, sourcing,
  galaxy, methodology, and the Chinese interface).

The demand model did not pass hold-out validation in any market at the time of capture, so the observed floor is
the reliable revenue figure; modelled estimates are labelled as not validated on every page.
