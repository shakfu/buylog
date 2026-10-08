"""Textual TUI interface for buylog tool"""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.data_table import RowDoesNotExist
from sqlalchemy import select
from sqlalchemy.orm import Session as SessionType
from rich.text import Text

from .config import Config
from .models import (
    Base,
    init_db,
    Brand,
    Product,
    Vendor,
    Quote,
    Forex,
    PriceAlert,
    PurchaseList,
    Watchlist,
)
from .services import (
    BrandService,
    ProductService,
    VendorService,
    QuoteService,
    PriceAlertService,
    QuoteHistoryService,
    ComparisonService,
    PurchaseListService,
    WatchlistService,
    SpecificationService,
    PurchaseOrderService,
    ForexService,
    DuplicateError,
    ValidationError,
    NotFoundError,
)


# Sparkline characters for mini trend graphs
SPARKLINE_CHARS = " _.-~'^"


def make_sparkline(values: list[float], width: int = 7) -> str:
    """Generate a sparkline string from a list of values."""
    if not values or len(values) < 2:
        return "-" * width

    # Normalize values to 0-6 range for sparkline chars
    min_val = min(values)
    max_val = max(values)
    range_val = max_val - min_val

    if range_val == 0:
        return SPARKLINE_CHARS[3] * min(len(values), width)

    # Take last 'width' values
    values = values[-width:]
    result = ""
    for v in values:
        normalized = int((v - min_val) / range_val * 6)
        normalized = max(0, min(6, normalized))
        result += SPARKLINE_CHARS[normalized]

    return result.ljust(width)


# Database setup
engine = Config.get_engine()
init_db(engine)
Session = Config.get_session_maker()


class AddBrandScreen(Screen[str | None]):
    """Full-screen form for adding a new brand."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="fullscreen-form"):
            yield Label("[bold]Add Brand[/bold]", id="form-title")
            with Horizontal(classes="form-row"):
                yield Label("Name *", classes="form-label")
                yield Input(placeholder="Brand name", id="brand-name")
            with Horizontal(id="form-buttons"):
                yield Button("Save (Ctrl+S)", variant="primary", id="save-btn")
                yield Button("Cancel (Esc)", variant="default", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self._do_save()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._do_save()

    def action_save(self) -> None:
        self._do_save()

    def _do_save(self) -> None:
        name = self.query_one("#brand-name", Input).value
        if not name:
            self.notify("Brand name is required", severity="error")
            return
        self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddProductScreen(Screen[tuple[str, str] | None]):
    """Full-screen form for adding a new product."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="fullscreen-form"):
            yield Label("[bold]Add Product[/bold]", id="form-title")
            with Horizontal(classes="form-row"):
                yield Label("Brand *", classes="form-label")
                yield Input(placeholder="Brand name", id="brand-name")
            with Horizontal(classes="form-row"):
                yield Label("Product *", classes="form-label")
                yield Input(placeholder="Product name", id="product-name")
            with Horizontal(id="form-buttons"):
                yield Button("Save (Ctrl+S)", variant="primary", id="save-btn")
                yield Button("Cancel (Esc)", variant="default", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self._do_save()
        else:
            self.dismiss(None)

    def action_save(self) -> None:
        self._do_save()

    def _do_save(self) -> None:
        brand = self.query_one("#brand-name", Input).value
        product = self.query_one("#product-name", Input).value
        if not brand or not product:
            self.notify("Brand and product names are required", severity="error")
            return
        self.dismiss((brand, product))

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddQuoteScreen(Screen[tuple[str, str, str | None, float] | None]):
    """Full-screen form for adding a new quote."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="fullscreen-form"):
            yield Label("[bold]Add Quote[/bold]", id="form-title")
            with Horizontal(classes="form-row"):
                yield Label("Vendor *", classes="form-label")
                yield Input(placeholder="Vendor name", id="vendor-name")
            with Horizontal(classes="form-row"):
                yield Label("Product *", classes="form-label")
                yield Input(placeholder="Product name", id="product-name")
            with Horizontal(classes="form-row"):
                yield Label("Brand", classes="form-label")
                yield Input(placeholder="Brand (if new product)", id="brand-name")
            with Horizontal(classes="form-row"):
                yield Label("Price *", classes="form-label")
                yield Input(placeholder="0.00", id="price")
            with Horizontal(id="form-buttons"):
                yield Button("Save (Ctrl+S)", variant="primary", id="save-btn")
                yield Button("Cancel (Esc)", variant="default", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self._do_save()
        else:
            self.dismiss(None)

    def action_save(self) -> None:
        self._do_save()

    def _do_save(self) -> None:
        vendor = self.query_one("#vendor-name", Input).value
        product = self.query_one("#product-name", Input).value
        brand = self.query_one("#brand-name", Input).value or None
        price_str = self.query_one("#price", Input).value
        if not vendor or not product:
            self.notify("Vendor and product are required", severity="error")
            return
        try:
            price = float(price_str) if price_str else 0.0
            self.dismiss((vendor, product, brand, price))
        except ValueError:
            self.notify("Invalid price", severity="error")

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddForexScreen(Screen[tuple[str, float, str | None] | None]):
    """Full-screen form for adding a forex rate."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="fullscreen-form"):
            yield Label("[bold]Add Forex Rate[/bold]", id="form-title")
            with Horizontal(classes="form-row"):
                yield Label("Currency *", classes="form-label")
                yield Input(placeholder="Currency code (e.g., EUR)", id="code")
            with Horizontal(classes="form-row"):
                yield Label("Rate *", classes="form-label")
                yield Input(placeholder="USD per unit (e.g., 1.085)", id="rate")
            with Horizontal(classes="form-row"):
                yield Label("Date", classes="form-label")
                yield Input(placeholder="YYYY-MM-DD (optional)", id="date")
            with Horizontal(id="form-buttons"):
                yield Button("Save (Ctrl+S)", variant="primary", id="save-btn")
                yield Button("Cancel (Esc)", variant="default", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self._do_save()
        else:
            self.dismiss(None)

    def action_save(self) -> None:
        self._do_save()

    def _do_save(self) -> None:
        code = self.query_one("#code", Input).value.upper()
        rate_str = self.query_one("#rate", Input).value
        date_str = self.query_one("#date", Input).value or None
        if not code:
            self.notify("Currency code is required", severity="error")
            return
        try:
            rate = float(rate_str) if rate_str else 0.0
            self.dismiss((code, rate, date_str))
        except ValueError:
            self.notify("Invalid rate", severity="error")

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddAlertScreen(Screen[tuple[str, float] | None]):
    """Full-screen form for adding a price alert."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="fullscreen-form"):
            yield Label("[bold]Add Price Alert[/bold]", id="form-title")
            with Horizontal(classes="form-row"):
                yield Label("Product *", classes="form-label")
                yield Input(placeholder="Product name", id="product-name")
            with Horizontal(classes="form-row"):
                yield Label("Threshold *", classes="form-label")
                yield Input(placeholder="Price threshold", id="threshold")
            with Horizontal(id="form-buttons"):
                yield Button("Save (Ctrl+S)", variant="primary", id="save-btn")
                yield Button("Cancel (Esc)", variant="default", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self._do_save()
        else:
            self.dismiss(None)

    def action_save(self) -> None:
        self._do_save()

    def _do_save(self) -> None:
        product = self.query_one("#product-name", Input).value
        threshold_str = self.query_one("#threshold", Input).value
        if not product:
            self.notify("Product name is required", severity="error")
            return
        try:
            threshold = float(threshold_str) if threshold_str else 0.0
            self.dismiss((product, threshold))
        except ValueError:
            self.notify("Invalid threshold value", severity="error")

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddPurchaseListScreen(Screen[tuple[str, str | None] | None]):
    """Full-screen form for adding a new purchase list."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="fullscreen-form"):
            yield Label("[bold]Create Purchase List[/bold]", id="form-title")
            with Horizontal(classes="form-row"):
                yield Label("Name *", classes="form-label")
                yield Input(placeholder="List name", id="list-name")
            with Horizontal(classes="form-row"):
                yield Label("Description", classes="form-label")
                yield Input(placeholder="Optional description", id="list-description")
            with Horizontal(id="form-buttons"):
                yield Button("Save (Ctrl+S)", variant="primary", id="save-btn")
                yield Button("Cancel (Esc)", variant="default", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self._do_save()
        else:
            self.dismiss(None)

    def action_save(self) -> None:
        self._do_save()

    def _do_save(self) -> None:
        name = self.query_one("#list-name", Input).value
        description = self.query_one("#list-description", Input).value or None
        if not name:
            self.notify("List name is required", severity="error")
            return
        self.dismiss((name, description))

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddWatchlistScreen(
    Screen[tuple[str, float | None, str | None, str | None] | None]
):
    """Full-screen form for adding a product to watchlist."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="fullscreen-form"):
            yield Label("[bold]Add to Watchlist[/bold]", id="form-title")
            with Horizontal(classes="form-row"):
                yield Label("Product *", classes="form-label")
                yield Input(placeholder="Product name", id="product-name")
            with Horizontal(classes="form-row"):
                yield Label("Target Price", classes="form-label")
                yield Input(placeholder="Optional target price", id="target-price")
            with Horizontal(classes="form-row"):
                yield Label("Currency", classes="form-label")
                yield Input(placeholder="USD", id="currency")
            with Horizontal(classes="form-row"):
                yield Label("Notes", classes="form-label")
                yield Input(placeholder="Optional notes", id="notes")
            with Horizontal(id="form-buttons"):
                yield Button("Save (Ctrl+S)", variant="primary", id="save-btn")
                yield Button("Cancel (Esc)", variant="default", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self._do_save()
        else:
            self.dismiss(None)

    def action_save(self) -> None:
        self._do_save()

    def _do_save(self) -> None:
        product = self.query_one("#product-name", Input).value
        target_str = self.query_one("#target-price", Input).value
        notes = self.query_one("#notes", Input).value or None
        currency = self.query_one("#currency", Input).value.strip() or None
        if not product:
            self.notify("Product name is required", severity="error")
            return
        try:
            target = float(target_str) if target_str else None
            self.dismiss((product, target, notes, currency))
        except ValueError:
            self.notify("Invalid target price", severity="error")

    def action_cancel(self) -> None:
        self.dismiss(None)


class SetQuoteStatusModal(ModalScreen[str | None]):
    """Modal for setting quote status."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        status_options = [
            ("Considering", "considering"),
            ("Ordered", "ordered"),
            ("Received", "received"),
        ]
        with Vertical(id="modal-dialog"):
            yield Label("Set Quote Status", id="modal-title")
            yield Select(status_options, id="status-select", value="considering")
            with Horizontal(id="modal-buttons"):
                yield Button("Set", variant="primary", id="set-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "set-btn":
            status = self.query_one("#status-select", Select).value
            if status != Select.BLANK:
                self.dismiss(str(status))
            else:
                self.dismiss(None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmDeleteModal(ModalScreen[bool]):
    """Modal for confirming deletion."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, entity_type: str, name: str) -> None:
        super().__init__()
        self.entity_type = entity_type
        self.entity_name = name

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label("Confirm Delete", id="modal-title")
            yield Label(f"Delete {self.entity_type} '{self.entity_name}'?")
            with Horizontal(id="modal-buttons"):
                yield Button("Delete", variant="error", id="delete-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "delete-btn")

    def action_cancel(self) -> None:
        self.dismiss(False)


class EditCellModal(ModalScreen[tuple[str, str] | None]):
    """Modal for editing a cell value."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, field_name: str, current_value: str) -> None:
        super().__init__()
        self.field_name = field_name
        self.current_value = current_value

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label(f"Edit {self.field_name}", id="modal-title")
            yield Input(value=self.current_value, id="edit-value")
            with Horizontal(id="modal-buttons"):
                yield Button("Save", variant="primary", id="save-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_mount(self) -> None:
        self.query_one("#edit-value", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            new_value = self.query_one("#edit-value", Input).value
            self.dismiss((self.field_name, new_value))
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss((self.field_name, event.value))

    def action_cancel(self) -> None:
        self.dismiss(None)


class QuoteFilterModal(ModalScreen[dict | None]):
    """Modal for filtering quotes."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, vendors: list[str], brands: list[str]) -> None:
        super().__init__()
        self.vendors = vendors
        self.brands = brands

    def compose(self) -> ComposeResult:
        vendor_options = [("All Vendors", "")] + [(v, v) for v in self.vendors]
        brand_options = [("All Brands", "")] + [(b, b) for b in self.brands]

        with Vertical(id="modal-dialog"):
            yield Label("Filter Quotes", id="modal-title")
            yield Label("Vendor:")
            yield Select(vendor_options, id="vendor-filter", value="")
            yield Label("Brand:")
            yield Select(brand_options, id="brand-filter", value="")
            yield Label("Min Price:")
            yield Input(placeholder="0.00", id="min-price")
            yield Label("Max Price:")
            yield Input(placeholder="99999.00", id="max-price")
            with Horizontal(id="modal-buttons"):
                yield Button("Apply", variant="primary", id="apply-btn")
                yield Button("Clear", variant="default", id="clear-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-btn":
            vendor = self.query_one("#vendor-filter", Select).value
            brand = self.query_one("#brand-filter", Select).value
            min_price_str = self.query_one("#min-price", Input).value
            max_price_str = self.query_one("#max-price", Input).value

            filters = {}
            if vendor:
                filters["vendor"] = vendor
            if brand:
                filters["brand"] = brand
            try:
                if min_price_str:
                    filters["min_price"] = float(min_price_str)
                if max_price_str:
                    filters["max_price"] = float(max_price_str)
            except ValueError:
                pass

            self.dismiss(filters if filters else None)
        elif event.button.id == "clear-btn":
            self.dismiss({})  # Empty dict means clear filters
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class CompareModal(ModalScreen[None]):
    """Modal for price comparison."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self, session, products: list[str], categories: list[str], brands: list[str]
    ) -> None:
        super().__init__()
        self.db_session = session
        self.products = products
        self.categories = categories
        self.brands = brands

    def compose(self) -> ComposeResult:
        compare_type_options = [
            ("Product (exact match)", "product"),
            ("Search (partial match)", "search"),
            ("Category", "category"),
            ("Brand", "brand"),
        ]

        with Vertical(id="compare-dialog"):
            yield Label("Price Comparison", id="modal-title")
            yield Label("Compare by:")
            yield Select(compare_type_options, id="compare-type", value="product")
            yield Label("Select or enter value:", id="value-label")
            yield Select([], id="compare-select")
            yield Input(placeholder="Enter search term...", id="compare-input")
            yield Static("", id="compare-results")
            with Horizontal(id="modal-buttons"):
                yield Button("Compare", variant="primary", id="compare-btn")
                yield Button("Close", variant="default", id="close-btn")

    def on_mount(self) -> None:
        # Initialize with product options
        self._update_value_selector("product")
        # Hide input by default (show select)
        self.query_one("#compare-input", Input).display = False

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "compare-type":
            if event.value != Select.BLANK:
                self._update_value_selector(str(event.value))

    def _update_value_selector(self, compare_type: str) -> None:
        """Update the value selector based on comparison type."""
        select_widget = self.query_one("#compare-select", Select)
        input_widget = self.query_one("#compare-input", Input)
        label_widget = self.query_one("#value-label", Label)

        if compare_type == "product":
            label_widget.update("Select product:")
            options = [(p, p) for p in self.products]
            select_widget.set_options(options)
            select_widget.display = True
            input_widget.display = False
            if options:
                select_widget.value = options[0][1]

        elif compare_type == "search":
            label_widget.update("Enter search term:")
            select_widget.display = False
            input_widget.display = True
            input_widget.placeholder = "e.g., iPhone, Galaxy, MacBook..."
            input_widget.value = ""
            input_widget.focus()

        elif compare_type == "category":
            label_widget.update("Select category:")
            options = [(c, c) for c in self.categories]
            if options:
                select_widget.set_options(options)
                select_widget.value = options[0][1]
            else:
                select_widget.set_options([("No categories defined", "")])
            select_widget.display = True
            input_widget.display = False

        elif compare_type == "brand":
            label_widget.update("Select brand:")
            options = [(b, b) for b in self.brands]
            select_widget.set_options(options)
            select_widget.display = True
            input_widget.display = False
            if options:
                select_widget.value = options[0][1]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "compare-btn":
            self._run_comparison()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "compare-input":
            self._run_comparison()

    def _run_comparison(self) -> None:
        results_widget = self.query_one("#compare-results", Static)
        compare_type = self.query_one("#compare-type", Select).value

        try:
            if compare_type == "product":
                select_value = self.query_one("#compare-select", Select).value
                if select_value == Select.BLANK or not select_value:
                    results_widget.update("[yellow]Please select a product[/yellow]")
                    return
                result = ComparisonService.compare_product(
                    self.db_session, str(select_value)
                )
                output = self._format_single_comparison(result)

            elif compare_type == "search":
                search_value = self.query_one("#compare-input", Input).value.strip()
                if not search_value:
                    results_widget.update("[yellow]Please enter a search term[/yellow]")
                    return
                result = ComparisonService.compare_by_search(
                    self.db_session, search_value
                )
                output = self._format_multi_comparison(
                    result["products"], f"Search: {search_value}"
                )

            elif compare_type == "category":
                select_value = self.query_one("#compare-select", Select).value
                if select_value == Select.BLANK or not select_value:
                    results_widget.update("[yellow]Please select a category[/yellow]")
                    return
                result = ComparisonService.compare_by_category(
                    self.db_session, str(select_value)
                )
                output = self._format_multi_comparison(
                    result["products"], f"Category: {select_value}"
                )

            elif compare_type == "brand":
                select_value = self.query_one("#compare-select", Select).value
                if select_value == Select.BLANK or not select_value:
                    results_widget.update("[yellow]Please select a brand[/yellow]")
                    return
                result = ComparisonService.compare_by_brand(
                    self.db_session, str(select_value)
                )
                output = self._format_multi_comparison(
                    result["products"], f"Brand: {select_value}"
                )

            else:
                output = "[yellow]Please select a comparison type[/yellow]"

            results_widget.update(output)
        except NotFoundError as e:
            results_widget.update(f"[red]Error: {e}[/red]")

    def _format_single_comparison(self, comparison: dict) -> str:
        product = comparison["product"]
        quotes = comparison["quotes"]

        lines = [f"[bold]{product.brand.name} {product.name}[/bold]"]
        if product.category:
            lines.append(f"Category: {product.category}")
        lines.append("-" * 50)

        if not quotes:
            lines.append("No quotes available")
            return "\n".join(lines)

        for q in quotes:
            price_str = f"${q.value:.2f}"
            if q == quotes[0]:  # Best price
                price_str = f"[bold green]{price_str}[/bold green]"
            lines.append(f"  {q.vendor.name}: {price_str} (total: ${q.total_cost:.2f})")

        lines.append("")
        lines.append(f"[green]Best:  ${comparison['best_price']:.2f}[/green]")
        lines.append(f"[red]Worst: ${comparison['worst_price']:.2f}[/red]")
        lines.append(f"Avg:   ${comparison['avg_price']:.2f}")
        lines.append(f"[yellow]Savings: ${comparison['savings']:.2f}[/yellow]")

        return "\n".join(lines)

    def _format_multi_comparison(self, comparisons: list, title: str) -> str:
        lines = [f"[bold]{title}[/bold] - {len(comparisons)} products", "=" * 50]

        for comp in comparisons[:5]:  # Limit to 5 for modal space
            product = comp["product"]
            if comp["best_price"]:
                lines.append(
                    f"[bold]{product.name}[/bold]: "
                    f"[green]${comp['best_price']:.2f}[/green] - "
                    f"[red]${comp['worst_price']:.2f}[/red] "
                    f"({comp['num_vendors']} vendors)"
                )
            else:
                lines.append(f"{product.name}: No quotes")

        if len(comparisons) > 5:
            lines.append(f"... and {len(comparisons) - 5} more products")

        return "\n".join(lines)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddVendorScreen(Screen[dict | None]):
    """Full-screen form for adding a vendor with all fields."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    def compose(self) -> ComposeResult:
        from textual.containers import VerticalScroll

        yield Header()
        with Vertical(id="fullscreen-form"):
            yield Label("[bold]Add Vendor[/bold]", id="form-title")
            with VerticalScroll(id="form-scroll-full"):
                # Basic info
                yield Label("[bold]Basic Information[/bold]", classes="section-header")
                with Horizontal(classes="form-row"):
                    yield Label("Name *", classes="form-label")
                    yield Input(placeholder="Vendor name", id="vendor-name")
                with Horizontal(classes="form-row"):
                    yield Label("Currency", classes="form-label")
                    yield Input(placeholder="USD", id="currency")
                with Horizontal(classes="form-row"):
                    yield Label("Discount Code", classes="form-label")
                    yield Input(placeholder="Optional", id="discount-code")
                with Horizontal(classes="form-row"):
                    yield Label("Discount %", classes="form-label")
                    yield Input(placeholder="0", id="discount")
                with Horizontal(classes="form-row"):
                    yield Label("URL", classes="form-label")
                    yield Input(placeholder="https://...", id="url")

                # Contact info
                yield Label("[bold]Contact[/bold]", classes="section-header")
                with Horizontal(classes="form-row"):
                    yield Label("Contact", classes="form-label")
                    yield Input(placeholder="Contact person", id="contact-person")
                with Horizontal(classes="form-row"):
                    yield Label("Email", classes="form-label")
                    yield Input(placeholder="email@example.com", id="email")
                with Horizontal(classes="form-row"):
                    yield Label("Phone", classes="form-label")
                    yield Input(placeholder="+1 555-1234", id="phone")

                # Address
                yield Label("[bold]Address[/bold]", classes="section-header")
                with Horizontal(classes="form-row"):
                    yield Label("Line 1", classes="form-label")
                    yield Input(placeholder="Street address", id="address-line1")
                with Horizontal(classes="form-row"):
                    yield Label("Line 2", classes="form-label")
                    yield Input(placeholder="Apt, suite, etc.", id="address-line2")
                with Horizontal(classes="form-row"):
                    yield Label("City", classes="form-label")
                    yield Input(placeholder="City", id="city")
                with Horizontal(classes="form-row"):
                    yield Label("State", classes="form-label")
                    yield Input(placeholder="State/Province", id="state")
                with Horizontal(classes="form-row"):
                    yield Label("Postal", classes="form-label")
                    yield Input(placeholder="Postal code", id="postal-code")
                with Horizontal(classes="form-row"):
                    yield Label("Country", classes="form-label")
                    yield Input(placeholder="Country", id="country")

                # Business
                yield Label("[bold]Business[/bold]", classes="section-header")
                with Horizontal(classes="form-row"):
                    yield Label("Tax ID", classes="form-label")
                    yield Input(placeholder="Tax ID", id="tax-id")
                with Horizontal(classes="form-row"):
                    yield Label("Terms", classes="form-label")
                    yield Input(placeholder="Payment terms", id="payment-terms")

            with Horizontal(id="form-buttons"):
                yield Button("Save (Ctrl+S)", variant="primary", id="save-btn")
                yield Button("Cancel (Esc)", variant="default", id="cancel-btn")
        yield Footer()

    def action_save(self) -> None:
        self._do_save()

    def _do_save(self) -> None:
        result = {
            "name": self.query_one("#vendor-name", Input).value,
            "currency": self.query_one("#currency", Input).value or "USD",
            "discount_code": self.query_one("#discount-code", Input).value or None,
            "discount": float(self.query_one("#discount", Input).value or 0),
            "url": self.query_one("#url", Input).value or None,
            "contact_person": self.query_one("#contact-person", Input).value or None,
            "email": self.query_one("#email", Input).value or None,
            "phone": self.query_one("#phone", Input).value or None,
            "address_line1": self.query_one("#address-line1", Input).value or None,
            "address_line2": self.query_one("#address-line2", Input).value or None,
            "city": self.query_one("#city", Input).value or None,
            "state": self.query_one("#state", Input).value or None,
            "postal_code": self.query_one("#postal-code", Input).value or None,
            "country": self.query_one("#country", Input).value or None,
            "tax_id": self.query_one("#tax-id", Input).value or None,
            "payment_terms": self.query_one("#payment-terms", Input).value or None,
        }
        if not result["name"]:
            self.notify("Vendor name is required", severity="error")
            return
        self.dismiss(result)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self._do_save()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddSpecificationScreen(Screen[dict | None]):
    """Full-screen form for adding a specification with features."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
        Binding("ctrl+n", "add_feature", "Add Feature", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.feature_count = 0

    def compose(self) -> ComposeResult:
        from textual.containers import VerticalScroll

        yield Header()
        with Vertical(id="fullscreen-form"):
            yield Label("[bold]Add Specification[/bold]", id="form-title")
            with VerticalScroll(id="form-scroll-full"):
                yield Label("[bold]Specification[/bold]", classes="section-header")
                with Horizontal(classes="form-row"):
                    yield Label("Name *", classes="form-label")
                    yield Input(placeholder="Specification name", id="spec-name")
                with Horizontal(classes="form-row"):
                    yield Label("Description", classes="form-label")
                    yield Input(
                        placeholder="Optional description", id="spec-description"
                    )

                yield Label(
                    "[bold]Features[/bold] (Ctrl+N to add more)",
                    classes="section-header",
                )
                yield Vertical(id="features-container")

            with Horizontal(id="form-buttons"):
                yield Button(
                    "Add Feature (Ctrl+N)", variant="default", id="add-feature-btn"
                )
                yield Button("Save (Ctrl+S)", variant="primary", id="save-btn")
                yield Button("Cancel (Esc)", variant="default", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        # Add one empty feature row by default
        self._add_feature_row()

    def _add_feature_row(self) -> None:
        self.feature_count += 1
        container = self.query_one("#features-container", Vertical)

        feature_row = Horizontal(
            id=f"feature-row-{self.feature_count}", classes="feature-row"
        )
        # Mount the row first, then add children
        container.mount(feature_row)
        feature_row.mount(
            Input(
                placeholder="Feature name",
                id=f"feat-name-{self.feature_count}",
                classes="feature-name",
            )
        )
        feature_row.mount(
            Select(
                [(t, t) for t in ["text", "number", "boolean"]],
                value="text",
                id=f"feat-type-{self.feature_count}",
                classes="feature-type",
            )
        )
        feature_row.mount(
            Input(
                placeholder="Unit (optional)",
                id=f"feat-unit-{self.feature_count}",
                classes="feature-unit",
            )
        )

    def action_save(self) -> None:
        self._do_save()

    def _do_save(self) -> None:
        name = self.query_one("#spec-name", Input).value
        if not name:
            self.notify("Specification name is required", severity="error")
            return

        features = []
        for i in range(1, self.feature_count + 1):
            try:
                feat_name = self.query_one(f"#feat-name-{i}", Input).value
                if feat_name:
                    feat_type = self.query_one(f"#feat-type-{i}", Select).value
                    feat_unit = self.query_one(f"#feat-unit-{i}", Input).value
                    features.append(
                        {
                            "name": feat_name,
                            "data_type": feat_type or "text",
                            "unit": feat_unit or None,
                            "is_required": False,
                        }
                    )
            except Exception:
                pass

        result = {
            "name": name,
            "description": self.query_one("#spec-description", Input).value or None,
            "features": features,
        }
        self.dismiss(result)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-feature-btn":
            self._add_feature_row()
        elif event.button.id == "save-btn":
            self._do_save()
        else:
            self.dismiss(None)

    def action_add_feature(self) -> None:
        self._add_feature_row()

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddPurchaseOrderScreen(Screen[dict | None]):
    """Full-screen form for adding a purchase order."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    def __init__(self, from_quote: Quote | None = None) -> None:
        super().__init__()
        self.from_quote = from_quote

    def compose(self) -> ComposeResult:
        from textual.containers import VerticalScroll
        import datetime

        yield Header()
        with Vertical(id="fullscreen-form"):
            title = "[bold]Add Purchase Order[/bold]"
            if self.from_quote:
                title += f" (from Quote #{self.from_quote.id})"
            yield Label(title, id="form-title")

            with VerticalScroll(id="form-scroll-full"):
                yield Label("[bold]Order Info[/bold]", classes="section-header")
                default_po = f"PO-{datetime.date.today().strftime('%Y%m%d')}-001"
                with Horizontal(classes="form-row"):
                    yield Label("PO # *", classes="form-label")
                    yield Input(
                        placeholder="PO Number", value=default_po, id="po-number"
                    )

                if self.from_quote:
                    with Horizontal(classes="form-row"):
                        yield Label("Vendor", classes="form-label")
                        yield Input(
                            value=self.from_quote.vendor.name,
                            id="vendor-name",
                            disabled=True,
                        )
                    with Horizontal(classes="form-row"):
                        yield Label("Product", classes="form-label")
                        yield Input(
                            value=self.from_quote.product.name,
                            id="product-name",
                            disabled=True,
                        )
                    with Horizontal(classes="form-row"):
                        yield Label("Unit Price", classes="form-label")
                        yield Input(value=str(self.from_quote.value), id="unit-price")
                else:
                    with Horizontal(classes="form-row"):
                        yield Label("Vendor *", classes="form-label")
                        yield Input(placeholder="Vendor name", id="vendor-name")
                    with Horizontal(classes="form-row"):
                        yield Label("Product *", classes="form-label")
                        yield Input(placeholder="Product name", id="product-name")
                    with Horizontal(classes="form-row"):
                        yield Label("Unit Price *", classes="form-label")
                        yield Input(placeholder="0.00", id="unit-price")

                with Horizontal(classes="form-row"):
                    yield Label("Quantity", classes="form-label")
                    yield Input(placeholder="1", value="1", id="quantity")
                with Horizontal(classes="form-row"):
                    yield Label("Currency", classes="form-label")
                    yield Input(placeholder="USD", value="USD", id="currency")

                yield Label("[bold]Costs[/bold]", classes="section-header")
                with Horizontal(classes="form-row"):
                    yield Label("Shipping", classes="form-label")
                    yield Input(placeholder="0.00", id="shipping-cost")
                with Horizontal(classes="form-row"):
                    yield Label("Tax", classes="form-label")
                    yield Input(placeholder="0.00", id="tax")

                yield Label("[bold]Dates[/bold]", classes="section-header")
                today = datetime.date.today().isoformat()
                with Horizontal(classes="form-row"):
                    yield Label("Order Date", classes="form-label")
                    yield Input(placeholder="YYYY-MM-DD", value=today, id="order-date")
                with Horizontal(classes="form-row"):
                    yield Label("Expected", classes="form-label")
                    yield Input(placeholder="YYYY-MM-DD", id="expected-delivery")

                yield Label("[bold]Additional[/bold]", classes="section-header")
                with Horizontal(classes="form-row"):
                    yield Label("Invoice #", classes="form-label")
                    yield Input(placeholder="Invoice number", id="invoice-number")
                with Horizontal(classes="form-row"):
                    yield Label("Notes", classes="form-label")
                    yield Input(placeholder="Notes", id="notes")

            with Horizontal(id="form-buttons"):
                yield Button("Save (Ctrl+S)", variant="primary", id="save-btn")
                yield Button("Cancel (Esc)", variant="default", id="cancel-btn")
        yield Footer()

    def action_save(self) -> None:
        self._do_save()

    def _do_save(self) -> None:
        try:
            result = {
                "po_number": self.query_one("#po-number", Input).value,
                "vendor": self.query_one("#vendor-name", Input).value,
                "product": self.query_one("#product-name", Input).value,
                "unit_price": float(self.query_one("#unit-price", Input).value or 0),
                "quantity": int(self.query_one("#quantity", Input).value or 1),
                "currency": self.query_one("#currency", Input).value or "USD",
                "shipping_cost": float(
                    self.query_one("#shipping-cost", Input).value or 0
                )
                or None,
                "tax": float(self.query_one("#tax", Input).value or 0) or None,
                "order_date": self.query_one("#order-date", Input).value or None,
                "expected_delivery": self.query_one("#expected-delivery", Input).value
                or None,
                "invoice_number": self.query_one("#invoice-number", Input).value
                or None,
                "notes": self.query_one("#notes", Input).value or None,
            }
            if self.from_quote:
                result["quote_id"] = self.from_quote.id

            if not result["po_number"]:
                self.notify("PO number is required", severity="error")
                return
            if not result["vendor"]:
                self.notify("Vendor is required", severity="error")
                return
            if not result["product"]:
                self.notify("Product is required", severity="error")
                return

            self.dismiss(result)
        except ValueError as e:
            self.notify(f"Invalid input: {e}", severity="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self._do_save()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class BuyerApp(App):
    """Buyer TUI Application."""

    CSS = """
    #modal-dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }

    #modal-dialog-large {
        width: 70;
        height: 35;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }

    #form-scroll {
        height: 1fr;
        margin: 1 0;
        scrollbar-gutter: stable;
    }

    .section-header {
        margin-top: 1;
        margin-bottom: 0;
        color: $text-muted;
    }

    .feature-row {
        height: 3;
    }

    .feature-row Input {
        width: 1fr;
        margin-right: 1;
    }

    .feature-row Select {
        width: 12;
        margin-right: 1;
    }

    #compare-dialog {
        width: 80;
        height: auto;
        max-height: 40;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }

    #compare-results {
        height: auto;
        max-height: 15;
        margin: 1 0;
        padding: 1;
        background: $surface-darken-1;
        overflow-y: auto;
    }

    #modal-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #modal-buttons {
        margin-top: 1;
        height: 3;
    }

    #modal-buttons Button {
        margin-right: 1;
    }

    Input {
        margin-bottom: 1;
    }

    #search-bar {
        height: 3;
        padding: 0 1;
    }

    #search-input {
        width: 1fr;
    }

    DataTable {
        height: 1fr;
    }

    .tab-content {
        height: 1fr;
    }

    #status-bar {
        height: 1;
        padding: 0 1;
        background: $primary;
    }

    /* Full-screen form styles */
    #fullscreen-form {
        width: 100%;
        height: 1fr;
        padding: 1 2;
        background: $surface;
    }

    #form-title {
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }

    #form-scroll-full {
        height: 1fr;
        margin: 1 0;
        scrollbar-gutter: stable;
    }

    .form-row {
        height: 3;
        margin-bottom: 0;
    }

    .form-label {
        width: 12;
        height: 3;
        content-align: left middle;
    }

    .form-row Input {
        width: 1fr;
    }

    #form-buttons {
        height: 3;
        margin-top: 1;
    }

    #form-buttons Button {
        margin-right: 1;
    }

    .feature-name {
        width: 2fr;
    }

    .feature-type {
        width: 14;
    }

    .feature-unit {
        width: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("a", "add", "Add"),
        Binding("d", "delete", "Delete"),
        Binding("r", "refresh", "Refresh"),
        Binding("s", "search", "Search"),
        Binding("/", "focus_search", "Search"),
        Binding("c", "compare", "Compare"),
        Binding("t", "set_status", "Status"),
        Binding("w", "add_to_watchlist", "Watch"),
        # Tab switching with Ctrl+number
        Binding("ctrl+1", "switch_tab('brands-tab')", "Brands", show=False),
        Binding("ctrl+2", "switch_tab('products-tab')", "Products", show=False),
        Binding("ctrl+3", "switch_tab('vendors-tab')", "Vendors", show=False),
        Binding("ctrl+4", "switch_tab('quotes-tab')", "Quotes", show=False),
        Binding("ctrl+5", "switch_tab('forex-tab')", "Forex", show=False),
        Binding("ctrl+6", "switch_tab('alerts-tab')", "Alerts", show=False),
        Binding("ctrl+7", "switch_tab('lists-tab')", "Lists", show=False),
        Binding("ctrl+8", "switch_tab('watchlist-tab')", "Watchlist", show=False),
        # Vim-style navigation
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("h", "prev_tab", "Prev Tab", show=False),
        Binding("l", "next_tab", "Next Tab", show=False),
        # Editing and filtering
        Binding("e", "edit", "Edit"),
        Binding("f", "filter_quotes", "Filter"),
        # Clipboard and URL operations
        Binding("y", "copy_to_clipboard", "Copy"),
        Binding("o", "open_url", "Open URL"),
        # Sorting (1-7 for columns)
        Binding("1", "sort_column(0)", "Sort Col 1", show=False),
        Binding("2", "sort_column(1)", "Sort Col 2", show=False),
        Binding("3", "sort_column(2)", "Sort Col 3", show=False),
        Binding("4", "sort_column(3)", "Sort Col 4", show=False),
        Binding("5", "sort_column(4)", "Sort Col 5", show=False),
        Binding("6", "sort_column(5)", "Sort Col 6", show=False),
        Binding("7", "sort_column(6)", "Sort Col 7", show=False),
    ]

    # Tab order for h/l navigation
    TAB_ORDER = [
        "brands-tab",
        "products-tab",
        "vendors-tab",
        "quotes-tab",
        "forex-tab",
        "alerts-tab",
        "lists-tab",
        "watchlist-tab",
    ]

    def __init__(self) -> None:
        super().__init__()
        self._session: SessionType | None = None
        self.sort_column_index: int | None = None
        self.sort_reverse: bool = False
        self.quote_filters: dict | None = None

    @property
    def session(self) -> SessionType:
        """Get the database session, asserting it's initialized."""
        assert self._session is not None, "Database session not initialized"
        return self._session

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="search-bar"):
            yield Input(placeholder="Search...", id="search-input")
            yield Button("Search", id="search-btn")
        with TabbedContent():
            with TabPane("Brands", id="brands-tab"):
                yield DataTable(id="brands-table")
            with TabPane("Products", id="products-tab"):
                yield DataTable(id="products-table")
            with TabPane("Vendors", id="vendors-tab"):
                yield DataTable(id="vendors-table")
            with TabPane("Quotes", id="quotes-tab"):
                yield DataTable(id="quotes-table")
            with TabPane("Forex", id="forex-tab"):
                yield DataTable(id="forex-table")
            with TabPane("Alerts", id="alerts-tab"):
                yield DataTable(id="alerts-table")
            with TabPane("Lists", id="lists-tab"):
                yield DataTable(id="lists-table")
            with TabPane("Watchlist", id="watchlist-tab"):
                yield DataTable(id="watchlist-table")
            with TabPane("Specs", id="specs-tab"):
                yield DataTable(id="specs-table")
            with TabPane("POs", id="pos-tab"):
                yield DataTable(id="pos-table")
        yield Footer()

    def on_mount(self) -> None:
        self._session = Session()
        self._setup_tables()
        self._refresh_all()

    def _setup_tables(self) -> None:
        """Setup column headers for all tables."""
        brands_table = self.query_one("#brands-table", DataTable)
        brands_table.add_columns("ID", "Name", "Products")
        brands_table.cursor_type = "row"

        products_table = self.query_one("#products-table", DataTable)
        products_table.add_columns("ID", "Name", "Brand")
        products_table.cursor_type = "row"

        vendors_table = self.query_one("#vendors-table", DataTable)
        vendors_table.add_columns("ID", "Name", "Currency", "Discount", "Quotes", "URL")
        vendors_table.cursor_type = "row"

        quotes_table = self.query_one("#quotes-table", DataTable)
        quotes_table.add_columns(
            "ID",
            "Vendor",
            "Product",
            "Price",
            "As Entered",
            "Total",
            "Status",
            "Trend",
            "Spark",
        )
        quotes_table.cursor_type = "row"

        forex_table = self.query_one("#forex-table", DataTable)
        forex_table.add_columns("ID", "Code", "USD/Unit", "Date")
        forex_table.cursor_type = "row"

        alerts_table = self.query_one("#alerts-table", DataTable)
        alerts_table.add_columns("ID", "Product", "Threshold", "Status", "Triggered At")
        alerts_table.cursor_type = "row"

        lists_table = self.query_one("#lists-table", DataTable)
        lists_table.add_columns("ID", "Name", "Description", "Quotes", "Total")
        lists_table.cursor_type = "row"

        watchlist_table = self.query_one("#watchlist-table", DataTable)
        watchlist_table.add_columns(
            "ID", "Product", "Brand", "Target Price", "Notes", "Status"
        )
        watchlist_table.cursor_type = "row"

        specs_table = self.query_one("#specs-table", DataTable)
        specs_table.add_columns("ID", "Name", "Description", "Features", "Products")
        specs_table.cursor_type = "row"

        pos_table = self.query_one("#pos-table", DataTable)
        pos_table.add_columns(
            "ID", "PO#", "Vendor", "Product", "Qty", "Total", "Status", "Order Date"
        )
        pos_table.cursor_type = "row"

    def _refresh_all(self, filter_by: str | None = None) -> None:
        """Refresh all data tables."""
        self._refresh_brands(filter_by)
        self._refresh_products(filter_by)
        self._refresh_vendors(filter_by)
        self._refresh_quotes(filter_by)
        self._refresh_forex(filter_by)
        self._refresh_alerts(filter_by)
        self._refresh_lists(filter_by)
        self._refresh_watchlist(filter_by)
        self._refresh_specs(filter_by)
        self._refresh_pos(filter_by)

    def _refresh_brands(self, filter_by: str | None = None) -> None:
        table = self.query_one("#brands-table", DataTable)
        table.clear()
        results = BrandService.get_all(self.session, filter_by=filter_by)
        for b in results:
            products = ", ".join([p.name for p in b.products])
            table.add_row(b.id, b.name, products, key=str(b.id))

    def _refresh_products(self, filter_by: str | None = None) -> None:
        table = self.query_one("#products-table", DataTable)
        table.clear()
        results = ProductService.get_all(self.session, filter_by=filter_by)
        for p in results:
            table.add_row(p.id, p.name, p.brand.name, key=str(p.id))

    def _refresh_vendors(self, filter_by: str | None = None) -> None:
        table = self.query_one("#vendors-table", DataTable)
        table.clear()
        results = VendorService.get_all(self.session, filter_by=filter_by)
        for v in results:
            discount = f"{v.discount}%" if v.discount else "-"
            url_indicator = "[link]" if v.url else "-"
            table.add_row(
                v.id,
                v.name,
                v.currency,
                discount,
                len(v.quotes),
                url_indicator,
                key=str(v.id),
            )

    def _refresh_quotes(self, filter_by: str | None = None) -> None:
        table = self.query_one("#quotes-table", DataTable)
        table.clear()
        results = QuoteService.get_all(self.session, filter_by=filter_by)

        # Apply quote filters if set
        if self.quote_filters:
            filtered = []
            for q in results:
                # Vendor filter
                if "vendor" in self.quote_filters:
                    if q.vendor.name != self.quote_filters["vendor"]:
                        continue
                # Brand filter
                if "brand" in self.quote_filters:
                    if q.product.brand.name != self.quote_filters["brand"]:
                        continue
                # Price range filter
                if "min_price" in self.quote_filters:
                    if q.value < self.quote_filters["min_price"]:
                        continue
                if "max_price" in self.quote_filters:
                    if q.value > self.quote_filters["max_price"]:
                        continue
                filtered.append(q)
            results = filtered

        # Get best prices for highlighting
        product_ids = list(set(q.product_id for q in results))
        best_prices = QuoteService.get_best_prices_by_product(self.session, product_ids)

        # Get triggered alerts for yellow highlighting
        triggered_alerts = PriceAlertService.get_triggered(self.session)
        alerted_product_ids = {a.product_id for a in triggered_alerts}

        # Build rows with data for sorting
        rows: list[dict[str, object]] = []
        for q in results:
            product_name = f"{q.product.brand.name} {q.product.name}"

            # Check if this is the best price
            is_best = (
                q.product_id in best_prices and best_prices[q.product_id].id == q.id
            )

            # Format price with highlighting
            price_str = f"{q.value:.2f}"
            if is_best:
                price_text = Text(price_str, style="bold green")
            elif q.product_id in alerted_product_ids:
                price_text = Text(price_str, style="yellow")
            else:
                price_text = Text(price_str)

            # Calculate total cost
            total_str = f"{q.total_cost:.2f}"

            # Compute trend and sparkline
            history = QuoteHistoryService.get_history(self.session, q.id)
            trend = QuoteHistoryService.compute_trend(history)
            trend_symbol = {"up": "^", "down": "v", "stable": "-", "new": "*"}.get(
                trend, "?"
            )

            # Generate sparkline from history
            history_values = [h.new_value for h in reversed(history)]
            if not history_values:
                history_values = [q.value]
            sparkline = make_sparkline(history_values)

            # Format status
            status_str = q.status.capitalize() if q.status else "-"
            if q.status == "received":
                status_text = Text(status_str, style="green")
            elif q.status == "ordered":
                status_text = Text(status_str, style="yellow")
            elif q.status == "considering":
                status_text = Text(status_str, style="cyan")
            else:
                status_text = Text(status_str, style="dim")

            rows.append(
                {
                    "id": q.id,
                    "vendor": q.vendor.name,
                    "product": product_name,
                    "price": q.value,
                    "price_text": price_text,
                    "source_price": q.source_price,
                    "total": q.total_cost,
                    "total_str": total_str,
                    "status": q.status or "",
                    "status_text": status_text,
                    "trend": trend_symbol,
                    "sparkline": sparkline,
                    "key": str(q.id),
                }
            )

        # Sort if needed
        if self.sort_column_index is not None:
            sort_keys = [
                "id",
                "vendor",
                "product",
                "price",
                "source_price",
                "total",
                "status",
                "trend",
                "sparkline",
            ]
            if self.sort_column_index < len(sort_keys):
                sort_key = sort_keys[self.sort_column_index]

                def sort_fn(
                    r: dict[str, object], k: str = sort_key
                ) -> tuple[bool, object]:
                    return (r.get(k) is None, r.get(k, ""))

                rows.sort(key=sort_fn, reverse=self.sort_reverse)

        # Add rows to table
        for row in rows:
            table.add_row(
                row["id"],
                row["vendor"],
                row["product"],
                row["price_text"],
                row["source_price"],
                row["total_str"],
                row["status_text"],
                row["trend"],
                row["sparkline"],
                key=str(row["key"]),
            )

    def _refresh_forex(self, filter_by: str | None = None) -> None:
        table = self.query_one("#forex-table", DataTable)
        table.clear()
        query = select(Forex).order_by(Forex.date.desc())
        if filter_by:
            query = query.where(Forex.code.ilike(f"%{filter_by}%"))
        results = self.session.execute(query).scalars().all()
        for f in results:
            table.add_row(f.id, f.code, f.usd_per_unit, str(f.date), key=str(f.id))

    def _refresh_alerts(self, filter_by: str | None = None) -> None:
        from rich.text import Text

        table = self.query_one("#alerts-table", DataTable)
        table.clear()
        alerts = PriceAlertService.get_all(self.session)

        for a in alerts:
            if filter_by and filter_by.lower() not in a.product.name.lower():
                continue

            if a.triggered_at:
                status_text = Text("Triggered", style="bold yellow")
            elif a.active:
                status_text = Text("Active", style="green")
            else:
                status_text = Text("Inactive", style="dim")

            triggered_at = str(a.triggered_at) if a.triggered_at else "-"

            table.add_row(
                a.id,
                a.product.name,
                f"${a.threshold_value:.2f}",
                status_text,
                triggered_at,
                key=str(a.id),
            )

    def _refresh_lists(self, filter_by: str | None = None) -> None:
        table = self.query_one("#lists-table", DataTable)
        table.clear()
        lists = PurchaseListService.get_all(self.session)

        for plist in lists:
            if filter_by and filter_by.lower() not in plist.name.lower():
                continue

            desc = plist.description or "-"
            if len(desc) > 30:
                desc = desc[:27] + "..."

            table.add_row(
                plist.id,
                plist.name,
                desc,
                len(plist.quotes),
                f"${plist.total_value:.2f}",
                key=str(plist.id),
            )

    def _refresh_watchlist(self, filter_by: str | None = None) -> None:
        from rich.text import Text

        table = self.query_one("#watchlist-table", DataTable)
        table.clear()
        items = WatchlistService.get_all(self.session)

        for w in items:
            if filter_by and filter_by.lower() not in w.product.name.lower():
                continue

            target = w.target_label
            notes = w.notes or "-"
            if len(notes) > 25:
                notes = notes[:22] + "..."

            if w.active:
                status_text = Text("Active", style="green")
            else:
                status_text = Text("Inactive", style="dim")

            table.add_row(
                w.id,
                w.product.name,
                w.product.brand.name,
                target,
                notes,
                status_text,
                key=str(w.id),
            )

    def _refresh_specs(self, filter_by: str | None = None) -> None:
        table = self.query_one("#specs-table", DataTable)
        table.clear()
        specs = SpecificationService.get_all(self.session)

        for s in specs:
            if filter_by and filter_by.lower() not in s.name.lower():
                continue

            desc = s.description or "-"
            if len(desc) > 30:
                desc = desc[:27] + "..."

            table.add_row(
                s.id,
                s.name,
                desc,
                len(s.features),
                len(s.products),
                key=str(s.id),
            )

    def _refresh_pos(self, filter_by: str | None = None) -> None:
        from rich.text import Text

        table = self.query_one("#pos-table", DataTable)
        table.clear()
        pos = PurchaseOrderService.get_all(self.session)

        for po in pos:
            if filter_by and filter_by.lower() not in po.po_number.lower():
                continue

            # Color status
            status_colors = {
                "pending": "yellow",
                "approved": "blue",
                "ordered": "cyan",
                "shipped": "magenta",
                "received": "green",
                "cancelled": "red",
            }
            status_text = Text(po.status, style=status_colors.get(po.status, "white"))

            order_date = str(po.order_date) if po.order_date else "-"

            table.add_row(
                po.id,
                po.po_number,
                po.vendor.name,
                po.product.name,
                po.quantity,
                f"${po.grand_total:.2f}",
                status_text,
                order_date,
                key=str(po.id),
            )

    def _get_active_tab(self) -> str:
        """Get the currently active tab ID."""
        tabbed = self.query_one(TabbedContent)
        return tabbed.active or "brands-tab"

    def action_add(self) -> None:
        """Add a new entity based on current tab."""
        active = self._get_active_tab()
        if active == "brands-tab":
            self.push_screen(AddBrandScreen(), self._on_brand_added)
        elif active == "products-tab":
            self.push_screen(AddProductScreen(), self._on_product_added)
        elif active == "vendors-tab":
            self.push_screen(AddVendorScreen(), self._on_vendor_full_added)
        elif active == "quotes-tab":
            self.push_screen(AddQuoteScreen(), self._on_quote_added)
        elif active == "forex-tab":
            self.push_screen(AddForexScreen(), self._on_forex_added)
        elif active == "alerts-tab":
            self.push_screen(AddAlertScreen(), self._on_alert_added)
        elif active == "lists-tab":
            self.push_screen(AddPurchaseListScreen(), self._on_list_added)
        elif active == "watchlist-tab":
            self.push_screen(AddWatchlistScreen(), self._on_watchlist_added)
        elif active == "specs-tab":
            self.push_screen(AddSpecificationScreen(), self._on_specification_added)
        elif active == "pos-tab":
            self.push_screen(AddPurchaseOrderScreen(), self._on_purchase_order_added)

    def _on_vendor_full_added(self, result: dict | None) -> None:
        if result:
            try:
                vendor = VendorService.create(
                    self.session,
                    name=result["name"],
                    currency=result["currency"],
                    discount_code=result["discount_code"],
                    discount=result["discount"],
                    url=result["url"],
                    contact_person=result["contact_person"],
                    email=result["email"],
                    phone=result["phone"],
                    address_line1=result["address_line1"],
                    address_line2=result["address_line2"],
                    city=result["city"],
                    state=result["state"],
                    postal_code=result["postal_code"],
                    country=result["country"],
                    tax_id=result["tax_id"],
                    payment_terms=result["payment_terms"],
                )
                self.notify(f"Added vendor: {vendor.name}")
                self._refresh_vendors()
            except DuplicateError:
                self.notify(
                    f"Vendor '{result['name']}' already exists", severity="warning"
                )
            except ValidationError as e:
                self.notify(str(e), severity="error")

    def _on_specification_added(self, result: dict | None) -> None:
        if result:
            try:
                spec = SpecificationService.create(
                    self.session,
                    name=result["name"],
                    description=result["description"],
                )
                for feature in result.get("features", []):
                    SpecificationService.add_feature(
                        self.session,
                        spec_name=spec.name,
                        feature_name=feature["name"],
                        data_type=feature.get("data_type", "text"),
                        unit=feature.get("unit"),
                        is_required=feature.get("is_required", False),
                    )
                self.notify(
                    f"Added specification: {spec.name} ({len(result.get('features', []))} features)"
                )
                self._refresh_specs()
            except DuplicateError:
                self.notify(
                    f"Specification '{result['name']}' already exists",
                    severity="warning",
                )
            except ValidationError as e:
                self.notify(str(e), severity="error")

    def _on_purchase_order_added(self, result: dict | None) -> None:
        if result:
            try:
                po = PurchaseOrderService.create(
                    self.session,
                    po_number=result["po_number"],
                    vendor_name=result["vendor"],
                    product_name=result["product"],
                    unit_price=result["unit_price"],
                    quantity=result["quantity"],
                    currency=result["currency"],
                    quote_id=result.get("quote_id"),
                    order_date=result.get("order_date"),
                    expected_delivery=result.get("expected_delivery"),
                    shipping_cost=result.get("shipping_cost"),
                    tax=result.get("tax"),
                    invoice_number=result.get("invoice_number"),
                    notes=result.get("notes"),
                )
                self.notify(f"Created PO: {po.po_number} (${po.grand_total:.2f})")
                self._refresh_pos()
            except DuplicateError:
                self.notify(
                    f"PO '{result['po_number']}' already exists", severity="warning"
                )
            except NotFoundError as e:
                self.notify(str(e), severity="error")
            except ValidationError as e:
                self.notify(str(e), severity="error")

    def _on_brand_added(self, result: str | None) -> None:
        if result:
            try:
                BrandService.create(self.session, result)
                self.notify(f"Added brand: {result}")
                self._refresh_brands()
            except DuplicateError:
                self.notify(f"Brand '{result}' already exists", severity="warning")
            except ValidationError as e:
                self.notify(str(e), severity="error")

    def _on_product_added(self, result: tuple[str, str] | None) -> None:
        if result:
            brand_name, product_name = result
            try:
                if not Brand.by_name(self.session, brand_name):
                    BrandService.create(self.session, brand_name)
                    self.notify(f"Created brand: {brand_name}")

                ProductService.create(self.session, product_name, brand_name)
                self.notify(f"Added product: {product_name}")
                self._refresh_products()
                self._refresh_brands()
            except DuplicateError:
                self.notify(
                    f"Product '{product_name}' already exists", severity="warning"
                )
            except (ValidationError, NotFoundError) as e:
                self.notify(str(e), severity="error")
            except Exception as e:
                self.session.rollback()
                self.notify(str(e), severity="error")

    def _on_quote_added(
        self, result: tuple[str, str, str | None, float] | None
    ) -> None:
        if result:
            vendor_name, product_name, brand_name, price = result
            try:
                QuoteService.create(
                    self.session,
                    vendor_name=vendor_name,
                    product_name=product_name,
                    price=price,
                    brand_name=brand_name,
                )
                self.notify(f"Added quote: {vendor_name} -> {product_name} = {price}")
                self._refresh_quotes()
            except (ValidationError, NotFoundError) as e:
                self.notify(str(e), severity="error")
            except Exception as e:
                self.session.rollback()
                self.notify(str(e), severity="error")

    def _on_forex_added(self, result: tuple[str, float, str | None] | None) -> None:
        if result:
            import datetime

            code, rate, date_str = result
            try:
                if date_str:
                    date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                else:
                    date = datetime.date.today()

                ForexService.create(self.session, code, rate, date=date)
                self.notify(f"Added forex rate: {code} = {rate}")
                self._refresh_forex()
            except DuplicateError:
                self.notify(
                    f"Forex rate for '{code}' on {date} already exists",
                    severity="warning",
                )
            except ValidationError as e:
                self.notify(str(e), severity="error")
            except ValueError:
                self.notify("Invalid date format. Use YYYY-MM-DD", severity="error")
            except Exception as e:
                self.session.rollback()
                self.notify(str(e), severity="error")

    def _on_alert_added(self, result: tuple[str, float] | None) -> None:
        if result:
            product_name, threshold = result
            try:
                PriceAlertService.create(self.session, product_name, threshold)
                self.notify(f"Added alert for '{product_name}' at ${threshold:.2f}")
                self._refresh_alerts()
            except NotFoundError as e:
                self.notify(str(e), severity="error")
            except ValidationError as e:
                self.notify(str(e), severity="error")
            except Exception as e:
                self.session.rollback()
                self.notify(str(e), severity="error")

    def _on_list_added(self, result: tuple[str, str | None] | None) -> None:
        if result:
            name, description = result
            try:
                PurchaseListService.create(self.session, name, description)
                self.notify(f"Created purchase list '{name}'")
                self._refresh_lists()
            except DuplicateError as e:
                self.notify(str(e), severity="warning")
            except ValidationError as e:
                self.notify(str(e), severity="error")
            except Exception as e:
                self.session.rollback()
                self.notify(str(e), severity="error")

    def _on_watchlist_added(
        self, result: tuple[str, float | None, str | None, str | None] | None
    ) -> None:
        if result:
            product_name, target_price, notes, currency = result
            try:
                if target_price is None:
                    quotes = PurchaseListService.consider_product(
                        self.session, product_name
                    )
                    self.notify(
                        f"Added {len(quotes)} quote(s) for '{product_name}' "
                        "to Considering"
                    )
                    self._refresh_lists()
                else:
                    WatchlistService.create(
                        self.session, product_name, target_price, notes, currency
                    )
                    self.notify(f"Added '{product_name}' to watchlist")
                    self._refresh_watchlist()
            except NotFoundError as e:
                self.notify(str(e), severity="error")
            except DuplicateError as e:
                self.notify(str(e), severity="warning")
            except Exception as e:
                self.session.rollback()
                self.notify(str(e), severity="error")

    def action_delete(self) -> None:
        """Delete the selected entity."""
        active = self._get_active_tab()
        table_id = active.replace("-tab", "-table")
        table = self.query_one(f"#{table_id}", DataTable)

        if table.cursor_row is None or table.row_count == 0:
            self.notify("No row selected", severity="warning")
            return

        row_key = table.get_row_at(table.cursor_row)
        entity_id = row_key[0]
        entity_name = row_key[1]

        entity_type = active.replace("-tab", "").rstrip("s")
        self.push_screen(
            ConfirmDeleteModal(entity_type, str(entity_name)),
            lambda confirmed: self._on_delete_confirmed(
                confirmed, entity_type, entity_id
            ),
        )

    def _on_delete_confirmed(
        self, confirmed: bool, entity_type: str, entity_id: int
    ) -> None:
        if not confirmed:
            return

        try:
            entity: Base | None = None
            if entity_type == "brand":
                entity = self.session.get(Brand, entity_id)
            elif entity_type == "product":
                entity = self.session.get(Product, entity_id)
            elif entity_type == "vendor":
                entity = self.session.get(Vendor, entity_id)
            elif entity_type == "quote":
                entity = self.session.get(Quote, entity_id)
            elif entity_type == "forex":
                entity = self.session.get(Forex, entity_id)
            elif entity_type == "alert":
                entity = self.session.get(PriceAlert, entity_id)
            elif entity_type == "list":
                entity = self.session.get(PurchaseList, entity_id)
            elif entity_type == "watchlist":
                entity = self.session.get(Watchlist, entity_id)

            if entity:
                # Named entities go through their service so dependent rows
                # (price history, alerts, watchlist entries) go with them.
                deleters = {
                    "brand": BrandService.delete,
                    "product": ProductService.delete,
                    "vendor": VendorService.delete,
                }
                if isinstance(entity, (Brand, Product, Vendor)):
                    deleters[entity_type](self.session, entity.name)
                elif entity_type == "quote":
                    QuoteService.delete(self.session, entity_id)
                else:
                    self.session.delete(entity)
                    self.session.commit()
                self.notify(f"Deleted {entity_type}")
                self._refresh_all()
        except (ValidationError, NotFoundError) as e:
            self.notify(str(e), severity="error")
        except Exception as e:
            self.session.rollback()
            self.notify(str(e), severity="error")

    def action_refresh(self) -> None:
        """Refresh all tables."""
        self._refresh_all()
        self.notify("Refreshed")

    def action_search(self) -> None:
        """Perform search with current input value."""
        search_input = self.query_one("#search-input", Input)
        query = search_input.value.strip() or None
        self._refresh_all(filter_by=query)
        if query:
            self.notify(f"Filtered by: {query}")
        else:
            self.notify("Filter cleared")

    def action_focus_search(self) -> None:
        """Focus the search input."""
        self.query_one("#search-input", Input).focus()

    def action_switch_tab(self, tab_id: str) -> None:
        """Switch to a specific tab."""
        tabbed = self.query_one(TabbedContent)
        tabbed.active = tab_id

    def action_prev_tab(self) -> None:
        """Switch to previous tab (vim h key)."""
        current = self._get_active_tab()
        if current in self.TAB_ORDER:
            idx = self.TAB_ORDER.index(current)
            new_idx = (idx - 1) % len(self.TAB_ORDER)
            self.action_switch_tab(self.TAB_ORDER[new_idx])

    def action_next_tab(self) -> None:
        """Switch to next tab (vim l key)."""
        current = self._get_active_tab()
        if current in self.TAB_ORDER:
            idx = self.TAB_ORDER.index(current)
            new_idx = (idx + 1) % len(self.TAB_ORDER)
            self.action_switch_tab(self.TAB_ORDER[new_idx])

    def action_cursor_down(self) -> None:
        """Move cursor down in current table (vim j key)."""
        active = self._get_active_tab()
        table_id = active.replace("-tab", "-table")
        try:
            table = self.query_one(f"#{table_id}", DataTable)
            table.action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        """Move cursor up in current table (vim k key)."""
        active = self._get_active_tab()
        table_id = active.replace("-tab", "-table")
        try:
            table = self.query_one(f"#{table_id}", DataTable)
            table.action_cursor_up()
        except Exception:
            pass

    def action_sort_column(self, col_index: int) -> None:
        """Sort current table by column index."""
        active = self._get_active_tab()
        table_id = active.replace("-tab", "-table")
        table = self.query_one(f"#{table_id}", DataTable)

        # Toggle sort direction if same column
        if self.sort_column_index == col_index:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column_index = col_index
            self.sort_reverse = False

        direction = "desc" if self.sort_reverse else "asc"
        self.notify(f"Sorting by column {col_index + 1} ({direction})")

        # Refresh the current tab's data
        if active == "quotes-tab":
            self._refresh_quotes()
        else:
            # For other tables, use DataTable's built-in sort
            try:
                column_keys = list(table.columns.keys())
                if col_index < len(column_keys):
                    table.sort(column_keys[col_index], reverse=self.sort_reverse)
                else:
                    self.notify("Invalid column index", severity="warning")
            except (IndexError, KeyError):
                self.notify("Cannot sort by this column", severity="warning")

    def action_edit(self) -> None:
        """Edit the selected row."""
        active = self._get_active_tab()
        table_id = active.replace("-tab", "-table")
        table = self.query_one(f"#{table_id}", DataTable)

        if table.cursor_row is None or table.row_count == 0:
            self.notify("No row selected", severity="warning")
            return

        try:
            row_data = table.get_row_at(table.cursor_row)
        except RowDoesNotExist:
            self.notify("No row selected", severity="warning")
            return

        entity_id = row_data[0]

        # Handle different entity types
        if active == "brands-tab":
            self.push_screen(
                EditCellModal("Name", str(row_data[1])),
                lambda result: self._on_brand_edited(result, entity_id),
            )
        elif active == "products-tab":
            self.push_screen(
                EditCellModal("Name", str(row_data[1])),
                lambda result: self._on_product_edited(result, entity_id),
            )
        elif active == "vendors-tab":
            self.push_screen(
                EditCellModal("Name", str(row_data[1])),
                lambda result: self._on_vendor_edited(result, entity_id),
            )
        elif active == "quotes-tab":
            # Edit price for quotes
            price_val = row_data[3]
            price_str = (
                str(price_val.plain) if hasattr(price_val, "plain") else str(price_val)
            )
            self.push_screen(
                EditCellModal("Price", price_str),
                lambda result: self._on_quote_edited(result, entity_id),
            )
        else:
            self.notify("Editing not supported for this tab", severity="warning")

    def _on_brand_edited(self, result: tuple[str, str] | None, entity_id: int) -> None:
        if result:
            _, new_value = result
            try:
                brand = self.session.get(Brand, entity_id)
                if brand:
                    BrandService.update(self.session, brand.name, new_value)
                    self.notify(f"Updated brand to: {new_value}")
                    self._refresh_brands()
            except (ValidationError, DuplicateError, NotFoundError) as e:
                self.notify(str(e), severity="error")
            except Exception as e:
                self.session.rollback()
                self.notify(str(e), severity="error")

    def _on_product_edited(
        self, result: tuple[str, str] | None, entity_id: int
    ) -> None:
        if result:
            _, new_value = result
            try:
                product = self.session.get(Product, entity_id)
                if product:
                    ProductService.update(self.session, product.name, new_value)
                    self.notify(f"Updated product to: {new_value}")
                    self._refresh_products()
            except (ValidationError, DuplicateError, NotFoundError) as e:
                self.notify(str(e), severity="error")
            except Exception as e:
                self.session.rollback()
                self.notify(str(e), severity="error")

    def _on_vendor_edited(self, result: tuple[str, str] | None, entity_id: int) -> None:
        if result:
            _, new_value = result
            try:
                vendor = self.session.get(Vendor, entity_id)
                if vendor:
                    VendorService.update(self.session, vendor.name, new_value)
                    self.notify(f"Updated vendor to: {new_value}")
                    self._refresh_vendors()
            except (ValidationError, DuplicateError, NotFoundError) as e:
                self.notify(str(e), severity="error")
            except Exception as e:
                self.session.rollback()
                self.notify(str(e), severity="error")

    def _on_quote_edited(self, result: tuple[str, str] | None, entity_id: int) -> None:
        if result:
            _, new_value = result
            try:
                new_price = float(new_value)
                QuoteService.update_price(self.session, entity_id, new_price)
                self.notify(f"Updated quote price to: ${new_price:.2f}")
                self._refresh_quotes()
            except ValueError:
                self.notify("Invalid price value", severity="error")
            except Exception as e:
                self.session.rollback()
                self.notify(str(e), severity="error")

    def action_filter_quotes(self) -> None:
        """Open quote filter modal."""
        active = self._get_active_tab()
        if active != "quotes-tab":
            # Switch to quotes tab first
            self.action_switch_tab("quotes-tab")

        # Get list of vendors and brands for filter options
        vendors = [v.name for v in VendorService.get_all(self.session)]
        brands = [b.name for b in BrandService.get_all(self.session)]

        self.push_screen(QuoteFilterModal(vendors, brands), self._on_filter_applied)

    def _on_filter_applied(self, result: dict | None) -> None:
        if result is None:
            # Cancelled
            return
        elif result == {}:
            # Clear filters
            self.quote_filters = None
            self.notify("Filters cleared")
        else:
            self.quote_filters = result
            filter_desc = ", ".join(f"{k}={v}" for k, v in result.items())
            self.notify(f"Filters applied: {filter_desc}")

        self._refresh_quotes()

    def action_set_status(self) -> None:
        """Set status on selected quote."""
        active = self._get_active_tab()
        if active != "quotes-tab":
            self.notify("Select a quote to set status", severity="warning")
            return

        table = self.query_one("#quotes-table", DataTable)
        if table.cursor_row is None or table.row_count == 0:
            self.notify("No quote selected", severity="warning")
            return

        try:
            row_data = table.get_row_at(table.cursor_row)
            quote_id = row_data[0]
            self.push_screen(
                SetQuoteStatusModal(),
                lambda status: self._on_status_set(status, quote_id),
            )
        except RowDoesNotExist:
            self.notify("No quote selected", severity="warning")

    def _on_status_set(self, status: str | None, quote_id: int) -> None:
        if status:
            try:
                QuoteService.set_status(self.session, quote_id, status)
                self.notify(f"Set status '{status}' for quote #{quote_id}")
                self._refresh_quotes()
            except Exception as e:
                self.session.rollback()
                self.notify(str(e), severity="error")

    def action_add_to_watchlist(self) -> None:
        """Add selected product to watchlist."""
        active = self._get_active_tab()
        if active == "products-tab":
            table = self.query_one("#products-table", DataTable)
            if table.cursor_row is None or table.row_count == 0:
                self.notify("No product selected", severity="warning")
                return

            try:
                self.push_screen(
                    AddWatchlistScreen(),
                    self._on_watchlist_added,
                )
            except RowDoesNotExist:
                self.notify("No product selected", severity="warning")
        else:
            self.push_screen(AddWatchlistScreen(), self._on_watchlist_added)

    def action_compare(self) -> None:
        """Open price comparison modal."""
        # Get products, categories, and brands for dropdowns
        products = [p.name for p in ProductService.get_all(self.session)]
        categories = ComparisonService.get_categories(self.session)
        brands = [b.name for b in BrandService.get_all(self.session)]

        self.push_screen(
            CompareModal(self.session, products, categories, brands),
            lambda _: None,  # No callback needed, modal handles display
        )

    def action_copy_to_clipboard(self) -> None:
        """Copy selected entity to clipboard."""
        from .services import ClipboardService, NotFoundError, ServiceError

        active = self._get_active_tab()
        table_id = active.replace("-tab", "-table")

        try:
            table = self.query_one(f"#{table_id}", DataTable)
            if table.cursor_row is None or table.row_count == 0:
                self.notify("No row selected", severity="warning")
                return

            row_data = table.get_row_at(table.cursor_row)

            if active == "quotes-tab":
                quote_id = row_data[0]
                ClipboardService.copy_quote(self.session, quote_id)
                self.notify(f"Copied quote #{quote_id} to clipboard")
            elif active == "products-tab":
                product_name = row_data[1]
                ClipboardService.copy_product(self.session, product_name)
                self.notify("Copied product to clipboard")
            elif active == "vendors-tab":
                vendor_name = row_data[1]
                ClipboardService.copy_vendor(self.session, vendor_name)
                self.notify("Copied vendor to clipboard")
            else:
                self.notify("Copy not available for this tab", severity="warning")

        except (NotFoundError, ServiceError) as e:
            self.notify(f"Error: {e}", severity="error")
        except Exception as e:
            self.notify(f"Copy failed: {e}", severity="error")

    def action_open_url(self) -> None:
        """Open URL for selected vendor."""
        from .services import (
            VendorURLService,
            NotFoundError,
            ValidationError,
            ServiceError,
        )

        active = self._get_active_tab()

        if active != "vendors-tab":
            self.notify("Select a vendor to open URL", severity="warning")
            return

        try:
            table = self.query_one("#vendors-table", DataTable)
            if table.cursor_row is None or table.row_count == 0:
                self.notify("No vendor selected", severity="warning")
                return

            row_data = table.get_row_at(table.cursor_row)
            vendor_name = row_data[1]

            url = VendorURLService.open_url(self.session, vendor_name)
            self.notify(f"Opening {url}")

        except ValidationError as e:
            self.notify(f"{e}", severity="warning")
        except (NotFoundError, ServiceError) as e:
            self.notify(f"Error: {e}", severity="error")
        except Exception as e:
            self.notify(f"Failed to open URL: {e}", severity="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "search-btn":
            self.action_search()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            self.action_search()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Handle column header click for sorting."""
        col_index = event.column_index

        # Toggle sort direction if same column
        if self.sort_column_index == col_index:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column_index = col_index
            self.sort_reverse = False

        direction = "desc" if self.sort_reverse else "asc"
        col_label = event.column_key
        self.notify(f"Sorting by {col_label} ({direction})")

        # Refresh appropriate table
        active = self._get_active_tab()
        if active == "quotes-tab":
            self._refresh_quotes()
        elif active == "brands-tab":
            self._refresh_brands()
        elif active == "products-tab":
            self._refresh_products()
        elif active == "vendors-tab":
            self._refresh_vendors()
        elif active == "forex-tab":
            self._refresh_forex()
        elif active == "alerts-tab":
            self._refresh_alerts()
        elif active == "lists-tab":
            self._refresh_lists()
        elif active == "watchlist-tab":
            self._refresh_watchlist()

    def on_unmount(self) -> None:
        if self.session:
            self.session.close()


def main() -> None:
    """Entry point for TUI."""
    app = BuyerApp()
    app.run()


if __name__ == "__main__":
    main()
