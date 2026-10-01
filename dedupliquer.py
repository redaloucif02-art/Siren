"""Fusionne data/agences_*.csv en un seul fichier propre.

Étapes :
  1. une ligne par SIREN (doublons entre départements retirés) ;
  2. retire les sociétés sans gérant exploitable ([Non-Diffusible] et
     commissaires aux comptes ne comptent pas) ;
  3. retire les gérants présents dans SEUIL sociétés ou plus (trop gros pour
     être contactés) : ils sont enlevés de la colonne gerants, et la société
     est supprimée s'il ne reste personne.

Usage : python dedupliquer.py                          (data/ -> agences_uniques.csv)
        python dedupliquer.py dossier sortie.csv
        python dedupliquer.py --seuil 10               (seuil personnalisé, 5 par défaut)
"""
import csv
import glob
import os
import sys
from collections import defaultdict

from gerants_multiples import valides


def main():
    args = sys.argv[1:]
    seuil = 5
    if "--seuil" in args:
        i = args.index("--seuil")
        seuil = int(args[i + 1])
        del args[i:i + 2]
    dossier = args[0] if len(args) > 0 else "data"
    sortie = args[1] if len(args) > 1 else "agences_uniques.csv"

    fichiers = sorted(glob.glob(os.path.join(dossier, "agences_*.csv")))
    if not fichiers:
        sys.exit(f"Aucun fichier agences_*.csv dans {dossier}/")

    # 1. doublons
    vus, uniques, total, champs = set(), [], 0, None
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
                uniques.append(ligne)

    # 2. nombre de sociétés par gérant (sur les sociétés uniques)
    nb = defaultdict(int)
    for ligne in uniques:
        for cle in {c for _, _, c in valides(ligne.get("gerants"))}:
            nb[cle] += 1

    # 3. filtres
    sans_gerant = trop_gros = 0
    finales = []
    for ligne in uniques:
        parts = list(valides(ligne.get("gerants")))
        if not parts:
            sans_gerant += 1
            continue
        gardes = [texte for texte, _, cle in parts if nb[cle] < seuil]
        if not gardes:
            trop_gros += 1
            continue
        ligne["gerants"] = " | ".join(gardes)
        finales.append(ligne)

    with open(sortie, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=champs)
        w.writeheader()
        w.writerows(finales)

    print(f"{total} lignes lues, {len(uniques)} sociétés uniques")
    print(f"{sans_gerant} retirées (sans gérant exploitable)")
    print(f"{trop_gros} retirées (gérants dans {seuil} sociétés ou plus)")
    print(f"{len(finales)} sociétés écrites dans {sortie}")


if __name__ == "__main__":
    main()
        
