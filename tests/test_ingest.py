from pathlib import Path

import duckdb

from assay.ingest.pipeline import ingest, load_rules

RULES = load_rules(Path("config/cleaning_rules.yaml"))

ROWS = (
    "shipment_id,carrier,origin,destination,shipped,promised,delivered,weight_kg,cost_usd,status\n"
    # four date formats, four spellings of two cities
    "SHP-1,BLZ,LAX,JFK,2024-07-01,2024-07-06,2024-07-09,10.0,100.00,DELIVERED\n"
    "SHP-2,COY,los angeles,NYC,01/07/2024,06/07/2024,N/A,20.0,200.00,in transit\n"
    'SHP-3,MRD,  LAX ,"New York, NY",Jul 01 2024,20240706,20240704,-5.0,300.00,delivered\n'
    # exact duplicate of SHP-1
    "SHP-1,BLZ,LAX,JFK,2024-07-01,2024-07-06,2024-07-09,10.0,100.00,DELIVERED\n"
    # impossible: delivered before it shipped
    "SHP-4,NPT,SEA,ATL,2024-08-01,2024-08-07,2024-07-20,30.0,400.00,delivered\n"
    # unmappable location
    "SHP-5,ORN,Atlantis,ATL,2024-08-01,2024-08-07,2024-08-08,40.0,500.00,delivered\n"
)


def _raw(tmp_path: Path) -> Path:
    (tmp_path / "shipments_x.csv").write_text(ROWS)
    (tmp_path / "carriers.csv").write_text(
        "carrier_code,name,tier\nBLZ,Blizzard Freight,express\nCOY,Coyote Logistics,standard\n"
    )
    return tmp_path


def _load(tmp_path: Path):
    raw, db = _raw(tmp_path), tmp_path / "w.duckdb"
    report = ingest(raw, db, RULES)
    return report, duckdb.connect(str(db), read_only=True)


def test_every_date_format_is_parsed_into_a_real_date(tmp_path):
    _, con = _load(tmp_path)
    shipped = con.execute("SELECT DISTINCT shipped_date FROM shipments").fetchall()
    assert all(row[0] is not None for row in shipped)


def test_the_four_spellings_of_a_city_become_one_code(tmp_path):
    _, con = _load(tmp_path)
    origins = {r[0] for r in con.execute("SELECT DISTINCT origin FROM shipments").fetchall()}
    assert origins <= {"LAX", "JFK", "SEA", "ATL", "ORD", "DFW"}
    assert "LAX" in origins


def test_null_markers_become_real_nulls(tmp_path):
    _, con = _load(tmp_path)
    value = con.execute(
        "SELECT delivered_date FROM shipments WHERE shipment_id = 'SHP-2'"
    ).fetchone()
    assert value[0] is None


def test_delay_days_is_derived_from_promised_and_delivered(tmp_path):
    _, con = _load(tmp_path)
    delay = con.execute("SELECT delay_days FROM shipments WHERE shipment_id = 'SHP-1'").fetchone()[
        0
    ]
    assert delay == 3


def test_the_duplicate_row_is_removed_and_the_removal_is_reported(tmp_path):
    report, con = _load(tmp_path)
    count = con.execute("SELECT count(*) FROM shipments WHERE shipment_id='SHP-1'").fetchone()[0]
    assert count == 1
    assert report["duplicates_removed"] == 1


def test_rows_that_cannot_be_cleaned_are_quarantined_not_dropped(tmp_path):
    report, con = _load(tmp_path)
    rejected = {
        r[0]: r[1] for r in con.execute("SELECT shipment_id, reject_reason FROM rejects").fetchall()
    }
    assert "SHP-4" in rejected and "before" in rejected["SHP-4"]
    assert "SHP-5" in rejected and "origin" in rejected["SHP-5"]
    assert report["rows_rejected"] == 2


def test_every_raw_row_is_accounted_for(tmp_path):
    report, _ = _load(tmp_path)
    assert (
        report["rows_read"]
        == report["rows_loaded"] + report["rows_rejected"] + report["duplicates_removed"]
    )


def test_an_impossible_weight_is_nulled_and_the_change_is_counted(tmp_path):
    report, con = _load(tmp_path)
    weight = con.execute("SELECT weight_kg FROM shipments WHERE shipment_id='SHP-3'").fetchone()[0]
    assert weight is None
    assert report["weights_nulled"] == 1


def test_the_carrier_dimension_is_loaded(tmp_path):
    _, con = _load(tmp_path)
    assert con.execute("SELECT count(*) FROM carriers").fetchone()[0] == 2
