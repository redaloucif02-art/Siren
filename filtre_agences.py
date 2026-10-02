import csv, os, random, time, threading, requests
from concurrent.futures import ThreadPoolExecutor

IN, OUT, DONE = "agences_sans_reseau.csv", "agences_filtrees.csv", "traites.txt"
API = "https://recherche-entreprises.api.gouv.fr/search"
RATE, WORKERS, MAX_SECONDS = 6, 8, 5.5 * 3600
LIMIT = int(os.environ.get("LIMIT") or 0)  # 0 = tout ; ex. 3000 pour un test
EXCLURE_EFFECTIF_INCONNU = False           # True = écarte aussi effectif inconnu + sans enseigne

EFFECTIF = {"00": "0", "01": "1-2", "02": "3-5", "03": "6-9", "11": "10-19",
            "12": "20-49", "21": "50-99", "22": "100-199", "31": "200-249",
            "32": "250-499", "41": "500-999", "42": "1000-1999"}
FIELDS = ["siren", "nom_complet", "adresse", "code_postal",
          "effectif", "creation", "dirigeant"]

lock, rate_lock = threading.Lock(), threading.Lock()
last_call = [0.0]
start = time.time()
stats = {"ok": 0}

def inc(k):
    with lock:
        stats[k] = stats.get(k, 0) + 1

def suivi():
    while True:
        time.sleep(30)
        print(f"[{int(time.time() - start)}s] {dict(sorted(stats.items()))}", flush=True)

def throttle():
    with rate_lock:
        wait = last_call[0] + 1 / RATE - time.time()
        if wait > 0:
            time.sleep(wait)
        last_call[0] = time.time()

def fetch(siren):
    for essai in range(5):
        throttle()
        try:
            resp = requests.get(API, params={"q": siren, "per_page": 1}, timeout=20)
            if resp.status_code == 200:
                inc("ok")
                res = resp.json().get("results") or []
                return res[0] if res and res[0]["siren"] == siren else None
            inc(f"http_{resp.status_code}")
            time.sleep(int(resp.headers.get("Retry-After", 2 ** essai)))
        except requests.RequestException:
            inc("exc")
            time.sleep(2 ** essai)
    return "ERREUR"

def motif(r):
    s = r["siege"]
    if r["etat_administratif"] != "A":
        return "rejet_cessee"
    if r.get("nature_juridique") == "1000":
        return "rejet_ei"
    teff = r.get("tranche_effectif_salarie")
    sans_enseigne = not (s.get("nom_commercial") or s.get("liste_enseignes"))
    if sans_enseigne and teff in ("NN", "00"):
        return "rejet_sans_salarie+sans_enseigne"
    if sans_enseigne and teff is None:
        return ("rejet_effectif_inconnu+sans_enseigne" if EXCLURE_EFFECTIF_INCONNU
                else "garde_effectif_inconnu+sans_enseigne")
    return "garde_agence"

def ligne(r):
    s = r["siege"]
    d = (r.get("dirigeants") or [{}])[0]
    return {
        "siren": r["siren"],
        "nom_complet": r["nom_complet"],
        "adresse": s.get("adresse") or "",
        "code_postal": s.get("code_postal") or "",
        "effectif": EFFECTIF.get(r.get("tranche_effectif_salarie"), "n/c"),
        "creation": r.get("date_creation") or "",
        "dirigeant": (f'{d.get("nom","")} {d.get("prenoms","")}'.strip()
                      or d.get("denomination") or ""),
    }

def traiter(siren, w, fdone):
    if time.time() - start > MAX_SECONDS:
        return
    r = fetch(siren)
    if r == "ERREUR":
        return  # non marqué traité : repris au prochain run
    with lock:
        if r is None:
            stats["introuvable"] = stats.get("introuvable", 0) + 1
        else:
            m = motif(r)
            stats[m] = stats.get(m, 0) + 1
            if m.startswith("garde"):
                w.writerow(ligne(r))
        fdone.write(siren + "\n")

def main():
    with open(IN, newline="", encoding="utf-8-sig") as f:
        sirens = {("".join(c for c in (row.get("siren") or "") if c.isdigit())).zfill(9)
                  for row in csv.DictReader(f)}
    sirens.discard("000000000")
    deja = set(open(DONE).read().split()) if os.path.exists(DONE) else set()
    restants = sorted(sirens - deja)
    if LIMIT:
        random.seed(42)
        restants = random.sample(restants, min(LIMIT, len(restants)))
    print(f"{len(sirens)} SIREN, {len(deja)} déjà traités, {len(restants)} à traiter",
          flush=True)

    t = requests.get(API, params={"q": restants[0], "per_page": 1}, timeout=20)
    print("Test API :", t.status_code, flush=True)
    threading.Thread(target=suivi, daemon=True).start()

    nouveau = not os.path.exists(OUT)
    with open(OUT, "a", newline="", encoding="utf-8") as fo, open(DONE, "a") as fdone:
        w = csv.DictWriter(fo, fieldnames=FIELDS)
        if nouveau:
            w.writeheader()
        with ThreadPoolExecutor(WORKERS) as ex:
            for i, _ in enumerate(ex.map(lambda s: traiter(s, w, fdone), restants)):
                if i % 500 == 0:
                    fo.flush(); fdone.flush()
    print("FIN", dict(sorted(stats.items())), flush=True)

if __name__ == "__main__":
    main()
