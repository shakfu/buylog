# TODO

## Critical

## High

- [x] One record for a purchase. `ordered` and `received` ensure a purchase order, so `buylog spend` sees it. See `docs/dev/261008-review.md`.

- [x] One price threshold, with a currency. Watchlist targets convert through `ForexService.to_usd`, same as price alerts. See `docs/dev/261008-review.md`.

## Medium

- [x] One place that answers what you might buy. Purchase lists of quotes are that answer. `considering` and a watchlist entry with no target price are written through the list. See `docs/dev/261008-review.md`.

- [x] One product classification that reports read. Compare and spend use category, otherwise the specification name, otherwise the product's tags. See `docs/dev/261008-review.md`.

## Low

- [x] Collapsed `Vendor.url` and `Vendor.website` into one stored link. See `docs/dev/261008-review.md`.

- [x] Looked at [crawl4ai](https://github.com/unclecode/crawl4ai) for AI web scraping. Findings in `docs/dev/261008-crawl4ai.md`. Do not add it.
