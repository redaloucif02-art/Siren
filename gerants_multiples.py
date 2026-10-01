"""Repère les gérants présents dans plusieurs sociétés.

Usage : python gerants_multiples.py                          (agences_uniques.csv)
        python gerants_multiples.py entree.csv sortie.csv

Sortie : une ligne par gérant (même nom normalisé) qui apparaît dans au moins
2 sociétés, triée par nombre de sociétés décroissant.
Attention : deux homonymes sont fusionnés. Regarde les villes pour juger.
"""
import csv
import re
import sys
import unicodedata
from collections import defaultdict


def normaliser(nom):
    nom = unicodedata.normalize("NFKD", nom)
    nom = "".join(c for c in nom if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", nom).strip().lower()


def extraire(gerants):
    """'Jean Dupont (Gérant) | HOLDING X (Président)' -> [(affichage, clé), ...]"""
    for part in gerants.split(" | "):
        affichage = re.sub(r"\s*\([^)]*\)\s*$", "", part).strip()
        if affichage:
            yield affichage, normaliser(affichage)


def main():
    entree = sys.argv[1] if len(sys.argv) > 1 else "agences_uniques.csv"
    sortie = sys.argv[2] if len(sys.argv) > 2 else "gerants_multiples.csv"

    groupes = defaultdict(lambda: {"nom": "", "societes": {}})
    with open(entree, newline="", encoding="utf-8") as f:
        for ligne in csv.DictReader(f):
            siren = ligne.get("siren", "")
            for affichage, cle in extraire(ligne.get("gerants") or ""):
                g = groupes[cle]
                g["nom"] = g["nom"] or affichage
                g["societes"][siren] = (ligne.get("nom", ""), ligne.get("ville", ""))

    multiples = [g for g in groupes.values() if len(g["societes"]) >= 2]
    multiples.sort(key=lambda g: -len(g["societes"]))

    with open(sortie, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["gerant", "nb_societes", "sirens", "societes", "villes"])
        for g in multiples:
            soc = g["societes"]
            w.writerow([
                g["nom"],
                len(soc),
                " | ".join(soc),
                " | ".join(n for n, _ in soc.values()),
                " | ".join(sorted({v for _, v in soc.values() if v})),
            ])

    total = sum(len(g["societes"]) for g in multiples)
    print(f"{len(groupes)} gérants distincts")
    print(f"{len(multiples)} gérants dans 2 sociétés ou plus ({total} sociétés concernées)")
    print(f"Écrit dans {sortie}")
    for g in multiples[:5]:
        print(f"  {g['nom']} : {len(g['societes'])} sociétés")


if __name__ == "__main__":
    main()
