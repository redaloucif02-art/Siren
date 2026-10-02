#!/usr/bin/env python3
"""
Scrape les agences Nestenn et produit agences_nestenn.csv.

Étapes :
  1. nestenn.com/liste-agences (+ ?page=N) -> pour chaque agence : lien de la page agence,
     nom, adresse, téléphone, email (lien "contact-autre?mail=...")
  2. Page agence (nestenn.com/liste-agences/agence-immobiliere-<ville>-<id>) -> directeur
     (le nom complet est dans l'attribut alt de la photo : "Photo de Prénom Nom")

Colonnes (mêmes que les scrapers Century 21 / 123webimmo / ERA, pour pouvoir fusionner les CSV) :
  Siren, Raison sociale, Nom, Services, Adresse, Téléphone, Email agence,
  Site web, Gérant, Tel gérant, Email gérant, Source

Laissées vides : Siren, Raison sociale, Services, Tel gérant, Email gérant (non publiés sur ces pages).
"Site web" = lien de la page agence sur nestenn.com (unique par agence, sert de clé de reprise).
"Source"   = colonnes renseignées, puis le lien de la page agence.

France uniquement : les agences à l'international (Bangkok, Miami, Maurice, Portugal...) sont exclues
(code postal français valide + pas de pays étranger dans l'adresse). Outre-mer (97x, 98x) inclus ;
FRANCE_METRO_ONLY=1 pour ne garder que la métropole et la Corse.

Reprise automatique : les agences déjà dans le CSV (même "Site web") sont sautées.
Variable d'env. LIMIT=N : ne traite que N agences (pour tester).

Usage :
    pip install requests beautifulsoup4
    LIMIT=5 python scrape_nestenn.py
"""
import csv
import os
import re
import time
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BASE = "https://nestenn.com"
INDEX = f"{BASE}/liste-agences"
OUTPUT = "agences_nestenn.csv"
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

AGENCE_HREF = re.compile(r"/liste-agences/(agence-immobiliere-[^/?#]+)")
# Titres considérés comme "gérant", par ordre de priorité ("Responsable transaction" n'en est pas un,
# les "adjoint(e)" sont exclus).
ROLE_PATTERNS = [
    r"\bdirecteur\b|\bdirectrice\b",
    r"\bg[ée]rant(?:e)?\b",
    r"\bdirigeant(?:e)?\b",
    r"responsable (?:d|de l).agence",
]

# Filtre France : code postal valide (métropole + Corse, DOM 971-976, COM 984-989) et pas de pays étranger.
FR_POSTAL_ALL = re.compile(r"\b(?:0[1-9]\d{3}|[1-8]\d{4}|9[0-5]\d{3}|97[1-6]\d{2}|98[4-9]\d{2})\b")
FR_POSTAL_METRO = re.compile(r"\b(?:0[1-9]\d{3}|[1-8]\d{4}|9[0-5]\d{3})\b")
NON_FR_WORDS = re.compile(
    r"thailand|tha[iï]lande|bangkok|\busa\b|united states|[ée]tats-unis|miami|florida|floride|"
    r"maurice|mauritius|portugal|lisbonne|lisboa|maroc|espagne|spain|suisse|belgique|luxembourg",
    re.I,
)
NON_FR_HOSTS = ("miami", "ile-maurice", "bangkok", "nestenn.pt")
FRANCE_METRO_ONLY = os.environ.get("FRANCE_METRO_ONLY") == "1"

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
    if len(digits) == 10 and digits.startswith("0"):
        return " ".join(digits[i : i + 2] for i in range(0, 10, 2))
    return clean(s)


# ---------------------------------------------------- Étape 1 : liste ----
def card_for(a):
    """Remonte du lien agence jusqu'à la plus grande carte qui ne contient qu'une seule agence."""
    node = a
    for _ in range(8):
        parent = node.parent
        if parent is None or parent.name in ("body", "html"):
            break
        slugs = {AGENCE_HREF.search(x["href"]).group(1) for x in parent.find_all("a", href=AGENCE_HREF)}
        if len(slugs) > 1:
            break
        node = parent
    return node


def parse_card(card):
    title = card.find(["h2", "h3"])
    name = clean(title.get_text(" ")) if title is not None else ""

    lines = [clean(t) for t in card.get_text("\n").split("\n")]
    lines = [l for l in lines if l]
    start = lines.index(name) + 1 if name in lines else 0
    stop = next((j for j, l in enumerate(lines) if re.match(r"voir les biens", l, re.I)), len(lines))
    address = ", ".join(l.rstrip(",") for l in lines[start:stop] if l.strip(", "))

    email = ""
    for x in card.find_all("a", href=True):
        if "mail=" in x["href"]:
            email = (parse_qs(urlparse(x["href"]).query).get("mail") or [""])[0].strip().lower()
            break
    tel = ""
    for x in card.find_all("a", href=True):
        if x["href"].lower().startswith("tel:"):
            tel = normalize_phone(x["href"][4:])
            break
    host = ""
    for x in card.find_all("a", href=True):
        if re.match(r"voir les biens", clean(x.get_text(" ")), re.I):
            host = urlparse(x["href"]).netloc.lower()
            break
    return name, address, tel, email, host


def parse_list_page(soup, page_url):
    found = {}
    for a in soup.find_all("a", href=AGENCE_HREF):
        url = urljoin(page_url, a["href"]).split("#")[0].split("?")[0].rstrip("/")
        if url in found:
            continue
        name, address, tel, email, host = parse_card(card_for(a))
        found[url] = {"page": url, "name": name, "address": address, "tel": tel, "email": email, "host": host}
    return found


def is_france(ag) -> bool:
    if any(h in (ag.get("host") or "") for h in NON_FR_HOSTS):
        return False
    if NON_FR_WORDS.search(ag["address"]):
        return False
    return bool((FR_POSTAL_METRO if FRANCE_METRO_ONLY else FR_POSTAL_ALL).search(ag["address"]))


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
        for a in soup.find_all("a", href=True):  # pagination : ?page=N
            href = urljoin(url, a["href"]).split("#")[0]
            if re.search(r"/liste-agences\?page=\d+$", href) and href not in seen_pages:
                queue.append(href)
        print(f"  {url} : +{len(new)} agences")
    if not agencies:
        raise SystemExit(f"Aucune agence trouvée sur {INDEX} (page inaccessible ou bloquée par robots.txt).")
    print(f"{len(agencies)} agences uniques trouvées")
    france = [ag for ag in agencies.values() if is_france(ag)]
    for ag in agencies.values():
        if not is_france(ag):
            print(f"  [hors France, exclue] {ag['name']} | {ag['address']}")
    print(f"{len(france)} agences en France")
    return france


# ------------------------------------------------ Étape 2 : page agence ----
def flip_name(s: str) -> str:
    """'BLANCHARD Mathieu' -> 'Mathieu BLANCHARD' ; un nom déjà 'Prénom Nom' reste tel quel."""
    tokens = clean(s).split(" ")
    if len(tokens) >= 2 and tokens[0].isupper() and not tokens[-1].isupper():
        k = 0
        while k < len(tokens) and tokens[k].isupper():
            k += 1
        return " ".join(tokens[k:] + tokens[:k])
    return clean(s)


def role_rank(role: str):
    r = role.lower().replace("’", "'")
    if "adjoint" in r:
        return None
    for i, pat in enumerate(ROLE_PATTERNS):
        if re.search(pat, r):
            return i
    return None


def parse_director(soup) -> str:
    """Équipe : chaque membre = photo (alt='Photo de Prénom Nom'), nom abrégé ('Mathieu B.') puis fonction."""
    best, best_rank = "", None
    for img in soup.select("img.pp_agent"):
        card = img.find_parent("div", class_="text_center")
        if card is None:
            continue
        lines = [clean(t) for t in card.get_text("\n").split("\n")]
        lines = [l for l in lines if l]
        if len(lines) < 2:
            continue
        rank = role_rank(lines[1])
        if rank is None or (best_rank is not None and rank >= best_rank):
            continue
        full = re.sub(r"^Photo de\s+", "", clean(img.get("alt", "")), flags=re.I)
        best, best_rank = flip_name(full or lines[0]), rank
    return best


# ----------------------------------------------------------------- Main ----
def load_done():
    if not os.path.exists(OUTPUT):
        return set()
    with open(OUTPUT, newline="", encoding="utf-8-sig") as f:
        return {r.get("Site web", "") for r in csv.DictReader(f)}


def commercial_name(list_name: str) -> str:
    city = re.sub(r"^Immobilier\s+", "", list_name, flags=re.I)
    return f"Nestenn {city}".strip()


def main():
    agencies = collect_agencies()
    done = load_done()
    is_new = not os.path.exists(OUTPUT)

    todo = [ag for ag in agencies if ag["page"] not in done]
    if LIMIT:
        todo = todo[:LIMIT]
    print(f"{len(agencies)} agences, {len(done)} déjà traitées, {len(todo)} à faire")

    stats = {"ok": 0, "echec": 0, "email": 0, "tel": 0, "gerant": 0}

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
            gerant = parse_director(BeautifulSoup(html, "html.parser")) if html else ""

            row = {
                "Siren": "",
                "Raison sociale": "",
                "Nom": commercial_name(ag["name"]),
                "Services": "",
                "Adresse": ag["address"],
                "Téléphone": ag["tel"],
                "Email agence": ag["email"],
                "Site web": url,
                "Gérant": gerant,
                "Tel gérant": "",
                "Email gérant": "",
            }
            filled = [c for c in ("Nom", "Adresse", "Téléphone", "Email agence", "Gérant", "Site web") if row[c]]
            row["Source"] = f"{', '.join(filled)} : {url}"
            w.writerow(row)
            out.flush()

            stats["ok"] += 1
            stats["email"] += bool(ag["email"])
            stats["tel"] += bool(ag["tel"])
            stats["gerant"] += bool(gerant)
            print(
                f"{i:>4}/{len(todo)} | {row['Nom']} | tel={ag['tel'] or '-'} | "
                f"email={ag['email'] or '-'} | gérant={gerant or '-'}"
            )

    print(f"\nTerminé : {stats['ok']} agences traitées, {stats['echec']} échecs (relancer pour les reprendre).")
    print(f"Tél : {stats['tel']} | Email : {stats['email']} | Gérant : {stats['gerant']}")


if __name__ == "__main__":
    main()
