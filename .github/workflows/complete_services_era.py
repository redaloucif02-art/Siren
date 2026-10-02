#!/usr/bin/env python3
"""
Complète la colonne "Services" de agences_era_ansi.csv (agences ERA Immobilier).

Pour chaque ligne dont "Services" est vide, le script ouvre la page agence (colonne "Site web"),
lit le bloc "Services" (ex. Neuf, Gestion Locative, Transaction, Location...) et l'écrit dans le CSV.
Les autres colonnes ne sont pas touchées, sauf "Source" où "Services" est ajouté à la liste des colonnes.

- Encodage (UTF-8 ou ANSI/cp1252) et séparateur (; ou ,) du fichier sont conservés.
- Reprise automatique : les lignes dont "Services" est déjà rempli sont sautées.
- Sauvegarde du CSV toutes les 10 agences (et à la fin), donc rien n'est perdu si le job s'arrête.
- Variable d'env. LIMIT=N : ne traite que N agences (pour tester). CSV_PATH pour changer le fichier.

Usage :
    pip install requests beautifulsoup4
    LIMIT=5 python complete_services_era.py
"""
import csv
import io
import os
import re
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

CSV_PATH = os.environ.get("CSV_PATH", "agences_era_ansi.csv")
DELAY = 1.0  # secondes entre deux requêtes
LIMIT = int(os.environ.get("LIMIT") or 0)
SAVE_EVERY = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TheGreatestDevBot/1.0; +https://thegreatestdev.com)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

session = requests.Session()
session.headers.update(HEADERS)


# ------------------------------------------------------------- Réseau ----
_robots = {}


def allowed(url: str) -> bool:
    p = urlparse(url)
    key = f"{p.scheme}://{p.netloc}"
    if key not in _robots:
        rp = None
        try:
            r = session.get(key + "/robots.txt", timeout=10)
            if r.status_code == 200 and "html" not in r.headers.get("Content-Type", "").lower():
                rp = RobotFileParser()
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp = None
        _robots[key] = rp
    rp = _robots[key]
    return True if rp is None else rp.can_fetch(HEADERS["User-Agent"], url)


def fetch(url: str, retries: int = 3):
    """HTML (str) si OK ; "" si page absente/interdite (définitif) ; None si échec temporaire."""
    if not allowed(url):
        print(f"[robots] {url}")
        return ""
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=30)
            time.sleep(DELAY)
            if r.status_code == 200:
                return r.text
            if r.status_code in (404, 410):
                return ""
            time.sleep(2 * (attempt + 1))
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
    return None


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ------------------------------------------------------------- Parsing ----
def parse_services(soup) -> str:
    """Bloc 'Services' de la page agence : une carte par service."""
    h2 = soup.find(lambda t: t.name in ("h2", "h3") and clean(t.get_text()).lower() == "services")
    if h2 is None:
        return ""
    box, cards = h2.parent, []
    for _ in range(4):  # on remonte jusqu'au conteneur qui contient les cartes
        cards = box.select(".service-card") if box is not None else []
        if cards:
            break
        box = box.parent if box is not None else None
    names = []
    for c in cards:
        p = c.find("p")
        name = clean(p.get_text(" ") if p else c.get_text(" "))
        if name and name not in names:
            names.append(name)
    return ", ".join(names)


def add_services_to_source(src: str) -> str:
    """'Nom, Adresse, ... : url' -> 'Nom, Services, Adresse, ... : url'."""
    head, sep, tail = (src or "").partition(" : ")
    if not sep:
        return src
    cols = [c.strip() for c in head.split(",") if c.strip()]
    if "Services" in cols:
        return src
    cols.insert(cols.index("Nom") + 1 if "Nom" in cols else 0, "Services")
    return f"{', '.join(cols)}{sep}{tail}"


# ----------------------------------------------------------------- CSV ----
def read_csv(path):
    raw = open(path, "rb").read()
    try:
        text, enc = raw.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        text, enc = raw.decode("cp1252"), "cp1252"
    first = text.split("\n", 1)[0]
    delim = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delim)
    return list(reader), reader.fieldnames, enc, delim


def write_csv(path, rows, fields, enc, delim):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding=enc, errors="replace") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=delim)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


# ----------------------------------------------------------------- Main ----
def main():
    rows, fields, enc, delim = read_csv(CSV_PATH)
    if "Services" not in fields or "Site web" not in fields:
        raise SystemExit(f"Colonnes 'Services' / 'Site web' introuvables dans {CSV_PATH} : {fields}")

    todo = [r for r in rows if not (r.get("Services") or "").strip() and (r.get("Site web") or "").startswith("http")]
    if LIMIT:
        todo = todo[:LIMIT]
    print(f"{len(rows)} agences, {len(todo)} à compléter (encodage {enc}, séparateur '{delim}')")

    stats = {"ok": 0, "vide": 0, "echec": 0}
    for i, r in enumerate(todo, 1):
        url = r["Site web"].strip()
        html = fetch(url)
        if html is None:  # échec temporaire : repris au prochain run
            stats["echec"] += 1
            print(f"[échec] {url}")
            continue
        services = parse_services(BeautifulSoup(html, "html.parser")) if html else ""
        if services:
            r["Services"] = services
            r["Source"] = add_services_to_source(r.get("Source", ""))
            stats["ok"] += 1
        else:
            stats["vide"] += 1
        print(f"{i:>4}/{len(todo)} | {r.get('Nom', '')} | {services or '-'}")
        if i % SAVE_EVERY == 0:
            write_csv(CSV_PATH, rows, fields, enc, delim)

    write_csv(CSV_PATH, rows, fields, enc, delim)
    print(f"\nTerminé : {stats['ok']} complétées, {stats['vide']} sans bloc Services, {stats['echec']} échecs (relancer pour les reprendre).")


if __name__ == "__main__":
    main()
