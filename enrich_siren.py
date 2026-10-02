#!/usr/bin/env python3
"""
Enrichit agences_orpi_details.csv à partir du SIREN (API publique
"Recherche d'entreprises", sans clé, limite 7 req/s) :
  - ajoute la colonne "Raison sociale" juste après "Siren"
  - complète "Gérant" uniquement quand il est vide (nom du dirigeant légal ;
    si c'est une holding, on remonte à la personne physique)

Entrée  : agences_orpi_details.csv
Sortie  : agences_orpi_enrichi.csv
Reprise automatique (même "Site web" = déjà traité). Erreurs réseau non écrites
=> reprises au prochain run.
"""
import csv
import os
import re
import time

import requests

API = "https://recherche-entreprises.api.gouv.fr/search"
INPUT = "agences_century21.csv"
OUTPUT = "agences_century21_enrichi.csv"
DELAY = 0.25
HEADERS = {"User-Agent": "TheGreatestDevBot/1.0 (+https://thegreatestdev.com)"}

QUALITE_RANK = [
    r"g[ée]rant",
    r"pr[ée]sident",
    r"directeur g[ée]n[ée]ral|directrice g[ée]n[ée]rale",
    r"directeur|directrice",
    r"administrateur",
]

session = requests.Session()
session.headers.update(HEADERS)
_cache = {}


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def lookup(siren):
    """Dict entreprise, None si introuvable. Lève RuntimeError si erreur réseau."""
    if siren in _cache:
        return _cache[siren]
    last_err = None
    for attempt in range(5):
        try:
            r = session.get(API, params={"q": siren, "page": 1, "per_page": 5}, timeout=30)
            time.sleep(DELAY)
            if r.status_code == 200:
                results = r.json().get("results", [])
                match = next((x for x in results if x.get("siren") == siren), None)
                _cache[siren] = match
                return match
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 2 * (attempt + 1))))
                continue
            last_err = f"HTTP {r.status_code}"
            time.sleep(2 * (attempt + 1))
        except (requests.RequestException, ValueError) as e:
            last_err = str(e)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(last_err or "erreur inconnue")


def person_name(d):
    prenoms = clean(d.get("prenoms", ""))
    prenom = prenoms.split(",")[0].strip().title() if prenoms else ""
    return clean(f"{prenom} {clean(d.get('nom', '')).upper()}")


def rank(d):
    q = clean(d.get("qualite", "")).lower()
    for i, pat in enumerate(QUALITE_RANK):
        if re.search(pat, q):
            return i
    return 99


def sorted_dirigeants(company):
    return sorted(
        company.get("dirigeants") or [],
        key=lambda d: (d.get("type_dirigeant") != "personne physique", rank(d)),
    )


def find_person(company, depth=0):
    """Premier dirigeant personne physique, en remontant les holdings (2 niveaux)."""
    for d in sorted_dirigeants(company):
        if d.get("type_dirigeant") == "personne physique":
            return person_name(d)
        sub_siren = d.get("siren", "")
        if depth < 2 and sub_siren:
            try:
                sub = lookup(sub_siren)
            except RuntimeError:
                sub = None
            if sub:
                name = find_person(sub, depth + 1)
                if name:
                    return name
    return ""


def load_done():
    if not os.path.exists(OUTPUT):
        return set()
    with open(OUTPUT, newline="", encoding="utf-8-sig") as f:
        return {r.get("Site web", "") for r in csv.DictReader(f)}


def main():
    with open(INPUT, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        in_fields = [c for c in (reader.fieldnames or []) if c != "Raison sociale"]
        rows = list(reader)

    # "Raison sociale" juste après "Siren"
    fields = []
    for c in in_fields:
        fields.append(c)
        if c == "Siren":
            fields.append("Raison sociale")
    if "Raison sociale" not in fields:
        fields.insert(0, "Raison sociale")

    done = load_done()
    is_new = not os.path.exists(OUTPUT)
    todo = [r for r in rows if r.get("Site web") not in done]
    print(f"{len(rows)} agences, {len(done)} déjà traitées, {len(todo)} à faire")

    stats = {"raison": 0, "gerant_ajoute": 0, "sans_siren": 0, "introuvable": 0, "erreur": 0}

    with open(OUTPUT, "a", newline="", encoding="utf-8-sig") as out:
        w = csv.DictWriter(out, fieldnames=fields)
        if is_new:
            w.writeheader()

        for i, row in enumerate(todo, 1):
            siren = re.sub(r"\D", "", row.get("Siren", ""))[:9]
            raison = ""
            added = False

            if len(siren) != 9:
                stats["sans_siren"] += 1
            else:
                try:
                    company = lookup(siren)
                except RuntimeError as e:
                    stats["erreur"] += 1
                    print(f"[erreur] {siren} : {e} (repris au prochain run)")
                    continue
                if company is None:
                    stats["introuvable"] += 1
                else:
                    raison = clean(company.get("nom_raison_sociale") or company.get("nom_complet", ""))
                    if not clean(row.get("Gérant", "")):
                        name = find_person(company)
                        if name:
                            row["Gérant"] = name
                            added = True
                            src = clean(row.get("Source", ""))
                            row["Source"] = (src + " | " if src else "") + (
                                "Gérant : API Recherche d'entreprises (SIREN)"
                            )

            stats["raison"] += bool(raison)
            stats["gerant_ajoute"] += added
            w.writerow({**row, "Raison sociale": raison})
            out.flush()
            print(f"{i:>5}/{len(todo)} | {siren or '-':<9} | {raison or '-'} | "
                  f"gérant={'+' + row['Gérant'] if added else (row.get('Gérant') or '-')}")

    print(
        f"\nTerminé : raison sociale {stats['raison']} | gérants ajoutés {stats['gerant_ajoute']} | "
        f"sans SIREN {stats['sans_siren']} | introuvables {stats['introuvable']} | "
        f"erreurs réseau {stats['erreur']}"
    )


if __name__ == "__main__":
    main()
