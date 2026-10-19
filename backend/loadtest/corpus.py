"""Made-up documents for the load test: the sample set's kinds (invoices, delivery notes,
contracts, price lists, emails, supplier reviews) by the thousand, a scanned PDF of hundreds of
pages, and workbooks of tens of megabytes. Everything is drawn from a seeded generator, so two
runs upload the same files, and the large ones are kept in the run's directory for the next."""

import random
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from docx import Document as WordDocument
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

PREFIXES = (
    "Northwind Baltic Alder Kestrel Harbour Granite Meridian Tamarack Juniper Corvid Lindqvist "
    "Halvorsen Ostara Brightwater Copperfield Fenwick Ravensworth Saltmarsh Thornbury Vantage "
    "Westerly Ashdown Bramley Calder Dunmore Elstree Fairhaven Glenmoor Holloway Ironbridge "
    "Kingsway Larkspur Marlow Nettlebed Oakhurst Penrose Quayside Rowan Stirling Tidewater"
).split()
TRADES = (
    "Fasteners Steelworks Logistics Components Supplies Hardware Industrial Metals Freight "
    "Fixings Engineering Packaging Distribution Tooling Plastics"
).split()
FORMS = ("Ltd", "GmbH", "BV", "AB", "Oy", "SAS", "Inc", "S.p.A.")
MATERIALS = ("stainless", "galvanised", "zinc-plated", "brass", "nylon", "aluminium", "carbon")
ITEMS = (
    "hex bolt",
    "hex nut",
    "flat washer",
    "spring washer",
    "wood screw",
    "machine screw",
    "anchor bolt",
    "rivet",
    "hinge",
    "angle bracket",
    "pipe clamp",
    "threaded rod",
    "cable tie",
    "gasket",
    "ball valve",
    "flange",
)
SIZES = ("M4", "M5", "M6", "M8", "M10", "M12", "M16", "M20")
FIRST_NAMES = (
    "Ada Ines Tomas Priya Kofi Mei Lars Amara Jonas Sofia Mateo Hana Ravi Elif Noor Oskar "
    "Leila Felix Yara Anton"
).split()
LAST_NAMES = (
    "Lindgren Okafor Moreau Tanaka Kowalski Haddad Fischer Rossi Novak Silva Brennan Varga "
    "Jensen Petrov Duarte Nakamura"
).split()
CITIES = (
    "Rotterdam Hamburg Gdansk Antwerp Lyon Milan Porto Gothenburg Tampere Leeds Bilbao Brno "
    "Ghent Malmo Turin Utrecht"
).split()
CONDITIONS = (
    "in good order",
    "with two cartons damaged",
    "short by one pallet",
    "three days late",
    "without the delivery note",
    "with the wrong labels",
    "on time and complete",
)
SENTENCES = (
    "{supplier} delivered {qty} {unit} of {product} to the {city} warehouse on {date}.",
    "This document covers the {product} ordered under purchase order {po}.",
    "Payment is due within {days} days of the invoice date by bank transfer.",
    "{person} at the {city} depot reported that the goods arrived {condition}.",
    "Late deliveries incur a penalty of {pct} percent of the order value for each week of delay.",
    "Prices are fixed until {date} and then reviewed against the published steel index.",
    "The supplier shall replace any {product} that fails inspection within {days} days.",
    "Either party may end this agreement with {days} days' written notice.",
    "Disputes are settled under the laws of {country} by the courts of {city}.",
    "{person} signed for {qty} {unit} and noted that the seals were intact.",
    "Volume discounts of {pct} percent apply above {qty} units a quarter.",
    "The carrier {carrier} collected the consignment from {city} on {date}.",
    "Quality checks at {city} rejected {qty} pieces for thread damage.",
    "Invoices that quote the wrong order number are returned unpaid.",
    "The {product} must be packed in cartons of at most {qty} pieces.",
    "{supplier} confirmed the revised delivery date of {date} by email.",
    "Credit notes are issued within {days} days of an agreed return.",
    "Our records show {qty} {unit} received against {qty2} ordered.",
    "The review scored delivery reliability at {pct} out of one hundred.",
    "All prices exclude VAT and include delivery to the {city} site.",
)
COUNTRIES = ("the Netherlands", "Germany", "Sweden", "France", "Italy", "Finland", "England")
CARRIERS = ("DHL Freight", "DSV", "Kuehne+Nagel", "DB Schenker", "Geodis", "PostNord")
UNITS = ("pallets", "cartons", "boxes", "bags", "drums")
KINDS = ("invoice", "delivery", "contract", "price_list", "email", "review")
# How often each kind of document and each format occurs: mostly invoices and delivery notes,
# as a supplier's paperwork is, and mostly Markdown, which is the quickest to make.
KIND_WEIGHTS = (30, 25, 10, 10, 15, 10)
FORMATS = (".md", ".pdf", ".docx")
FORMAT_WEIGHTS = (60, 25, 15)


@dataclass
class Element:
    """One piece of a document: a heading, a field ("Name: value"), a paragraph or a table."""

    kind: str
    text: str = ""
    level: int = 1
    rows: list[list[str]] | None = None


class World:
    """The suppliers, products, people and places one seed's documents talk about."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.suppliers = sorted(
            {f"{rng.choice(PREFIXES)} {rng.choice(TRADES)} {rng.choice(FORMS)}" for _ in range(160)}
        )
        self.products = sorted(
            {f"{rng.choice(SIZES)} {rng.choice(MATERIALS)} {rng.choice(ITEMS)}" for _ in range(300)}
        )
        self.people = [f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}" for _ in range(80)]

    def date(self) -> str:
        return f"2026-{self.rng.randint(1, 12):02d}-{self.rng.randint(1, 28):02d}"

    def money(self, low: float, high: float) -> str:
        return f"EUR {self.rng.uniform(low, high):,.2f}"

    def sentence(self, supplier: str) -> str:
        rng = self.rng
        return rng.choice(SENTENCES).format(
            supplier=supplier,
            qty=rng.randint(2, 900),
            qty2=rng.randint(2, 900),
            unit=rng.choice(UNITS),
            product=rng.choice(self.products),
            city=rng.choice(CITIES),
            date=self.date(),
            po=f"PO-{rng.randrange(10**5):05d}",
            days=rng.choice((14, 30, 45, 60, 90)),
            person=rng.choice(self.people),
            condition=rng.choice(CONDITIONS),
            pct=rng.randint(1, 99),
            country=rng.choice(COUNTRIES),
            carrier=rng.choice(CARRIERS),
        )

    def paragraph(self, supplier: str, sentences: tuple[int, int] = (3, 7)) -> Element:
        count = self.rng.randint(*sentences)
        return Element("p", " ".join(self.sentence(supplier) for _ in range(count)))

    def items(self, count: int) -> list[list[str]]:
        rows = [["Item", "Quantity", "Unit price", "Amount"]]
        for _ in range(count):
            quantity, price = self.rng.randint(1, 400), self.rng.uniform(0.05, 60)
            rows.append(
                [
                    self.rng.choice(self.products),
                    str(quantity),
                    f"{price:,.2f}",
                    f"{quantity * price:,.2f}",
                ]
            )
        return rows

    def document(self, number: int) -> tuple[str, list[Element]]:
        """The kind and elements of document `number`, whose fields carry the number, so that
        every document has identifiers of its own to search for."""
        rng = self.rng
        kind = rng.choices(KINDS, KIND_WEIGHTS)[0]
        supplier = rng.choice(self.suppliers)
        customer = rng.choice(self.suppliers)
        made = getattr(self, f"_{kind}")(number, supplier, customer)
        return kind, made

    def _invoice(self, number: int, supplier: str, customer: str) -> list[Element]:
        return [
            Element("h", f"Invoice INV-2026-{number:05d}"),
            Element("field", f"Supplier: {supplier}"),
            Element("field", f"Customer: {customer}"),
            Element("field", f"Invoice number: INV-2026-{number:05d}"),
            Element("field", f"Issue date: {self.date()}"),
            Element("field", f"Due date: {self.date()}"),
            Element("field", f"PO number: PO-{self.rng.randrange(10**5):05d}"),
            Element("field", f"Total amount: {self.money(80, 40_000)}"),
            Element("field", f"Payment terms: Net {self.rng.choice((14, 30, 45, 60))}"),
            Element("table", rows=self.items(self.rng.randint(2, 14))),
            self.paragraph(supplier, (2, 4)),
        ]

    def _delivery(self, number: int, supplier: str, customer: str) -> list[Element]:
        return [
            Element("h", f"Delivery note DN-{number:05d}"),
            Element("field", f"Supplier: {supplier}"),
            Element("field", f"Delivered to: {customer}, {self.rng.choice(CITIES)}"),
            Element("field", f"Delivery date: {self.date()}"),
            Element("field", f"Carrier: {self.rng.choice(CARRIERS)}"),
            Element("field", f"Received by: {self.rng.choice(self.people)}"),
            Element("table", rows=[row[:2] for row in self.items(self.rng.randint(1, 10))]),
            self.paragraph(supplier, (1, 4)),
        ]

    def _contract(self, number: int, supplier: str, customer: str) -> list[Element]:
        made = [
            Element("h", f"Supply agreement SA-{number:05d} between {supplier} and {customer}"),
            Element("field", f"Effective date: {self.date()}"),
            Element("field", f"Term length: {self.rng.choice((12, 24, 36))} months"),
            Element("field", f"Notice period: {self.rng.choice((30, 60, 90))} days"),
            Element("field", f"Governing law: {self.rng.choice(COUNTRIES)}"),
        ]
        headings = ("Scope", "Prices", "Delivery", "Quality", "Liability", "Termination")
        for position, heading in enumerate(headings[: self.rng.randint(3, 6)], start=1):
            made.append(Element("h", f"{position}. {heading}", level=2))
            made.extend(self.paragraph(supplier) for _ in range(self.rng.randint(1, 3)))
        return made

    def _price_list(self, number: int, supplier: str, customer: str) -> list[Element]:
        rows = [["Product", "Unit", "Price"]]
        for product in self.rng.sample(self.products, self.rng.randint(15, 60)):
            unit = self.rng.choice(("piece", "box of 100", "kg"))
            rows.append([product, unit, self.money(0, 90)])
        return [
            Element("h", f"Price list PL-{number:05d}, {supplier}"),
            Element("field", f"Valid from: {self.date()}"),
            Element("field", f"Supplier: {supplier}"),
            Element("table", rows=rows),
            self.paragraph(supplier, (1, 3)),
        ]

    def _email(self, number: int, supplier: str, customer: str) -> list[Element]:
        return [
            Element("h", f"Re: order PO-{number:05d}"),
            Element("field", f"From: {self.rng.choice(self.people)}, {supplier}"),
            Element("field", f"To: {self.rng.choice(self.people)}, {customer}"),
            Element("field", f"Sent: {self.date()}"),
            *(self.paragraph(supplier, (2, 5)) for _ in range(self.rng.randint(2, 4))),
        ]

    def _review(self, number: int, supplier: str, customer: str) -> list[Element]:
        scores = [["Measure", "Score"]] + [
            [measure, str(self.rng.randint(40, 100))]
            for measure in ("Delivery", "Quality", "Price", "Communication")
        ]
        return [
            Element("h", f"Supplier review SR-{number:05d}: {supplier}"),
            Element("field", f"Reviewer: {self.rng.choice(self.people)}"),
            Element("field", f"Review date: {self.date()}"),
            Element("table", rows=scores),
            *(self.paragraph(supplier) for _ in range(self.rng.randint(2, 4))),
        ]

    def question(self) -> str:
        """A question a member asks of the library."""
        rng = self.rng
        return rng.choice(
            (
                f"Which deliveries of {rng.choice(self.products)} arrived late?",
                f"What does {rng.choice(self.suppliers)} charge for {rng.choice(self.products)}?",
                f"What is the notice period in the agreement with {rng.choice(self.suppliers)}?",
                f"Were any goods delivered to {rng.choice(CITIES)} damaged?",
                "What penalty applies to late deliveries?",
                f"What are the payment terms of {rng.choice(self.suppliers)}?",
            )
        )

    def query(self) -> str:
        """A search a member types into the library: two to four words."""
        rng = self.rng
        return rng.choice(
            (
                rng.choice(self.products),
                f"{rng.choice(self.suppliers).split()[0]} invoice",
                f"late delivery {rng.choice(CITIES)}",
                f"{rng.choice(ITEMS)} damaged",
                "penalty late deliveries",
                f"notice period {rng.choice(COUNTRIES).split()[-1]}",
                f"{rng.choice(self.people)} signed",
            )
        )


def library(directory: Path, count: int, seed: int = 1) -> list[Path]:
    """`count` documents in `directory`, made once and kept for the next run."""
    directory.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    world = World(rng)
    paths = []
    for number in range(1, count + 1):
        kind, elements = world.document(number)
        suffix = rng.choices(FORMATS, FORMAT_WEIGHTS)[0]
        path = directory / f"{kind}-{number:05d}{suffix}"
        if not path.exists():
            WRITERS[suffix](elements, path)
        paths.append(path)
    return paths


def _markdown(elements: list[Element], path: Path) -> None:
    lines: list[str] = []
    for element in elements:
        if element.kind == "h":
            lines += [f"{'#' * element.level} {element.text}", ""]
        elif element.kind == "field":
            lines.append(f"- {element.text}")
        elif element.kind == "p":
            lines += ["", element.text, ""]
        elif element.rows:
            lines += ["", f"| {' | '.join(element.rows[0])} |"]
            lines.append(f"|{'---|' * len(element.rows[0])}")
            lines += [f"| {' | '.join(row)} |" for row in element.rows[1:]]
            lines.append("")
    path.write_text("\n".join(lines) + "\n")


def _pdf(elements: list[Element], path: Path) -> None:
    """A PDF as an office suite prints one: a gap after each field, which the parser takes for
    a paragraph's end, and ruled tables, which it finds by their lines (app/parsing/pdf.py)."""
    styles = getSampleStyleSheet()
    ruled = TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)])
    flow: list = []
    for element in elements:
        if element.kind == "h":
            style = styles["Heading1" if element.level == 1 else "Heading2"]
            flow.append(Paragraph(element.text, style))
        elif element.kind == "field":
            flow += [Paragraph(element.text, styles["Normal"]), Spacer(1, 8)]
        elif element.kind == "p":
            flow += [Spacer(1, 6), Paragraph(element.text, styles["BodyText"])]
        elif element.rows:
            flow += [Spacer(1, 6), Table(element.rows, style=ruled)]
    SimpleDocTemplate(str(path), pagesize=A4).build(flow)


def _docx(elements: list[Element], path: Path) -> None:
    document = WordDocument()
    for element in elements:
        if element.kind == "h":
            document.add_heading(element.text, level=element.level)
        elif element.kind == "field":
            document.add_paragraph(element.text, style="List Bullet")
        elif element.kind == "p":
            document.add_paragraph(element.text)
        elif element.rows:
            table = document.add_table(rows=len(element.rows), cols=len(element.rows[0]))
            for cells, row in zip(table.rows, element.rows, strict=True):
                for cell, value in zip(cells.cells, row, strict=True):
                    cell.text = value
    document.save(str(path))


WRITERS = {".md": _markdown, ".pdf": _pdf, ".docx": _docx}


def scanned_pdf(path: Path, pages: int, seed: int = 2) -> Path:
    """A PDF of `pages` scanned pages, pictures and nothing else: A4 at 150 dpi in grey, lines
    of print with a scanner's speckle, about 150 KB a page as JPEG."""
    if path.exists():
        return path
    rng = random.Random(seed)
    font = ImageFont.load_default(size=22)
    words = ("pallet", "carton", "delivered", "received", "bolts", "steel", "checked", "signed")

    def page() -> Image.Image:
        image = Image.new("L", (1240, 1754), 245)
        draw = ImageDraw.Draw(image)
        draw.text((110, 80), f"Delivery record {rng.randrange(10**5):05d}", fill=20, font=font)
        for line in range(44):
            words_on_line = " ".join(rng.choice(words) for _ in range(rng.randint(6, 10)))
            draw.text((110, 150 + line * 34), words_on_line, fill=35, font=font)
        return Image.blend(image, Image.effect_noise(image.size, 18), 0.07)

    first, *rest = (page() for _ in range(pages))
    first.save(path, save_all=True, append_images=rest, quality=60)
    return path


ORDER_HEADER = (
    "Date Order Customer Product SKU Quantity Price Amount Currency Warehouse Status Note"
).split()


def workbook(path: Path, megabytes: int, rows_per_sheet: int, seed: int = 3) -> Path:
    """An order export of about `megabytes` as .xlsx, at most `rows_per_sheet` rows a sheet
    (one sheet a year of orders, say), each row as a sales system writes it."""
    if path.exists():
        return path
    rng = random.Random(seed)
    rows = megabytes * 1_000_000 // ROW_BYTES
    book = Workbook(write_only=True)
    for start in range(0, rows, rows_per_sheet):
        sheet = book.create_sheet(f"Orders {start // rows_per_sheet + 1}")
        sheet.append(ORDER_HEADER)
        for row in _orders(rng, min(rows_per_sheet, rows - start)):
            sheet.append(row)
    book.save(path)
    return path


# What one order row takes in an .xlsx, measured on 20,000 of them (zip-compressed XML).
ROW_BYTES = 80


def _orders(rng: random.Random, count: int) -> Iterator[list]:
    notes = ("late", "damaged", "partial", "signed", "checked", "short", "complete")
    for _ in range(count):
        quantity, price = rng.randint(1, 500), round(rng.uniform(0.1, 90), 2)
        yield [
            f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            f"SO-{rng.randrange(10**6):06d}",
            f"Customer {rng.randrange(5000)}",
            f"{rng.choice(SIZES)} {rng.choice(MATERIALS)} {rng.choice(ITEMS)}",
            f"SKU-{rng.randrange(10**5):05d}",
            quantity,
            price,
            round(quantity * price, 2),
            "EUR",
            rng.choice(CITIES),
            rng.choice(("shipped", "open", "held", "returned")),
            " ".join(rng.choice(notes) for _ in range(rng.randint(0, 3))),
        ]
