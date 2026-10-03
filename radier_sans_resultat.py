#!/usr/bin/env python3
"""Radie de agences.csv les agences sans résultat Google Maps.

Usage :
  python scripts/radier_sans_resultat.py                 # radie seulement les agences SANS aucune fiche
  python scripts/radier_sans_resultat.py --hors-immo     # radie aussi celles dont aucune fiche n'est immobilière
  python scripts/radier_sans_resultat.py --inplace       # remplace data/agences.csv (sauvegarde dans agences_original.csv)

Sorties (dans data/) :
  agences_filtrees.csv   les agences conservées (mêmes colonnes que agences.csv)
  agences_radiees.csv    les agences retirées, avec la colonne "motif"

Les agences en erreur (data/errors.jsonl) ou jamais traitées ne sont PAS radiées :
seules celles présentes dans places.jsonl avec un résultat vide (ou hors immo) le sont.
"""
import csv
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_places import is_immo  # même définition de "immobilier" que le contrôle

ROOT = Path(__file__).resolve().parent.parent
CSV_IN = ROOT / "data/agences.csv"
PLACES = ROOT / "data/places.jsonl"
HORS_IMMO = "--hors-immo" in sys.argv
INPLACE = "--inplace" in sys.argv


def main():
    motifs = {}  # siren -> motif
    for line in open(PLACES, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        places = r.get("places") or []
        if not places:
            motifs[r["siren"]] = "aucun résultat Google Maps"
        elif HORS_IMMO and not any(is_immo(p) for p in places):
            motifs[r["siren"]] = "aucune fiche immobilière"

    with open(CSV_IN, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames
        rows = list(rd)

    keep = [r for r in rows if r["siren"] not in motifs]
    drop = [dict(r, motif=motifs[r["siren"]]) for r in rows if r["siren"] in motifs]

    out_keep = ROOT / "data/agences_filtrees.csv"
    out_drop = ROOT / "data/agences_radiees.csv"
    for path, data, fields in ((out_keep, keep, cols), (out_drop, drop, cols + ["motif"])):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(data)

    print(f"Agences au départ : {len(rows)}")
    print(f"Radiées           : {len(drop)}")
    print(f"Conservées        : {len(keep)}")

    if INPLACE:
        shutil.copy(CSV_IN, ROOT / "data/agences_original.csv")
        shutil.copy(out_keep, CSV_IN)
        print("data/agences.csv remplacé (original sauvegardé dans data/agences_original.csv)")


if __name__ == "__main__":
    main()
