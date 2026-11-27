import csv

from coldforge.discover import discover_csv, discover_osm, estimate


def test_discover_osm_filters_names_and_domains():
    payload = {"elements": [
        {"tags": {"name": "A", "website": "https://www.a.fr", "phone": "1"}},
        {"tags": {"name": "A duplicate", "website": "https://a.fr/about"}},
        {"tags": {"website": "https://unnamed.fr"}},
    ]}
    companies = discover_osm("plumber", "Lyon", fetch_json=lambda _url: payload)
    assert [company.name for company in companies] == ["A"]
    assert estimate()["cost_eur"] == 0


def test_discover_csv(tmp_path):
    path = tmp_path / "companies.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("name", "website", "city", "category", "phone"))
        writer.writeheader()
        writer.writerow({"name": "A", "website": "a.fr", "city": "Paris", "category": "x", "phone": "2"})
    assert discover_csv(path)[0].domain == "a.fr"
