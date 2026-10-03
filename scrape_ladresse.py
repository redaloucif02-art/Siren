#!/usr/bin/env python3
"""
Scrape les agences L'Adresse (ladresse.com) et produit agences_ladresse.csv.

Étapes :
  1. ladresse.com/trouver-votre-agence ne montre que les dernières ouvertures : le script y lit les liens
     des 13 régions (/recherche/agence/<region>), qui listent TOUTES les agences. Pour chaque agence :
     nom, adresse, téléphone, lien de la page agence (/agence/<slug>/<id>).
  2. Page agence -> bloc "Mentions légales" (Représentant légal, Numéro RCS = Siren) et, en repli,
     le directeur/gérant de l'équipe. L'onglet #contact est sur la même page : une seule requête par agence.

Colonnes (mêmes que les autres scrapers, pour pouvoir fusionner les CSV) :
  Siren, Raison sociale, Nom, Services, Adresse, Téléphone, Email agence,
  Site web, Gérant, Tel gérant, Email gérant, Source

Siren = numéro RCS (9 chiffres) ; repli sur le numéro de TVA si le RCS manque.
Gérant = "Représentant légal" ; repli sur Directeur/Directrice/Gérant(e) de l'équipe.
Laissées vides : Raison sociale, Services, Email agence, Tel gérant, Email gérant (non publiés).
"Site web" = lien de la page agence sur ladresse.com (unique, sert de clé de reprise).
"Source"   = colonnes renseignées, puis le lien de la page agence.

Reprise automatique : les agences déjà dans le CSV (même "Site web") sont sautées.
Variable d'env. LIMIT=N : ne traite que N agences (pour tester).

Usage :
    pip install requests beautifulsoup4
    LIMIT=5 python scrape_ladresse.py
"""
import csv
import os
import re
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BASE = "https://www.ladresse.com"
INDEX = f"{BASE}/trouver-votre-agence"
OUTPUT = "agences_ladresse.csv"
DELAY = 1.0  # secondes entre deux requêtes
LIMIT = int(os.environ.get("LIMIT") or 0)
MAX_LIST_PAGES = 60
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TheGreatestDevBot/1.0; +https://thegreatestdev.com)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

FIELDS = [
    "Siren",
    "Raison sociale",
    "Nom",
    "Services",
    "Adresse",
    "Téléphone",
    "Email agence",
    "Site web",
    "Gérant",
    "Tel gérant",
    "Email gérant",
    "Source",
]

AGENCE_HREF = re.compile(r"^/agence/([^/?#]+/\d+)")  # /agence/l-adresse-vinon-sur-verdon/434
REGION_HREF = re.compile(r"^/recherche/agence/([a-z\-]+)/?$")  # pas de chiffres : exclut les pages ville
PHONE_LINE = re.compile(r"^(?:\+33|0)[\d\s.]{8,}$")
ROLE_PATTERNS = [
    r"\bdirecteur\b|\bdirectrice\b",
    r"\bg[ée]rant(?:e)?\b",
    r"\bdirigeant(?:e)?\b",
    r"responsable (?:d|de l).agence",
]
COMPANY_WORDS = re.compile(r"\b(sarl|sas|sasu|eurl|sci|sa|holding|groupe|immobilier|agence|capital)\b", re.I)

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


def normalize_phone(s: str) -> str:
    digits = re.sub(r"\D", "", s or "")
    if digits.startswith("0033"):
        digits = "0" + digits[4:]
    elif digits.startswith("33") and len(digits) == 11:
        digits = "0" + digits[2:]
    if len(digits) == 10:
        return " ".join(digits[i : i + 2] for i in range(0, 10, 2))
    return clean(s)


# ---------------------------------------------------- Étape 1 : listes ----
def agency_path(href: str):
    path = urlparse(href).path
    m = AGENCE_HREF.match(path)
    return f"{BASE}/agence/{m.group(1)}" if m else None


def card_for(a):
    """Remonte du lien 'Voir l'agence' jusqu'à la plus grande carte qui ne contient qu'une seule agence."""
    node = a
    for _ in range(8):
        parent = node.parent
        if parent is None or parent.name in ("body", "html"):
            break
        pages = {agency_path(x["href"]) for x in parent.find_all("a", href=True)} - {None}
        if len(pages) > 1:
            break
        node = parent
    return node


def parse_card(card):
    lines = [clean(t) for t in card.get_text("\n").split("\n")]
    lines = [l for l in lines if l and not l.lower().startswith("warning")]
    name = lines[0] if lines else ""
    stop = next(
        (j for j, l in enumerate(lines) if j > 0 and (PHONE_LINE.match(l) or re.match(r"bar[èe]me|voir l.agence|contacter", l, re.I))),
        len(lines),
    )
    address = ", ".join(l.rstrip(",") for l in lines[1:stop] if l.strip(", "))
    tel = ""
    for x in card.find_all("a", href=True):
        if x["href"].lower().startswith("tel:"):
            tel = normalize_phone(x["href"][4:])
            break
    return name, address, tel


def parse_list_page(soup, page_url):
    found = {}
    for a in soup.find_all("a", href=True):
        url = agency_path(urljoin(page_url, a["href"]))
        if not url or url in found:
            continue
        name, address, tel = parse_card(card_for(a))
        found[url] = {"page": url, "name": name, "address": address, "tel": tel}
    return found


def collect_agencies():
    queue, seen_pages, agencies = [INDEX], set(), {}
    while queue and len(seen_pages) < MAX_LIST_PAGES:
        url = queue.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        html = fetch(url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        found = parse_list_page(soup, url)
        new = [u for u in found if u not in agencies]
        for u in new:
            agencies[u] = found[u]
        for a in soup.find_all("a", href=True):  # régions (+ pagination éventuelle)
            href = urljoin(url, a["href"]).split("#")[0]
            path = urlparse(href).path
            if (REGION_HREF.match(path) or (path.startswith("/recherche/agence/") and "page=" in href)) and href not in seen_pages:
                if REGION_HREF.match(path) or "page=" in href:
                    queue.append(href)
        print(f"  {url} : +{len(new)} agences")
    if not agencies:
        raise SystemExit(f"Aucune agence trouvée sur {INDEX} (page inaccessible ou bloquée par robots.txt).")
    print(f"{len(agencies)} agences uniques trouvées")
    return list(agencies.values())


# ------------------------------------------------ Étape 2 : page agence ----
def flip_name(s: str) -> str:
    """'TELLISSI Paul' -> 'Paul TELLISSI' ; un nom déjà 'Prénom NOM' reste tel quel."""
    tokens = clean(s).split(" ")
    if len(tokens) >= 2 and tokens[0].isupper() and not tokens[-1].isupper():
        k = 0
        while k < len(tokens) and tokens[k].isupper():
            k += 1
        return " ".join(tokens[k:] + tokens[:k])
    return clean(s)


def looks_like_name(s: str) -> bool:
    s = clean(s)
    if not s or len(s) > 50 or re.search(r"\d|@", s) or COMPANY_WORDS.search(s):
        return False
    tokens = s.split(" ")
    if not 2 <= len(tokens) <= 4:
        return False
    return all(re.match(r"^[A-ZÀ-ÖØ-Þ][\w'’\-À-ÿ]*\.?$", t) for t in tokens)


def role_rank(role: str):
    r = role.lower().replace("’", "'")
    if "adjoint" in r:
        return None
    for i, pat in enumerate(ROLE_PATTERNS):
        if re.search(pat, r):
            return i
    return None


def legal_value(lines, label_regex):
    """Valeur d'un champ des mentions légales : 'Libellé :' puis valeur sur la ligne suivante (ou en ligne)."""
    pat = re.compile(rf"^(?:{label_regex})\s*:?\s*(.*)$", re.I)
    for i, l in enumerate(lines):
        m = pat.match(l)
        if m:
            inline = clean(m.group(1))
            if inline:
                return inline
            if i + 1 < len(lines) and not lines[i + 1].rstrip().endswith(":"):
                return clean(lines[i + 1])
    return ""


def extract_siren(rcs: str, tva: str) -> str:
    digits = re.sub(r"\D", "", rcs or "")
    if len(digits) == 9:
        return digits
    m = re.findall(r"\b(\d{3}[\s.]?\d{3}[\s.]?\d{3})\b", rcs or "")
    if m:
        return re.sub(r"\D", "", m[-1])
    tva_digits = re.sub(r"\D", "", tva or "")
    return tva_digits[-9:] if len(tva_digits) >= 9 else ""


def team_director(lines) -> str:
    """Équipe ('Notre équipe vous accueille') : paires Nom / Fonction. Repli si pas de représentant légal."""
    try:
        start = next(i for i, l in enumerate(lines) if re.search(r"notre [ée]quipe", l, re.I))
    except StopIteration:
        return ""
    end = next((i for i in range(start, len(lines)) if re.match(r"nous rejoindre", lines[i], re.I)), len(lines))
    seg = lines[start:end]
    best, best_rank = "", None
    for i in range(len(seg) - 1):
        rank = role_rank(seg[i + 1])
        if rank is not None and looks_like_name(seg[i]) and (best_rank is None or rank < best_rank):
            best, best_rank = flip_name(seg[i]), rank
    return best


def parse_agence(soup):
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    lines = [clean(t) for t in soup.get_text("\n").split("\n")]
    lines = [l for l in lines if l]

    rcs = legal_value(lines, r"num[ée]ro rcs|rcs")
    tva = legal_value(lines, r"num[ée]ro de tva")
    rep = legal_value(lines, r"repr[ée]sentant l[ée]gal")
    gerant = flip_name(rep) if looks_like_name(rep) else team_director(lines)
    return {
        "siren": extract_siren(rcs, tva),
        "gerant": gerant,
        "tel_transaction": normalize_phone(legal_value(lines, r"transaction")),
    }


# ----------------------------------------------------------------- Main ----
def load_done():
    if not os.path.exists(OUTPUT):
        return set()
    with open(OUTPUT, newline="", encoding="utf-8-sig") as f:
        return {r.get("Site web", "") for r in csv.DictReader(f)}


def main():
    agencies = collect_agencies()
    done = load_done()
    is_new = not os.path.exists(OUTPUT)

    todo = [ag for ag in agencies if ag["page"] not in done]
    if LIMIT:
        todo = todo[:LIMIT]
    print(f"{len(agencies)} agences, {len(done)} déjà traitées, {len(todo)} à faire")

    stats = {"ok": 0, "echec": 0, "siren": 0, "tel": 0, "gerant": 0}

    with open(OUTPUT, "a", newline="", encoding="utf-8-sig") as out:
        w = csv.DictWriter(out, fieldnames=FIELDS)
        if is_new:
            w.writeheader()

        for i, ag in enumerate(todo, 1):
            url = ag["page"]
            html = fetch(url)
            if html is None:  # échec temporaire : repris au prochain run
                stats["echec"] += 1
                print(f"[échec] {url}")
                continue
            d = parse_agence(BeautifulSoup(html, "html.parser")) if html else {"siren": "", "gerant": "", "tel_transaction": ""}

            tel = ag["tel"] or d["tel_transaction"]
            row = {
                "Siren": d["siren"],
                "Raison sociale": "",
                "Nom": ag["name"],
                "Services": "",
                "Adresse": ag["address"],
                "Téléphone": tel,
                "Email agence": "",
                "Site web": url,
                "Gérant": d["gerant"],
                "Tel gérant": "",
                "Email gérant": "",
            }
            filled = [c for c in ("Nom", "Adresse", "Téléphone", "Siren", "Gérant", "Site web") if row[c]]
            row["Source"] = f"{', '.join(filled)} : {url}"
            w.writerow(row)
            out.flush()

            stats["ok"] += 1
            stats["siren"] += bool(d["siren"])
            stats["tel"] += bool(tel)
            stats["gerant"] += bool(d["gerant"])
            print(f"{i:>4}/{len(todo)} | {ag['name']} | siren={d['siren'] or '-'} | tel={tel or '-'} | gérant={d['gerant'] or '-'}")

    print(f"\nTerminé : {stats['ok']} agences traitées, {stats['echec']} échecs (relancer pour les reprendre).")
    print(f"Siren : {stats['siren']} | Tél : {stats['tel']} | Gérant : {stats['gerant']}")


if __name__ == "__main__":
    main()
