#!/usr/bin/env python3
"""Choisit, pour chaque agence, la fiche Google Maps qui lui correspond le mieux.

Entrée  : data/places.jsonl
Sorties : data/agences_enrichies.csv  (1 ligne par SIREN : ta ligne d'origine + la meilleure fiche + score)
          data/a_revoir.csv           (agences sans résultat, sans fiche immobilière ou à faible confiance)

Score sur 100 :
  adresse : code postal (30) + numéro de rue (15) + mots de la rue (25)
  nom     : ressemblance raison sociale / titre de la fiche (20)
  type    : fiche immobilière (+10)
Confiance : haute (>=70 et immobilier) | moyenne (>=50) | faible (>=30) | aucune (<30 ou pas de fiche)
"""
import csv
import json
import re
import sys
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data/places.jsonl"
OUT = ROOT / "data/agences_enrichies.csv"
REVIEW = ROOT / "data/a_revoir.csv"

IMMO_KEYWORDS = (
    "immobili", "real estate", "estate agent", "property", "syndic",
    "administrateur de biens", "gestionnaire de biens", "gestion locative",
    "promoteur", "agent de location", "location de logement", "foncier",
    "lotisseur", "marchand de biens", "expert immobilier", "diagnostic immobilier",
    "location de vacances", "location d'appartement", "location de meubles",
)
ABBREV = {
    "av": "avenue", "ave": "avenue", "bd": "boulevard", "bld": "boulevard", "boul": "boulevard",
    "all": "allee", "imp": "impasse", "pl": "place", "chem": "chemin", "rte": "route",
    "fg": "faubourg", "fbg": "faubourg", "res": "residence", "zi": "zone", "za": "zone",
    "st": "saint", "ste": "sainte", "mal": "marechal", "gal": "general", "pdt": "president",
}
STREET_STOP = {
    "rue", "avenue", "boulevard", "allee", "impasse", "place", "chemin", "route", "quai", "cours",
    "passage", "square", "faubourg", "residence", "zone", "de", "du", "des", "la", "le", "les", "l",
    "d", "et", "en", "au", "aux", "sur", "bis", "ter", "france", "cedex", "batiment", "bat", "etage",
}
NAME_STOP = {
    "sarl", "sas", "sasu", "sa", "eurl", "sci", "snc", "scp", "selarl", "sca", "scs", "ei", "entreprise",
    "individuelle", "societe", "soc", "cabinet", "agence", "immobilier", "immobiliere", "immo", "immobiliers",
    "de", "du", "des", "la", "le", "les", "l", "d", "et", "en", "au", "aux", "groupe", "gestion",
    "transaction", "transactions", "conseil", "france",
}


def norm(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9' ]+", " ", s.replace("'", " ")).strip()


def tokens(s):
    return [ABBREV.get(t, t) for t in norm(s).split()]


def parse_address(addr):
    t = tokens(addr)
    cp = next((x for x in t if re.fullmatch(r"\d{5}", x)), None)
    num = t[0] if t and re.fullmatch(r"\d+", t[0]) else None
    street = {x for x in t if not x.isdigit() and x not in STREET_STOP and len(x) > 1}
    return cp, num, street


def types_of(p):
    out = [p.get("type")] + list(p.get("types") or []) + [p.get("category")]
    return [t for t in dict.fromkeys(out) if t]


def is_immo(p):
    return any(k in norm(t) for t in types_of(p) for k in IMMO_KEYWORDS)


def name_score(src_name, title):
    a = [t for t in tokens(src_name) if t not in NAME_STOP and len(t) > 1]
    b = set(tokens(title))
    ratio = SequenceMatcher(None, norm(src_name), norm(title)).ratio()
    overlap = sum(1 for t in a if t in b) / len(a) if a else 0
    return 20 * max(overlap, ratio * 0.8)


def score(src, p):
    cp_s, num_s, street_s = parse_address(src["adresse"])
    cp_p, num_p, street_p = parse_address(p.get("address", ""))
    adr = 0.0
    if cp_s and cp_p:
        adr += 30 if cp_s == cp_p else (10 if cp_s[:2] == cp_p[:2] else 0)
    ov = len(street_s & street_p) / len(street_s) if street_s else 0
    adr += 25 * ov
    if num_s and num_p and num_s == num_p and ov > 0:
        adr += 15
    nom = name_score(src["nom_complet"], p.get("title", ""))
    immo = is_immo(p)
    return adr + nom + (10 if immo else 0), adr, nom, immo


def confidence(sc, immo):
    if sc >= 70 and immo:
        return "haute"
    if sc >= 50:
        return "moyenne"
    if sc >= 30:
        return "faible"
    return "aucune"


def main():
    recs = [json.loads(l) for l in open(PATH, encoding="utf-8") if l.strip()]
    src_cols = list(recs[0]["source"].keys())
    cols = src_cols + ["n_fiches", "confiance", "score", "score_adresse", "score_nom", "immo",
                       "match_title", "match_type", "match_address", "match_phone", "match_website",
                       "match_rating", "match_ratingCount", "match_latitude", "match_longitude", "match_cid"]
    stats = Counter()
    with open(OUT, "w", newline="", encoding="utf-8") as fo, open(REVIEW, "w", newline="", encoding="utf-8") as fr:
        wo = csv.DictWriter(fo, cols)
        wr = csv.DictWriter(fr, ["raison"] + cols)
        wo.writeheader()
        wr.writeheader()
        for r in recs:
            src, places = r["source"], r.get("places") or []
            row = {c: src.get(c, "") for c in src_cols}
            row["n_fiches"] = len(places)
            best = None
            if places:
                scored = [(score(src, p), p) for p in places]
                (sc, adr, nom, immo), best = max(scored, key=lambda x: x[0][0])
                row.update(
                    confiance=confidence(sc, immo), score=round(sc, 1), score_adresse=round(adr, 1),
                    score_nom=round(nom, 1), immo=immo, match_title=best.get("title"),
                    match_type=" | ".join(types_of(best)), match_address=best.get("address"),
                    match_phone=best.get("phoneNumber"), match_website=best.get("website"),
                    match_rating=best.get("rating"), match_ratingCount=best.get("ratingCount"),
                    match_latitude=best.get("latitude"), match_longitude=best.get("longitude"),
                    match_cid=best.get("cid"),
                )
            else:
                row.update(confiance="aucune", score=0, immo=False)
            if row["confiance"] == "aucune":  # pas de fiche exploitable : on ne garde pas la fiche douteuse
                for k in cols[len(src_cols) + 5:]:
                    row[k] = ""
            wo.writerow(row)
            stats[row["confiance"]] += 1
            if not places:
                wr.writerow({"raison": "aucun résultat Google", **row})
            elif row["confiance"] == "aucune":
                wr.writerow({"raison": "fiches trouvées mais aucune ne correspond", **row})
            elif row["confiance"] == "faible":
                wr.writerow({"raison": "correspondance faible", **row})

    n = len(recs)
    print(f"Agences : {n}")
    for k in ("haute", "moyenne", "faible", "aucune"):
        print(f"  confiance {k:8s}: {stats[k]:6d}  ({100 * stats[k] / n:.1f} %)")
    print("\nFichiers écrits : data/agences_enrichies.csv, data/a_revoir.csv")


if __name__ == "__main__":
    main()
