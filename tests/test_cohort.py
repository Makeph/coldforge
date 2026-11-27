from coldforge.cohort import cohort, count, export_csv
from coldforge.companies import Company
from coldforge.db import Store


def test_cohort_filters_and_exports(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        company = store.upsert_company(Company("A", website="a.fr"))
        store.add_company_signal(company.id, "ADS", "1", "a.fr")
        store.add_company_signal(company.id, "SIZE", "12", "a.fr")
        selected = cohort(store, must=["ADS"], none_of=["NO_SITE"], ranges={"SIZE": (10, 20)})
        assert [item.id for item in selected] == [company.id]
        assert count(store, must=["ADS"]) == 1
    path = tmp_path / "out.csv"
    assert export_csv(selected, path) == 1
    assert "name,domain" in path.read_text(encoding="utf-8")
