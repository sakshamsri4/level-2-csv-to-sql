from pathlib import Path

from assay.ingest.pipeline import load_rules, profile

RULES = load_rules(Path("config/cleaning_rules.yaml"))


def _write(tmp_path: Path) -> Path:
    (tmp_path / "shipments_x.csv").write_text(
        "shipment_id,carrier,origin,destination,shipped,promised,delivered,"
        "weight_kg,cost_usd,status\n"
        "SHP-1,BLZ,LAX,JFK,2024-07-01,2024-07-06,2024-07-08,10.0,100.00,DELIVERED\n"
        "SHP-2,COY,los angeles,NYC,01/07/2024,06/07/2024,N/A,-5.0,N/A,in transit\n"
        "SHP-1,BLZ,LAX,JFK,2024-07-01,2024-07-06,2024-07-08,10.0,100.00,DELIVERED\n"
        "SHP-3,MRD,Atlantis,JFK,Jul 01 2024,20240706,20240628,7.0,50.00,delivered\n"
    )
    return tmp_path


def test_profiling_counts_the_null_markers_it_finds(tmp_path):
    report = profile(_write(tmp_path), RULES)[0]
    assert report["nulls"]["delivered_date"] == 1
    assert report["nulls"]["cost_usd"] == 1


def test_profiling_reports_duplicate_ids_before_anything_is_fixed(tmp_path):
    report = profile(_write(tmp_path), RULES)[0]
    assert report["duplicate_ids"] == 1


def test_profiling_names_the_locations_the_alias_table_does_not_know(tmp_path):
    report = profile(_write(tmp_path), RULES)[0]
    assert "Atlantis" in report["unmapped_locations"]


def test_profiling_counts_rows_that_could_not_have_happened(tmp_path):
    report = profile(_write(tmp_path), RULES)[0]
    assert report["delivered_before_shipped"] == 1
    assert report["negative_weight"] == 1


def test_profiling_shows_how_many_dates_use_each_format(tmp_path):
    report = profile(_write(tmp_path), RULES)[0]
    # Four rows x three date columns, minus the one N/A that parses as nothing.
    assert sum(report["date_formats"].values()) == 11
    assert report["date_formats"]["%Y-%m-%d"] > 0
    assert report["date_formats"]["%d/%m/%Y"] > 0
