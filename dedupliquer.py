"""Fusionne data/agences_*.csv en un seul fichier, une ligne par SIREN,
en retirant les sociétés sans gérant renseigné.

Usage : python dedupliquer.py                      (data/ -> agences_uniques.csv)
        python dedupliquer.py dossier sortie.csv
"""
import csv
import glob
import os
import sys


def main():
    dossier = sys.argv[1] if len(sys.argv) > 1 else "data"
    sortie = sys.argv[2] if len(sys.argv) > 2 else "agences_uniques.csv"
    fichiers = sorted(glob.glob(os.path.join(dossier, "agences_*.csv")))
    if not fichiers:
        sys.exit(f"Aucun fichier agences_*.csv dans {dossier}/")

    vus, lignes, total, champs, sans_gerant = set(), [], 0, None, 0
    for chemin in fichiers:
        with open(chemin, newline="", encoding="utf-8") as f:
            lecteur = csv.DictReader(f)
            champs = champs or lecteur.fieldnames
            for ligne in lecteur:
                total += 1
                siren = ligne.get("siren", "")
                if siren and siren in vus:
                    continue
                vus.add(siren)
                if not (ligne.get("gerants") or "").strip():
                    sans_gerant += 1
                    continue
                lignes.append(ligne)

    with open(sortie, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=champs)
        w.writeheader()
        w.writerows(lignes)
    print(f"{total} lignes lues, {len(vus)} sociétés uniques, "
          f"{sans_gerant} retirées (sans gérant)")
    print(f"{len(lignes)} sociétés écrites dans {sortie}")


if __name__ == "__main__":
    main()
