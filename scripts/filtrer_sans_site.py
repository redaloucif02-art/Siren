#!/usr/bin/env python3
"""Retire les fiches Google qui n'ont pas de site web.

Usage :
  python scripts/filtrer_sans_site.py                    # part de data/places_immo.jsonl (sinon places.jsonl)
  python scripts/filtrer_sans_site.py --source data/places.jsonl
  python scripts/filtrer_sans_site.py --inplace          # remplace le fichier source (copie *_original.jsonl)

Sorties (dans data/) :
  places_site.jsonl         1 ligne par agence, "places" ne contient que les fiches AVEC un site web
                            ("n_places" mis à jour, "n_places_avant_site" = nombre avant ce filtre)
  fiches_sans_site.jsonl    les fiches retirées (siren, title, address, phoneNumber, cid)

Une agence dont toutes les fiches sont retirées est gardée avec "places": [] (rien n'est supprimé du CSV).
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DST = DATA / "places_site.jsonl"
REMOVED = DATA / "fiches_sans_site.jsonl"
INPLACE = "--inplace" in sys.argv


def pick_source():
    if "--source" in sys.argv:
        return ROOT / sys.argv[sys.argv.index("--source") + 1]
    immo = DATA / "places_immo.jsonl"
    return immo if immo.exists() else DATA / "places.jsonl"


def has_site(p):
    return bool(str(p.get("website") or "").strip())


def main():
    src = pick_source()
    print(f"Source : {src.name}")
    n_ag = n_before = n_after = n_empty = 0
    with open(src, encoding="utf-8") as fi, open(DST, "w", encoding="utf-8") as fo, \
            open(REMOVED, "w", encoding="utf-8") as fr:
        for line in fi:
            if not line.strip():
                continue
            r = json.loads(line)
            places = r.get("places") or []
            kept = [p for p in places if has_site(p)]
            for p in places:
                if not has_site(p):
                    fr.write(json.dumps({"siren": r["siren"], "title": p.get("title"),
                                         "address": p.get("address"),
                                         "phoneNumber": p.get("phoneNumber"),
                                         "cid": p.get("cid")}, ensure_ascii=False) + "\n")
            r["n_places_avant_site"] = len(places)
            r["places"] = kept
            r["n_places"] = len(kept)
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
            n_ag += 1
            n_before += len(places)
            n_after += len(kept)
            n_empty += 1 if (places and not kept) else 0

    print(f"Agences                      : {n_ag}")
    print(f"Fiches avant                 : {n_before}")
    print(f"Fiches avec site web         : {n_after}")
    print(f"Fiches retirées (sans site)  : {n_before - n_after} ({100 * (n_before - n_after) / max(1, n_before):.1f} %)")
    print(f"Agences devenues sans fiche  : {n_empty}")

    if INPLACE:
        shutil.copy(src, src.with_name(src.stem + "_original.jsonl"))
        shutil.copy(DST, src)
        print(f"{src.name} remplacé (original sauvegardé dans {src.stem}_original.jsonl)")


if __name__ == "__main__":
    main()
