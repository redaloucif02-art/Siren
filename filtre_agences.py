import csv, os, random, time, threading, requests
from concurrent.futures import ThreadPoolExecutor

IN, OUT, DONE, ERR = ("agences_sans_reseau.csv", "agences_filtrees.csv",
                      "traites.txt", "erreurs_429.csv")
API = "https://recherche-entreprises.api.gouv.fr/search"
BASE_RATE, MIN_RATE, WORKERS, MAX_SECONDS = 3, 1.5, 4, 5.5 * 3600
MAX_TENTATIVES = 3
LIMIT = int(os.environ.get("LIMIT") or 0)       # 0 = tout
MODE = os.environ.get("MODE", "normal")         # "normal" ou "retry"

EFFECTIF = {"00": "0", "01": "1-2", "02": "3-5", "03": "6-9", "11": "10-19",
            "12": "20-49", "21": "50-99", "22": "100-199", "31": "200-249",
            "32": "250-499", "41": "500-999", "42": "1000-1999"}
FIELDS = ["siren", "nom_complet", "adresse", "code_postal", "effectif",
          "nb_etablissements", "creation", "dirigeant"]

lock, rate_lock = threading.Lock(), threading.Lock()
start = time.time()
stats = {"ok": 0}
state = {"interval": 1 / BASE_RATE, "pause_until": 0.0, "last": 0.0,
         "streak": 0, "last_cut": 0.0}

def inc(k):
    with lock:
        stats[k] = stats.get(k, 0) + 1

def suivi():
    while True:
        time.sleep(30)
        print(f"[{int(time.time() - start)}s] rate={1 / state['interval']:.1f}/s "
              f"{dict(sorted(stats.items()))}", flush=True)

def throttle():
    with rate_lock:
        now = time.time()
        wait = max(state["pause_until"] - now, state["last"] + state["interval"] - now)
        if wait > 0:
            time.sleep(wait)
        state["last"] = time.time()

def on_429(retry_after):
    with lock:
        now = time.time()
        if now - state["last_cut"] < 15:   # une rafale de 429 = un seul ralentissement
            return
        state["last_cut"] = now
        state["interval"] = min(state["interval"] * 1.25, 1 / MIN_RATE)
        state["pause_until"] = max(state["pause_until"], now + min(max(retry_after, 2), 10))
        state["streak"] = 0

def on_ok():
    with lock:
        state["streak"] += 1
        if state["streak"] >= 20:
            state["interval"] = max(1 / BASE_RATE, state["interval"] * 0.85)
            state["streak"] = 0

def fetch(siren):
    code = "exc"
    for essai in range(MAX_TENTATIVES):
        throttle()
        try:
            resp = requests.get(API, params={"q": siren, "per_page": 1}, timeout=20)
            code = resp.status_code
            if code == 200:
                inc("ok")
                on_ok()
                res = resp.json().get("results") or []
                return res[0] if res and res[0]["siren"] == siren else None
            inc(f"http_{code}")
            if code == 429:
                on_429(float(resp.headers.get("Retry-After") or 5))
            else:
                time.sleep(2 ** essai)
        except requests.RequestException:
            inc("exc")
            code = "exc"
            time.sleep(2 ** essai)
    return ("ERR", code)

def motif(r):
    if r["etat_administratif"] != "A":
        return "rejet_cessee"
    if r.get("nature_juridique") == "1000":      # entreprise individuelle
        return "rejet_ei"
    teff = r.get("tranche_effectif_salarie")
    if teff is None:
        return "garde_effectif_inconnu"
    if teff in ("NN", "00"):
        return "garde_sans_salarie"
    return "garde_avec_salarie"

def ligne(r):
    s = r["siege"]
    d = (r.get("dirigeants") or [{}])[0]
    return {
        "siren": r["siren"],
        "nom_complet": r["nom_complet"],
        "adresse": s.get("adresse") or "",
        "code_postal": s.get("code_postal") or "",
        "effectif": EFFECTIF.get(r.get("tranche_effectif_salarie"), "n/c"),
        "nb_etablissements": r.get("nombre_etablissements_ouverts", ""),
        "creation": r.get("date_creation") or "",
        "dirigeant": (f'{d.get("nom","")} {d.get("prenoms","")}'.strip()
                      or d.get("denomination") or ""),
    }

def traiter(siren, w, fdone, ferr):
    if time.time() - start > MAX_SECONDS:
        return
    r = fetch(siren)
    with lock:
        if isinstance(r, tuple):                 # échec : mis de côté pour retry
            ferr.writerow([siren, r[1]])
            stats["a_reprendre"] = stats.get("a_reprendre", 0) + 1
            return
        if r is None:
            stats["introuvable"] = stats.get("introuvable", 0) + 1
        else:
            m = motif(r)
            stats[m] = stats.get(m, 0) + 1
            if m.startswith("garde"):
                w.writerow(ligne(r))
        fdone.write(siren + "\n")

def lire_set(path):
    return set(open(path).read().split()) if os.path.exists(path) else set()

def lire_err():
    if not os.path.exists(ERR):
        return {}
    with open(ERR, newline="") as f:
        return {row["siren"]: row["code"] for row in csv.DictReader(f)}

def main():
    deja = lire_set(DONE)
    err = lire_err()

    if MODE == "retry":
        restants = sorted(set(err) - deja)
    else:
        with open(IN, newline="", encoding="utf-8-sig") as f:
            sirens = {("".join(c for c in (row.get("siren") or "") if c.isdigit())).zfill(9)
                      for row in csv.DictReader(f)}
        sirens.discard("000000000")
        restants = sorted(sirens - deja - set(err))
    if LIMIT:
        random.seed(42)
        restants = random.sample(restants, min(LIMIT, len(restants)))
    print(f"MODE={MODE} | {len(deja)} traités, {len(err)} en erreur, "
          f"{len(restants)} à traiter", flush=True)
    if not restants:
        return

    if MODE == "retry" or not os.path.exists(ERR):
        open(ERR, "w", newline="").write("siren,code\n")

    threading.Thread(target=suivi, daemon=True).start()

    nouveau = not os.path.exists(OUT)
    with open(OUT, "a", newline="", encoding="utf-8") as fo, \
         open(DONE, "a") as fdone, \
         open(ERR, "a", newline="") as fe:
        w, ferr = csv.DictWriter(fo, fieldnames=FIELDS), csv.writer(fe)
        if nouveau:
            w.writeheader()
        with ThreadPoolExecutor(WORKERS) as ex:
            for i, _ in enumerate(ex.map(lambda s: traiter(s, w, fdone, ferr), restants)):
                if i % 200 == 0:
                    fo.flush(); fdone.flush(); fe.flush()

    if MODE == "retry":
        done_now = lire_set(DONE)
        nouveaux = lire_err()
        reste = {s: err.get(s, "?") for s in err if s not in done_now}
        reste.update(nouveaux)
        with open(ERR, "w", newline="") as f:
            cw = csv.writer(f)
            cw.writerow(["siren", "code"])
            cw.writerows(sorted(reste.items()))
    print("FIN", dict(sorted(stats.items())), flush=True)

if __name__ == "__main__":
    main()
