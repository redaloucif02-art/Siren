#!/usr/bin/env python3
"""Contrôle qualité de data/places.jsonl.

Usage : python scripts/check_places.py [data/places.jsonl]

Affiche :
  1. les agences sans aucun résultat Google Maps ;
  2. le taux de remplissage de chaque champ des fiches ;
  3. la répartition des types (type / types / category) ;
  4. les fiches dont le type n'est PAS immobilier.
Écrit aussi data/check_non_immo.csv (fiches hors immobilier) et
data/check_sans_immo.csv (agences dont AUCUNE fiche n'est immobilière).
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data/places.jsonl"

# Un type est "immobilier" s'il contient un de ces morceaux (minuscules, sans accents).
IMMO_KEYWORDS = (
    "immobili", "real estate", "estate agent", "property", "syndic",
    "administrateur de biens", "gestionnaire de biens", "gestion locative",
    "promoteur", "agent de location", "location de logement", "foncier",
    "lotisseur", "marchand de biens", "expert immobilier", "diagnostic immobilier",
)
FIELDS = ["title", "address", "latitude", "longitude", "rating", "ratingCount",
          "type", "types", "website", "phoneNumber", "description", "cid", "placeId"]


def norm(s):
    s = str(s).lower()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("ô", "o"), ("î", "i"), ("ç", "c")):
        s = s.replace(a, b)
    return s


def types_of(p):
    out = []
    if p.get("type"):
        out.append(p["type"])
    out += [t for t in (p.get("types") or []) if t]
    if p.get("category"):
        out.append(p["category"])
    return list(dict.fromkeys(out))


def is_immo(p):
    return any(k in norm(t) for t in types_of(p) for k in IMMO_KEYWORDS)


def empty(v):
    return v is None or v == "" or v == [] or v == {}


def main():
    recs = [json.loads(l) for l in open(PATH, encoding="utf-8") if l.strip()]
    n = len(recs)
    if not n:
        sys.exit("Fichier vide.")

    no_place = [r for r in recs if not r.get("places")]
    places = [(r, p) for r in recs for p in r.get("places", [])]
    print(f"Agences traitées : {n}")
    print(f"Fiches Google récupérées : {len(places)} (moyenne {len(places) / n:.1f} par agence)")
    print(f"Agences SANS résultat : {len(no_place)} ({100 * len(no_place) / n:.1f} %)\n")

    print("Remplissage des champs (sur toutes les fiches) :")
    for f in FIELDS:
        filled = sum(1 for _, p in places if not empty(p.get(f)))
        print(f"  {f:12s} {100 * filled / max(1, len(places)):5.1f} %  ({len(places) - filled} vides)")

    ctypes = Counter(t for _, p in places for t in types_of(p))
    print("\nTypes les plus fréquents :")
    for t, c in ctypes.most_common(25):
        print(f"  {c:7d}  {t}{'' if any(k in norm(t) for k in IMMO_KEYWORDS) else '   <-- hors immo'}")

    non_immo = [(r, p) for r, p in places if not is_immo(p)]
    sans_immo = [r for r in recs if r.get("places") and not any(is_immo(p) for p in r["places"])]
    print(f"\nFiches hors immobilier : {len(non_immo)} / {len(places)} ({100 * len(non_immo) / max(1, len(places)):.1f} %)")
    print(f"Agences dont aucune fiche n'est immobilière : {len(sans_immo)}")

    with open(ROOT / "data/check_non_immo.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["siren", "nom_complet", "title", "types", "address"])
        for r, p in non_immo:
            w.writerow([r["siren"], r["source"]["nom_complet"], p.get("title"), " | ".join(types_of(p)), p.get("address")])
    with open(ROOT / "data/check_sans_immo.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["siren", "nom_complet", "adresse", "nb_fiches", "types_trouves"])
        for r in sans_immo:
            ts = sorted({t for p in r["places"] for t in types_of(p)})
            w.writerow([r["siren"], r["source"]["nom_complet"], r["source"]["adresse"], len(r["places"]), " | ".join(ts)])
    print("\nFichiers écrits : data/check_non_immo.csv, data/check_sans_immo.csv")


if __name__ == "__main__":
    main()
