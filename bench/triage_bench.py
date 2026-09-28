#!/usr/bin/env python3
"""triage_bench.py — quelle voie de triage classe le mieux les reponses entrantes ?

`coldforge.replies` a trois voies possibles : l'heuristique par mots-cles (gratuite,
sans cle), un modele local (laya, pas de reseau) et un modele heberge (Anthropic).
Ce harnais les fait tourner sur un meme jeu de cas etiquetes et sort de quoi
decider : exactitude, matrice de confusion, latence.

LA METRIQUE QUI DECIDE N'EST PAS L'EXACTITUDE GLOBALE.
`classify_reply` n'appelle un modele QUE lorsque l'heuristique repond "other".
Un modele peut donc afficher 90 % global et etre inutile, s'il se trompe
precisement sur les cas ou l'heuristique abstient. Le harnais isole cette
tranche et l'affiche separement : c'est la seule qui justifie de brancher quoi
que ce soit.

DEUX RESERVES, ECRITES ICI PARCE QU'ELLES LIMITENT TOUTE CONCLUSION.

1. Les cas livres dans `cases.jsonl` sont SYNTHETIQUES et ecrits a la main. Ils
   ne sont pas un echantillon de reponses reelles : ils refletent ce que leur
   auteur imagine d'une reponse. Toute mesure prise dessus est indicative, pas
   probante.

2. La table `replies` ne conserve PAS le texte -- son schema est
   (id, lead_id, campaign_id, detected_at, source, category). Le sujet et le
   corps sont jetes apres classification. Il est donc IMPOSSIBLE de rejouer des
   reponses reelles a posteriori. Pour que ce harnais devienne probant, il
   faudrait persister le texte (ou au moins un condense) au moment du scan.
   Voir --emit-schema-note.

Usage:
  python bench/triage_bench.py                       # heuristique seule
  python bench/triage_bench.py --laya                # + modele local
  python bench/triage_bench.py --laya --anthropic    # les trois
  python bench/triage_bench.py --cases mes_cas.jsonl
  python bench/triage_bench.py --laya --model convaiinnovations/laya-multilingual
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from coldforge.replies import CATEGORIES, _heuristic  # noqa: E402

DEFAUT_CAS = Path(__file__).resolve().parent / "cases.jsonl"
SEUIL_CONFIANCE_DEFAUT = 0.0   # 0 = on accepte toute reponse du modele


# --------------------------------------------------------------------------- #
# Voies de classification. Chacune rend (categorie, confiance|None).
# --------------------------------------------------------------------------- #
def voie_heuristique(subject: str, body: str):
    return _heuristic(f"{subject}\n{body}".strip()), None


class VoieLaya:
    """Modele local. Import garde : absent -> la voie n'est pas proposee."""

    QUESTIONS = {
        "category": {
            "type": "choice",
            "instructions": "Classify this reply to a cold outreach email.",
            "criteria": {
                "interested": "wants to know more, asks for pricing, a demo, "
                              "a call or a meeting, says yes",
                "not_interested": "declines, says no, already equipped, no budget, "
                                  "bad timing, not a fit",
                "unsubscribe": "asks to be removed from the list, to stop being "
                               "contacted, opts out",
                "ooo": "automatic out-of-office or vacation auto-reply",
                "other": "none of the above, unclear, or an unrelated question",
            },
        }
    }

    def __init__(self, model: str, device: str = "cpu"):
        from laya import Agent            # noqa: PLC0415 -- garde volontaire
        self.agent = Agent(model, device=device)
        self.model = model

    def __call__(self, subject: str, body: str):
        texte = f"{subject}\n{body}".strip()
        res = self.agent.predict({"request": texte}, self.QUESTIONS)
        d = res["answers"]["category"]
        return d["choice"], d.get("confidence")


class VoieAnthropic:
    """Modele heberge, via le chemin deja present dans coldforge.replies."""

    def __init__(self):
        from coldforge.config import get_settings   # noqa: PLC0415
        from coldforge.replies import _llm          # noqa: PLC0415
        self.settings = get_settings()
        if not self.settings.has_ai:
            raise RuntimeError("ANTHROPIC_API_KEY absent")
        self._llm = _llm

    def __call__(self, subject: str, body: str):
        texte = f"{subject}\n{body}".strip()
        return self._llm(texte, self.settings) or "other", None


# --------------------------------------------------------------------------- #
def charger_cas(chemin: Path):
    cas = []
    for n, ligne in enumerate(chemin.read_text(encoding="utf-8").splitlines(), 1):
        ligne = ligne.strip()
        if not ligne or ligne.startswith("//"):
            continue
        try:
            o = json.loads(ligne)
        except json.JSONDecodeError as e:
            raise SystemExit("cases.jsonl ligne %d: %s" % (n, e))
        if o.get("expected") not in CATEGORIES:
            raise SystemExit("cases.jsonl ligne %d: 'expected' doit etre dans %s"
                             % (n, list(CATEGORIES)))
        cas.append(dict(subject=o.get("subject", ""), body=o.get("body", ""),
                        expected=o["expected"], note=o.get("note", "")))
    if not cas:
        raise SystemExit("aucun cas dans %s" % chemin)
    return cas


def matrice(paires):
    """paires: liste de (attendu, obtenu). Rend la matrice imprimee."""
    m = defaultdict(Counter)
    for att, obt in paires:
        m[att][obt] += 1
    larg = max(len(c) for c in CATEGORIES) + 1
    print("      %s%s" % ("obtenu ->".ljust(larg), "".join(c[:6].rjust(8) for c in CATEGORIES)))
    for att in CATEGORIES:
        if not m[att]:
            continue
        cells = "".join(
            (str(m[att][o]) if m[att][o] else "·").rjust(8) for o in CATEGORIES)
        print("      %s%s" % (att.ljust(larg), cells))


def evaluer(nom, voie, cas, seuil, verbeux):
    paires, lats, abstentions = [], [], 0
    detail = []
    for c in cas:
        t0 = time.time()
        try:
            obt, conf = voie(c["subject"], c["body"])
        except Exception as e:           # une voie qui casse ne tue pas le banc
            obt, conf = "other", None
            detail.append((c, "other", None, "ERREUR: %s" % str(e)[:40]))
            paires.append((c["expected"], "other"))
            lats.append((time.time() - t0) * 1000)
            continue
        lats.append((time.time() - t0) * 1000)
        abstenu = conf is not None and conf < seuil
        if abstenu:
            abstentions += 1
            obt = "other"                # sous le seuil, on n'affirme pas
        paires.append((c["expected"], obt))
        detail.append((c, obt, conf, "ABSTENU" if abstenu else ""))

    bons = sum(1 for a, o in paires if a == o)
    n = len(paires)
    # la tranche qui compte: la ou l'heuristique abstient
    amb = [(c, o) for (c, o, _cf, _e) in detail
           if _heuristic(f"{c['subject']}\n{c['body']}".strip()) == "other"]
    amb_bons = sum(1 for c, o in amb if c["expected"] == o)

    print("\n" + "=" * 74)
    print("%s" % nom)
    print("=" * 74)
    print("  exactitude globale        %2d/%-2d  (%3.0f %%)" % (bons, n, 100 * bons / n))
    # Pour une voie qui peut s'abstenir, l'exactitude globale melange erreurs et
    # abstentions. La paire honnete est precision-quand-elle-repond / couverture.
    repondus = [(c, o) for (c, o, _cf, e) in detail if e != "ABSTENU"]
    if len(repondus) < n:
        just = sum(1 for c, o in repondus if c["expected"] == o)
        print("  PRECISION quand elle repond %2d/%-2d  (%3.0f %%)   couverture %2d/%-2d (%3.0f %%)"
              % (just, len(repondus), 100 * just / len(repondus) if repondus else 0,
                 len(repondus), n, 100 * len(repondus) / n))
    if amb:
        print("  SUR LA TRANCHE QUI COMPTE %2d/%-2d  (%3.0f %%)   <- heuristique abstient"
              % (amb_bons, len(amb), 100 * amb_bons / len(amb)))
    else:
        print("  tranche qui compte        aucun cas ou l'heuristique abstient")
    if lats:
        print("  latence                   mediane %.0f ms, max %.0f ms"
              % (sorted(lats)[len(lats) // 2], max(lats)))
    if seuil > 0:
        print("  abstentions (conf < %.2f)  %d" % (seuil, abstentions))
    print()
    matrice(paires)

    if verbeux:
        print("\n  detail des erreurs :")
        for c, obt, conf, err in detail:
            if obt == c["expected"] and not err:
                continue
            if err == "ABSTENU" and obt != c["expected"]:
                pass   # une abstention n'est pas une erreur, mais on la montre
            txt = (c["subject"] + " " + c["body"]).strip()[:46]
            print("    attendu %-15s obtenu %-15s %s %s%s"
                  % (c["expected"], obt,
                     ("conf %.3f" % conf) if isinstance(conf, (int, float)) else "conf -",
                     txt, (" | " + err) if err else ""))
    return dict(nom=nom, bons=bons, n=n, amb_bons=amb_bons, amb_n=len(amb))


NOTE_SCHEMA = """
Pour que ce harnais devienne probant, la table `replies` doit conserver le texte.
Son schema actuel ne le fait pas :

    replies(id, lead_id, campaign_id, detected_at, source, category)

Migration minimale, dans le style des migrations deja presentes dans db.py
(cf. le ALTER TABLE qui a ajoute `category`) :

    reply_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(replies)")}
    if "subject" not in reply_cols:
        self.conn.execute("ALTER TABLE replies ADD COLUMN subject TEXT DEFAULT ''")
    if "body" not in reply_cols:
        self.conn.execute("ALTER TABLE replies ADD COLUMN body TEXT DEFAULT ''")

Reserve : cela met du texte de prospect dans la base. C'est coherent avec la
these du projet (tout reste chez l'utilisateur, dans un fichier SQLite qu'il peut
supprimer), mais c'est une donnee personnelle en plus, et la decision appartient
a l'auteur -- pas au harnais.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cases", type=Path, default=DEFAUT_CAS)
    ap.add_argument("--laya", action="store_true", help="ajouter la voie locale")
    ap.add_argument("--anthropic", action="store_true", help="ajouter la voie hebergee")
    ap.add_argument("--model", default="convaiinnovations/laya-multilingual")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--seuil", type=float, default=SEUIL_CONFIANCE_DEFAUT,
                    help="sous ce seuil de confiance, la voie s'abstient (rend 'other')")
    ap.add_argument("-v", "--verbose", action="store_true", help="detail des erreurs")
    ap.add_argument("--emit-schema-note", action="store_true",
                    help="afficher ce qu'il faudrait changer pour mesurer sur du reel")
    a = ap.parse_args()

    if a.emit_schema_note:
        print(NOTE_SCHEMA)
        return 0

    cas = charger_cas(a.cases)
    synth = sum(1 for c in cas if c.get("note", "").startswith("synthetique"))
    print("%d cas charges depuis %s" % (len(cas), a.cases.name))
    if synth:
        print("  ATTENTION: %d/%d sont marques SYNTHETIQUES -- indicatif, pas probant"
              % (synth, len(cas)))
    print("  repartition attendue: %s"
          % dict(Counter(c["expected"] for c in cas)))

    voies = [("heuristique (mots-cles, sans cle)", voie_heuristique)]
    if a.laya:
        try:
            print("\nchargement de %s ..." % a.model, file=sys.stderr)
            voies.append(("laya local — %s" % a.model.split("/")[-1],
                          VoieLaya(a.model, a.device)))
        except ImportError:
            print("  laya absent (pip install laya) — voie ignoree", file=sys.stderr)
        except Exception as e:
            print("  laya indisponible: %s — voie ignoree" % str(e)[:60], file=sys.stderr)
    if a.anthropic:
        try:
            voies.append(("anthropic heberge", VoieAnthropic()))
        except Exception as e:
            print("  anthropic indisponible: %s — voie ignoree" % str(e)[:60],
                  file=sys.stderr)

    res = [evaluer(nom, voie, cas, a.seuil, a.verbose) for nom, voie in voies]

    print("\n" + "=" * 74)
    print("COMPARAISON")
    print("=" * 74)
    print("  %-40s %12s %14s" % ("voie", "global", "tranche utile"))
    for r in sorted(res, key=lambda r: -(r["amb_bons"] / r["amb_n"] if r["amb_n"] else -1)):
        util = ("%d/%d (%.0f %%)" % (r["amb_bons"], r["amb_n"],
                                     100 * r["amb_bons"] / r["amb_n"])) if r["amb_n"] else "n/a"
        print("  %-40s %5d/%-3d %3.0f %% %14s"
              % (r["nom"][:40], r["bons"], r["n"], 100 * r["bons"] / r["n"], util))
    print("\n  La colonne qui decide est 'tranche utile': les cas ou l'heuristique")
    print("  abstient sont les SEULS ou coldforge appelle un modele.")
    if synth:
        print("\n  Et elle reste indicative tant que les cas sont synthetiques.")
        print("  `--emit-schema-note` dit ce qu'il faudrait changer pour du reel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
