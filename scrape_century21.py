#!/usr/bin/env python3
"""
Scrape les agences CENTURY 21 (France) et produit agences_century21.csv.

Étapes :
  1. century21.fr/trouver_agence/ -> liens départements / régions / villes
     -> pages liste -> chaque agence (ID, nom, adresse, site officiel)
  2. <site agence>/agence/            -> Nom, Services ("Nos spécialités"), équipe -> Gérant
  3. <site agence>/mentions_legales/  -> Raison sociale, Siren (RCS), Téléphone, Email
     (l'email est publié sous forme d'IMAGE -> lu par OCR avec tesseract)

Les agences sans site officiel (pas de mentions légales) sont quand même
exportées (Nom, Services, Adresse) avec Site web = fiche century21.fr.

Colonnes (même ordre que Orpi, + Raison sociale juste après Siren) :
  Siren, Raison sociale, Nom, Services, Adresse, Téléphone, Email agence,
  Site web, Gérant, Tel gérant, Email gérant, Source

Reprise automatique : les agences déjà dans le CSV (même "Site web") sont sautées.
Variable d'env. LIMIT=N : ne traite que N agences (pour tester).

Usage :
    sudo apt-get install -y tesseract-ocr        # pour l'email
    pip install requests beautifulsoup4 pillow pytesseract
    python scrape_century21.py
"""
import base64
import csv
import io
import os
import re
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, NavigableString

try:
    import pytesseract
    from PIL import Image, ImageOps
except ImportError:  # OCR indisponible -> Email agence restera vide
    pytesseract = None

BASE = "https://www.century21.fr"
INDEX = f"{BASE}/trouver_agence/"
OUTPUT = "agences_century21.csv"
DELAY = 1.0  # secondes entre deux requêtes
LIMIT = int(os.environ.get("LIMIT") or 0)
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

# Titres considérés comme "gérant", par ordre de priorité (comme pour Orpi).
ROLE_PATTERNS = [
    r"g[ée]rant",
    r"dirigeant",
    r"pr[ée]sident",
    r"titulaire",
    r"directeur\(ice\)\s*g[ée]n[ée]ral|directeur g[ée]n[ée]ral|directrice g[ée]n[ée]rale",
    r"directeur|directrice",
    r"responsable d'agence|responsable de l'agence",
]
FALLBACK_TO_ADJOINT = True

AGENCE_HREF = re.compile(r"/trouver_agence/agence/(\d+)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
SOCIAL_RE = re.compile(r"facebook|instagram|linkedin|youtube|tiktok|twitter|(^|\.)x\.com")

session = requests.Session()
session.headers.update(HEADERS)


# ------------------------------------------------------------- Réseau ----
_robots = {}


def allowed(url: str) -> bool:
    """robots.txt par domaine (chaque agence a son propre domaine)."""
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


def fetch_bytes(url: str, retries: int = 2):
    if url.startswith("data:"):
        try:
            return base64.b64decode(url.split(",", 1)[1])
        except Exception:
            return None
    if not allowed(url):
        return None
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=30)
            time.sleep(DELAY)
            if r.status_code == 200:
                return r.content
            if r.status_code in (404, 410):
                return None
            time.sleep(2 * (attempt + 1))
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
    return None


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------- Étape 1 : liste ----
def card_for(a):
    """Remonte du lien 'Plus d'informations' jusqu'à la carte (li) de l'agence."""
    node = a
    for _ in range(8):
        parent = node.parent
        if parent is None or parent.name in ("body", "html"):
            break
        ids = {AGENCE_HREF.search(x["href"]).group(1) for x in parent.find_all("a", href=AGENCE_HREF)}
        if len(ids) > 1:
            break
        node = parent
        if node.name in ("li", "article"):
            break
    return node


def parse_card(card, page_url):
    lines = [clean(t) for t in card.get_text("\n").split("\n")]
    lines = [l for l in lines if l]
    stop = next((i for i, l in enumerate(lines) if re.match(r"plus d.informations", l, re.I)), len(lines))
    head = lines[:stop]
    name = head[0] if head else ""
    address = ", ".join(head[1:])

    site = ""
    for x in card.find_all("a", href=True):
        href = urljoin(page_url, x["href"])
        u = urlparse(href)
        host = u.netloc.lower()
        if not host or host.endswith("century21.fr") or SOCIAL_RE.search(host):
            continue
        if "century21" in host or "site officiel" in x.get_text().lower():
            site = f"{u.scheme}://{u.netloc}"
            break
    return name, address, site


def collect_agencies():
    html = fetch(INDEX)
    if not html:
        raise SystemExit(f"Impossible de charger {INDEX}")
    soup = BeautifulSoup(html, "html.parser")

    pages, seen = [], set()
    for kind in (r"d-[^/]+", r"region/[^/]+", r"(?:v|cpv)-[^/]+"):  # départements d'abord
        for a in soup.find_all("a", href=True):
            href = urljoin(INDEX, a["href"]).split("#")[0]
            if href not in seen and re.search(rf"/trouver_agence/{kind}/?$", href):
                seen.add(href)
                pages.append(href)
    print(f"{len(pages)} pages liste à parcourir (départements, régions, villes)")

    agencies = {}
    for i, url in enumerate(pages, 1):
        html = fetch(url)
        if not html:
            continue
        psoup = BeautifulSoup(html, "html.parser")
        found_here = 0
        for a in psoup.find_all("a", href=AGENCE_HREF):
            ag_id = AGENCE_HREF.search(a["href"]).group(1)
            if ag_id in agencies:
                continue
            name, address, site = parse_card(card_for(a), url)
            agencies[ag_id] = {
                "id": ag_id,
                "name": name,
                "address": address,
                "site": site,
                "page": urljoin(url, a["href"]),
                "listing": url,
            }
            found_here += 1

        m = re.search(r"(\d+)\s+r[ée]sultats?", psoup.get_text(" "))
        announced = int(m.group(1)) if m else None
        if "/d-" in url and announced is not None:
            shown = len(psoup.find_all("a", href=AGENCE_HREF))
            flag = "" if shown >= announced else "  <-- moins que prévu (pagination ?)"
            print(f"  [{i}/{len(pages)}] {url.rsplit('/', 2)[-2]} : {shown}/{announced}{flag}")
    print(f"{len(agencies)} agences uniques trouvées")
    return list(agencies.values())


# ------------------------------------------------ Étape 2 : page agence ----
def normalize_phone(s: str) -> str:
    digits = re.sub(r"\D", "", s or "")
    if len(digits) == 10:
        return " ".join(digits[i : i + 2] for i in range(0, 10, 2))
    return clean(s)


def parse_team(soup):
    """Membres [{'name','title'}] : la page affiche Nom, Titre(s), puis <img alt=Nom>."""
    node = soup.find(string=re.compile(r"Notre [ée]quipe", re.I))
    if node is None:
        return []
    head = node.parent
    members, buf = [], []
    for el in head.next_elements:
        if el is node:
            continue
        if type(el) is NavigableString:
            s = clean(str(el))
            if not s:
                continue
            if re.match(r"(vous [êe]tes|retrouvez|envoyer un message|contacter)", s, re.I):
                break
            buf.append(s)
        elif getattr(el, "name", None) == "img":
            alt = clean(el.get("alt", ""))
            if not alt:
                continue
            idxs = [i for i, s in enumerate(buf) if s.lower() == alt.lower()]
            if not idxs:
                continue
            members.append({"name": alt, "title": " ".join(buf[idxs[-1] + 1 :])})
            buf = []
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


def pick_manager(members) -> str:
    best, best_rank = None, None
    for m in members:
        r = role_rank(m["title"])
        if r is not None and (best_rank is None or r < best_rank):
            best, best_rank = m, r
    return best["name"] if best else ""


def parse_agence_page(soup):
    h1s = soup.find_all("h1")
    h1 = next((h for h in h1s if re.match(r"\s*Agence immobili", h.get_text(), re.I)), h1s[0] if h1s else None)
    name = re.sub(r"^Agence immobili[èe]re\s+", "", clean(h1.get_text()), flags=re.I) if h1 else ""

    services = []
    node = soup.find(string=re.compile(r"^\s*Nos sp[ée]cialit[ée]s\s*$", re.I))
    if node is not None:
        ul = node.parent.find_next("ul")
        if ul is not None:
            services = [clean(li.get_text(" ")) for li in ul.find_all("li") if clean(li.get_text())]
    return name, ", ".join(services), pick_manager(parse_team(soup))


# --------------------------------------------- Étape 3 : mentions légales ----
def decode_cfemail(h: str) -> str:
    try:
        key = int(h[:2], 16)
        return "".join(chr(int(h[i : i + 2], 16) ^ key) for i in range(2, len(h), 2))
    except ValueError:
        return ""


def extract_text_email(container) -> str:
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


OCR_WHITELIST = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@.-_"


def ocr_email(data: bytes) -> str:
    """Lit l'email dans l'image (texte rendu en PNG). Retourne '' si illisible."""
    if pytesseract is None or not data:
        return ""
    try:
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGBA")
        variants = []
        for bg in ("white", "black"):  # fond transparent : on teste les deux
            base = Image.new("RGBA", im.size, bg)
            base.alpha_composite(im)
            g = base.convert("L")
            if bg == "black":
                g = ImageOps.invert(g)
            g = g.resize((g.width * 4, g.height * 4), Image.LANCZOS)
            variants.append(ImageOps.expand(g, border=24, fill=255))
        for v in variants:
            for psm in (7, 6):
                cfg = f"--psm {psm} -c tessedit_char_whitelist={OCR_WHITELIST}"
                txt = pytesseract.image_to_string(v, config=cfg).replace(" ", "").replace("\n", "")
                m = EMAIL_RE.search(txt)
                if m:
                    return m.group(0).lower()
    except Exception as e:  # image corrompue, tesseract absent...
        print(f"[ocr] {e}")
    return ""


def find_email(soup, page_url):
    """(email, méthode) pour le 1er bloc 'Adresse mail' : texte/mailto sinon image + OCR."""
    node = soup.find(string=re.compile(r"Adresse (?:e-?)?mail", re.I))
    if node is None:
        return "", ""
    container = node.parent
    for _ in range(3):
        email = extract_text_email(container)
        if email:
            return email.lower(), "texte"
        img = container.find("img")
        if img is not None:
            src = img.get("src") or img.get("data-src") or ""
            if src:
                email = ocr_email(fetch_bytes(urljoin(page_url, src)))
                return email, ("OCR" if email else "")
        if container.parent is None:
            break
        container = container.parent
    return "", ""


def digits_after(pattern: str, text: str) -> str:
    m = re.search(pattern, text)
    return re.sub(r"\D", "", m.group(1)) if m else ""


def parse_legal(soup, page_url):
    """Retourne dict(siren, raison, tel, email, email_method) pour la 1ère société de la page."""
    email, method = find_email(soup, page_url)

    for t in soup(["script", "style"]):
        t.decompose()
    text = clean(soup.get_text(" "))

    # la page peut lister plusieurs sociétés (ex. transaction + gestion) : on prend la 1ère
    starts = [m.start() for m in re.finditer(r"Raison sociale\s*:", text, re.I)]
    block = text[starts[0] : starts[1]] if len(starts) > 1 else text[starts[0] : starts[0] + 1500] if starts else ""

    raison = ""
    m = re.search(
        r"Raison sociale\s*:\s*(.+?)\s+(?=RCS\b|SIRET\b|SIREN\b|Adresse du si[èe]ge|"
        r"(?:SARL|SAS|SASU|EURL|SA|SCI)\s+au capital)",
        block,
        re.I,
    )
    if m:
        raison = clean(m.group(1))
    if not raison:  # repli : phrase "Le présent site est édité par X au capital de..."
        m = re.search(r"est [ée]dit[ée] par\s+(.+?)\s+(?:au capital|,)", text, re.I)
        raison = clean(m.group(1)) if m else ""

    num = r"((?:\d[\s\u00a0.]*){9,14})"
    siren = digits_after(r"RCS\s*:?\s*[^\d]{0,60}?" + num, block)[:9]
    if len(siren) < 9:
        siren = digits_after(r"SIRE[NT]\s*:?\s*" + num, block)[:9]
    if len(siren) < 9:
        siren = digits_after(r"est [ée]dit[ée] par.{0,300}?SIREN\s*:?\s*" + num, text)[:9]
    if len(siren) < 9:  # TVA : FR + clé (2 chiffres) + SIREN
        siren = digits_after(r"TVA\s*:?\s*FR\s*\d{2}\s*((?:\d[\s\u00a0.]*){9})", block)[:9]
    if len(siren) < 9:
        siren = ""

    m = re.search(r"T[ée]l[ée]phone\s*:\s*((?:\+?\d[\s.]*){9,12})", block)
    if not m:
        m = re.search(r"T[ée]l\.?\s*((?:\+?\d[\s.]*){9,12})", text[text.lower().find("est édité") :] if "est édité" in text.lower() else "")
    tel = normalize_phone(m.group(1)) if m else ""

    return {"siren": siren, "raison": raison, "tel": tel, "email": email, "email_method": method}


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

    todo = []
    for ag in agencies:
        ag["key"] = ag["site"] or ag["page"]  # = valeur de la colonne "Site web"
        if ag["key"] not in done:
            todo.append(ag)
    if LIMIT:
        todo = todo[:LIMIT]
    print(f"{len(agencies)} agences, {len(done)} déjà traitées, {len(todo)} à faire")
    if pytesseract is None:
        print("[!] pytesseract/Pillow absents : la colonne Email agence restera vide")

    stats = {"ok": 0, "echec": 0, "siren": 0, "email": 0, "gerant": 0, "sans_site": 0}

    with open(OUTPUT, "a", newline="", encoding="utf-8-sig") as out:
        w = csv.DictWriter(out, fieldnames=FIELDS)
        if is_new:
            w.writeheader()

        for i, ag in enumerate(todo, 1):
            site = ag["site"]
            agence_url = site.rstrip("/") + "/agence/" if site else ag["page"]
            legal_url = site.rstrip("/") + "/mentions_legales/" if site else ""

            html = fetch(agence_url)
            if html is None:  # échec temporaire : repris au prochain run
                stats["echec"] += 1
                print(f"[échec] {agence_url}")
                continue

            name, services, gerant = ag["name"], "", ""
            if html:
                n, services, gerant = parse_agence_page(BeautifulSoup(html, "html.parser"))
                name = n or name

            legal = {"siren": "", "raison": "", "tel": "", "email": "", "email_method": ""}
            if legal_url:
                lhtml = fetch(legal_url)
                if lhtml is None:
                    stats["echec"] += 1
                    print(f"[échec] {legal_url}")
                    continue
                if lhtml:
                    legal = parse_legal(BeautifulSoup(lhtml, "html.parser"), legal_url)
            else:
                stats["sans_site"] += 1

            src = []
            if legal_url:
                mail_label = "Email agence (lu par OCR, à vérifier)" if legal["email_method"] == "OCR" else "Email agence"
                src.append(f"Siren, Raison sociale, Téléphone, {mail_label} : {legal_url}")
            src.append(f"Nom, Services{', Gérant' if gerant else ''} : {agence_url}")
            src.append(f"Adresse : {ag['listing']}")

            w.writerow(
                {
                    "Siren": legal["siren"],
                    "Raison sociale": legal["raison"],
                    "Nom": name,
                    "Services": services,
                    "Adresse": ag["address"],
                    "Téléphone": legal["tel"],
                    "Email agence": legal["email"],
                    "Site web": ag["key"],
                    "Gérant": gerant,
                    "Tel gérant": "",  # non publié sur la page équipe
                    "Email gérant": "",
                    "Source": " | ".join(src),
                }
            )
            out.flush()

            stats["ok"] += 1
            stats["siren"] += bool(legal["siren"])
            stats["email"] += bool(legal["email"])
            stats["gerant"] += bool(gerant)
            print(
                f"{i:>5}/{len(todo)} | siren={legal['siren'] or '-':<9} | "
                f"raison={legal['raison'] or '-'} | email={legal['email'] or '-'} | "
                f"gérant={gerant or '-'} | {ag['key']}"
            )

    print(
        f"\nTerminé : {stats['ok']} agences traitées, {stats['echec']} échecs "
        f"(relancer le script pour les reprendre)."
    )
    print(
        f"Siren : {stats['siren']} | Email : {stats['email']} | Gérant : {stats['gerant']} "
        f"| Sans site officiel : {stats['sans_site']}"
    )


if __name__ == "__main__":
    main()
