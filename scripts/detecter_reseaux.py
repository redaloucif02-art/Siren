#!/usr/bin/env python3
"""Retire les fiches qui appartiennent à un réseau (liste dans data/reseaux.txt).

Usage :
  python scripts/detecter_reseaux.py                       # source : data/places_site.jsonl
  python scripts/detecter_reseaux.py --source data/places_immo.jsonl
  python scripts/detecter_reseaux.py --inplace             # remplace le fichier source (copie *_original.jsonl)

Une fiche est considérée "réseau" si un nom de la liste apparaît dans :
  - son titre   (mots entiers, avec ou sans espaces : "Re/max" trouve "REMAX Dijon" et "RE/MAX Lyon")
  - le domaine de son site web (ex. remax-dijon.fr, www.orpi.com)

Sorties (dans data/) :
  places_independants.jsonl   mêmes lignes que la source, sans les fiches réseau
                              ("n_places" mis à jour, "n_places_avant_reseau" = nombre avant ce filtre)
  fiches_reseau.csv           les fiches RETIRÉES (siren, nom_complet, title, website, reseau, match)
                              -> à relire pour repérer d'éventuels faux positifs (noms courts : Era, Axo, Lamy...)

Une agence dont toutes les fiches sont retirées est gardée avec "places": [] (rien n'est supprimé du CSV).
"""
import csv
import json
import re
import shutil
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INPLACE = "--inplace" in sys.argv
SRC = ROOT / sys.argv[sys.argv.index("--source") + 1] if "--source" in sys.argv else DATA / "places_site.jsonl"
DST = DATA / "places_independants.jsonl"


def norm(s):
    s = unicodedata.normalize("NFKD", str(s).lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def load_names():
    seen, out = set(), []
    for line in open(DATA / "reseaux.txt", encoding="utf-8"):
        raw = line.strip()
        n = norm(raw)
        if n and n not in seen:
            seen.add(n)
            out.append((raw, n, n.replace(" ", "")))
    return out


def domain_label(url):
    host = urlparse(url if "//" in url else "//" + url).netloc.lower().split(":")[0]
    host = re.sub(r"^www\d?\.", "", host)
    parts = host.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else host  # sans l'extension finale


def find_network(title, website, names):
    t = " " + norm(title) + " "
    t_tokens = set(norm(title).split())
    d = norm(domain_label(website)) if website else ""
    d_padded = " " + d + " "
    d_tokens = set(d.split())
    d_squash = d.replace(" ", "")
    for raw, n, sq in names:
        if f" {n} " in t or sq in t_tokens:
            return raw, "titre"
        if d and (f" {n} " in d_padded or sq in d_tokens or d_squash == sq or (len(sq) >= 7 and sq in d_squash)):
            return raw, "site"
    return None, None


def main():
    names = load_names()
    print(f"Source : {SRC.name} | {len(names)} réseaux (doublons ignorés)")
    n_ag = n_before = n_after = n_empty = 0
    counts, ex, removed = Counter(), {}, []
    with open(SRC, encoding="utf-8") as fi, open(DST, "w", encoding="utf-8") as fo:
        for line in fi:
            if not line.strip():
                continue
            r = json.loads(line)
            places = r.get("places") or []
            kept = []
            for p in places:
                name, how = find_network(p.get("title", ""), p.get("website", ""), names)
                if name:
                    counts[name] += 1
                    ex.setdefault(name, p.get("title"))
                    removed.append([r["siren"], r.get("source", {}).get("nom_complet"), p.get("title"),
                                    p.get("website"), name, how])
                else:
                    kept.append(p)
            r["n_places_avant_reseau"] = len(places)
            r["places"] = kept
            r["n_places"] = len(kept)
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
            n_ag += 1
            n_before += len(places)
            n_after += len(kept)
            n_empty += 1 if (places and not kept) else 0

    with open(DATA / "fiches_reseau.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["siren", "nom_complet", "title", "website", "reseau", "match"])
        w.writerows(removed)

    print(f"Agences                       : {n_ag}")
    print(f"Fiches avant                  : {n_before}")
    print(f"Fiches réseau retirées        : {len(removed)} ({100 * len(removed) / max(1, n_before):.1f} %)")
    print(f"Fiches indépendantes gardées  : {n_after}")
    print(f"Agences devenues sans fiche   : {n_empty}")
    print("\nRéseaux retirés (avec un exemple de titre, à vérifier) :")
    for name, c in counts.most_common(40):
        print(f"  {c:6d}  {name:28s} ex. {ex[name]}")

    if INPLACE:
        shutil.copy(SRC, SRC.with_name(SRC.stem + "_original.jsonl"))
        shutil.copy(DST, SRC)
        print(f"\n{SRC.name} remplacé (original sauvegardé dans {SRC.stem}_original.jsonl)")


if __name__ == "__main__":
    main()
