# TODO

## Critical

## High

- [x] One record for a purchase. `ordered` and `received` ensure a purchase order, so `buylog spend` sees it. See `docs/dev/261008-review.md`.

- [x] One price threshold, with a currency. Watchlist targets convert through `ForexService.to_usd`, same as price alerts. See `docs/dev/261008-review.md`.

- [x] One price target table. `price_alert` and `watchlist` are `price_target`, which fires once. See `docs/dev/261011-model.md`, section 2.

- [x] Derive quote status from purchase orders and purchase lists, and drop `quote.status`. See `docs/dev/261011-model.md`, section 1.

- [x] A TUI price edit no longer fires a price target that `buylog check` then never reports. See `docs/dev/261011-model.md`, section 2.

## Medium

- [x] One place that answers what you might buy. Purchase lists of quotes are that answer. `considering` and a watchlist entry with no target price are written through the list. See `docs/dev/261008-review.md`.

- [x] One product classification that reports read. Compare and spend use category, otherwise the specification name, otherwise the product's tags. See `docs/dev/261008-review.md`.

## Low

- [x] Drop `quote.currency`, which is always `USD`. See `docs/dev/261011-model.md`, section 3.

- [x] One base currency per database, set by `buylog db init --base`. See `docs/dev/261011-model.md`, section 4.

- [ ] Record the entered currency on every quote, including quotes entered in the base, where `original_currency` is null today. See `docs/dev/261011-model.md`, section 4.

- [ ] Delete `Vendor.add_product`, or make it take a USD price. It stores an unconverted price as USD and has no app caller. See `docs/dev/261011-model.md`, section 3.

- [x] Collapsed `Vendor.url` and `Vendor.website` into one stored link. See `docs/dev/261008-review.md`.

- [x] Looked at [crawl4ai](https://github.com/unclecode/crawl4ai) for AI web scraping. Findings in `docs/dev/261008-crawl4ai.md`. Do not add it.
