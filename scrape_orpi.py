#!/usr/bin/env python3
"""
Récupère la liste des agences Orpi depuis l'annuaire public orpi.com
et l'exporte dans agences_orpi.csv

Colonnes : Nom, Services, Adresse, Téléphone, Site web

Usage :
    pip install requests beautifulsoup4
    python scrape_orpi.py
"""
import csv
import re
import time
from collections import deque
from urllib.parse import urljoin, urldefrag
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BASE = "https://www.orpi.com"
START = f"{BASE}/agences-immobilieres/"
OUTPUT = "agences_orpi.csv"
DELAY = 1.0  # secondes entre deux requêtes
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TheGreatestDevBot/1.0; +https://thegreatestdev.com)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Pages d'annuaire à parcourir : /agences-immobilieres-<région|ville>/
DIRECTORY_RE = re.compile(r"^https://www\.orpi\.com/agences-immobilieres-[^/?#]+/?$")
SITE_LINK_TEXT = "voir le site de l'agence"

session = requests.Session()
session.headers.update(HEADERS)

robots = RobotFileParser()
robots.set_url(f"{BASE}/robots.txt")
try:
    robots.read()
except Exception:
    robots = None  # si robots.txt est inaccessible, on continue prudemment


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


def parse_agencies(soup: BeautifulSoup, page_url: str):
    """Extrait les cartes agence d'une page d'annuaire."""
    agencies = []
    for link in soup.find_all("a"):
        if SITE_LINK_TEXT not in clean(link.get_text()).lower():
            continue

        # Remonter jusqu'au bloc qui contient le titre (h2) de cette agence seule
        container = link
        while container.parent is not None:
            container = container.parent
            if container.find("h2") and len(container.find_all("h2")) == 1:
                break
        else:
            continue
        if len(container.find_all("h2")) != 1:
            continue

        name = clean(container.find("h2").get_text())
        services = [clean(li.get_text()) for li in container.find_all("li")]
        services = [s for s in services if s]

        tel_link = container.find("a", href=re.compile(r"^tel:"))
        phone = clean(tel_link.get_text()) if tel_link else ""

        # Adresse = lignes de texte situées entre les services et le téléphone
        lines = [clean(x) for x in container.get_text("\n", strip=True).split("\n")]
        lines = [x for x in lines if x]
        address_lines = []
        if phone and phone in lines:
            end = lines.index(phone)
            for x in lines[1:end]:
                if x in services or x == name:
                    continue
                address_lines.append(x)
        address = ", ".join(address_lines)

        website = urljoin(page_url, link.get("href", ""))
        agencies.append(
            {
                "Nom": name,
                "Services": " | ".join(services),
                "Adresse": address,
                "Téléphone": phone,
                "Site web": website,
            }
        )
    return agencies


def main():
    queue = deque([START])
    visited = set()
    agencies = {}  # dédoublonnage sur l'URL du site de l'agence

    while queue:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        if not allowed(url):
            print(f"[robots] ignoré : {url}")
            continue

        html = fetch(url)
        time.sleep(DELAY)
        if not html:
            print(f"[échec] {url}")
            continue

        soup = BeautifulSoup(html, "html.parser")

        found = parse_agencies(soup, url)
        new = 0
        for a in found:
            if a["Site web"] not in agencies:
                agencies[a["Site web"]] = a
                new += 1

        # Ajouter les pages d'annuaire trouvées (régions, villes, arrondissements)
        for a in soup.find_all("a", href=True):
            link = urldefrag(urljoin(url, a["href"]))[0]
            if "/recherche" in link:
                continue
            if DIRECTORY_RE.match(link) and link not in visited:
                queue.append(link)

        print(f"{len(visited):>5} pages | {len(agencies):>5} agences | +{new} | {url}")

    rows = sorted(agencies.values(), key=lambda r: (r["Adresse"], r["Nom"]))
    with open(OUTPUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f, fieldnames=["Nom", "Services", "Adresse", "Téléphone", "Site web"]
        )
        w.writeheader()
        w.writerows(rows)

    print(f"\nTerminé : {len(rows)} agences uniques -> {OUTPUT}")
    print("Attendu : environ 1 250. Si l'écart est grand, vérifier le parsing.")


if __name__ == "__main__":
    main()
