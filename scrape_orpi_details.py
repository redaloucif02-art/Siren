#!/usr/bin/env python3
"""
Enrichit agences_orpi.csv avec, pour chaque agence :
  - Siren (= "Numéro RCS" des mentions légales)
  - Email agence (mentions légales, protégé par Cloudflare -> décodé)
  - Gérant + Tel gérant (page équipe)

Entrée  : agences_orpi.csv        (produit par scrape_orpi.py)
Sortie  : agences_orpi_details.csv

Colonnes finales :
  Siren, Nom, Services, Adresse, Téléphone, Email agence, Site web,
  Gérant, Tel gérant, Email gérant, Source

Reprise automatique : si agences_orpi_details.csv existe, les agences déjà
traitées (même "Site web") sont sautées.

Usage :
    pip install requests beautifulsoup4
    python scrape_orpi_details.py
"""
import csv
import os
import re
import time
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, NavigableString

BASE = "https://www.orpi.com"
INPUT = "agences_orpi.csv"
OUTPUT = "agences_orpi_details.csv"
DELAY = 1.0  # secondes entre deux requêtes
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TheGreatestDevBot/1.0; +https://thegreatestdev.com)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

FIELDS = [
    "Siren",
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
# Colonnes citées dans "Source" (toutes sauf Email gérant et Source elle-même)
SOURCE_COLS = [f for f in FIELDS if f not in ("Email gérant", "Source")]

# Titres considérés comme "gérant", par ordre de priorité (le 1er qui matche gagne).
ROLE_PATTERNS = [
    r"g[ée]rant",
    r"dirigeant",
    r"pr[ée]sident",
    r"titulaire",
    r"directeur\(ice\)\s*g[ée]n[ée]ral|directeur g[ée]n[ée]ral|directrice g[ée]n[ée]rale",
    r"directeur|directrice",
    r"responsable d'agence|responsable de l'agence",
]
# Si aucun titre ci-dessus n'existe, on retombe sur un(e) directeur(ice) adjoint(e).
# Mettre à False pour laisser Gérant vide dans ce cas.
FALLBACK_TO_ADJOINT = True

AGENT_RE = re.compile(r"agentId=(\d+)")

session = requests.Session()
session.headers.update(HEADERS)

def load_robots():
    """Charge robots.txt avec requests (et notre User-Agent).

    RobotFileParser.read() utilise l'UA de urllib et passe en "tout interdit"
    sur un 401/403, ce qui ferait ignorer toutes les agences à tort.
    """
    try:
        r = session.get(f"{BASE}/robots.txt", timeout=30)
    except requests.RequestException as e:
        print(f"[robots] inaccessible ({e}) -> on continue sans")
        return None
    if r.status_code != 200:
        print(f"[robots] HTTP {r.status_code} -> on continue sans")
        return None
    rp = RobotFileParser()
    rp.parse(r.text.splitlines())
    print("[robots] robots.txt chargé")
    return rp


robots = load_robots()


def allowed(url: str) -> bool:
    if robots is None:
        return True
    return robots.can_fetch(HEADERS["User-Agent"], url)


def fetch(url: str, retries: int = 3):
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return r.text
            if r.status_code in (404, 410):
                return None
            time.sleep(2 * (attempt + 1))
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
    return None


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------- Email ----
def decode_cfemail(h: str) -> str:
    """Décode la protection email de Cloudflare (data-cfemail / email-protection#)."""
    try:
        key = int(h[:2], 16)
        return "".join(chr(int(h[i : i + 2], 16) ^ key) for i in range(2, len(h), 2))
    except ValueError:
        return ""


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def extract_email(container) -> str:
    if container is None:
        return ""
    el = container.find(attrs={"data-cfemail": True})
    if el:
        return decode_cfemail(el["data-cfemail"])
    for a in container.find_all("a", href=True):
        href = a["href"]
        if "email-protection#" in href:
            return decode_cfemail(href.split("#", 1)[1])
        if href.lower().startswith("mailto:"):
            return href[7:].split("?")[0].strip()
    m = EMAIL_RE.search(container.get_text(" "))
    return m.group(0) if m else ""


# ---------------------------------------------------------- Mentions légales
def extract_siren(text: str) -> str:
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 9:
        return digits[:9]  # 9 = SIREN ; 14 = SIRET -> on garde les 9 premiers
    return ""


def parse_legal(soup: BeautifulSoup):
    """Retourne (siren, email_agence) depuis le bloc 'Mentions légales'."""
    siren = ""
    node = soup.find(string=re.compile(r"Num[ée]ro RCS", re.I))
    if node is not None:
        txt = node.parent.get_text(" ")
        siren = extract_siren(re.split(r"RCS", txt, maxsplit=1, flags=re.I)[-1])

    if not siren:  # repli : TVA = FR + clé (2 chiffres) + SIREN
        node = soup.find(string=re.compile(r"Num[ée]ro TVA", re.I))
        if node is not None:
            digits = re.sub(r"\D", "", node.parent.get_text(" "))
            if len(digits) >= 11:
                siren = digits[2:11]

    email = ""
    node = soup.find(string=re.compile(r"^\s*Adresse e-?mail\s*:", re.I))
    if node is not None:
        container = node.parent
        for _ in range(3):  # remonte au plus 3 niveaux pour trouver l'email
            email = extract_email(container)
            if email or container.parent is None:
                break
            container = container.parent
    return siren, email


# --------------------------------------------------------------- Équipe ----
def normalize_phone(s: str) -> str:
    digits = re.sub(r"\D", "", s or "")
    if len(digits) == 10:
        return " ".join(digits[i : i + 2] for i in range(0, 10, 2))
    return clean(s)


PHONE_LIKE = re.compile(r"^[\d\s.+()-]{8,}$")


def parse_team(soup: BeautifulSoup):
    """Liste des membres : {'name', 'title', 'phones'} (dédoublonnés par agentId)."""
    members, seen = [], set()
    for a in soup.find_all("a", href=AGENT_RE):
        agent_id = AGENT_RE.search(a["href"]).group(1)
        if agent_id in seen:
            continue
        head = a.find_previous(["h3", "h4", "h5"])
        if head is None:
            continue
        name = clean(head.get_text())

        texts, phones = [], []
        for el in head.next_elements:
            if el is a:
                break
            if isinstance(el, NavigableString):
                s = clean(str(el))
                if s:
                    texts.append(s)
            elif el.name == "a" and el.get("href", "").startswith("tel:"):
                phones.append(normalize_phone(el.get_text() or el["href"][4:]))
            if len(texts) > 12:  # on a dérivé hors de la carte du membre
                texts = None
                break
        if texts is None:
            continue

        title = ""
        for s in texts:
            if s == name or PHONE_LIKE.match(s) or s.lower() == "nous contacter":
                continue
            title = s
            break

        seen.add(agent_id)
        members.append({"name": name, "title": title, "phones": phones})
    return members


def role_rank(title: str):
    t = title.lower().replace("’", "'")
    if "adjoint" in t:
        if FALLBACK_TO_ADJOINT and re.search(r"directeu?r|directrice", t):
            return 90
        return None
    for i, pat in enumerate(ROLE_PATTERNS):
        if re.search(pat, t):
            return i
    return None


def pick_manager(members):
    best, best_rank = None, None
    for m in members:
        r = role_rank(m["title"])
        if r is not None and (best_rank is None or r < best_rank):
            best, best_rank = m, r
    if best is None:
        return "", ""
    phones = best["phones"]
    mobile = next((p for p in phones if re.sub(r"\D", "", p)[:2] in ("06", "07")), "")
    return best["name"], mobile or (phones[0] if phones else "")


# ----------------------------------------------------------------- Main ----
def load_done():
    if not os.path.exists(OUTPUT):
        return set()
    with open(OUTPUT, newline="", encoding="utf-8-sig") as f:
        return {r.get("Site web", "") for r in csv.DictReader(f)}


def build_source(row: dict, url: str) -> str:
    return f"{', '.join(SOURCE_COLS)} : {url}"


def main():
    with open(INPUT, newline="", encoding="utf-8-sig") as f:
        agencies = list(csv.DictReader(f))

    done = load_done()
    is_new = not os.path.exists(OUTPUT)
    todo = [a for a in agencies if a.get("Site web") and a["Site web"] not in done]
    print(f"{len(agencies)} agences en entrée, {len(done)} déjà traitées, {len(todo)} à faire")

    stats = {"ok": 0, "echec": 0, "ignore": 0, "siren": 0, "email": 0, "gerant": 0}

    with open(OUTPUT, "a", newline="", encoding="utf-8-sig") as out:
        w = csv.DictWriter(out, fieldnames=FIELDS)
        if is_new:
            w.writeheader()

        for i, ag in enumerate(todo, 1):
            url = ag["Site web"]
            if not url.startswith(BASE):
                stats["ignore"] += 1
                print(f"[ignoré : URL hors {BASE}] {url!r}")
                continue
            if not allowed(url):
                stats["ignore"] += 1
                print(f"[ignoré : interdit par robots.txt] {url}")
                continue

            html = fetch(url)
            time.sleep(DELAY)
            if not html:
                stats["echec"] += 1
                print(f"[échec] {url}")
                continue

            soup = BeautifulSoup(html, "html.parser")
            siren, email = parse_legal(soup)
            gerant, tel_gerant = pick_manager(parse_team(soup))

            row = {
                "Siren": siren,
                "Nom": ag.get("Nom", ""),
                "Services": ag.get("Services", ""),
                "Adresse": ag.get("Adresse", ""),
                "Téléphone": ag.get("Téléphone", ""),
                "Email agence": email,
                "Site web": url,
                "Gérant": gerant,
                "Tel gérant": tel_gerant,
                "Email gérant": "",  # non publié sur le site (formulaire de contact uniquement)
            }
            row["Source"] = build_source(row, url)
            w.writerow(row)
            out.flush()

            stats["ok"] += 1
            stats["siren"] += bool(siren)
            stats["email"] += bool(email)
            stats["gerant"] += bool(gerant)
            print(
                f"{i:>5}/{len(todo)} | siren={siren or '-':<9} | "
                f"email={'oui' if email else 'non'} | gérant={gerant or '-'} | {url}"
            )

    print(
        f"\nTerminé : {stats['ok']} agences traitées, {stats['echec']} échecs, "
        f"{stats['ignore']} ignorées (relancer le script pour reprendre les échecs)."
    )
    print(
        f"Siren : {stats['siren']} | Email agence : {stats['email']} | "
        f"Gérant : {stats['gerant']}"
    )


if __name__ == "__main__":
    main()
      
