#!/usr/bin/env python3
"""Ne garde dans places.jsonl que les fiches dont le type est exactement "Agence immobilière".

Usage :
  python scripts/filtrer_fiches_immo.py             # type exact "Agence immobilière" (défaut)
  python scripts/filtrer_fiches_immo.py --large     # tous les types immobiliers (agent, syndic, promoteur, gestion...)
  python scripts/filtrer_fiches_immo.py --inplace   # (combinable) remplace places.jsonl

Par défaut écrit data/places_immo.jsonl (+ fiches retirées à part)
  python scripts/filtrer_fiches_immo.py --inplace   # remplace data/places.jsonl (original -> places_original.jsonl)

Sorties (dans data/) :
  places_immo.jsonl        1 ligne par agence, "places" ne contient que les fiches immobilières
                           ("n_places" mis à jour, "n_places_avant" = nombre avant filtrage)
  fiches_retirees.jsonl    les fiches supprimées (siren, title, types, address) pour pouvoir les relire

Une agence dont toutes les fiches sont retirées est GARDÉE avec "places": [] (rien n'est radié ici ;
utilise radier_sans_resultat.py pour retirer des agences de agences.csv).
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_places import is_immo as is_immo_large, norm, types_of

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data/places.jsonl"
DST = ROOT / "data/places_immo.jsonl"
REMOVED = ROOT / "data/fiches_retirees.jsonl"
INPLACE = "--inplace" in sys.argv
LARGE = "--large" in sys.argv
TYPES_GARDES = {"agence immobiliere"}  # comparaison sans accents ni majuscules


def is_immo(p):
    if LARGE:
        return is_immo_large(p)
    return any(norm(t).strip() in TYPES_GARDES for t in types_of(p))


def main():
    n_ag = n_before = n_after = n_empty = 0
    with open(SRC, encoding="utf-8") as fi, open(DST, "w", encoding="utf-8") as fo, \
            open(REMOVED, "w", encoding="utf-8") as fr:
        for line in fi:
            if not line.strip():
                continue
            r = json.loads(line)
            places = r.get("places") or []
            kept = [p for p in places if is_immo(p)]
            for p in places:
                if p not in kept:
                    fr.write(json.dumps({"siren": r["siren"], "title": p.get("title"),
                                         "types": types_of(p), "address": p.get("address"),
                                         "cid": p.get("cid")}, ensure_ascii=False) + "\n")
            r["n_places_avant"] = len(places)
            r["places"] = kept
            r["n_places"] = len(kept)
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
            n_ag += 1
            n_before += len(places)
            n_after += len(kept)
            n_empty += 1 if (places and not kept) else 0

    print(f"Agences                      : {n_ag}")
    print(f"Fiches avant                 : {n_before}")
    print(f"Fiches conservées (immo)     : {n_after}")
    print(f"Fiches retirées              : {n_before - n_after} ({100 * (n_before - n_after) / max(1, n_before):.1f} %)")
    print(f"Agences devenues sans fiche  : {n_empty}")

    if INPLACE:
        shutil.copy(SRC, ROOT / "data/places_original.jsonl")
        shutil.copy(DST, SRC)
        print("data/places.jsonl remplacé (original sauvegardé dans data/places_original.jsonl)")


if __name__ == "__main__":
    main()
