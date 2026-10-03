#!/usr/bin/env python3
"""Repère les réseaux/franchises probables qui NE SONT PAS dans data/reseaux.txt. Ne supprime rien.

Usage :
  python scripts/voir_reseaux_inconnus.py                      # source : data/places_independants.jsonl (sinon places_site.jsonl)
  python scripts/voir_reseaux_inconnus.py --source data/places_site.jsonl
  python scripts/voir_reseaux_inconnus.py --min-agences 4 --min-dep 3

Principe : un réseau laisse deux traces répétées chez des agences DIFFÉRENTES (SIREN distincts) et
dans des départements DIFFÉRENTS (un nom de ville, lui, reste dans un seul département) :
  - le même domaine de site web   (ex. nouveau-reseau.fr, ou xxx.nouveau-reseau.fr)
  - le même nom dans le titre     (hors mots génériques : agence, immobilier, cabinet, ville...)

Sortie : data/reseaux_candidats.csv
  type, candidat, nb_agences, nb_departements, exemples_titres, exemples_sites
et le top 50 affiché. À toi de décider quoi ajouter à data/reseaux.txt.
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detecter_reseaux import find_network, load_names, norm  # même logique que le retrait

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def arg(name, default):
    return type(default)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


MIN_AG = arg("--min-agences", 4)
MIN_DEP = arg("--min-dep", 3)
if "--source" in sys.argv:
    SRC = ROOT / sys.argv[sys.argv.index("--source") + 1]
else:
    SRC = DATA / "places_independants.jsonl"
    if not SRC.exists():
        SRC = DATA / "places_site.jsonl"

STOP = set("""agence agences immobilier immobiliere immobilieres immo cabinet de du des la le les et l d en au aux sur
sous sarl sas sasu eurl sa groupe group transaction transactions gestion location locations syndic conseil conseils
france francaise expert experts services service habitat patrimoine biens bien maison maisons home house
real estate the of and a pour par vos votre notre mon ma mes nos immobiliers locatif agent mandataire""".split())

# Sites qui ne sont pas des réseaux (plateformes, annuaires, hébergeurs, réseaux sociaux...)
PLATEFORMES = set("""facebook.com instagram.com linkedin.com twitter.com x.com youtube.com google.com google.fr
business.site wixsite.com wix.com site123.me jimdofree.com jimdosite.com webador.com over-blog.com blogspot.com
wordpress.com weebly.com godaddysites.com sites.google.com leboncoin.fr seloger.com bienici.com logic-immo.com
pagesjaunes.fr yelp.fr yelp.com tripadvisor.fr societe.com pappers.fr infogreffe.fr linktr.ee""".split())


def departement(source):
    cp = str(source.get("code_postal") or "").strip()
    return cp[:3] if cp.startswith("97") else cp[:2]


def registered_domain(url):
    host = urlparse(url if "//" in url else "//" + url).netloc.lower().split(":")[0]
    host = re.sub(r"^www\d?\.", "", host)
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def ngrams(title):
    toks = [t for t in norm(title).split() if t not in STOP and len(t) >= 3 and not t.isdigit()]
    out = set(toks)
    out |= {f"{a} {b}" for a, b in zip(toks, toks[1:])}
    return out


def main():
    known = load_names()
    print(f"Source : {SRC.name} | seuils : {MIN_AG} agences distinctes, {MIN_DEP} départements")
    dom = defaultdict(lambda: {"sirens": set(), "deps": set(), "titles": [], "sites": []})
    tit = defaultdict(lambda: {"sirens": set(), "deps": set(), "titles": [], "sites": []})

    with open(SRC, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            dep = departement(r.get("source", {}))
            for p in r.get("places") or []:
                title, site = p.get("title", ""), p.get("website", "")
                if site:
                    d = registered_domain(site)
                    if d and d not in PLATEFORMES:
                        g = dom[d]
                        g["sirens"].add(r["siren"]); g["deps"].add(dep)
                        if len(g["titles"]) < 3: g["titles"].append(title)
                        if len(g["sites"]) < 2: g["sites"].append(site)
                for ng in ngrams(title):
                    g = tit[ng]
                    g["sirens"].add(r["siren"]); g["deps"].add(dep)
                    if len(g["titles"]) < 3: g["titles"].append(title)
                    if site and len(g["sites"]) < 2: g["sites"].append(site)

    rows = []
    for kind, groups in (("domaine", dom), ("titre", tit)):
        for cand, g in groups.items():
            if len(g["sirens"]) < MIN_AG or len(g["deps"]) < MIN_DEP:
                continue
            probe_title = cand if kind == "titre" else cand.rsplit(".", 1)[0]
            probe_site = cand if kind == "domaine" else ""
            if find_network(probe_title, probe_site, known)[0]:
                continue  # déjà dans ta liste
            rows.append([kind, cand, len(g["sirens"]), len(g["deps"]),
                         " | ".join(g["titles"]), " | ".join(g["sites"])])

    # un n-gramme court inclus dans un candidat plus long de même fréquence = doublon : on garde les deux, tri par taille
    rows.sort(key=lambda x: (-x[2], -x[3]))
    with open(DATA / "reseaux_candidats.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["type", "candidat", "nb_agences", "nb_departements", "exemples_titres", "exemples_sites"])
        w.writerows(rows)

    print(f"{len(rows)} candidats écrits dans data/reseaux_candidats.csv\n")
    print(f"{'type':8s} {'agences':>7s} {'dép.':>5s}  candidat  (exemples)")
    for kind, cand, na, nd, titles, _ in rows[:50]:
        print(f"{kind:8s} {na:7d} {nd:5d}  {cand}   ex. {titles.split(' | ')[0]}")


if __name__ == "__main__":
    main()
