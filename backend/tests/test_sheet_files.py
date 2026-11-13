"""Excel workbooks as the agent writes, reads and edits them (app/sheet_files.py): cells typed as
Excel reads what is typed, a workbook read cell by cell with its formulas and their values, edits
made in order and whole, the formulas that name what moved moving as Excel moves them, and a
workbook openpyxl would lose something of, or whose formulas reach outside it, never edited. The
formulas' values LibreOffice worked out are written into openpyxl's own file."""

import io
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest
from lxml import etree
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Font, PatternFill
from openpyxl.workbook.defined_name import DefinedName

from app import sheet_files
from app.sheet_files import (
    CellFormat,
    Change,
    EditRefused,
    Sheet,
    TooLarge,
    created,
    edited,
    has_formulas,
    reading,
    typed,
    with_values,
)

# LibreOffice's own saving of `priced()` (the converter's image, `?to=xlsx`, 2026-11-13), whose
# formulas it worked out: LibreOffice 25.2's sheets, shared strings and attributes as they are.
CALCULATED = Path(__file__).parent / "fixtures" / "calculated.xlsx"
_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def priced() -> bytes:
    """The workbook `CALCULATED` was worked out from."""
    return created(
        [
            Sheet(
                "Prices",
                [
                    ["Item", "Qty", "Unit price", "Total"],
                    ["Bolts", "120", "0.35", "=B2*C2"],
                    ["Nuts", "3", "1.5", "=B3*C3"],
                    ["Total", "", "", "=SUM(D2:D3)"],
                    ["Names", "", "", '=A2&" and "&A3'],
                    ["Ratio", "", "", "=D4/0"],
                    ["Large", "", "", "=D4>10"],
                    ["Due", "2026-03-01", "", "=B8+30"],
                    ["Joined", "", "", '=_xlfn.TEXTJOIN(", ",TRUE,A2:A3)'],
                ],
            ),
            Sheet("Q1 'notes' list", [["Twice", "='Prices'!D4*2"]], header=False),
        ]
    )


def worked_out(data: bytes, values: dict[str, object]) -> bytes:
    """`data` as LibreOffice would save it once it worked its formulas out, the values given by
    each formula cell's address (`Sheet!D2`, or `D2` on every sheet): a stand-in for
    `Converter.calculate`."""
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        workbook = etree.fromstring(package.read("xl/workbook.xml"))
        names = [sheet.get("name") for sheet in workbook.iter(f"{{{_MAIN}}}sheet")]
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as target:
            for entry in package.infolist():
                part = package.read(entry)
                if entry.filename.startswith("xl/worksheets/sheet"):
                    name = names[int(entry.filename.removeprefix("xl/worksheets/sheet")[:-4]) - 1]
                    sheet = etree.fromstring(part)
                    for cell in sheet.iter(f"{{{_MAIN}}}c"):
                        if cell.find(f"{{{_MAIN}}}f") is None:
                            continue
                        address = cell.get("r")
                        value = values.get(f"{name}!{address}", values.get(address))
                        if value is None:
                            continue
                        if isinstance(value, bool):
                            cell.set("t", "b")
                            value = int(value)
                        elif isinstance(value, str):
                            cell.set("t", "str")
                        cell.find(f"{{{_MAIN}}}v").text = str(value)
                    part = etree.tostring(sheet)
                target.writestr(entry, part)
    return out.getvalue()


def opened(data: bytes, **options):
    return load_workbook(io.BytesIO(data), **options)


def book(*rows: list[object], title: str = "Sheet") -> Workbook:
    workbook = Workbook()
    workbook.active.title = title
    for row in rows:
        workbook.active.append(row)
    return workbook


def saved(workbook: Workbook) -> bytes:
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def test_cells_are_typed_as_excel_reads_what_is_typed():
    assert typed("") == (None, None)
    assert typed("120") == (120, None)
    assert typed("-0.35") == (-0.35, None)
    assert typed("1e3") == (1000.0, None)
    assert typed("12.5%") == (0.125, "0.0%")
    assert typed("7%") == (0.07, "0%")
    assert typed("true") == (True, None)
    assert typed("2026-03-01") == (date(2026, 3, 1), "yyyy-mm-dd")
    assert typed("2026-03-01 14:30") == (datetime(2026, 3, 1, 14, 30), "yyyy-mm-dd hh:mm")
    assert typed("=SUM(A1:A3)") == ("=SUM(A1:A3)", None)
    # Text: an apostrophe keeps anything so, and numbers of things stay as written.
    assert typed("'120") == ("120", None)
    assert typed("'=A1") == ("=A1", None)
    assert typed("00123") == ("00123", None)
    assert typed("1234567890123456") == ("1234567890123456", None)
    assert typed("1,255.00") == ("1,255.00", None)
    assert typed("2026-02-30") == ("2026-02-30", None)
    assert typed("Bolts\x07") == ("Bolts", None)


def test_a_new_workbook_has_its_sheets_its_header_and_its_formats():
    data = created(
        [
            Sheet(
                "Invoices",
                [
                    ["Invoice", "Total", "Due", "Share"],
                    ["INV-1", "1255", "2026-03-01", "=B2/B4"],
                    ["INV-2", "98.5", "2026-03-15", "=_xlfn.CONCAT(A2,A3)"],
                    ["All", "=SUM(B2:B3)", "'=not a formula", '=TEXTJOIN(", ",TRUE,A2:A3)'],
                ],
                formats=(CellFormat("B:B", "#,##0.00"), CellFormat("D2", "0.0%")),
            ),
            Sheet("Notes", [["Read from the invoices of March."]], header=False),
        ]
    )
    workbook = opened(data)
    assert workbook.sheetnames == ["Invoices", "Notes"]
    invoices = workbook["Invoices"]
    assert [cell.font.b for cell in invoices[1]] == [True] * 4
    assert invoices.freeze_panes == "A2"
    assert (invoices["B2"].value, invoices["B2"].number_format) == (1255, "#,##0.00")
    assert invoices["B4"].value == "=SUM(B2:B3)"
    assert invoices["D2"].number_format == "0.0%"
    assert invoices["C2"].value == datetime(2026, 3, 1)
    # A newer function is written as the file must hold it, whether given so or not.
    assert invoices["D3"].value == "=_xlfn.CONCAT(A2,A3)"
    assert invoices["D4"].value == '=_xlfn.TEXTJOIN(", ",TRUE,A2:A3)'
    assert (invoices["C4"].value, invoices["C4"].data_type) == ("=not a formula", "s")
    assert invoices.column_dimensions["A"].width == 9
    notes = workbook["Notes"]
    assert (notes["A1"].font.b, notes.freeze_panes) == (False, None)
    assert notes.column_dimensions["A"].width == 34
    assert has_formulas(data)
    assert not has_formulas(created([Sheet("Plain", [["A", "1"]])]))


def test_a_formula_may_not_reach_outside_its_workbook_nor_spill():
    def refused(formula: str) -> str:
        with pytest.raises(EditRefused) as refusal:
            created([Sheet("Sheet", [["Price", formula]])])
        return str(refusal.value)

    assert refused('=WEBSERVICE("https://example.com/")') == (
        'Sheet 1 ("Sheet"): B1: the formula calls WEBSERVICE, which fetches from outside the '
        "workbook, which a formula here may not do."
    )
    assert "calls IMAGE" in refused('=_xlfn.IMAGE("https://example.com/a.png")')
    assert "takes values from another workbook" in refused("=[Prices.xlsx]Sheet1!A1")
    assert "takes values from another workbook" in refused("='C:\\data\\[a.xlsx]S'!A1")
    assert "is not a formula Excel reads" in refused("=cmd|'/C calc'!A0")
    assert "FILTER spills its result" in refused("=FILTER(A1:A9,A1:A9>0)")
    # Sheets a workbook may not have.
    for name, words in [
        ("Q1/Q2", "may not hold"),
        ("History", "keeps"),
        ("x" * 32, "1 to 31 characters"),
    ]:
        with pytest.raises(EditRefused, match=words):
            created([Sheet(name, [["A"]])])
    with pytest.raises(EditRefused, match='has a sheet "prices" already'):
        created([Sheet("Prices", [["A"]]), Sheet("prices", [["B"]])])


def test_the_values_libreoffice_worked_out_are_written_into_openpyxls_file():
    written = priced()
    filled = with_values(written, CALCULATED.read_bytes())

    values = opened(filled, data_only=True)
    prices = values["Prices"]
    assert [prices[f"D{row}"].value for row in range(2, 10)] == [
        42,
        4.5,
        46.5,
        "Bolts and Nuts",
        "#DIV/0!",
        True,
        46112,
        "Bolts, Nuts",
    ]
    assert values["Q1 'notes' list"]["B1"].value == 93
    # The formulas stay, and every other part is openpyxl's as it wrote it.
    assert opened(filled)["Prices"]["D4"].value == "=SUM(D2:D3)"
    with (
        zipfile.ZipFile(io.BytesIO(written)) as before,
        zipfile.ZipFile(io.BytesIO(filled)) as after,
    ):
        assert before.namelist() == after.namelist()
        for name in before.namelist():
            if not name.startswith("xl/worksheets/"):
                assert before.read(name) == after.read(name), name
    assert "D4 =SUM(D2:D3) → 46.5" in reading(filled)
    # What is no workbook is refused, never half used.
    with pytest.raises(sheet_files.Unreadable):
        with_values(written, b"PK\x03\x04 not a workbook")


def test_a_workbook_is_read_a_row_a_line_with_its_formulas_and_their_values():
    workbook = book(
        ["Item", "Price", "Ordered", "Total", "Note"],
        ["Bolts", 0.35, datetime(2026, 3, 1), "=B2*120", None],
        [],
        ["Nuts", 1.5, None, "=B4*3", 'Says "rush"'],
        title="Prices",
    )
    workbook.active["B2"].number_format = "#,##0.00"
    workbook.create_sheet("Empty")
    data = worked_out(saved(workbook), {"D2": 42})

    assert reading(data).splitlines() == [
        '2 sheets: "Prices" (A1:E4), "Empty" (A1:A1).',
        'Sheet "Prices":',
        'A1 "Item" | B1 "Price" | C1 "Ordered" | D1 "Total" | E1 "Note"',
        'A2 "Bolts" | B2 0.35 [#,##0.00] | C2 2026-03-01 | D2 =B2*120 → 42',
        'A4 "Nuts" | B4 1.5 | D4 =B4*3 → (not worked out) | E4 "Says \\"rush\\""',
        'Sheet "Empty":',
        "(empty)",
    ]
    assert reading(data, "empty").splitlines()[1:] == ['Sheet "Empty":', "(empty)"]
    assert reading(data, "Summary") == (
        'The workbook has no sheet "Summary". Its sheets: "Prices", "Empty".'
    )
    # A newer function is read as one types it.
    assert "B1 =TEXTJOIN" in reading(created([Sheet("S", [["a", '=TEXTJOIN(",",TRUE,A1)']])]))


def test_a_long_sheet_is_read_in_parts(monkeypatch):
    monkeypatch.setattr(sheet_files, "READ_BUDGET", 200)
    data = saved(book(*([f"Row {n}", n] for n in range(1, 41)), title="Long"))

    first = reading(data).splitlines()
    assert first[-1] == '... Call read_file again with sheet="Long" and from_row=12 to continue.'
    assert first[-2] == 'A11 "Row 11" | B11 11'
    second = reading(data, "Long", 12).splitlines()
    assert second[1:3] == ['Sheet "Long", from row 12:', 'A12 "Row 12" | B12 12']


def test_an_edit_sets_cells_and_their_format_and_keeps_what_it_does_not_touch():
    workbook = book(["Item", "Price"], ["Bolts", 0.35], title="Prices")
    workbook.active["A1"].font = Font(bold=True)
    workbook.active["B2"].fill = PatternFill("solid", fgColor="FFFF00")
    data, done = edited(
        saved(workbook),
        [
            Change("set", cell="A3", rows=[["Nuts", "1.5"], ["Total", "=SUM(B2:B3)"]]),
            Change("set", sheet="prices", cell="B2:B4", format="#,##0.00"),
            Change("set", cell="C1", rows=[["Checked"]], format="@"),
        ],
    )

    assert done == ('set A3:B4 of "Prices"; formatted B2:B4 of "Prices"; set C1 of "Prices"')
    sheet = opened(data)["Prices"]
    assert [[cell.value for cell in row] for row in sheet.iter_rows()] == [
        ["Item", "Price", "Checked"],
        ["Bolts", 0.35, None],
        ["Nuts", 1.5, None],
        ["Total", "=SUM(B2:B3)", None],
    ]
    assert sheet["A1"].font.b and sheet["B2"].fill.fgColor.rgb == "00FFFF00"
    assert {sheet[f"B{row}"].number_format for row in (2, 3, 4)} == {"#,##0.00"}


def test_rows_and_columns_inserted_or_deleted_move_the_formulas_that_name_them():
    workbook = book(
        ["Item", "Qty", "Price", "Total"],
        ["Bolts", 120, 0.35, "=B2*C2"],
        ["Nuts", 3, 1.5, "=B3*C3"],
        ["Total", None, None, "=SUM(D2:D3)"],
        title="Prices",
    )
    sheet = workbook.active
    sheet.merge_cells("A6:D6")
    sheet["A6"] = "Checked against the contract"
    sheet.auto_filter.ref = "A1:D3"
    sheet["E2"].hyperlink = "https://example.com/bolts"
    summary = workbook.create_sheet("Summary")
    summary["A1"] = "=Prices!D4"
    summary["A2"] = "=SUM(Prices!C:C)"
    summary["A3"] = "=SUM(Prices!2:3)"
    workbook.defined_names["Totals"] = DefinedName("Totals", attr_text="Prices!$D$2:$D$3")

    data, done = edited(
        saved(workbook),
        [
            Change("insert_rows", sheet="Prices", row=3, count=2),
            Change("set", sheet="Prices", cell="A3", rows=[["Washers", "10", "0.1", "=B3*C3"]]),
            Change("insert_columns", sheet="Prices", column="B"),
        ],
    )

    assert done == (
        'inserted rows 3–4 of "Prices"; set A3:D3 of "Prices"; inserted column B of "Prices"'
    )
    workbook = opened(data)
    prices = workbook["Prices"]
    assert [prices.cell(row, 5).value for row in (2, 3, 5, 6)] == [
        "=C2*D2",
        "=C3*D3",
        "=C5*D5",
        "=SUM(E2:E5)",
    ]
    assert [str(each) for each in prices.merged_cells.ranges] == ["A8:E8"]
    assert prices["A8"].value == "Checked against the contract"
    assert prices.auto_filter.ref == "A1:E5"
    assert prices["F2"].hyperlink.target == "https://example.com/bolts"
    assert [workbook["Summary"][f"A{row}"].value for row in (1, 2, 3)] == [
        "=Prices!E6",
        "=SUM(Prices!D:D)",
        "=SUM(Prices!2:5)",
    ]
    assert workbook.defined_names["Totals"].attr_text == "Prices!$E$2:$E$5"

    # Deleted, a row a formula names leaves #REF!, and a range loses it.
    data, done = edited(
        data,
        [Change("delete_rows", sheet="Prices", row=2), Change("delete_columns", column="C")],
    )
    assert done == 'deleted row 2 of "Prices"; deleted column C of "Prices"'
    workbook = opened(data)
    prices = workbook["Prices"]
    assert [prices.cell(row, 4).value for row in (2, 4, 5)] == [
        "=#REF!*C2",
        "=#REF!*C4",
        "=SUM(D2:D4)",
    ]
    assert workbook["Summary"]["A1"].value == "=Prices!D5"
    assert workbook["Summary"]["A2"].value == "=SUM(Prices!C:C)"
    assert workbook["Summary"]["A3"].value == "=SUM(Prices!2:4)"
    assert workbook.defined_names["Totals"].attr_text == "Prices!$D$2:$D$4"


def test_a_sheet_renamed_or_deleted_changes_the_formulas_that_name_it():
    workbook = book(["Total", 46.5], title="Prices")
    summary = workbook.create_sheet("Summary")
    summary["A1"] = "=Prices!B1*2"
    summary["A2"] = "=Summary!A1+1"

    data, done = edited(
        saved(workbook), [Change("rename_sheet", sheet="Prices", name="March prices")]
    )
    assert done == 'renamed the sheet "Prices" "March prices"'
    assert opened(data)["Summary"]["A1"].value == "='March prices'!B1*2"

    data, done = edited(data, [Change("delete_sheet", sheet="March prices")])
    workbook = opened(data)
    assert workbook.sheetnames == ["Summary"]
    assert [workbook["Summary"][cell].value for cell in ("A1", "A2")] == [
        "=#REF!*2",
        "=Summary!A1+1",
    ]

    data, done = edited(data, [Change("add_sheet", name="Checks", rows=[["Rule"], ["Totals"]])])
    workbook = opened(data)
    assert workbook.sheetnames == ["Summary", "Checks"]
    assert workbook["Checks"]["A1"].font.b
    with pytest.raises(EditRefused, match="at least one sheet"):
        edited(saved(book(["A"])), [Change("delete_sheet")])


def test_an_edit_is_made_whole_or_not_at_all():
    data = saved(book(["Item", "Price"], ["Bolts", 0.35], title="Prices"))
    for changes, words in [
        (
            [Change("set", cell="A3", rows=[["Nuts"]]), Change("set", sheet="Costs", rows=[["x"]])],
            'Change 2 (set): the workbook has no sheet "Costs". Its sheets: "Prices".',
        ),
        ([Change("set", cell="B")], "Change 1 (set): give rows of cells to set"),
        ([Change("set", cell="ZZZZ1", rows=[["x"]])], '"ZZZZ1" is no cell'),
        ([Change("insert_rows")], "give the row by its number"),
        ([Change("insert_columns", column="12")], "give the column by its letters"),
        ([Change("delete_rows", row=1, count=20_000)], "count is 1 to 10,000"),
        ([Change("rename_sheet", name="history")], 'Excel keeps "History" for itself'),
        ([Change("add_sheet", name="PRICES")], 'has a sheet "PRICES" already'),
        ([Change("set", rows=[['=WEBSERVICE("https://example.com/")']])], "calls WEBSERVICE"),
    ]:
        with pytest.raises(EditRefused) as refusal:
            edited(data, changes)
        assert words in str(refusal.value)
    # A merged range is set by its first cell.
    merged = book(["Note", None])
    merged.active.merge_cells("A1:B1")
    with pytest.raises(EditRefused, match="B1 is inside merged cells"):
        edited(saved(merged), [Change("set", cell="B1", rows=[["x"]])])


def test_a_workbook_holding_what_openpyxl_would_lose_is_read_but_never_edited():
    workbook = book(["Month", "Total"], ["March", 46.5], title="Totals")
    chart = BarChart()
    chart.add_data(
        Reference(workbook.active, min_col=2, min_row=1, max_row=2), titles_from_data=True
    )
    workbook.active.add_chart(chart, "D2")
    data = saved(workbook)

    assert reading(data).splitlines()[1] == (
        "It holds charts, pictures or shapes, so it is read here but changed only in Excel."
    )
    with pytest.raises(EditRefused) as refusal:
        edited(data, [Change("set", cell="B2", rows=[["50"]])])
    assert str(refusal.value) == (
        "The workbook holds charts, pictures or shapes, which these tools would lose. The member "
        "may change it in Excel and upload it as its next version."
    )

    # Parts of their own, and an extension of a sheet openpyxl drops with a warning.
    plain = saved(book(["Month", "Total"]))
    for part, words in [
        ("xl/pivotTables/pivotTable1.xml", "pivot tables"),
        ("xl/vbaProject.bin", "macros"),
        ("xl/externalLinks/externalLink1.xml", "links to other workbooks"),
    ]:
        with pytest.raises(EditRefused, match=words):
            edited(_with_part(plain, part, b"<x/>"), [Change("set", rows=[["x"]])])
    extended = _with_extension(plain)
    with pytest.raises(EditRefused, match="holds data validation of a kind these tools would lose"):
        edited(extended, [Change("set", rows=[["x"]])])


def test_a_workbook_whose_formulas_reach_outside_it_is_not_edited():
    workbook = book(["Rate", '=WEBSERVICE("https://example.com/rate")'])
    with pytest.raises(EditRefused) as refusal:
        edited(saved(workbook), [Change("set", cell="A2", rows=[["x"]])])
    assert str(refusal.value) == (
        'A formula of the sheet "Sheet" calls WEBSERVICE, which fetches from outside the '
        "workbook, which these tools do not work out. The member may change it in Excel and "
        "upload it as its next version."
    )


def test_rows_of_a_sheet_with_conditional_formatting_stay_where_they_are():
    workbook = book(["Total", 46.5], ["Total", 50])
    workbook.active.conditional_formatting.add(
        "B1:B2", CellIsRule(operator="greaterThan", formula=["48"], fill=PatternFill("solid"))
    )
    data = saved(workbook)
    with pytest.raises(EditRefused, match="has conditional formatting, which these tools cannot"):
        edited(data, [Change("insert_rows", row=2)])
    # Its cells are set all the same.
    changed, _ = edited(data, [Change("set", cell="B2", rows=[["47"]])])
    assert opened(changed).active["B2"].value == 47


def test_a_workbook_too_large_for_these_tools_says_so(monkeypatch):
    data = saved(book(["Total", 46.5]))
    monkeypatch.setattr(sheet_files, "MAX_EDITED", 100)
    monkeypatch.setattr(sheet_files, "MAX_READ", 100)
    with pytest.raises(TooLarge, match=r"larger than these tools edit \(0 MB unpacked\)"):
        edited(data, [Change("set", rows=[["x"]])])
    with pytest.raises(TooLarge, match="larger than these tools read"):
        reading(data)
    with pytest.raises(sheet_files.Unreadable):
        reading(b"not a workbook")


def _with_part(data: bytes, name: str, part: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for entry in source.infolist():
            target.writestr(entry, source.read(entry))
        target.writestr(name, part)
    return out.getvalue()


def _with_extension(data: bytes) -> bytes:
    """The workbook with a newer Excel's data validation in its first sheet's extensions."""
    extension = (
        b'<extLst><ext uri="{CCE6A557-97BC-4b89-ADB6-D9C93CAAB3DF}" '
        b'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
        b'<x14:dataValidations count="0"/></ext></extLst></worksheet>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for entry in source.infolist():
            part = source.read(entry)
            if entry.filename == "xl/worksheets/sheet1.xml":
                part = part.replace(b"</worksheet>", extension)
            target.writestr(entry, part)
    return out.getvalue()
