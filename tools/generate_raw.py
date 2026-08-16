"""Writes the messy raw CSVs that the pipeline exists to clean.

Dev tool. Run once, commit the output. Seeded, so every defect is *known* —
that is what makes `make profile` counts verifiable rather than guessed.

    uv run python tools/generate_raw.py
"""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from random import Random

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"

CARRIERS: list[tuple[str, str, str]] = [
    ("BLZ", "Blizzard Freight", "express"),
    ("COY", "Coyote Logistics", "standard"),
    ("MRD", "Meridian Cargo", "economy"),
    ("NPT", "Neptune Shipping", "standard"),
    ("ORN", "Orion Transport", "express"),
]

# One canonical location, every way the legacy systems wrote it down.
ALIASES: dict[str, list[str]] = {
    "LAX": ["LAX", "Los Angeles, CA", "los angeles", "  LAX ", "Los Angeles"],
    "JFK": ["JFK", "New York, NY", "new york", "NYC", " JFK"],
    "ORD": ["ORD", "Chicago, IL", "chicago", "Chicago IL"],
    "DFW": ["DFW", "Dallas, TX", "dallas", "Dallas"],
    "SEA": ["SEA", "Seattle, WA", "seattle", "SEATTLE"],
    "ATL": ["ATL", "Atlanta, GA", "atlanta", "Atlanta"],
}

# (transit days, probability the delivery misses its promised date).
# SEA->ATL is deliberately the worst, so "which route had the highest delay
# rate last quarter?" has a real answer to check the demo against.
ROUTES: dict[tuple[str, str], tuple[int, float]] = {
    ("LAX", "JFK"): (5, 0.34),
    ("JFK", "LAX"): (5, 0.22),
    ("SEA", "ATL"): (6, 0.52),
    ("ATL", "SEA"): (6, 0.31),
    ("ORD", "DFW"): (2, 0.08),
    ("DFW", "ORD"): (2, 0.11),
    ("LAX", "SEA"): (2, 0.17),
    ("ORD", "JFK"): (3, 0.14),
}

NULLS = ["", "NULL", "N/A", "-", "n/a"]

# Same ten fields, different names and different column order per file.
HEADERS_A = {
    "shipment_id": "shipment_id",
    "carrier_code": "carrier",
    "origin": "origin",
    "destination": "destination",
    "shipped_date": "shipped",
    "promised_date": "promised",
    "delivered_date": "delivered",
    "weight_kg": "weight_kg",
    "cost_usd": "cost_usd",
    "status": "status",
}
HEADERS_B = {
    "shipment_id": "id",
    "origin": "from_loc",
    "destination": "to_loc",
    "carrier_code": "carrier_code",
    "shipped_date": "ship_date",
    "promised_date": "promise_date",
    "delivered_date": "delivery_date",
    "weight_kg": "weight",
    "cost_usd": "cost",
    "status": "status",
}


def fmt_date(d: date, rng: Random, defects: Counter[str]) -> str:
    """Four formats, mixed within a single column. Separators disambiguate them."""
    style = rng.randrange(4)
    defects[f"date_style_{style}"] += 1
    return [d.isoformat(), d.strftime("%d/%m/%Y"), d.strftime("%b %d %Y"), d.strftime("%Y%m%d")][
        style
    ]


def make_row(
    rng: Random, seq: int, start: date, span: int, defects: Counter[str]
) -> dict[str, str]:
    origin, dest = rng.choice(list(ROUTES))
    transit, late_p = ROUTES[(origin, dest)]
    shipped = start + timedelta(days=rng.randrange(span))
    promised = shipped + timedelta(days=transit)
    if rng.random() < late_p:
        delivered = promised + timedelta(days=rng.randrange(1, 10))
    else:
        delivered = promised - timedelta(days=rng.randrange(0, 3))

    row = {
        "shipment_id": f"SHP-{seq:05d}",
        "carrier_code": rng.choice(CARRIERS)[0],
        "origin": rng.choice(ALIASES[origin]),
        "destination": rng.choice(ALIASES[dest]),
        "shipped_date": fmt_date(shipped, rng, defects),
        "promised_date": fmt_date(promised, rng, defects),
        "delivered_date": fmt_date(delivered, rng, defects),
        "weight_kg": f"{rng.uniform(5, 900):.1f}",
        "cost_usd": f"{rng.uniform(50, 4000):.2f}",
        "status": rng.choice(["DELIVERED", "delivered", "Delivered"]),
    }

    if rng.random() < 0.07:  # still in transit: no delivery date yet
        row["delivered_date"] = rng.choice(NULLS)
        row["status"] = rng.choice(["IN_TRANSIT", "in transit", "In Transit"])
        defects["missing_delivered"] += 1
    if rng.random() < 0.05:
        row["weight_kg"] = rng.choice(NULLS)
        defects["missing_weight"] += 1
    if rng.random() < 0.04:
        row["cost_usd"] = rng.choice(NULLS)
        defects["missing_cost"] += 1
    if rng.random() < 0.03:
        row["carrier_code"] = rng.choice(NULLS)
        defects["missing_carrier"] += 1
    if rng.random() < 0.02:
        row["weight_kg"] = f"-{rng.uniform(5, 200):.1f}"
        defects["negative_weight"] += 1
    if rng.random() < 0.02:  # impossible: delivered before it shipped
        row["delivered_date"] = fmt_date(
            shipped - timedelta(days=rng.randrange(1, 5)), rng, defects
        )
        defects["delivered_before_shipped"] += 1
    return row


def write_csv(path: Path, headers: dict[str, str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers.values())
        writer.writerows([row[field] for field in headers] for row in rows)


def main() -> None:
    rng = Random(7)
    defects: Counter[str] = Counter()
    RAW.mkdir(parents=True, exist_ok=True)

    q3 = [make_row(rng, i, date(2024, 7, 1), 92, defects) for i in range(1, 241)]
    q4 = [make_row(rng, i, date(2024, 10, 1), 92, defects) for i in range(241, 441)]
    for batch in (q3, q4):
        for _ in range(4):
            batch.append(dict(rng.choice(batch)))
            defects["duplicate_row"] += 1

    write_csv(RAW / "shipments_2024_q3.csv", HEADERS_A, q3)
    write_csv(RAW / "shipments_2024_q4.csv", HEADERS_B, q4)
    write_csv(
        RAW / "carriers.csv",
        {"carrier_code": "carrier_code", "carrier_name": "name", "service_tier": "tier"},
        [{"carrier_code": c, "carrier_name": n, "service_tier": t} for c, n, t in CARRIERS],
    )

    # The check: if the generator stops seeding a defect, the pipeline built to
    # clean it silently loses its only test case. Fail here instead.
    assert all(defects[f"date_style_{i}"] for i in range(4)), "not every date format was used"
    assert defects["duplicate_row"] == 8
    for defect in (
        "missing_delivered",
        "missing_weight",
        "missing_cost",
        "missing_carrier",
        "negative_weight",
        "delivered_before_shipped",
    ):
        assert defects[defect] > 0, f"no {defect} rows were generated"

    print(f"{len(q3)} + {len(q4)} shipment rows, {len(CARRIERS)} carriers -> {RAW}")
    for name, count in sorted(defects.items()):
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
