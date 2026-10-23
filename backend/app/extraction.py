"""Extraction templates: the fields the enrich stage asks the model to read from a project's
documents of each kind (app/indexing/enrich.py), so that every invoice of a project has its
`invoice_number` and `total_amount` under those names, as facts the agent's `query_facts`
filters and compares and a member checks.

A template is for one kind of document (`doc_type`, lowercase words such as "invoice"). A project
has its own (`ExtractionTemplate` rows, made and changed from the library's Templates page,
app/api/templates.py), and the built-in ones below apply to the kinds it has none of: a project's
own invoice template replaces the built-in one, and deleting it brings that back. The built-in
ones are code, the same for every project, moved by a release; their field names follow the
names the stage prefers without a template (FACT_INSTRUCTIONS), so that a project's facts are
named alike whichever way they were read.

Which template a document is for is the model's to say, since a document's kind is known only
once it is read: the stage lists the project's templates and the reply names the one the
document is (indexing-approach.md, "Extraction templates"). The feature is behind the flag
`extraction_templates` (app/flags.py): with it off, the stage lists none and asks as before.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExtractionTemplate

# How many templates of its own a project may have. Every template in force is listed in the
# enrich stage's prompt, its fields described in full while the listing is short and named alone
# past LISTING_CHARS (app/indexing/enrich.py); this and a template's own ceilings
# (MAX_TEMPLATE_FIELDS in app/schemas.py) bound what reading each document costs.
MAX_TEMPLATES = 12


@dataclass(frozen=True)
class TemplateField:
    """A field of a template: the fact's name (snake_case, as `normalize_fact_name` makes it),
    its type (one of FACT_TYPES), what it is, whether a document of the kind should state it, and
    values as documents write them."""

    name: str
    type: str
    description: str = ""
    required: bool = False
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class Template:
    """A template in force for a project: its own (`id` its row's) or a built-in one (None)."""

    doc_type: str
    title: str
    description: str
    fields: tuple[TemplateField, ...]
    id: int | None = None

    def field(self, name: str) -> TemplateField | None:
        return next((field for field in self.fields if field.name == name), None)


def _field(
    name: str,
    type: str,
    description: str,
    required: bool = False,
    examples: tuple[str, ...] = (),
) -> TemplateField:
    return TemplateField(name, type, description, required, examples)


# The built-in templates, in the order they are listed. A change to one changes what the next
# run of the enrich stage reads from every project's documents of its kind that have no template
# of their own; the documents read before keep their facts until they are indexed again.
BUILT_IN: tuple[Template, ...] = (
    Template(
        "invoice",
        "Invoice",
        "A bill for goods or services supplied, asking for payment.",
        (
            _field("invoice_number", "text", "The invoice's own number.", True, ("INV-2026-0412",)),
            _field("issue_date", "date", "The date the invoice was issued.", True),
            _field("due_date", "date", "The date payment is due, as the invoice states it."),
            _field(
                "vendor_name",
                "text",
                "Who issued the invoice and is to be paid.",
                True,
                ("Acme Industrial Supplies Ltd.",),
            ),
            _field("customer_name", "text", "Who is billed.", True),
            _field("po_number", "text", "The number of the purchase order the invoice is for."),
            _field(
                "contract_reference",
                "text",
                "The contract or agreement the invoice bills under, as the invoice names it.",
                examples=("Master Services Agreement (1 March 2026)",),
            ),
            _field(
                "line_item",
                "money",
                "Each line billed, as written: what was supplied, the quantity, the unit price "
                "and the line's amount, which is the number.",
                examples=("Hex bolts M10 (pallet), 40 x 185.00 = 7,400.00",),
            ),
            _field(
                "unit_price",
                "money",
                "Each line's unit price, written with what the line is for as the line names it; "
                "the price is the number.",
                examples=("Hex bolts M10 (pallet): 185.00",),
            ),
            _field(
                "quantity",
                "number",
                "Each line's quantity, written with what the line is for as the line names it; "
                "the quantity is the number.",
                examples=("Hex bolts M10 (pallet): 40",),
            ),
            _field("subtotal_amount", "money", "The total before tax."),
            _field(
                "tax_amount",
                "money",
                "The tax charged, with its rate when stated.",
                examples=("VAT 19%: 1,881.00",),
            ),
            _field("total_amount", "money", "The total due, tax included.", True),
            _field(
                "payment_terms",
                "text",
                "When and how payment is to be made.",
                examples=("30 days",),
            ),
            _field(
                "bank_account",
                "text",
                "The account to pay into: an IBAN or an account number, as written.",
            ),
            _field("vendor_tax_id", "text", "The issuer's VAT or tax identification number."),
        ),
    ),
    Template(
        "contract",
        "Contract",
        "An agreement between parties: who they are, when it runs, what is paid and on what terms.",
        (
            _field(
                "party",
                "text",
                "Each party to the contract, with its role in brackets when stated.",
                True,
                ("Acme Industrial Supplies Ltd. (Supplier)",),
            ),
            _field("effective_date", "date", "The date the contract takes effect.", True),
            _field("expiry_date", "date", "The date the contract ends, when it states one."),
            _field("term_length", "text", "How long the contract runs.", examples=("24 months",)),
            _field("renewal_terms", "text", "Whether and how the contract renews."),
            _field(
                "notice_period",
                "text",
                "The notice a party gives to end the contract.",
                examples=("ninety (90) days' written notice",),
            ),
            _field("payment_terms", "text", "When payment is due."),
            _field(
                "price",
                "money",
                "Each price, fee or rate the contract sets, with what it is for.",
            ),
            _field(
                "penalty",
                "text",
                "Each penalty, service credit or interest the contract sets, and its trigger.",
                examples=("Late payments accrue interest at 1.5% per month",),
            ),
            _field("liability_cap", "money", "The most a party is liable for, when it is capped."),
            _field("governing_law", "text", "The law that governs the contract."),
            _field("signature_date", "date", "Each date a party signed."),
            _field("bank_account", "text", "The account payments are to be made into."),
        ),
    ),
    Template(
        "purchase order",
        "Purchase order",
        "An order a buyer places with a supplier for goods or services.",
        (
            _field("po_number", "text", "The order's own number.", True),
            _field("issue_date", "date", "The date the order was placed.", True),
            _field("customer_name", "text", "The buyer placing the order.", True),
            _field("vendor_name", "text", "The supplier the order is placed with.", True),
            _field("delivery_date", "date", "The date delivery is asked for."),
            _field("delivery_address", "text", "Where the goods are to be delivered."),
            _field(
                "line_item",
                "money",
                "Each line ordered, as written: what, the quantity, the unit price and the "
                "line's amount, which is the number.",
            ),
            _field(
                "unit_price",
                "money",
                "Each line's unit price, written with what the line is for as the line names it; "
                "the price is the number.",
            ),
            _field(
                "quantity",
                "number",
                "Each line's quantity, written with what the line is for as the line names it; "
                "the quantity is the number.",
            ),
            _field("total_amount", "money", "The order's total.", True),
            _field("payment_terms", "text", "When and how payment is to be made."),
            _field(
                "contract_reference",
                "text",
                "The contract or agreement the order is placed under, as the order names it.",
            ),
        ),
    ),
    Template(
        "receipt",
        "Receipt",
        "Proof of a payment made at a shop, a restaurant or for a service.",
        (
            _field("vendor_name", "text", "The merchant paid.", True),
            _field("issue_date", "date", "The date of the purchase.", True),
            _field("receipt_number", "text", "The receipt's or the transaction's number."),
            _field("line_item", "money", "Each item bought, with its quantity and price."),
            _field("tax_amount", "money", "The tax included, with its rate when stated."),
            _field("total_amount", "money", "The amount paid.", True),
            _field(
                "payment_method",
                "text",
                "How it was paid: in cash, by card, with the card's last digits when printed, "
                "or otherwise.",
                examples=("Visa ending 4421",),
            ),
        ),
    ),
    Template(
        "bank statement",
        "Bank statement",
        "A bank's statement of an account's transactions over a period.",
        (
            _field("bank_name", "text", "The bank that issued the statement.", True),
            _field("account_holder", "text", "Whose account it is.", True),
            _field("account_number", "text", "The account's number or IBAN, as printed.", True),
            _field("period_start", "date", "The first day the statement covers.", True),
            _field("period_end", "date", "The last day the statement covers.", True),
            _field("opening_balance", "money", "The balance at the start of the period.", True),
            _field("closing_balance", "money", "The balance at the end of the period.", True),
            _field("total_credits", "money", "The sum paid in over the period, when stated."),
            _field("total_debits", "money", "The sum paid out over the period, when stated."),
        ),
    ),
    Template(
        "delivery note",
        "Delivery note",
        "A note that comes with a delivery and lists what was delivered.",
        (
            _field("delivery_note_number", "text", "The note's own number.", True),
            _field("delivery_date", "date", "The date of the delivery.", True),
            _field("vendor_name", "text", "The supplier delivering.", True),
            _field("customer_name", "text", "Who the goods were delivered to.", True),
            _field("po_number", "text", "The purchase order the delivery is for."),
            _field("delivery_address", "text", "Where the goods were delivered."),
            _field(
                "delivered_item",
                "number",
                "Each item delivered, as written, with its quantity, which is the number.",
                examples=("Hex bolts M10 (pallet): 40",),
            ),
            _field("received_by", "text", "Who received or signed for the delivery."),
        ),
    ),
    Template(
        "price list",
        "Price list",
        "A supplier's list of the prices of what it sells.",
        (
            _field("vendor_name", "text", "Whose prices they are.", True),
            _field("issue_date", "date", "The date the list was issued or its prices start."),
            _field("valid_until", "date", "The last day its prices hold, when it states one."),
            _field(
                "unit_price",
                "money",
                "Each item's price, written with the item as the list names it, its code and unit "
                "when stated; the price is the number.",
                True,
                ("Hex bolts M10 (FB-M10): 185.00 per pallet",),
            ),
        ),
    ),
)
BUILT_IN_TYPES = frozenset(template.doc_type for template in BUILT_IN)

_SEPARATORS = re.compile(r"[\s_]+")


def kind_of(text: str) -> str:
    """A kind of document as templates name it: lowercase words with single spaces, so that
    "Purchase_Order" and "purchase order" are one kind."""
    return _SEPARATORS.sub(" ", text).strip().lower()[:100]


def template_of(row: ExtractionTemplate) -> Template:
    """A project's template row as the stage and the page read it."""
    return Template(
        row.doc_type,
        row.title,
        row.description,
        tuple(
            TemplateField(
                field["name"],
                field["type"],
                field.get("description", ""),
                bool(field.get("required", False)),
                tuple(field.get("examples", ())),
            )
            for field in row.fields
        ),
        row.id,
    )


def templates_for(session: Session, project_id: int) -> list[Template]:
    """The templates in force for the project: its own, by title, then the built-in ones of the
    kinds it has none of, in their order."""
    own = [
        template_of(row)
        for row in session.scalars(
            select(ExtractionTemplate)
            .where(ExtractionTemplate.project_id == project_id)
            .order_by(ExtractionTemplate.title, ExtractionTemplate.id)
        )
    ]
    kinds = {template.doc_type for template in own}
    return own + [template for template in BUILT_IN if template.doc_type not in kinds]


def chosen(templates: Sequence[Template], reply: str | None) -> Template | None:
    """The template a reply names, by its kind or its title as the listing wrote them; None for a
    reply that names none of them."""
    if not reply:
        return None
    named = kind_of(reply)
    return next(
        (t for t in templates if named in (t.doc_type, kind_of(t.title))),
        None,
    )
