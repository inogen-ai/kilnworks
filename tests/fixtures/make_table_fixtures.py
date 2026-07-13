"""Generate the tiny table fixtures used by tests/adapters/test_parsers.py.

Run once with `uv run python tests/fixtures/make_table_fixtures.py`; the
outputs (sample.csv, sample.tsv, sample.xlsx) are committed so tests don't
need to regenerate them on every run.
"""

import re
import zipfile
from pathlib import Path

from openpyxl import Workbook

FIXTURES_DIR = Path(__file__).parent


def make_csv() -> None:
    (FIXTURES_DIR / "sample.csv").write_text(
        "name,age,city\nAda,30,London\nGrace,85,New York\nAlan,41,\n",
        encoding="utf-8",
    )


def make_tsv() -> None:
    (FIXTURES_DIR / "sample.tsv").write_text(
        "name\tcolor\nMug\tBlue\nBowl\tGreen\n",
        encoding="utf-8",
    )


def make_xlsx() -> None:
    path = FIXTURES_DIR / "sample.xlsx"
    workbook = Workbook()

    orders = workbook.active
    orders.title = "Orders"
    orders.append(["Item", "Qty", "Price", "Total"])
    orders.append(["Mug", 3, 12, "=B2*C2"])
    orders.append(["Bowl", 2, 20, 40])

    notes = workbook.create_sheet("Notes")
    notes.append(["Field", "Value"])
    notes.append(["Kiln", "Cone 6"])
    notes.append(["Glaze", "Celadon"])

    workbook.save(path)
    # openpyxl never writes a cached <v> alongside a formula's <f>, so a
    # data_only=True read gets None back for that cell. Patch the saved XML
    # to inject the cached result, mimicking what Excel/LibreOffice write.
    _patch_formula_cache(
        path, sheet_file="xl/worksheets/sheet1.xml", formula="B2*C2", cached_value="36"
    )


def _patch_formula_cache(path: Path, sheet_file: str, formula: str, cached_value: str) -> None:
    with zipfile.ZipFile(path) as zin:
        names = zin.namelist()
        contents = {name: zin.read(name) for name in names}

    xml = contents[sheet_file].decode("utf-8")
    # openpyxl writes a formula cell as `<f>…</f><v></v>` (or a self-closing `<v/>`) — an
    # EMPTY value element. Replace that empty <v> with the cached value rather than
    # appending a second <v>: OOXML's CT_Cell allows at most one <v>, so appending would
    # produce a schema-invalid cell that only openpyxl's lenient reader tolerates.
    pattern = re.compile(rf"(<f>{re.escape(formula)}</f>)(<v\s*/>|<v></v>)?")
    if not pattern.search(xml):
        raise RuntimeError(f"formula {formula!r} not found in {sheet_file}")
    contents[sheet_file] = pattern.sub(
        rf"\g<1><v>{cached_value}</v>", xml, count=1
    ).encode("utf-8")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in names:
            zout.writestr(name, contents[name])


if __name__ == "__main__":
    make_csv()
    make_tsv()
    make_xlsx()
    print("wrote sample.csv, sample.tsv, sample.xlsx")
