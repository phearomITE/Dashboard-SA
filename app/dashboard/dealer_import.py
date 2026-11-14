import csv
from io import BytesIO, StringIO
from math import isfinite
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from app.data.controllers import normalize_group_code
from app.database.models import DealerLocation


def seed_dealer_stations(db):
    path = Path(__file__).resolve().parents[1] / "data" / "dealer_stations.csv"
    with path.open(encoding="utf-8", newline="") as source:
        stations = parse_table(csv.reader(source))
    for code, (lat, lon) in stations.items():
        region, dealer = code.split("-", 1)
        if db.get(DealerLocation, (region, dealer)) is None:
            db.add(DealerLocation(region=region, dealer=dealer, lat=lat, lon=lon))
    db.commit()


def parse_table(rows):
    aliases = {"dealer code": "dealer", "dealer_code": "dealer", "lat": "latitude", "lon": "longitude", "lng": "longitude"}
    headers = [str(v or "").strip().lower() for v in next(rows, ())]
    headers = [aliases.get(h, h) for h in headers]
    required = ("region", "dealer", "latitude", "longitude")
    if any(headers.count(h) != 1 for h in required):
        raise ValueError("Include one column each for Region, Dealer, Latitude, and Longitude.")
    columns = [headers.index(h) for h in required]
    records = {}
    for line, row in enumerate(rows, 2):
        if line > 10001:
            raise ValueError("Upload up to 10,000 rows at a time.")
        if all(v in (None, "") for v in row):
            continue
        try:
            region, dealer, lat, lon = (row[i] for i in columns)
            code = normalize_group_code(f"{str(region).strip()}-{str(dealer).strip()}")
            lat, lon = float(lat), float(lon)
            if not (isfinite(lat) and isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
        except (IndexError, TypeError, ValueError):
            raise ValueError(f"Row {line}: enter a valid region, dealer code, latitude, and longitude.") from None
        if code in records and records[code] != (lat, lon):
            raise ValueError(f"Row {line}: the file contains conflicting coordinates for {code}.")
        records[code] = lat, lon
    if not records:
        raise ValueError("The file contains no dealer locations.")
    return records


def import_dealers(db, content, extension):
    if extension == "csv":
        try:
            records = parse_table(csv.reader(StringIO(content.decode("utf-8-sig")), strict=True))
        except (UnicodeError, csv.Error):
            raise ValueError("Provide a UTF-8 CSV or an Excel workbook.") from None
    else:
        try:
            with ZipFile(BytesIO(content)) as archive:
                if sum(v.file_size for v in archive.infolist()) > 30_000_000:
                    raise ValueError("The workbook is too large after decompression.")
            book = load_workbook(BytesIO(content), read_only=True, data_only=True)
            try:
                records = parse_table(book.active.values)
            finally:
                book.close()
        except (BadZipFile, InvalidFileException, KeyError, SyntaxError, OSError):
            raise ValueError("This file could not be read as an Excel workbook.") from None
    for code, (lat, lon) in records.items():
        region, dealer = code.split("-", 1)
        row = db.get(DealerLocation, (region, dealer))
        if row is None:
            row = DealerLocation(region=region, dealer=dealer)
            db.add(row)
        row.lat, row.lon = lat, lon
    db.commit()
    return {"count": len(records)}
