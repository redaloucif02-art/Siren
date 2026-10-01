"""Compte les agences dans les CSV de data/.

Usage : python compter_agences.py            (dossier data/ par défaut)
        python compter_agences.py mon_dossier
Affiche : agences par département, total, doublons de SIREN,
départements manquants, et part des agences avec un gérant renseigné.
"""
import csv
import glob
import os
import sys

DEPARTEMENTS = (
    [f"{i:02d}" for i in range(1, 96) if i != 20]
    + ["2A", "2B", "971", "972", "973", "974", "976"]
)


def main():
    dossier = sys.argv[1] if len(sys.argv) > 1 else "data"
    fichiers = sorted(glob.glob(os.path.join(dossier, "agences_*.csv")))
    if not fichiers:
        sys.exit(f"Aucun fichier agences_*.csv dans {dossier}/")

    par_dep, sirens = {}, set()
    total = doublons = avec_gerant = 0
    for chemin in fichiers:
        dep = os.path.basename(chemin)[len("agences_"):-len(".csv")]
        n = 0
        with open(chemin, newline="", encoding="utf-8") as f:
            for ligne in csv.DictReader(f):
                n += 1
                s = ligne.get("siren", "")
                if s in sirens:
                    doublons += 1
                sirens.add(s)
                if (ligne.get("gerants") or "").strip():
                    avec_gerant += 1
        par_dep[dep] = n
        total += n

    for dep in sorted(par_dep):
        print(f"{dep:>4} : {par_dep[dep]:>6}")
    print("-" * 14)
    print(f"Départements faits : {len(par_dep)} / {len(DEPARTEMENTS)}")
    print(f"Total agences      : {total}")
    print(f"SIREN uniques      : {len(sirens)}  (doublons : {doublons})")
    print(f"Avec gérant        : {avec_gerant} ({avec_gerant / total:.0%})")
    manquants = [d for d in DEPARTEMENTS if d not in par_dep]
    if manquants:
        print("Manquants          : " + ", ".join(manquants))


if __name__ == "__main__":
    main()
