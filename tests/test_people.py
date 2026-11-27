from coldforge.companies import Company
from coldforge.people import Person, candidates, find_people, guess_email_pattern


def test_find_people_public_pages_only_and_limit_two():
    seen = []

    def fetch(url):
        seen.append(url)
        return "<p>Gérante : Alice Martin</p><p>Bob Durand — Founder</p>"

    people = find_people(Company("X", website="https://x.fr"), fetch=fetch)
    assert len(people) == 2
    assert all(person.source_url.startswith("https://x.fr/") for person in people)
    assert all("linkedin" not in url for url in seen)


def test_email_pattern_and_candidates():
    assert guess_email_pattern(["alice.martin@x.fr"], "x.fr") == "first.last"
    person = Person("Élodie", "Le Brun", "Directrice", "https://x.fr/equipe")
    pattern = guess_email_pattern(["alice.martin@x.fr"], "x.fr")
    assert candidates(person, pattern) == ["elodie.lebrun@x.fr"]
