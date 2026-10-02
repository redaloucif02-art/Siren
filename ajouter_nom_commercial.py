"""Ajoute le nom commercial (source INSEE, via le SIRET) à agences_sans_reseau.csv.

Usage : INSEE_API_KEY=xxxx python ajouter_nom_commercial.py
        INSEE_API_KEY=xxxx python ajouter_nom_commercial.py --push   (commit régulier, pour GitHub)
        python ajouter_nom_commercial.py entree.csv sortie.csv

Clé gratuite : compte sur https://portail-api.insee.fr, créer une application
« simple », souscrire à « API Sirene », copier la clé (limite : 30 requêtes/min).

Fonctionnement :
  - interroge l'API Sirene de l'INSEE par lots de 100 SIRET ;
  - nom_commercial = dénomination usuelle de l'établissement, sinon celle de
    l'unité légale (les valeurs masquées [ND] sont ignorées) ;
  - reprise automatique : chaque résultat est écrit dans
    nom_commercial_cache.csv, un relancement saute les SIRET déjà traités.
Sortie : agences_avec_nom_commercial.csv (toutes les colonnes + nom_commercial).
"""
import csv
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

URL = "https://api.insee.fr/api-sirene/3.11/siret"
LOT = 100
PAUSE = 2.2          # 30 requêtes/minute maximum
CACHE = "nom_commercial_cache.csv"


def propre(valeur):
    valeur = (valeur or "").strip()
    return "" if valeur.startswith("[ND]") or valeur == "[ND]" else valeur


def appel(sirets, cle):
    """Une requête POST pour un lot de SIRET. Retourne la liste des établissements."""
    q = " OR ".join(f"siret:{s}" for s in sirets)
    corps = urllib.parse.urlencode({"q": q, "nombre": len(sirets)}).encode()
    for essai in range(6):
        req = urllib.request.Request(URL, data=corps, headers={
            "X-INSEE-Api-Key-Integration": cle,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r).get("etablissements") or []
        except urllib.error.HTTPError as e:
            if e.code == 404:                 # aucun résultat pour ce lot
                return []
            if e.code in (401, 403):
                sys.exit(f"Clé API refusée (HTTP {e.code}). Vérifie INSEE_API_KEY.")
            if e.code == 429 or e.code >= 500:
                time.sleep(30 * (essai + 1))  # trop de requêtes ou panne : on attend
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(10 * (essai + 1))
    raise RuntimeError("API injoignable après plusieurs essais")


def extraire(etab):
    periodes = etab.get("periodesEtablissement") or []
    actuelle = next((p for p in periodes if not p.get("dateFin")),
                    periodes[0] if periodes else {})
    nc = propre(actuelle.get("denominationUsuelleEtablissement"))
    if not nc:
        nc = propre((etab.get("uniteLegale") or {}).get("denominationUsuelle1UniteLegale"))
    return nc


def git_push(fichiers, message):
    subprocess.run(["git", "add"] + fichiers, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(["git", "commit", "-m", message], check=True)
    for essai in range(4):
        if subprocess.run(["git", "push"]).returncode == 0:
            return
        subprocess.run(["git", "pull", "--rebase"])
        time.sleep(2 * (essai + 1))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    push = "--push" in sys.argv
    entree = args[0] if len(args) > 0 else "agences_sans_reseau.csv"
    sortie = args[1] if len(args) > 1 else "agences_avec_nom_commercial.csv"
    cle = os.environ.get("INSEE_API_KEY", "")
    if not cle:
        sys.exit("Il manque la clé : INSEE_API_KEY=xxxx python ajouter_nom_commercial.py")

    with open(entree, newline="", encoding="utf-8") as f:
        lecteur = csv.DictReader(f)
        champs = [c for c in lecteur.fieldnames if c != "nom_commercial"]
        lignes = list(lecteur)

    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE, newline="", encoding="utf-8") as f:
            cache = {r["siret"]: r["nom_commercial"] for r in csv.DictReader(f)}

    a_faire = sorted({l["siret_siege"] for l in lignes
                      if l.get("siret_siege") and l["siret_siege"] not in cache})
    print(f"{len(lignes)} sociétés, {len(cache)} SIRET déjà en cache, "
          f"{len(a_faire)} à interroger ({-(-len(a_faire) // LOT)} requêtes)")

    nouveau = not os.path.exists(CACHE)
    with open(CACHE, "a", newline="", encoding="utf-8") as fc:
        wc = csv.writer(fc)
        if nouveau:
            wc.writerow(["siret", "nom_commercial"])
        for i in range(0, len(a_faire), LOT):
            lot = a_faire[i:i + LOT]
            trouves = {e.get("siret"): extraire(e) for e in appel(lot, cle)}
            for s in lot:                     # SIRET introuvable = vide, pas de nouvel essai
                cache[s] = trouves.get(s, "")
                wc.writerow([s, cache[s]])
            fc.flush()
            n = i // LOT + 1
            if n % 20 == 0:
                print(f"  {min(i + LOT, len(a_faire))}/{len(a_faire)} SIRET traités")
                if push:
                    git_push([CACHE], "Cache nom commercial")
            time.sleep(PAUSE)

    with open(sortie, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=champs + ["nom_commercial"])
        w.writeheader()
        for l in lignes:
            l["nom_commercial"] = cache.get(l.get("siret_siege", ""), "")
            w.writerow({c: l.get(c, "") for c in champs + ["nom_commercial"]})

    remplis = sum(1 for l in lignes if l["nom_commercial"])
    print(f"{remplis} sociétés avec un nom commercial sur {len(lignes)} "
          f"({remplis / len(lignes):.0%}) -> {sortie}")
    if push:
        git_push([CACHE, sortie], "Agences avec nom commercial")


if __name__ == "__main__":
    main()
