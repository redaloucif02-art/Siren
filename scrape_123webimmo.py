#!/usr/bin/env python3
"""
Scrape les agences 123webimmo (France) et produit agences_123webimmo.csv.

Étapes :
  1. 123webimmo.com/agences -> liens des agences (/agences/<slug>), pagination suivie si présente
  2. Chaque page agence -> Nom, Adresse, Téléphone, Email, Raison sociale, Directeur

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
import json
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

AGENCE_PATH = re.compile(r"^/agences/[^/?#]+/?$")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?:(?:\+|00)33[\s.]*|0)[1-9](?:[\s.\-]*\d{2}){4}")
ROLE_WORDS = r"directeur|directrice|g[ée]rant(?:e)?|dirigeant(?:e)?|responsable d.agence|titulaire"
ROLE_RE = re.compile(rf"\b(?:{ROLE_WORDS})\b", re.I)
NAME_STOP = re.compile(
    r"agence|contact|immobili|nous|notre|votre|[ée]quipe|voir|appel|envoyer|estim|vendre|acheter|louer",
    re.I,
)

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
            "ou structure des liens différente)."
        )
    print(f"{len(agencies)} agences uniques trouvées")
    return list(agencies.values())


# ------------------------------------------------ Étape 2 : page agence ----
def jsonld_items(soup):
    items = []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or s.get_text() or "")
        except ValueError:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                items.append(d)
                stack.extend(v for v in d.values() if isinstance(v, (dict, list)))
    return items


def decode_cfemail(h: str) -> str:
    try:
        key = int(h[:2], 16)
        return "".join(chr(int(h[i : i + 2], 16) ^ key) for i in range(2, len(h), 2))
    except ValueError:
        return ""


def normalize_phone(s: str) -> str:
    digits = re.sub(r"\D", "", s or "")
    if digits.startswith("0033"):
        digits = "0" + digits[4:]
    elif digits.startswith("33") and len(digits) == 11:
        digits = "0" + digits[2:]
    if len(digits) == 10:
        return " ".join(digits[i : i + 2] for i in range(0, 10, 2))
    return clean(s)


def find_email(soup, ld):
    el = soup.find(attrs={"data-cfemail": True})
    if el:
        e = decode_cfemail(el["data-cfemail"])
        if e:
            return e.lower()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "email-protection#" in href:
            e = decode_cfemail(href.split("#", 1)[1])
            if e:
                return e.lower()
        if href.lower().startswith("mailto:"):
            e = href[7:].split("?")[0].strip()
            if e:
                return e.lower()
    for d in ld:
        if isinstance(d.get("email"), str) and EMAIL_RE.search(d["email"]):
            return EMAIL_RE.search(d["email"]).group(0).lower()
    m = EMAIL_RE.search(soup.get_text(" "))
    return m.group(0).lower() if m else ""


def find_phone(soup, ld, text):
    for a in soup.find_all("a", href=True):
        if a["href"].lower().startswith("tel:"):
            p = normalize_phone(a["href"][4:])
            if p:
                return p
    for d in ld:
        if isinstance(d.get("telephone"), str) and d["telephone"].strip():
            return normalize_phone(d["telephone"])
    m = PHONE_RE.search(text)
    return normalize_phone(m.group(0)) if m else ""


def find_address(soup, ld):
    for d in ld:
        a = d.get("address")
        if isinstance(a, dict):
            parts = [a.get("streetAddress"), a.get("postalCode"), a.get("addressLocality")]
            addr = clean(" ".join(p for p in parts if isinstance(p, str)))
            if addr:
                return addr
        elif isinstance(a, str) and clean(a):
            return clean(a)
    tag = soup.find("address")
    if tag is not None and clean(tag.get_text(" ")):
        return clean(tag.get_text(" "))
    # repli : ligne contenant un code postal à 5 chiffres
    for line in [clean(t) for t in soup.get_text("\n").split("\n")]:
        if 8 < len(line) < 120 and re.search(r"\b\d{5}\b", line) and not PHONE_RE.search(line):
            return line
    return ""


def cut_legal(s: str) -> str:
    s = re.split(r"\s+(?:RCS|SIRET|SIREN|au capital|Adresse du si[èe]ge)\b", s, maxsplit=1, flags=re.I)[0]
    return clean(s.strip(" :,.-"))


def find_raison(lines, flat):
    for i, l in enumerate(lines):
        m = re.search(r"Raison sociale\s*:?\s*(.*)", l, re.I)
        if m:
            val = m.group(1) or (lines[i + 1] if i + 1 < len(lines) else "")
            val = cut_legal(val)
            if val:
                return val
    m = re.search(
        r"(?:exploit[ée]e?|g[ée]r[ée]e?|[ée]dit[ée]e?|propos[ée]e?) par\s+(.+?)(?:\s*[,.(]|\s+au capital|\s+RCS|\s+SIRET|\s+SIREN)",
        flat,
        re.I,
    )
    return cut_legal(m.group(1)) if m else ""


def looks_like_name(s: str) -> bool:
    s = clean(s)
    if not s or len(s) > 50 or re.search(r"\d|@", s) or NAME_STOP.search(s) or ROLE_RE.search(s):
        return False
    tokens = s.split(" ")
    if not 2 <= len(tokens) <= 4:
        return False
    return all(re.match(r"^[A-ZÀ-ÖØ-Þ][\w'’\-À-ÿ]*\.?$", t) for t in tokens)


def find_director(lines):
    for i, l in enumerate(lines):
        if len(l) > 120 or not ROLE_RE.search(l):
            continue
        m = re.search(rf"(?:{ROLE_WORDS})(?:\s+(?:de l.agence|d.agence))?\s*:\s*(.+)", l, re.I)
        if m and looks_like_name(m.group(1)):
            return clean(m.group(1))
        m = re.match(rf"(.+?)\s*[,\-–|]\s*(?:le |la )?(?:{ROLE_WORDS})", l, re.I)
        if m and looks_like_name(m.group(1)):
            return clean(m.group(1))
        for j in (i - 1, i + 1):
            if 0 <= j < len(lines) and looks_like_name(lines[j]):
                return clean(lines[j])
    return ""


def find_name(soup, ld):
    cand = ""
    h1 = soup.find("h1")
    if h1 is not None:
        cand = clean(h1.get_text(" "))
    if not cand:
        for d in ld:
            if isinstance(d.get("name"), str) and clean(d["name"]):
                cand = clean(d["name"])
                break
    if not cand and soup.title is not None:
        cand = clean(re.split(r"\s[|\-–]\s", soup.title.get_text())[0])
    if cand and "123webimmo" not in cand.lower():
        cand = "123webimmo " + cand
    return cand


def parse_agence(soup):
    ld = jsonld_items(soup)
    name = find_name(soup, ld)
    email = find_email(soup, ld)
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    lines = [clean(t) for t in soup.get_text("\n").split("\n")]
    lines = [l for l in lines if l]
    flat = " ".join(lines)
    return {
        "name": name,
        "address": find_address(soup, ld),
        "tel": find_phone(soup, ld, flat),
        "email": email,
        "raison": find_raison(lines, flat),
        "gerant": find_director(lines),
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
            filled = [c for c in ("Nom", "Adresse", "Téléphone", "Email agence", "Raison sociale", "Gérant", "Site web") if row[c]]
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

    print(f"\nTerminé : {stats['ok']} agences traitées, {stats['echec']} échecs (relancer pour les reprendre), {stats['vide']} pages vides/interdites.")
    print(
        f"Raison sociale : {stats['raison']} | Tél : {stats['tel']} | Email : {stats['email']} | Gérant : {stats['gerant']}"
    )


if __name__ == "__main__":
    main()
