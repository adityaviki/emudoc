"""How a document's language writes its dates and numbers in figures, which the enrich stage tells
the model (`reading_line`) and reads its facts' values by (`read_number`, `read_date`).

A fact's `number` and `date` are the model's reading of the value as the document writes it
(app/indexing/enrich.py). Where the value settles them by itself ("1.250,00", whose last mark is
the one before the decimals; "1 250,00"; "2026-04-03"; "13.04.2026", which has no thirteenth
month) or by the way its language writes them ("1.250" in German, "03.04.2026" in French), the
value's own reading replaces the model's, so that what the agent compares and orders is what
the document says, whatever the model made of its separators. The language's way
is used only for a value found as written in the block the fact comes from: a model that wrote a
German "1.250" as "1,250" has written it as English does. Where neither settles it, the model's
reading stands: a value in words ("3 April 2026", "1,5 Mio. EUR"), a two-digit year, or what the
language leaves open. English puts the day first in Britain and the month first in the United
States, and the detection names English alone (py3langid says `en`, never `en-GB`), so an
English "03/04/2026" stays open: the model is told to read it by what the document shows of its
country or its other dates, and to leave the date out where nothing does, rather than guess.

The language is the document's own (app/languages.py), detected in its words or set by a member;
where that is `simple`, which names none, it is the one the parse stage detected
(`documents.read_language_code`), such as Polish, which the database cannot stem but which puts
the day first all the same. A document without a language of its own is searched in its
organisation's, but the model is told only that the organisation works in the language it chose,
if it chose one, and nothing is read by that language's way: the document is one whose words
did not show a language, such as a delivery log of dates and figures, and a deployment's
FTS_LANGUAGE says nothing of how it was written. A language WRITINGS does not know, or one whose
dates may count years from another era (Thai, Persian, Arabic), is told nothing and read by its
values alone.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.languages import CONFIGURATIONS, WORDS_AS_WRITTEN


@dataclass(frozen=True)
class Writing:
    """How a language writes a date and a number in figures."""

    # Its name, as the prompt says it.
    name: str
    # What a date in figures starts with: the day (03.04.2026) or the year (2026.04.03); None
    # where the countries that write the language differ, as English's do.
    first: Literal["day", "year"] | None
    # The mark before a number's decimals; None where its countries differ: Spain writes
    # 1.250,00 and Mexico 1,250.00.
    decimal: Literal[",", "."] | None
    # A currency symbol several countries share, and the currency it is in this language.
    symbol: tuple[str, str] | None = None


# By the ISO 639 code the detection names, as the Unicode CLDR's short dates and decimal marks
# have them.
WRITINGS = {
    "af": Writing("Afrikaans", "year", ","),
    "bg": Writing("Bulgarian", "day", ","),
    "ca": Writing("Catalan", "day", ","),
    "cs": Writing("Czech", "day", ","),
    "cy": Writing("Welsh", "day", "."),
    "da": Writing("Danish", "day", ",", ("kr", "DKK")),
    "de": Writing("German", "day", ","),
    "el": Writing("Greek", "day", ","),
    "en": Writing("English", None, "."),
    "es": Writing("Spanish", "day", None),
    "et": Writing("Estonian", "day", ","),
    "eu": Writing("Basque", "year", ","),
    "fi": Writing("Finnish", "day", ","),
    "fr": Writing("French", "day", ","),
    "ga": Writing("Irish", "day", "."),
    "gl": Writing("Galician", "day", ","),
    "he": Writing("Hebrew", "day", "."),
    "hi": Writing("Hindi", "day", "."),
    "hr": Writing("Croatian", "day", ","),
    "hu": Writing("Hungarian", "year", ","),
    "id": Writing("Indonesian", "day", ","),
    "is": Writing("Icelandic", "day", ",", ("kr", "ISK")),
    "it": Writing("Italian", "day", ","),
    "ja": Writing("Japanese", "year", ".", ("¥", "JPY")),
    "ko": Writing("Korean", "year", "."),
    "lt": Writing("Lithuanian", "year", ","),
    "lv": Writing("Latvian", "day", ","),
    "mk": Writing("Macedonian", "day", ","),
    "ms": Writing("Malay", "day", "."),
    "mt": Writing("Maltese", "day", "."),
    "nl": Writing("Dutch", "day", ","),
    "nn": Writing("Norwegian", "day", ",", ("kr", "NOK")),
    "no": Writing("Norwegian", "day", ",", ("kr", "NOK")),
    "pl": Writing("Polish", "day", ","),
    "pt": Writing("Portuguese", "day", ","),
    "ro": Writing("Romanian", "day", ","),
    "ru": Writing("Russian", "day", ","),
    "sk": Writing("Slovak", "day", ","),
    "sl": Writing("Slovenian", "day", ","),
    "sq": Writing("Albanian", "day", ","),
    "sr": Writing("Serbian", "day", ","),
    "sv": Writing("Swedish", "year", ",", ("kr", "SEK")),
    "tr": Writing("Turkish", "day", ","),
    "uk": Writing("Ukrainian", "day", ","),
    "vi": Writing("Vietnamese", "day", ","),
    "zh": Writing("Chinese", "year", ".", ("¥", "CNY")),
}
# The language each text search configuration stems, the first code CONFIGURATIONS gives it.
_CODES = {configuration: code for code, configuration in reversed(CONFIGURATIONS.items())}

# A currency written beside a figure: a code, a symbol (with the letters of the country before
# it, as US$ and R$), or one of the words that stand for one.
_CURRENCY = (
    r"(?:[A-Z]{3}|[A-Z]{0,3}[$€£¥₹₽₺₩₪₫₱₦₴₸₼₾฿¢₡₲₵₭₮₨円元원]|kr\.?|zł|Kč|Ft|Fr\.|lei|Rs\.?|лв\.?)"
)
# A value that is a figure alone, with a sign and a currency at most around it, and the dash
# German and Swiss amounts end in for no decimals ("50,–"). Spaces are one space by then.
_AMOUNT = re.compile(
    rf"(?P<sign>[-−+])? ?(?:{_CURRENCY} ?)?(?P<signed>[-−+])? ?"
    rf"(?P<figure>\d(?:[\d.,' ]*\d)?)(?:[.,][-–—]{{1,2}})?(?: ?{_CURRENCY})?"
)
# What sets thousands apart and never stands before decimals: a space, which French, Swedish and
# Polish write, and the apostrophe Swiss German writes.
_GROUPING = " '"
_MARKS = ".,"
# A date in figures alone: three numbers, one mark between each, and Hungarian's final point.
_DATE = re.compile(r"(\d{1,4}) ?([./-]) ?(\d{1,2}) ?\2 ?(\d{1,4})\.?")


def writing_of(language: str, read_code: str | None) -> Writing | None:
    """How `language`, a text search configuration, writes dates and numbers; where that is
    WORDS_AS_WRITTEN, the language the parse stage detected, `read_code`. None for a language
    WRITINGS does not know."""
    code = read_code if language == WORDS_AS_WRITTEN else _CODES.get(language)
    return WRITINGS.get(code) if code else None


def reading_line(writing: Writing | None, own: bool = True) -> str:
    """The paragraph of an enrich prompt that says how the document writes its dates and
    numbers, in its own language or, not `own`, likely in its organisation's; nothing for a
    language WRITINGS does not know."""
    if writing is None:
        return ""
    if own:
        said = [f"The document is written in {writing.name}."]
    else:
        said = [
            "The document's words do not show its language, but its organisation works in "
            f"{writing.name}: where the document shows nothing else, read its dates and numbers "
            f"as {writing.name} writes them."
        ]
    if writing.first == "day":
        said.append(
            "A date in figures puts the day first: 03.04.2026 and 03/04/2026 are 2026-04-03."
        )
    elif writing.first == "year":
        said.append(
            "A date in figures puts the year first: 2026.04.03 and 26/04/03 are 2026-04-03."
        )
    else:
        said.append(
            "A date in figures may put the day or the month first: read one such as 03/04/2026 "
            "by what the document shows of its country, or by its other dates (24/03/2026 puts "
            "the day first), and where nothing shows which, leave its date null."
        )
    if writing.decimal == ",":
        said.append(
            "A number puts a comma before its decimals: 1.250,00 and 1 250,00 are 1250.00, and "
            "1.250 is 1250."
        )
    elif writing.decimal == ".":
        said.append(
            "A number puts a point before its decimals: 1,250.00 is 1250.00, and 1,250 is 1250."
        )
    else:
        said.append(
            "A number may put a comma or a point before its decimals: read one such as 1.250 by "
            "the document's other numbers or its country."
        )
    if writing.symbol:
        symbol, currency = writing.symbol
        said.append(f"An amount in {symbol} is in {currency}.")
    return " ".join(said)


def read_number(written: str, writing: Writing | None = None) -> float | None:
    """The number a value states when it is a figure alone, with a sign and a currency at most
    around it, read by its marks where they settle it and by `writing`'s decimal mark where they
    do not ("1,250" is 1250 in English and 1.25 in German); None for any other value."""
    text = " ".join(written.split()).replace("’", "'")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    match = _AMOUNT.fullmatch(text)
    if match is None:
        return None
    if match["sign"] and match["signed"]:
        return None
    number = _figure(match["figure"], writing.decimal if writing else None)
    if number is None:
        return None
    return -number if negative or (match["sign"] or match["signed"] or "+") in "-−" else number


def _figure(figure: str, decimal: str | None) -> float | None:
    """A figure's number, or None where neither its marks nor `decimal` settle which of them
    stands before the decimals, or where its digits are not set apart as thousands are."""
    marks = [character for character in figure if character in _MARKS]
    grouped = any(character in _GROUPING for character in figure)
    point: str | None = None
    if len(set(marks)) == 2:
        # Both marks: the last stands before the decimals, the other sets thousands apart.
        point = marks[-1]
        if grouped or marks.count(point) > 1:
            return None
    elif grouped:
        # Spaces set thousands apart, so a mark stands before the decimals.
        if len(marks) > 1:
            return None
        point = marks[0] if marks else None
    elif len(marks) == 1:
        point = marks[0]
        whole, fraction = figure.split(point)
        if len(fraction) == 3 and len(whole) <= 3 and whole != "0":
            # "1,250": thousands or decimals, as the language writes them.
            if decimal is None:
                return None
            point = point if decimal == point else None
    # Otherwise one mark written more than once sets thousands apart (1.250.000), or none is.
    whole, fraction = figure.rsplit(point, 1) if point else (figure, "")
    groups = re.split(r"[.,' ]", whole)
    if (fraction and not fraction.isdigit()) or not _in_thousands(groups):
        return None
    digits = "".join(groups)
    return float(f"{digits}.{fraction}" if fraction else digits)


def _in_thousands(groups: list[str]) -> bool:
    """Whether the digits between a figure's separators are set apart as thousands are: three at
    a time after the first, or in twos before the last three, as India writes lakhs (1,25,000)."""
    if len(groups) == 1:
        return True
    first, *inner, last = groups
    return 1 <= len(first) <= 3 and len(last) == 3 and all(len(group) in (2, 3) for group in inner)


def read_date(written: str, writing: Writing | None = None) -> date | None:
    """The day a value states when it is a date in figures alone with a year of four digits,
    read by its numbers where they settle it (2026-04-03, 13.04.2026) and by `writing`'s order
    where they do not (03.04.2026 puts the day first in German); None for any other value."""
    match = _DATE.fullmatch(" ".join(written.split()))
    if match is None:
        return None
    first, _, second, last = match.groups()
    if len(first) == 4 and len(last) <= 2:
        year, month, day = int(first), int(second), int(last)
    elif len(last) == 4 and len(first) <= 2:
        year = int(last)
        a, b = int(first), int(second)
        if a > 12 or a == b or (writing is not None and writing.first == "day" and b <= 12):
            day, month = a, b
        elif b > 12:
            day, month = b, a
        else:
            return None
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None
