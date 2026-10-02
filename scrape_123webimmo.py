#!/usr/bin/env python3
"""
Scrape les agences 123webimmo (France) et produit agences_123webimmo.csv.

Étapes :
  1. 123webimmo.com/agences -> liens des agences (/agences/<slug>), pagination suivie si présente
  2. Chaque page agence -> bloc "Détails et tarifs de votre Agence" :
       Nom (Nom commercial), Raison sociale (Mentions légales), Adresse,
       Téléphone + Email (Contact), Directeur d'agence

Colonnes (mêmes que le scraper Century 21, pour pouvoir fusionner les CSV) :
  Siren, Raison sociale, Nom, Services, Adresse, Téléphone, Email agence,
  Site web, Gérant, Tel gérant, Email gérant, Source

Laissées vides volontairement : Siren, Services, Tel gérant, Email gérant.
"Site web" = lien de la page agence sur 123webimmo (sert aussi de clé de reprise).
"Source"   = colonnes renseignées, puis le lien de la page agence.

Reprise automatique : les agences déjà dans le CSV (même "Site web") sont sautées.
Variable d'env. LIMIT=N : ne traite que N agences (pour tester).

Usage :
    pip install requests beautifulsoup4
    LIMIT=5 python scrape_123webimmo.py
"""
import csv
import os
import re
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BASE = "https://www.123webimmo.com"
INDEX = f"{BASE}/agences"
OUTPUT = "agences_123webimmo.csv"
DELAY = 1.0  # secondes entre deux requêtes
LIMIT = int(os.environ.get("LIMIT") or 0)
MAX_LIST_PAGES = 200
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

AGENCE_PATH = re.compile(r"^/agences/[^/?#]+/?$")  # exclut /agences/baremes/<slug>
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

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


# ---------------------------------------------------- Étape 1 : liste ----
def agency_links(soup, page_url):
    out = []
    for a in soup.find_all("a", href=True):
        u = urlparse(urljoin(page_url, a["href"]))
        if u.netloc.lower().replace("www.", "") != "123webimmo.com":
            continue
        if AGENCE_PATH.match(u.path) and u.path.rstrip("/") != "/agences":
            out.append(f"{BASE}{u.path.rstrip('/')}")
    return out


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
        before = len(agencies)
        for link in agency_links(soup, url):
            agencies.setdefault(link, {"page": link})
        # pagination éventuelle : ?page=N sur /agences
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"]).split("#")[0]
            if (
                re.search(r"[?&]page=\d+", href)
                and urlparse(href).path.rstrip("/") == "/agences"
                and href not in seen_pages
            ):
                queue.append(href)
        print(f"  {url} : +{len(agencies) - before} agences")
    if not agencies:
        raise SystemExit(
            f"Aucune agence trouvée sur {INDEX} (page inaccessible, bloquée par robots.txt, "
            "ou liens générés en JavaScript)."
        )
    print(f"{len(agencies)} agences uniques trouvées")
    return list(agencies.values())


# ------------------------------------------------ Étape 2 : page agence ----
def normalize_phone(s: str) -> str:
    digits = re.sub(r"\D", "", s or "")
    if digits.startswith("0033"):
        digits = "0" + digits[4:]
    elif digits.startswith("33") and len(digits) == 11:
        digits = "0" + digits[2:]
    if len(digits) == 10:
        return " ".join(digits[i : i + 2] for i in range(0, 10, 2))
    return clean(s)


def flip_name(s: str) -> str:
    """'SGARRA Maria' -> 'Maria SGARRA' (la page écrit NOM Prénom)."""
    tokens = clean(s).split(" ")
    if len(tokens) >= 2 and tokens[0].isupper() and not tokens[-1].isupper():
        k = 0
        while k < len(tokens) and tokens[k].isupper():
            k += 1
        return " ".join(tokens[k:] + tokens[:k])
    return clean(s)


def after_colon(s: str) -> str:
    return clean(s.split(":", 1)[1]) if ":" in s else ""


def info_blocks(section):
    """{titre h3 (minuscule): bloc div.section__info} dans le bloc 'détails de l'agence'."""
    blocks = {}
    for div in section.select("div.section__info"):
        h3 = div.find("h3")
        if h3 is not None:
            blocks[clean(h3.get_text(" ")).lower()] = div
    return blocks


def parse_agence(soup):
    out = {"name": "", "address": "", "tel": "", "email": "", "raison": "", "gerant": ""}
    section = soup.select_one(".section--agency-details")
    if section is None:
        if soup.title is not None:
            out["name"] = clean(soup.title.get_text())
        return out
    blocks = info_blocks(section)

    # Directeur d'agence (repli : responsable d'agence, dont le libellé peut manquer : ": NOM Prénom")
    resp = blocks.get("responsable d'agence") or blocks.get("responsable d’agence")
    if resp is not None:
        lis = [clean(li.get_text(" ")) for li in resp.find_all("li")]
        directeur = next((after_colon(l) for l in lis if re.match(r"directeu?r|directrice", l, re.I)), "")
        responsable = next((after_colon(l) for l in lis if not re.match(r"directeu?r|directrice", l, re.I)), "")
        out["gerant"] = flip_name(directeur or responsable)

    # Adresse
    for title, div in blocks.items():
        if title.startswith("adresse"):
            addr = clean(div.get_text(" ").replace(div.find("h3").get_text(), "", 1))
            out["address"] = re.sub(r"\b(\d{2})\s(\d{3})\b", r"\1\2", addr)  # "38 560" -> "38560"

    # Contact (les liens tel: du footer sont ceux du siège 123webimmo, donc on les ignore)
    contact = blocks.get("contact")
    if contact is not None:
        for p in contact.find_all("p"):
            t = clean(p.get_text(" "))
            if re.match(r"t[ée]l", t, re.I):
                out["tel"] = normalize_phone(after_colon(t) or t)
            elif EMAIL_RE.search(t):
                out["email"] = EMAIL_RE.search(t).group(0).lower()

    # Mentions légales : 1er paragraphe = raison sociale, puis "Nom commercial : ..."
    legal = blocks.get("mentions légales")
    if legal is not None:
        for p in legal.find_all("p"):
            t = clean(p.get_text(" "))
            if not t:
                continue
            if t.lower().startswith("nom commercial"):
                out["name"] = after_colon(t)
            elif not out["raison"] and ":" not in t:
                out["raison"] = t

    if not out["name"] and soup.title is not None:
        out["name"] = clean(soup.title.get_text())
    return out


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

    stats = {"ok": 0, "echec": 0, "vide": 0, "email": 0, "tel": 0, "raison": 0, "gerant": 0}

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
            if not html:  # 404 / interdit par robots.txt
                stats["vide"] += 1
                continue

            d = parse_agence(BeautifulSoup(html, "html.parser"))

            row = {
                "Siren": "",
                "Raison sociale": d["raison"],
                "Nom": d["name"],
                "Services": "",
                "Adresse": d["address"],
                "Téléphone": d["tel"],
                "Email agence": d["email"],
                "Site web": url,
                "Gérant": d["gerant"],
                "Tel gérant": "",
                "Email gérant": "",
            }
            filled = [
                c
                for c in ("Nom", "Adresse", "Téléphone", "Email agence", "Raison sociale", "Gérant", "Site web")
                if row[c]
            ]
            row["Source"] = f"{', '.join(filled)} : {url}"
            w.writerow(row)
            out.flush()

            stats["ok"] += 1
            stats["email"] += bool(d["email"])
            stats["tel"] += bool(d["tel"])
            stats["raison"] += bool(d["raison"])
            stats["gerant"] += bool(d["gerant"])
            print(
                f"{i:>5}/{len(todo)} | {d['name'] or '-'} | raison={d['raison'] or '-'} | "
                f"tel={d['tel'] or '-'} | email={d['email'] or '-'} | gérant={d['gerant'] or '-'}"
            )

    print(
        f"\nTerminé : {stats['ok']} agences traitées, {stats['echec']} échecs (relancer pour les reprendre), "
        f"{stats['vide']} pages vides/interdites."
    )
    print(
        f"Raison sociale : {stats['raison']} | Tél : {stats['tel']} | Email : {stats['email']} | Gérant : {stats['gerant']}"
    )


if __name__ == "__main__":
    main()
      
