# Spec — pipeline « prospection » dans coldforge (idées de Sellbird, code original)

Licence : coldforge est MIT, Sellbird est AGPL-3.0. **Ne copier aucun code ni aucun prompt de Sellbird.**
On reprend des idées (un pipeline découvrir → profiler → décideur → vérifier → garde d'envoi), pas le texte.
Explee (icp build + leads score) et BabyLoveGrowth (geo check + content plan/draft) existent déjà : ne pas y toucher.

## Contraintes dures
- Stdlib + `requests` + `typer` + `rich` + `pyyaml` seulement (dépendances actuelles). Aucun nouveau paquet obligatoire.
- Tout passe par `Store` (coldforge/db.py) ; migration additive dans `_migrate`, jamais de DROP.
- Zéro appel réseau dans les tests : injecter une fonction `fetch(url) -> str | None` ; fixtures HTML dans `tests/fixtures/`.
- Aucun envoi réel : l'existant `DryRunSender` reste le défaut. Rien n'est envoyé sans approbation.
- Chaque module a une fonction pure testable ; les 38 tests existants restent verts.
- Une règle d'honnêteté par module : un échec est une observation (« aucune page équipe trouvée »), jamais un silence.

## Modules à créer (dans `coldforge/`)

### 1. `companies.py` + tables `companies`, `company_signals`
`Company(id, name, domain, website, city, category, phone, source, created_at)`. Clé unique : `domain` (sinon `name+city`).
`Store.upsert_company`, `list_companies`, `add_company_signal(company_id, key, value, evidence_url)`, `signals_of(company_id)`.

### 2. `discover.py`
- `discover_osm(category, city, *, fetch_json) -> list[Company]` : Overpass API (gratuit, sans clé). Filtre les entrées sans nom, dédoublonne par domaine.
- `discover_csv(path)` : import d'un CSV (name, website, city, category, phone).
- `estimate(...)` : toujours afficher le coût avant (0 € pour OSM) — le contrat `--estimate` existe pour de futures sources payantes.
- CLI : `coldforge discover --source osm --category plumber --city Lyon [--estimate]`.

### 3. `signals.py` — profileur HTTP gratuit (aucun appel modèle)
Catalogue déclaratif (dict) de signaux, chacun avec `key`, `label`, `cost` (« free »), `detect(html, site) -> (value, evidence) | None` :
`NO_WEBSITE`, `NO_ONLINE_BOOKING` (pas de lien Calendly/Doctolib/Planity/formulaire de réservation), `NO_SCHEMA_MARKUP` (pas de JSON-LD),
`BLOG_STALE` (dernier article > 12 mois ou absent), `TECH_STACK` (WordPress/Wix/Shopify/Webflow par signatures), `BLOCKS_AI_CRAWLERS` (robots.txt : GPTBot/ClaudeBot/PerplexityBot),
`RUNNING_GOOGLE_ADS` (gtag AW-/googleadservices), `HIRING_ROLE` (page carrières), `CONTACT_FORM_URL`.
Mots-clés en **français et anglais**.
**Porte de confirmation** : aucun signal n'est collecté tant que l'utilisateur ne l'a pas confirmé (`~/.coldforge/signals.yaml`).
CLI : `coldforge signals list`, `signals confirm free|KEY…`, `signals profile [--limit N]`.
Budget réseau : 3 requêtes max par entreprise (home, robots.txt, une page utile), User-Agent déclaré, timeout 10 s, respecte robots.txt pour le crawl des pages.

### 4. `people.py` — qui décide
`find_people(company, *, fetch) -> list[Person]`, **2 personnes max** par entreprise, uniquement depuis les pages publiques de l'entreprise
(`/equipe`, `/a-propos`, `/about`, `/team`, `/mentions-legales` — le dirigeant y est nommé en France). Titres décideurs FR+EN (gérant, dirigeant, fondateur, directeur, owner, CEO…).
Chaque personne porte `source_url`. Aucun scraping de réseaux sociaux.
`guess_email_pattern(known_emails, domain)` + `candidates(person, pattern)` ; la vérification réutilise `verify.verify_email`.
Seuls les statuts `ok` sont envoyables ; `risky`/`invalid` ne le sont jamais. Les personnes deviennent des `Lead` (table existante) liés à la `Company`.
CLI : `coldforge people find [--company X]`.

### 5. `cohort.py`
`cohort(store, must=[…], none_of=[…], ranges={key:(lo,hi)}) -> list[Company]` et `count`. CLI : `coldforge cohort --must NO_ONLINE_BOOKING --must RUNNING_GOOGLE_ADS --none-of NO_WEBSITE` affiche le compte puis la liste avec la **raison** de chaque inclusion.
`coldforge export --cohort … --csv out.csv` : CSV prêt pour un séquenceur (liste seule, sans envoi).

### 6. `gate.py` + file d'approbation
`check_message(store, settings, message, now) -> GateResult(ok, reasons[])`, déterministe, sans modèle :
adresse supprimée, `verify_status != ok`, plafond quotidien (`sent_today`), heures calmes (08h–19h locale du destinataire par défaut, configurable), ligne de désinscription et adresse postale présentes (réutiliser `lint.py`), réponse déjà reçue.
Nouvelle colonne `messages.approved INTEGER DEFAULT 0` (migration additive) ; `tick` n'envoie que `approved=1` sauf `--auto-approve`.
CLI : `coldforge outbox list`, `outbox approve <id…|--all-passing>`, `outbox reject <id>`. Une approbation qui échoue la garde est refusée avec les raisons.

## Tests (pytest, hors réseau)
Un fichier par module : `tests/test_companies.py`, `test_discover.py`, `test_signals.py`, `test_people.py`, `test_cohort.py`, `test_gate.py`.
Chaque détecteur de signal : un cas positif ET un cas négatif en fixture HTML (FR et EN).
`test_gate.py` : un test par raison de refus + un cas passant. Cible : toute la suite verte, `ruff check` propre.

## Hors périmètre
Sources payantes (DataForSEO, Serper…), LinkedIn, tableau de bord web, boucle « heartbeat » autonome, envoi via Gmail/Microsoft. À noter dans le README comme suite possible.
