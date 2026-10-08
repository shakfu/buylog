import string

import factory
import factory.fuzzy

# from . import models
import buylog.models as models

ALPHANUM = string.ascii_letters + string.digits


class BrandFactory(factory.alchemy.SQLAlchemyModelFactory):
    id = factory.Sequence(lambda n: "%s" % n)
    name = factory.Faker("company")

    class Meta:
        model = models.Brand


class ProductFactory(factory.alchemy.SQLAlchemyModelFactory):
    id = factory.Sequence(lambda n: "%s" % n)
    name = factory.Faker("word")
    brand = factory.SubFactory(BrandFactory)

    class Meta:
        model = models.Product


class VendorFactory(factory.alchemy.SQLAlchemyModelFactory):
    id = factory.Sequence(lambda n: "%s" % n)
    name = factory.Faker("company")
    currency = factory.Faker("currency_code")
    discount_code = factory.fuzzy.FuzzyText(length=8, chars=ALPHANUM)
    discount = factory.fuzzy.FuzzyFloat(0.05, 0.15)
    url = factory.Faker("url")
    contact_person = factory.Faker("name")
    email = factory.Faker("email")
    phone = factory.Faker("phone_number")
    address_line1 = factory.Faker("street_address")
    address_line2 = factory.Faker("secondary_address")
    city = factory.Faker("city")
    state = factory.Faker("state")
    postal_code = factory.Faker("postcode")
    country = factory.Faker("country")
    tax_id = factory.Faker("ssn")
    payment_terms = factory.fuzzy.FuzzyChoice(
        ["Net 30", "Net 60", "Net 90", "Due on receipt"]
    )

    class Meta:
        model = models.Vendor


class QuoteFactory(factory.alchemy.SQLAlchemyModelFactory):
    id = factory.Sequence(lambda n: "%s" % n)
    currency = factory.Faker("currency_code")
    discount = factory.fuzzy.FuzzyFloat(0.05, 0.15)
    value = factory.fuzzy.FuzzyFloat(50.0, 1000.0)
    product = factory.SubFactory(ProductFactory)
    vendor = factory.SubFactory(VendorFactory)

    class Meta:
        model = models.Quote


class QuoteHistoryFactory(factory.alchemy.SQLAlchemyModelFactory):
    id = factory.Sequence(lambda n: "%s" % n)
    quote = factory.SubFactory(QuoteFactory)
    old_value = factory.fuzzy.FuzzyFloat(50.0, 1000.0)
    new_value = factory.fuzzy.FuzzyFloat(50.0, 1000.0)
    change_type = "update"

    class Meta:
        model = models.QuoteHistory


class PriceAlertFactory(factory.alchemy.SQLAlchemyModelFactory):
    id = factory.Sequence(lambda n: "%s" % n)
    product = factory.SubFactory(ProductFactory)
    threshold_value = factory.fuzzy.FuzzyFloat(50.0, 1000.0)
    threshold_currency = "USD"
    active = 1

    class Meta:
        model = models.PriceAlert


class SpecificationFactory(factory.alchemy.SQLAlchemyModelFactory):
    id = factory.Sequence(lambda n: "%s" % n)
    name = factory.Faker("word")
    description = factory.Faker("sentence")

    class Meta:
        model = models.Specification


class SpecificationFeatureFactory(factory.alchemy.SQLAlchemyModelFactory):
    id = factory.Sequence(lambda n: "%s" % n)
    specification = factory.SubFactory(SpecificationFactory)
    name = factory.Faker("word")
    data_type = factory.fuzzy.FuzzyChoice(["text", "number", "boolean"])
    unit = None
    is_required = 0
    min_value = None
    max_value = None

    class Meta:
        model = models.SpecificationFeature


class ProductFeatureFactory(factory.alchemy.SQLAlchemyModelFactory):
    id = factory.Sequence(lambda n: "%s" % n)
    product = factory.SubFactory(ProductFactory)
    specification_feature = factory.SubFactory(SpecificationFeatureFactory)
    value_text = factory.Faker("word")
    value_number = None
    value_boolean = None

    class Meta:
        model = models.ProductFeature


class PurchaseOrderFactory(factory.alchemy.SQLAlchemyModelFactory):
    id = factory.Sequence(lambda n: "%s" % n)
    po_number = factory.Sequence(lambda n: "PO-%05d" % n)
    vendor = factory.SubFactory(VendorFactory)
    product = factory.SubFactory(ProductFactory)
    quote = None
    status = models.PO_STATUS_PENDING
    order_date = factory.Faker("date_object")
    expected_delivery = None
    actual_delivery = None
    quantity = factory.fuzzy.FuzzyInteger(1, 10)
    unit_price = factory.fuzzy.FuzzyFloat(50.0, 1000.0)
    currency = "USD"
    shipping_cost = factory.fuzzy.FuzzyFloat(0.0, 50.0)
    tax = factory.fuzzy.FuzzyFloat(0.0, 100.0)
    invoice_number = None
    notes = None

    @factory.lazy_attribute
    def total_amount(self):
        return self.unit_price * self.quantity

    @factory.lazy_attribute
    def grand_total(self):
        shipping = self.shipping_cost or 0.0
        tax = self.tax or 0.0
        return self.total_amount + shipping + tax

    class Meta:
        model = models.PurchaseOrder
