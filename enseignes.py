"""Liste les enseignes d'agences_uniques.csv et compte combien se répètent.

Usage : python enseignes.py                          (agences_uniques.csv)
        python enseignes.py entree.csv sortie.csv

Sortie : enseignes.csv, une ligne par enseigne, triée par nombre de sociétés
décroissant. Une enseigne qui revient sur beaucoup de sociétés = un réseau.
"""
import csv
import re
import sys
import unicodedata
from collections import Counter, defaultdict


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texte).strip().lower()


def main():
    entree = sys.argv[1] if len(sys.argv) > 1 else "agences_uniques.csv"
    sortie = sys.argv[2] if len(sys.argv) > 2 else "enseignes.csv"

    compteur = Counter()
    formes = defaultdict(Counter)   # clé -> écritures d'origine rencontrées
    total = sans_enseigne = 0
    with open(entree, newline="", encoding="utf-8") as f:
        for ligne in csv.DictReader(f):
            total += 1
            cles = set()
            for e in (ligne.get("enseigne") or "").split(" | "):
                e = e.strip()
                if e:
                    cle = normaliser(e)
                    cles.add(cle)
                    formes[cle][e] += 1
            if not cles:
                sans_enseigne += 1
            for cle in cles:   # une société compte une fois par enseigne
                compteur[cle] += 1

    classement = compteur.most_common()
    with open(sortie, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["enseigne", "nb_societes"])
        for cle, n in classement:
            w.writerow([formes[cle].most_common(1)[0][0], n])

    repetees = [(c, n) for c, n in classement if n >= 2]
    print(f"{total} sociétés, dont {total - sans_enseigne} avec une enseigne "
          f"et {sans_enseigne} sans")
    print(f"{len(classement)} enseignes distinctes")
    print(f"{len(repetees)} enseignes sur 2 sociétés ou plus "
          f"({sum(n for _, n in repetees)} sociétés concernées)")
    for seuil in (5, 10, 50):
        gros = [n for _, n in classement if n >= seuil]
        print(f"  {len(gros)} enseignes sur {seuil} sociétés ou plus "
              f"({sum(gros)} sociétés)")
    print("Top 15 :")
    for cle, n in classement[:15]:
        print(f"  {formes[cle].most_common(1)[0][0]} : {n}")
    print(f"Écrit dans {sortie}")


if __name__ == "__main__":
    main()
