from coldforge.companies import Company
from coldforge.db import Store


def test_company_upsert_and_signals(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        first = store.upsert_company(Company("Acme", website="https://www.acme.fr", city="Lyon"))
        second = store.upsert_company(Company("Acme SAS", website="https://acme.fr/about"))
        assert first.id == second.id
        store.add_company_signal(first.id, "SIZE", "12", "https://acme.fr")
        assert store.signals_of(first.id)["SIZE"] == ("12", "https://acme.fr")


def test_company_without_domain_dedupes_on_name_city(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        a = store.upsert_company(Company("Plomberie Rhône", city="Lyon"))
        b = store.upsert_company(Company("plomberie rhône", city="LYON"))
        assert a.id == b.id
