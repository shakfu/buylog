# buylog

A Python tool to help you keep track of what you buy and want to buy.

## Features

- **Command Line Interface (CLI)** - Full CRUD operations for managing brands, products, vendors, and quotes

- **Text User Interface (TUI)** - Interactive terminal UI built with Textual

- **Data Persistence** - SQLAlchemy ORM with SQLite support

- **Multi-currency Support** - Each database has one base currency (USD unless set with `buylog db init --base`). Every amount is normalized to it at entry, so prices in any currency compare. Forex rates refresh from a public API, and the price as entered is shown beside the base amount

- **Quote Analysis** - Best price highlighting, price history tracking, and price targets

- **Automation** - `buylog check` for cron, with notifications via ntfy, webhook, desktop or email; scheduled re-scraping of tracked product URLs

- **Spend Analytics** - What you actually spent, by month, vendor and category, plus how each purchase compared to the best price on record

- **Service Layer** - Business logic separation with validation and error handling

- **Audit Logging** - Track entity creation, updates, and deletions

- **Testing** - pytest-based test suite with transactional fixtures

## Installation

```sh
pip install buylog
```

To build

```bash
# Clone the repository
git clone https://github.com/shakfu/buylog
cd buylog

# Install dependencies
uv sync

# Or install with development dependencies
uv sync --group dev
```

## Usage

### Command Line Interface

Choose the base currency before recording any amounts. It defaults to USD and is fixed once quotes, orders or rates exist:

```sh
buylog db init --base JPY
```

Commands take the form `buylog <noun> <verb> [target] [--options]`. Verbs are the same across nouns where they apply: `add`, `list`, `show`, `edit`, `rm`. `buylog <noun>` lists a noun's verbs. `-y`/`--yes` goes before or after the command.

```sh
# Catalog
buylog brand add Apple
buylog brand list --search app
buylog brand edit Apple --name "Apple Inc"
buylog brand rm Apple
buylog product add "iPhone 15" --brand Apple --category "Mobile Phones"
buylog product edit "iPhone 15" --category Phones    # Also --name, --brand
buylog product categories                            # Classifications in use
buylog product history "iPhone 15"                   # Price history across quotes
buylog vendor add Amazon --currency USD --url https://amazon.com
buylog vendor edit Amazon --discount 5 --no-url      # Also --name, --currency, --url
buylog vendor open Amazon                            # Open the vendor URL

# Quotes
buylog quote add -v Amazon -p "iPhone 15" --price 999
buylog quote add -v Amazon -p "iPhone 15" --price 999 --shipping 10 --tax-rate 8.5
buylog -y quote add -v Amazon -p "iPhone 16" -b Apple --price 1099   # Create missing product
buylog quote add -v Amazon -p "iPhone 15" --url https://example.com/p  # Scrape the price
buylog quote list --search iphone                    # Or --status S, --receipts, --tracked
buylog quote status 7 considering                    # Put the quote on the Considering list
buylog quote status 7 ordered                        # Also records a purchase order
buylog quote history 7
buylog quote rm 7
buylog quote copy 7                                  # Copy to the clipboard
buylog quote receipt attach 7 receipt.pdf            # Also: receipt open 7, receipt rm 7
buylog quote scrape https://example.com/p            # Show the scraped price only
buylog quote refresh                                 # Re-scrape every tracked quote

# Compare
buylog compare "iPhone 15"
buylog compare --search iPhone                       # Or --category CAT, --brand NAME

# Wants: price targets and purchase lists
buylog watch add "iPhone 15" 900                     # Notify once at or below $900
buylog watch add "iPhone 15" 800 --currency EUR
buylog watch list                                    # Also --all, --triggered
buylog watch edit 1 750                              # Re-arms the target
buylog watch off 1                                   # Or: watch rm 1
buylog purchase-list consider "iPhone 15"            # Its quotes go on Considering
buylog purchase-list add "My List" --description "Weekend shopping"
buylog purchase-list add-quote "My List" 7
buylog purchase-list rm-quote "My List" 7
buylog purchase-list show "My List"
buylog purchase-list list
buylog purchase-list might-buy                       # Unbought quotes on any list
buylog purchase-list rm "My List"

# Purchase orders and spend
buylog po add PO-001 -v Amazon -p "iPhone 15" --price 999.99 --quantity 2
buylog po add PO-002 --from-quote 7
buylog po list --status pending
buylog po show PO-001
buylog po edit PO-001 --status received
buylog spend                                         # month, vendor, category, variance, totals
buylog spend vendor --from 2026-01-01 --to 2026-06-30

# Specifications, tags, notes
buylog spec add "Camera Spec" --description "For camera products"
buylog spec feature "Camera Spec" Resolution --type number --unit MP
buylog spec show "Camera Spec"
buylog tag add product 1 sale                        # KIND ID TAG
buylog tag list product 1
buylog tag find sale
buylog note add vendor 1 "Fast shipping"             # KIND ID TEXT
buylog note list vendor 1
buylog note rm 1

# Forex
buylog fx refresh                                    # Every currency your vendors use
buylog fx refresh EUR GBP --date 2026-08-31
buylog fx add EUR 1.085                              # Base currency per unit, by hand
buylog fx list

# Monitoring
buylog check                                         # Report what triggered
buylog check --quiet --notify ntfy --refresh-fx --refresh-scrapes

# HTML reports (stdout, or -o FILE)
buylog report prices --search iPhone -o prices.html
buylog report purchases -o summary.html
buylog report vendors -o vendors.html
buylog report spend --from 2026-01-01 -o spend.html

# Import, export, templates
buylog data import quotes.csv                        # .csv and .json: quotes
buylog data import vendors.xlsx -t vendors           # .xlsx needs -t
buylog data export                                   # All tables to buylog-db.xlsx
buylog data export -t quotes --format csv -o quotes.csv
buylog data template -t vendors -f yaml

# Duplicates
buylog vendor dupes --threshold 0.7
buylog vendor merge 1 2 3                            # Merge 2 and 3 into 1
buylog product dupes
buylog product merge 1 2

# Database
buylog db init --base JPY                            # New database only
buylog db backup -o my-backup.db
buylog db backups
buylog db restore my-backup.db                       # Backs up the current db first
buylog db migrate --dry-run
buylog db migrate
buylog db seed                                       # Sample data
```

#### Running on a schedule

`buylog check` is built for cron: it takes no interactive input, prints nothing when `--quiet` and nothing has triggered, and exits non-zero on failure.

```cron
# Refresh rates and prices, then notify, every morning at 08:00
0 8 * * *  BUYLOG_NOTIFY=ntfy BUYLOG_NTFY_TOPIC=my-buylog \
             buylog check --quiet --refresh-fx --refresh-scrapes
```

#### Notification channels

Channels are selected with `BUYLOG_NOTIFY` (comma-separated) or `--notify`, and configured entirely by environment variable so a cron entry needs no arguments.

| Channel   | Required                          | Optional |
| --------- | --------------------------------- | -------- |
| `stdout`  | -                                 | - |
| `ntfy`    | `BUYLOG_NTFY_TOPIC`               | `BUYLOG_NTFY_SERVER`, `BUYLOG_NTFY_TOKEN` |
| `webhook` | `BUYLOG_WEBHOOK_URL`              | - |
| `desktop` | `notify-send`/`terminal-notifier`/`osascript` | - |
| `email`   | `BUYLOG_SMTP_HOST`, `BUYLOG_EMAIL_TO` | `BUYLOG_SMTP_PORT`, `BUYLOG_SMTP_USER`, `BUYLOG_SMTP_PASSWORD`, `BUYLOG_SMTP_STARTTLS`, `BUYLOG_EMAIL_FROM` |

A channel that is selected but not configured reports the variable it needs rather than failing silently.

#### Exit codes

The CLI is safe to script: `0` on success, `1` on a handled error, `130` on interrupt. Confirmation prompts never block on a non-interactive stdin - pass `--yes` to confirm automatically.

### Text User Interface (TUI)

Launch the interactive TUI:

```bash
buylog tui
```

The TUI provides:

- Tabbed interface for Brands, Products, Vendors, Quotes, Forex rates, price Targets, Lists, Specifications, and Purchase Orders

- DataTables with row selection

- Modal forms for adding entities

- Search/filter functionality

- **Quote Analysis Features**:

  - Best prices highlighted in green

  - Total cost calculation (with discount, shipping, tax)

  - Price trend indicators (^ up, v down, - stable, * new)

  - Sparkline mini-graphs showing price history

  - Quotes of products whose price target is met highlighted in yellow

  - **Status column** with color coding (considering=cyan, ordered=yellow, received=green)

- **Workflow Features**:

  - **Lists tab** - View and manage purchase lists

  - **Targets tab** - Price targets: Met (not yet notified), Triggered (notified by `buylog check`), Active, Inactive

  - Set quote status with `t` key

  - Watch the selected product with `w` key (Products tab)

- **Integration Features**:

  - **URL column** in Vendors tab showing link indicators

  - Copy to clipboard with `y` key (quotes, products, vendors)

  - Open vendor URL with `o` key (on Vendors tab)

- **Quick Filters** for quotes by vendor, brand, or price range

- **Inline Editing** - Edit entities directly with `e` key

- **Column Sorting** - Click headers or press number keys to sort

- **Price Comparison** - Compare prices with `c` key (select type: Product/Search/Category/Brand)

- Keyboard shortcuts. The footer shows only the keys that work on the current tab:

  - `q` - Quit

  - `a` - Add new entity

  - `c` - Compare prices

  - `d` - Delete selected entity

  - `e` - Edit selected entity

  - `f` - Filter quotes

  - `o` - Open vendor URL (vendors tab)

  - `r` - Refresh data

  - `/` - Focus search (Enter runs it)

  - `t` - Set quote status

  - `w` - Watch the selected product

  - `y` - Copy to clipboard

  - `Ctrl+1`-`Ctrl+9` - Switch to tab 1-9

  - `h/l` - Previous/next tab (vim-style)

  - `j/k` - Move cursor down/up (vim-style)

  - `1-7` - Sort by column number

## Development

### Running Tests

```bash
# Run all tests
make test
# or
uv run pytest

# Run with coverage report
make coverage
# or
uv run pytest --cov-report=html:cov_html --cov-report=term-missing --cov=buylog
```

### Generate ER Diagram

```bash
make diagram
# or
uv run python scripts/gen_diagram.py

# Output: docs/er_model.md  (Mermaid, renders inline on GitHub)
#         docs/er_model.er  (eralchemy markup)
#         docs/er_model.dot (Graphviz source)
#         docs/er_model.svg (only when Graphviz is installed)
```

### Clean Build Artifacts

```bash
make clean
```

## Architecture

### Project Structure

```text
buylog/
├── src/buylog/          # Main package
│   ├── models.py        # SQLAlchemy ORM models
│   ├── cli.py           # CLI interface
│   ├── tui.py           # Textual TUI interface
│   ├── services.py      # Business logic layer
│   ├── excel.py         # Excel import/export (openpyxl)
│   ├── templates.py     # YAML/JSON template generation
│   ├── migrate.py       # Database schema migration
│   ├── config.py        # Configuration management
│   ├── notify.py        # Notification delivery (ntfy/webhook/desktop/email)
│   └── audit.py         # Audit logging
├── tests/               # Test suite
│   ├── conftest.py      # pytest fixtures
│   ├── test_models.py   # Model tests
│   ├── test_services.py # Service layer tests
│   ├── test_schema.py   # Schema creation and migration tests
│   ├── test_cli.py      # CLI end-to-end tests
│   ├── test_tui.py      # TUI smoke tests
│   ├── test_notify.py   # Notification channel tests
│   ├── test_automation.py # check/fx refresh/scrape refresh/spend tests
│   ├── test_excel.py    # Excel import/export tests
│   └── test_quote_analysis.py # Quote analysis feature tests
├── docs/                # Documentation
│   ├── er_model.md      # Auto-generated ER diagram (Mermaid)
│   ├── er_model.er      # Auto-generated ER diagram (eralchemy)
│   └── er_model.dot     # Auto-generated ER diagram (Graphviz)
└── pyproject.toml       # Project dependencies
```

### Data Model

The core domain models:

- **Vendor** - Selling entities with currency, discount codes, contact info, and address

- **Brand** - Manufacturing entities linked to products and vendors

- **Product** - Items with brand associations and optional specification links

- **Quote** - Price quotes from vendors with shipping, tax, and total cost. Status is derived from the quote's purchase orders and purchase lists, not stored

- **QuoteHistory** - Price change tracking for quotes (create/update events)

- **PriceTarget** - A price threshold per product, in any currency, that fires once

- **Forex** - Currency exchange rates for multi-currency support

- **PurchaseList** - Named shopping lists grouping quotes

- **PurchaseOrder** - Committed purchases with status tracking and delivery dates

- **Specification** - Structured product attribute definitions

- **SpecificationFeature** - Feature definitions with data types and validation

- **ProductFeature** - Feature values for products

- **Note** - Freeform notes attachable to any entity (polymorphic)

- **Tag** - Categorization tags with optional color

- **EntityTag** - Junction table for tagging any entity

Key relationships:

- Many-to-many between Vendors and Brands (via `vendor_brand` junction table)

- One-to-many from Brand to Products

- One-to-many from Vendor and Product to Quotes

- One-to-many from Quote to QuoteHistory

- One-to-many from Product to PriceTarget

- Many-to-many between PurchaseList and Quotes (via `purchase_list_quote` junction table)

See the auto-generated ER diagram: [`docs/er_model.md`](docs/er_model.md)

### Service Layer

Business logic is separated into service classes:

- `BrandService` - Brand CRUD with validation

- `ProductService` - Product management with eager loading

- `VendorService` - Vendor operations with extended contact/address fields

- `QuoteService` - Quote management with currency conversion, best price detection, price updates, and status tracking

- `QuoteHistoryService` - Price history tracking and trend computation

- `PriceTargetService` - Price target creation, firing, re-arming, and management

- `ComparisonService` - Price comparison by product, search, category, or brand

- `PurchaseListService` - Purchase list CRUD, add/remove quotes

- `PurchaseOrderService` - Purchase order CRUD, status transitions, create from quote

- `SpecificationService` - Specification and feature management

- `ProductFeatureService` - Product feature value management

- `NoteService` - Note CRUD for any entity type

- `TagService` - Tag management and entity tagging

- `ReportService` - HTML report generation (price comparison, purchase summary, vendor analysis)

- `AuditService` - Entity change tracking

## Technologies

- **Python 3.13+** - Core language

- **SQLAlchemy 2.0+** - ORM and database abstraction

- **Textual** - Modern terminal UI framework

- **openpyxl** - Excel file read/write

- **pytest** - Testing framework

- **uv** - Fast Python package manager

- **eralchemy** - ER diagram generation

- **tabulate** - CLI table formatting

## Configuration

The application uses a configuration system via `config.py`:

- Database path: `~/.buylog/buylog.db` (configurable via `BUYLOG_DB_PATH`)

- Log level: `INFO` by default (configurable via `BUYLOG_LOG_LEVEL`)

- Logging: Configured for both file and console output

Notifications are configured separately, via the `BUYLOG_NOTIFY` and per-channel environment variables listed under [Notification channels](#notification-channels).

## License

MIT

## Contributing

Feedback, bug reports and code contributions are welcome!
