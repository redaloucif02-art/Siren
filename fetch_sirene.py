"""Récupère les agences immobilières (APE 68.31Z) d'un département
via l'API publique Recherche d'entreprises (sans clé) et écrit un CSV.

Usage : python fetch_sirene.py 75             (un département)
        python fetch_sirene.py all            (tous les départements)
        python fetch_sirene.py all --push     (commit + push après chaque département)
        python fetch_sirene.py all --force    (refait aussi les départements déjà présents)

Reprise : un département dont le CSV existe déjà est sauté (sauf --force).
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
from datetime import date

API = "https://recherche-entreprises.api.gouv.fr/search"
NAF = "68.31Z"
PER_PAGE = 25          # maximum autorisé par l'API
PAUSE = 0.25           # l'API limite à 7 appels/seconde
MAX_PAGES = 400        # l'API ne renvoie pas plus de 10 000 résultats

DEPARTEMENTS = (
    [f"{i:02d}" for i in range(1, 96) if i != 20]
    + ["2A", "2B", "971", "972", "973", "974", "976"]
)


def get(params):
    url = API + "?" + urllib.parse.urlencode(params)
    for essai in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "gh-agences/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r), url
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (essai + 1))
                continue
            raise
        except urllib.error.URLError:
            time.sleep(2 * (essai + 1))
    raise RuntimeError("API injoignable : " + url)


def gerants(entreprise):
    noms = []
    for d in entreprise.get("dirigeants") or []:
        if d.get("nom"):
            nom = f"{d.get('prenoms', '').title()} {d['nom'].title()}".strip()
        else:
            nom = d.get("denomination", "")
        if nom:
            q = d.get("qualite", "")
            noms.append(f"{nom} ({q})" if q else nom)
    return " | ".join(noms)


def run(dep):
    sortie = f"data/agences_{dep}.csv"
    if not FORCE and os.path.exists(sortie) and os.path.getsize(sortie) > 0:
        print(f"{dep} : déjà fait, ignoré ({sortie})")
        return False
    colonnes = ["siren", "siret_siege", "nom", "enseigne", "adresse",
                "code_postal", "ville", "date_creation", "gerants",
                "source", "date_collecte"]
    lignes, page = [], 1
    while page <= MAX_PAGES:
        data, url = get({"activite_principale": NAF, "departement": dep,
                         "etat_administratif": "A",
                         "per_page": PER_PAGE, "page": page})
        resultats = data.get("results") or []
        if not resultats:
            break
        for e in resultats:
            s = e.get("siege") or {}
            enseignes = s.get("liste_enseignes") or []
            lignes.append({
                "siren": e.get("siren", ""),
                "siret_siege": s.get("siret", ""),
                "nom": e.get("nom_complet", ""),
                "enseigne": " | ".join(enseignes),
                "adresse": s.get("adresse", ""),
                "code_postal": s.get("code_postal", ""),
                "ville": s.get("libelle_commune", ""),
                "date_creation": e.get("date_creation", ""),
                "gerants": gerants(e),
                "source": "https://annuaire-entreprises.data.gouv.fr/entreprise/"
                          + e.get("siren", ""),
                "date_collecte": date.today().isoformat(),
            })
        if page >= (data.get("total_pages") or 1):
            break
        page += 1
        time.sleep(PAUSE)

    if len(lignes) >= PER_PAGE * MAX_PAGES:
        print(f"ATTENTION : {dep} atteint la limite de 10 000 résultats, "
              "il faut découper par code postal ou commune.")

    os.makedirs("data", exist_ok=True)
    tmp = sortie + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=colonnes)
        w.writeheader()
        w.writerows(lignes)
    os.replace(tmp, sortie)   # écriture atomique : pas de CSV à moitié écrit
    print(f"{len(lignes)} agences écrites dans {sortie}")
    return True


def git_push(dep):
    """Commit + push du CSV du département (utilisé par le workflow)."""
    subprocess.run(["git", "add", "data/"], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(["git", "commit", "-m", f"Agences département {dep}"], check=True)
    for essai in range(4):
        if subprocess.run(["git", "push"]).returncode == 0:
            return
        subprocess.run(["git", "pull", "--rebase"])
        time.sleep(2 * (essai + 1))
    raise RuntimeError(f"push impossible pour {dep}")


FORCE = False


def main():
    global FORCE
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    FORCE = "--force" in sys.argv
    push = "--push" in sys.argv
    if not args:
        sys.exit(__doc__)
    deps = DEPARTEMENTS if args[0] == "all" else [args[0]]

    echecs = []
    for dep in deps:
        try:
            if run(dep) and push:
                git_push(dep)
        except Exception as e:   # on continue avec le département suivant
            print(f"ERREUR {dep} : {e}")
            echecs.append(dep)
    if echecs:
        print("Départements en échec (relancer pour reprendre) : " + ", ".join(echecs))
        sys.exit(1)


if __name__ == "__main__":
    main()
