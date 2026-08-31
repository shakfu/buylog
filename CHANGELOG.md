# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0]

### Upgrading

This release changes the schema (a `(code, date)` unique constraint on `forex`, new `quote.source_url` and `purchase_order.grand_total_usd` columns, and the `audit_log` table that was never created). Run:

```sh
buylog migrate --dry-run   # review
buylog migrate
```

Existing databases keep their data. If `migrate` reports a duplicate-key failure on `forex`, you have more than one rate recorded for the same currency and date; remove the extras and re-run. Foreign keys are now enforced, so deleting a vendor or product that a purchase order references is refused rather than silently leaving a dangling reference.

### Fixed

- **`audit_log` table was never created, poisoning the database session** - `AuditLog` is declared in `audit.py`, which was only imported lazily (via `services.py`), so it was not registered on `Base.metadata` when `create_all()` ran at CLI/TUI import time. Every `buylog add --brand X` then failed to write its audit record with `no such table: audit_log`; the swallowed exception left the session in a rolled-back state, so all later operations raised `PendingRollbackError` - breaking every subsequent action in the TUI (one long-lived session) and any chained CLI command.

  - Added `models.init_db(engine)`, which imports `audit` before `create_all()`; `cli.py`, `tui.py`, and the test fixtures now use it instead of calling `Base.metadata.create_all()` directly.

  - `AuditService.log_action` now rolls the session back when the audit write fails, so a failed audit can no longer break unrelated operations.

  - `audit.py` no longer creates `~/.buylog/audit.log` and opens a file handler at import time; the handler is attached on first audit write.

- **Foreign-currency quotes crashed once a currency had two dated rates** - the CLI, the TUI, `QuoteService.create` and `ImportService` each looked up forex rates with `select(Forex).where(Forex.code == ...).scalar_one_or_none()`, with no `limit(1)`. Since `add-fx` allows one rate per currency per date, adding a second EUR rate made every EUR quote raise `MultipleResultsFound`.

  - Added `ForexService.to_usd()`, now the single place currency normalization happens; the CLI, TUI, `QuoteService.create` and the seed/import paths all route through it (replacing four divergent copies of the conversion logic).

  - Added a database-level `UniqueConstraint("code", "date")` on `forex`, so duplicate rates are rejected by the schema and not just by service validation.

- **`buylog migrate` silently produced a schema missing two join tables** - `generate_migrations` emitted an inline `PRIMARY KEY` on every primary-key column, so the composite-key join tables `vendor_brand` and `purchase_list_quote` produced DDL SQLite rejects (`table ... has more than one primary key`). `run_migrations` swallowed the error and still reported "Applied 18 migration(s)", leaving a database with no vendor-brand or purchase-list-quote relationships.

  - Composite primary keys are now emitted as a table-level `PRIMARY KEY (...)` clause.

  - `run_migrations` now reports how many of the statements actually applied, logs failures at error level, returns only the applied statements, and raises the new `MigrationError` if any failed. `buylog migrate` reports the failure and exits non-zero instead of claiming success.

### Added

- **`buylog check` and a notification layer** - price alerts and watchlist targets were only visible inside the app. `buylog check` evaluates both against current best prices and reports what fired, then delivers a notification. It is built for cron: no interactive input, silent when `--quiet` and nothing triggered, non-zero exit on failure.

  - New `notify.py` with five channels - `stdout`, `ntfy`, `webhook`, `desktop` (notify-send / terminal-notifier / osascript) and `email` (SMTP) - selected with `BUYLOG_NOTIFY` or `--notify` and configured entirely by environment variable. A selected-but-unconfigured channel names the variable it needs instead of failing silently, and one channel failing does not stop the others.

  - New `MonitorService.check()` behind the command.

  - `buylog check --refresh-fx --refresh-scrapes` refreshes rates and re-scrapes tracked URLs before evaluating, so a single cron entry keeps everything current.

- **`buylog fx refresh`** - forex rates had to be entered by hand, which made the multi-currency support only as trustworthy as the user's upkeep. `fx refresh` pulls current rates from Frankfurter (free, no API key, ECB reference rates) and stores them. With no arguments it refreshes every currency the user's vendors actually use. `buylog fx list` lists what is on file.

- **`buylog scrape refresh`** - re-fetches prices for quotes that record where they came from and records any change in price history, which drives the trend indicators, sparklines and alerts automatically. Quotes created by `buylog scrape quote` now store the page they came from in the new `Quote.source_url`; `--list` shows what is tracked, `--quote-id` refreshes one. A dead link is reported and skipped rather than stopping the run.

- **`buylog spend` and the `spend-analysis` report** - purchase orders recorded what was bought but nothing reported on it. New `SpendAnalyticsService` and command covering spend by month, by vendor and by category, plus a variance report answering "did I buy at the best recorded price" for every order. `--from`/`--to` restrict the period, and `buylog report spend-analysis` renders the same figures as a self-contained HTML page.

- **Source-currency display.** Quotes are normalized to USD at entry, but the price the user actually typed was dropped from every view, forcing mental arithmetic on anyone outside the US. Added `Quote.source_price` and `Quote.display_price`, and surfaced the original value and currency in `buylog list quotes`, the TUI quotes table, the price-comparison report, and the quotes CSV export (new `original_price` and `original_currency` columns).

- `ProductService.create` accepts a `category`, and `buylog add --product` accepts `--category`; the field existed on the model but nothing could set it.

- **`buylog po` commands** - purchase orders were fully implemented in `PurchaseOrderService` and reachable from the TUI, but the CLI exposed them only through `import`/`export`. The README documented them as if they existed.

  - `buylog po create PO-001 --vendor V --product P --price 999.99 [--quantity N]` (also `--from-quote ID`, `--shipping`, `--tax`, `--order-date`, `--expected-delivery`, `--invoice`, `--notes`)

  - `buylog po list [--status S] [--vendor V] [--limit N]`

  - `buylog po show PO-001`

  - `buylog po update PO-001 --status ordered [--delivery-date YYYY-MM-DD]`

- **`buylog spec` commands** - likewise for `SpecificationService`.

  - `buylog spec create "Camera Spec" [--description D]`

  - `buylog spec add-feature "Camera Spec" Resolution [--type text|number|boolean]
    [--unit U] [--required] [--min N] [--max N]`

  - `buylog spec list [--limit N]`, `buylog spec show NAME`, `buylog spec delete NAME`

- `tests/test_schema.py` - coverage for `models.init_db` and `migrate`, which previously had none: the audit table is created, a migrated database matches `Base.metadata` table-for-table and column-for-column (join tables included), migrations are idempotent, and a dry run changes nothing.

- `tests/test_cli.py` - end-to-end smoke tests for `cli.main()`, which previously had no coverage at all: entity creation, the new `po`/`spec` commands, exit codes, non-interactive confirmation, and a regression test that adding a brand no longer breaks later commands.

- `tests/test_tui.py` - smoke tests driving the real Textual app through its test pilot: it boots, every tab builds its columns, seeded rows appear, and the service-backed add/edit/delete handlers work. Includes regressions for the two bugs that hit the TUI hardest - a brand add poisoning the app's long-lived session, and a second forex rate breaking foreign-currency quotes.

- `tests/test_audit_config.py` - coverage for `audit.py` and `config.py`: audit rows are written, brand creation is audited, a failed audit write leaves the session usable, importing `audit` no longer creates a log file, the database path follows the environment, and engines enable SQLite foreign keys.

- `tests/test_notify.py` and `tests/test_automation.py` - coverage for the notification channels (each with its transport stubbed), forex refresh, the monitor, scrape refresh and the spend reports.

- Tests for the new service `update`/`delete` methods and for the delete cascades, including regression coverage for the orphaned `quote_history` rows, the currency handling in import/scraping/alerts/purchase orders, and the Excel example-row matching.

Test count went from 286 to 424 and statement coverage from 39% to 58%; the modules that previously had no coverage at all now sit at 92% (`migrate`), 91% (`config`), 77% (`audit`), 41% (`cli`) and 26% (`tui`).

### Changed

- **Build and dev toolchain now works.**

  - `make coverage` pointed at `buylog/tests`, which does not exist, and `pytest-cov` was not installed - it failed with "unrecognized arguments". Added `pytest-cov` to the dev dependencies and corrected the path to `tests`.

  - `make diagram` ran `uv run python buylog/models.py` (wrong path, and the `__main__` block there is a demo that never produced a diagram). Added `scripts/gen_diagram.py`, which writes `doc/er_model.md` (Mermaid, renders inline on GitHub), `doc/er_model.er` and `doc/er_model.dot` from `Base.metadata`, plus `doc/er_model.svg` when Graphviz is installed - and says so plainly when it is not, instead of failing silently.

  - `make version` / `bump` / `tag` crashed with `FileNotFoundError`: `scripts/version.py` resolved its paths against `scripts/` rather than the project root, looking for `scripts/src/buylog/__init__.py`.

  - Removed the stale `doc/er_model.svg`, which showed 6 of the 19 tables, a `forex.units_per_usd` column that is a Python property, and a `quote.date_created` column that is named `created_at`. `make diagram` regenerates it wherever Graphviz is available.

- **All CLI and TUI writes now go through the service layer.** Early CLI commands and several TUI handlers wrote to the ORM directly, duplicating - and diverging from - the services, which is how the two currency bugs above ended up in four places. `add`, `add-fx`, `update` and `delete` in the CLI, and the brand/product/ quote/forex add, inline-edit and delete handlers in the TUI, now call the services and get their validation, error model and audit logging.

  - Added the missing service methods the CLI needed: `ProductService.update`/ `delete`, `VendorService.update`/`delete`, and `QuoteService.get_by_id`/ `update`/`delete`.

  - `QuoteService.create` now accepts `shipping_cost` and `tax_rate` and records the "create" price-history entry itself, instead of leaving both to callers.

  - Added the `commit_or_rollback` context manager, replacing the copy-pasted `try / commit / except IntegrityError / except SQLAlchemyError / rollback / raise` block.

- **Import, scraping, alerts and purchase orders are now currency-correct.**

  - `ImportService._import_quote_row` stored a foreign-currency price labelled `USD` whenever no forex rate was on file, silently corrupting every later comparison. It now normalizes through `ForexService.to_usd` and rejects the row when no rate exists, matching `QuoteService.create`.

  - A row that failed part-way through left the vendor/product it had already flushed to be committed by the next successful row. Each row now rolls back its own work before the error propagates.

  - `ScraperService.create_quote_from_scrape` hardcoded `currency="USD"` and ignored the currency its own `scrape_price` returned, so a scraped GBP or EUR price was recorded as USD. It now converts the scraped price and keeps the original value and currency.

  - `PriceAlertService.check_alerts` compared the USD price against `threshold_value` regardless of `threshold_currency`. Non-USD thresholds are now converted before comparison; an alert whose currency has no rate is skipped with a warning rather than silently miscompared.

  - `PurchaseOrderService.create` defaulted `currency` to `"USD"`, labelling a EUR vendor's prices as USD. It now defaults to the vendor's currency and records `grand_total_usd` alongside the native total, so orders in different currencies can be compared and summed.

- **Shipping cost was never currency-converted.** `QuoteService.create` normalized the price to USD but stored `shipping_cost` verbatim while labelling the quote `USD`, so `total_cost` added e.g. EUR shipping onto a USD base - a EUR 100 quote with EUR 20 shipping at 1.10 totalled $130.00 instead of $132.00. Wrong figures propagated into `PurchaseList.total_value`, the vendor-analysis report and `PurchaseOrderService.create_from_quote`. Shipping is now normalized in `create`, `update` and the import path.

- **The scraper claimed USD for prices whose currency it never read.** The meta-tag extractor hardcoded `"USD"` and ignored the companion `product:price:currency` / `og:price:currency` tags, and the JSON-LD and regex paths defaulted to `"USD"` when the page said nothing. Extractors now report `None` for "the page did not say", which lets the caller fall back to the vendor's currency, and the meta-tag path reads the currency tag.

- **Spend reports dropped undated orders and counted cancelled ones.** `SpendAnalyticsService` filtered on the nullable `order_date` column while bucketing by `created_at`, so an order with no order date appeared in `buylog spend` but vanished the moment `--from`/`--to` was passed. Filtering and bucketing now both use the same effective date. Cancelled orders - money explicitly never committed - were also counted in every total and in the average; they are now excluded by default.

- **A partially-failed notification was reported as success.** `notify.send()` returned only the channels that succeeded, so `BUYLOG_NOTIFY=stdout,email` with SMTP down printed `Notified via: stdout` and exited 0 - a cron job could lose every email alert forever while reporting fine. `send()` now returns `(delivered, failures)` and `buylog check` reports each failure and exits 1.

- **The scrape path mutated `vendor.url` outside any transaction**, so a rejected quote left a dirty session behind and creating a product committed the unrelated URL change as a side effect. The update now happens inside the quote's own transaction.

- **The purchase-order template's example row was only recognized on the day it was generated.** Its PO number and order date embed the generation date and were compared exactly, so a template generated one day and imported the next created a bogus `PO-…-001 / Amazon / iPhone 15` order. The two dated fields are now matched by shape and the rest exactly.

- `commit_or_rollback` now also rolls back when the block raises a non-database error, instead of leaving the caller's mutations pending for whatever writes next.

- **Excel import no longer drops legitimate rows.** The importers skipped any row by name - a quote whose vendor was `Amazon`, a specification called `Camera Spec`, a PO number starting `PO-` from Amazon - to avoid re-importing the template's example row. Example rows are now defined once in `EXAMPLE_ROWS`/`purchase_order_example_row()`, shared by the generators and the importers, and a row is skipped only when it is the first data row and matches the example exactly.

- **The CLI is usable from scripts and cron.**

  - `main()` now takes an `argv` list and returns an exit code: 0 on success, 1 on a handled error, 130 on interrupt. Every failure path previously exited 0, so CI and cron could not detect failures. All 59 error and usage branches were converted, so `buylog compare --product NoSuchProduct`, a failed `buylog import`, and every bare subcommand (`buylog history`, `buylog scrape`, ...) now exit non-zero consistently.

  - Confirmation prompts no longer block on `input()` when stdin is not a terminal. A piped or cron invocation raised `EOFError` and surfaced as "Unexpected error occurred: EOF when reading a line"; it now reports what it needed and suggests the new global `--yes` / `-y` flag.

  - `buylog add --vendor X --quote 600` created the vendor and silently discarded the price. It now reports that `--quote` also requires `--product` and exits 1.

- **Deletes no longer silently destroy or strand purchase orders.** With foreign keys now enforced, deleting a vendor or product that a purchase order references would fail with a bare "FOREIGN KEY constraint failed". `VendorService.delete` and `ProductService.delete` now refuse up front, naming the orders in the way; `QuoteService.delete` detaches the optional `purchase_order.quote_id` link instead, so the order survives with its own recorded prices.

- **Deletes no longer leave orphaned rows.** `quote_history` had no relationship from `Quote` at all, so deleting a quote stranded its price history; the same applied to a product's alerts and watchlist entries. Added `cascade="all, delete-orphan"` for `Quote.history`, `Product.quotes`, `Product.price_alerts`, `Product.watchlist_entries` and `Vendor.quotes`, and turned on SQLite foreign-key enforcement (`PRAGMA foreign_keys=ON`), which is off by default and had been letting dangling references through. `Brand.products` deliberately does *not* cascade - that would chain through to every quote and wipe the catalogue - so `BrandService.delete` refuses while a brand still has products, naming them. `buylog delete` now states how many quotes a deletion will take with it before asking for confirmation.

- **README now matches the CLI.** Corrected the documented `import`/`export` syntax (`buylog import FILE [-t TABLE]` and `buylog export [-t TABLE] [-o PATH]
  [--format xlsx|csv]`; the documented `--file`, `--filter` and `--no-create` flags
  do not exist), removed `cache.py` from the architecture tree (no such module), and dropped the inaccurate "factory pattern" / Factory Boy testing claims.

## [0.1.9]

### Added

- **Excel Import/Export** - Full openpyxl integration for data management

  - **Export to Excel**

    - `buylog export -t vendors -o vendors.xlsx` - Export single table

    - `buylog export -o database.xlsx` - Export all tables to single file

    - Exports: brands, products, vendors, quotes, specifications, purchase_orders

    - Auto-generated default filenames (e.g., `buylog-vendors.xlsx`)

  - **Import from Excel**

    - `buylog import vendors.xlsx -t vendors` - Import from Excel file

    - Supports: vendors, products, quotes, specifications, purchase_orders

    - Data validation dropdowns in templates

    - Error reporting with row numbers

  - **Template Generation**

    - `buylog template -t vendors -f xlsx` - Generate Excel template

    - `buylog template -t vendors -f yaml` - Generate YAML template

    - `buylog template -t specs -f json` - Generate JSON template

    - Auto-generated filenames (e.g., `vendors-template.xlsx`)

    - Templates include example data and data validation

- **Purchase Order Model** - Track committed purchases separately from quotes

  - Fields: po_number, vendor, product, quantity, unit_price, currency

  - Calculated fields: total_amount, shipping_cost, tax, grand_total

  - Status tracking: pending, approved, ordered, shipped, received, cancelled

  - Date fields: order_date, expected_delivery, actual_delivery

  - Optional: invoice_number, notes, linked quote

- **Specification System** - Define structured product attributes

  - `Specification` model with name and description

  - `SpecificationFeature` - Define features with data types (text/number/boolean)

  - `ProductFeature` - Store feature values for products

  - Support for units, required flags, min/max validation

  - Products can be linked to specifications

- **Enhanced Vendor Fields** - Extended contact and business information

  - Contact: contact_person, email, phone, website

  - Address: address_line1, address_line2, city, state, postal_code, country

  - Business: tax_id, payment_terms

- **TUI Enhancements**

  - New **Purchase Orders** tab (Ctrl+9)

  - New **Specifications** tab (Ctrl+0)

  - Fullscreen forms for all data entry (replaces modal dialogs)

- **New Services**

  - `PurchaseOrderService` - PO CRUD, status transitions, create from quote

  - `SpecificationService` - Specification and feature management

  - `ProductFeatureService` - Product feature value management

- **Database Migration** - Automatic schema updates

  - `buylog migrate` - Apply pending migrations

  - `buylog migrate --dry-run` - Preview SQL without executing

## [0.1.8]

### Added

- **HTML Report Generation** - Generate professional HTML reports for analysis

  - `buylog report price-comparison [--filter TERM] [--output FILE]` - Compare prices across vendors

    - Highlights best prices in green

    - Shows potential savings per product

    - Displays vendor discount codes

  - `buylog report purchase-summary [--output FILE]` - Summary grouped by status

    - Groups quotes by considering/ordered/received

    - Totals per status and grand total

    - Color-coded status rows

  - `buylog report vendor-analysis [--output FILE]` - Vendor statistics

    - Quote counts and average prices per vendor

    - Currency breakdown

    - Discount code availability

  - Self-contained CSS (no external dependencies)

  - Print-friendly styles

  - Responsive tables

- **New Services**

  - `ReportService` - HTML report generation with three preset types

## [0.1.7]

### Added

- **Integration Features** - Connect with external tools and data

  - **Clipboard Support** - Copy data to clipboard

    - `buylog clipboard quote <id>` - Copy quote details to clipboard

    - `buylog clipboard product <name>` - Copy product info to clipboard

    - `buylog clipboard vendor <name>` - Copy vendor info (including URL) to clipboard

    - TUI: Press `y` to copy selected item to clipboard

  - **Vendor URL Management** - Store and open vendor URLs

    - `buylog vendor-url set <vendor> <url>` - Set vendor URL

    - `buylog vendor-url open <vendor>` - Open vendor URL in browser

    - `buylog vendor-url clear <vendor>` - Clear vendor URL

    - TUI: Press `o` on vendors tab to open selected vendor's URL

    - New "URL" column in vendors table showing link indicator

  - **Receipt Attachments** - Link receipt files to quotes

    - `buylog receipt attach <quote_id> <file_path>` - Attach receipt to quote

    - `buylog receipt open <quote_id>` - Open receipt file

    - `buylog receipt detach <quote_id>` - Remove receipt from quote

    - `buylog receipt list` - List all quotes with receipts

  - **Web Scraping** - Fetch product prices from URLs

    - `buylog scrape url <url>` - Scrape price from URL

    - `buylog scrape quote <url> --vendor <name> --product <name>` - Create quote from scraped URL

    - Supports JSON-LD structured data and common price patterns

    - Auto-sets vendor URL from scraped page

- **New Model Fields**

  - `Vendor.url` - Store vendor website URL

  - `Quote.receipt_path` - Store path to receipt file

- **New Services**

  - `ClipboardService` - Copy entity data to clipboard

  - `VendorURLService` - Manage vendor URLs

  - `ReceiptService` - Attach/open/detach receipt files

  - `ScraperService` - Web scraping for product prices

- **Tests** - 38 new tests for integration features (203 total tests)

## [0.1.6]

### Added

- **Data Management Features** - Import, export, backup, and deduplication

  - **CSV/JSON Import** - Bulk import quotes from files

    - `buylog import quotes data.csv` - Import from CSV

    - `buylog import quotes data.json` - Import from JSON

    - Auto-creates missing vendors, products, and brands

    - Use `--no-create` to require existing entities

  - **Export Reports** - Export data to CSV or Markdown

    - `buylog export quotes --format csv --file quotes.csv`

    - `buylog export quotes --format markdown --file report.md`

    - `buylog export products --file products.csv`

    - `buylog export vendors --file vendors.csv`

    - Supports `--filter` for filtered exports

  - **Backup/Restore** - Database backup management

    - `buylog backup` - Create timestamped backup

    - `buylog backup --output my-backup.db` - Custom backup path

    - `buylog restore my-backup.db` - Restore from backup

    - `buylog backups` - List available backups

  - **Merge Duplicates** - Detect and merge similar entities

    - `buylog duplicates vendors` - Find similar vendors

    - `buylog duplicates products` - Find similar products

    - `buylog duplicates merge-vendors 1 2 3` - Merge vendors 2,3 into 1

    - `buylog duplicates merge-products 1 2 3` - Merge products 2,3 into 1

    - Uses Jaccard similarity with configurable threshold

- **New Services**

  - `ImportService` - CSV/JSON quote import with entity creation

  - `ExportService` - CSV/Markdown export for quotes, products, vendors

  - `BackupService` - Database backup, restore, and listing

  - `DeduplicationService` - Find similar entities and merge them

- **Tests** - 25 new tests for data management (165 total tests)

## [0.1.5]

### Added

- **Workflow Features** - Support the full purchasing lifecycle

  - **Purchase Lists** - Group quotes into named shopping lists

    - Create lists: `buylog purchase-list create "My List" --description "Weekend shopping"`

    - Add quotes: `buylog purchase-list add "My List" 123`

    - View list: `buylog purchase-list show "My List"`

    - Total value calculation for each list

  - **Status Tracking** - Mark quotes as "considering", "ordered", "received"

    - Set status: `buylog status set 123 ordered`

    - List by status: `buylog status list ordered`

    - Color-coded status in TUI (cyan/yellow/green)

  - **Notes** - Add freeform notes to any entity

    - Add note: `buylog note add product 1 "Great product!"`

    - List notes: `buylog note list product 1`

  - **Tags** - Categorize entities with tags

    - Add tag: `buylog tag add "sale" product 1`

    - Search by tag: `buylog tag search "sale"`

  - **Watchlist** - Track products for price monitoring

    - Add to watchlist: `buylog watchlist add "iPhone 15" --target-price 800`

    - View watchlist: `buylog watchlist list`

- **TUI Enhancements for Workflow**

  - New **Lists** tab for managing purchase lists (Ctrl+7)

  - New **Watchlist** tab for monitoring products (Ctrl+8)

  - **Status column** in Quotes tab with color coding

  - Press `t` to set quote status

  - Press `w` to add product to watchlist

- **New Models**

  - `PurchaseList` - Named shopping lists with quote associations

  - `Note` - Polymorphic notes attachable to any entity

  - `Tag` and `EntityTag` - Flexible tagging system

  - `Watchlist` - Product monitoring with target prices

- **New Services**

  - `PurchaseListService` - List CRUD, add/remove quotes

  - `NoteService` - Note CRUD for any entity

  - `TagService` - Tag management and entity tagging

  - `WatchlistService` - Watchlist management

- **Tests** - 40 new tests for workflow features (140 total tests)

## [0.1.4]

### Added

- **Price Comparison** - Compare prices across vendors for better purchasing decisions

  - Compare by exact product name: `buylog compare --product "iPhone 15"`

  - Compare by search term: `buylog compare --search "iPhone"` (matches all iPhone models)

  - Compare by category: `buylog compare --category "Mobile Phones"`

  - Compare by brand: `buylog compare --brand "Apple"`

  - Shows best/worst prices, average, and potential savings

  - CLI commands for all comparison types

- **TUI Comparison Modal** - Press `c` to open comparison dialog

  - Dropdown to select comparison type (Product, Search, Category, Brand)

  - Dynamic input field changes based on selection

  - Dropdown for Product/Category/Brand, text input for Search

  - Results displayed with color-coded best/worst prices

- **Product Categories** - Organize products by type

  - New `category` field on Product model

  - Set categories: `buylog category set "iPhone 15" "Mobile Phones"`

  - List categories: `buylog category list`

  - Seed data includes categories (Mobile Phones, Laptops, Tablets, etc.)

- **ComparisonService** - New service for price comparison logic

  - `compare_product()` - Compare prices for a single product

  - `compare_by_search()` - Compare products matching a search term

  - `compare_by_category()` - Compare products in a category

  - `compare_by_brand()` - Compare products from a brand

  - `get_categories()` - List all product categories

  - `set_product_category()` - Set/update product category

- **Tests** - 12 new tests for comparison functionality (100 total tests)

## [0.1.3]

### Added

- **TUI Enhancements** - Improved daily usability

  - **Tab Switching** - `Ctrl+1-6` to switch tabs directly

  - **Vim-style Navigation** - `j/k` for rows, `h/l` for tabs

  - **Column Sorting** - Click column headers or press `1-7` to sort

  - **Quick Filters** - Press `f` to filter quotes by vendor, brand, or price range

  - **Inline Editing** - Press `e` to edit selected entity

  - **Sparklines** - Mini price trend graphs in quote rows showing price history

## [0.1.2]

### Added

- **Quote Analysis Features** - Tools for making better purchasing decisions

  - **Best Price Detection** - Automatically identifies lowest price per product

  - **Total Cost Calculation** - Includes discount, shipping, and tax in price comparison

  - **Price History Tracking** - Records all price changes with timestamps

  - **Price Trend Indicators** - Shows if prices are going up (^), down (v), stable (-), or new (*)

  - **Price Alerts** - Set threshold alerts and get notified when prices drop

- **New Models**

  - `QuoteHistory` - Tracks price changes (create/update events) for quotes

  - `PriceAlert` - Price threshold alerts with product association and trigger tracking

- **New Services**

  - `QuoteHistoryService` - Record changes, get history, compute trends

  - `PriceAlertService` - Create, check, trigger, and manage price alerts

  - Extended `QuoteService` with `get_best_prices_by_product()` and `update_price()`

- **CLI Commands**

  - `buylog alert add <product> <threshold>` - Create price alert

  - `buylog alert list [--triggered]` - List all or triggered alerts

  - `buylog alert deactivate <id>` - Deactivate an alert

  - `buylog history --product <name>` - View product price history

  - `buylog history --quote-id <id>` - View quote price history

  - Extended `buylog add` with `--shipping` and `--tax-rate` options

- **TUI Enhancements**

  - New **Alerts tab** for managing price alerts

  - Best prices highlighted in **green** in Quotes tab

  - **Total cost** column showing calculated total with shipping/tax

  - **Trend indicator** column (^/v/-/*)

  - Products with triggered alerts highlighted in **yellow**

- **Quote Model Extensions**

  - `shipping_cost` field for shipping charges

  - `tax_rate` field for tax percentage

  - `created_at` timestamp

  - `total_cost` property calculating `(base * (1-discount) + shipping) * (1 + tax_rate)`

- **Tests**

  - `test_quote_analysis.py` with 30 new tests covering all quote analysis features

## [0.1.1]

### Added

- **Textual TUI** - Interactive terminal user interface (`buylog tui`)

  - Tabbed interface for Brands, Products, Vendors, Quotes, and Forex

  - DataTables with row selection and keyboard navigation

  - Modal forms for adding new entities

  - Delete confirmation dialogs

  - Search/filter functionality across all tables

  - Keyboard shortcuts (q=quit, a=add, d=delete, r=refresh, s=search)

- **Database seeding** - `buylog seed` command to populate sample data

  - 10 sample brands (Apple, Samsung, Sony, etc.)

  - 14 sample products

  - 9 sample vendors with various currencies

  - 5 forex rates (EUR, GBP, JPY, CAD, AUD)

  - 21 sample quotes with currency conversion

### Changed

- Default log level changed from DEBUG to INFO (reduces SQLAlchemy noise)

### Removed

- **Web interface** - Removed FastAPI/HTMX web UI (`web.py`)

- **Pydantic schemas** - Removed `schemas.py` (was only used by web interface)

- **Dependencies** - Removed `fastapi`, `jinja2`, `pydantic` from requirements

## [0.1.0]

### Added

- Command Line Interface with CRUD operations

- SQLAlchemy ORM models (Brand, Product, Vendor, Quote, Forex)

- Service layer with business logic separation

- Multi-currency support with forex conversion

- Audit logging for entity changes

- pytest test suite with Factory Boy
