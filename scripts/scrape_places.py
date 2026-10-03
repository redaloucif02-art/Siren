#!/usr/bin/env python3
"""Enrichit data/agences.csv avec Google Maps via Serper.dev (endpoint /places).

- 1 appel Serper par agence (requête = raison sociale + adresse).
- TOUS les résultats renvoyés sont conservés (champ "places").
- Sortie : data/places.jsonl (1 ligne JSON par agence), data/errors.jsonl.
- Reprise automatique via data/progress.json (index + crédits utilisés par clé).
- Rotation sur plusieurs clés (secret SERPER_API_KEYS, une clé par ligne ou séparées par des virgules).
- Commit + push sur la branche courante (main) tous les COMMIT_EVERY agences.
"""
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / os.getenv("CSV_PATH", "data/agences.csv")
OUT_PATH = ROOT / "data/places.jsonl"
ERR_PATH = ROOT / "data/errors.jsonl"
PROGRESS_PATH = ROOT / "data/progress.json"

API_URL = os.getenv("SERPER_URL", "https://google.serper.dev/places")
CREDITS_PER_KEY = int(os.getenv("CREDITS_PER_KEY", "2500"))
WORKERS = int(os.getenv("WORKERS", "8"))
COMMIT_EVERY = int(os.getenv("COMMIT_EVERY", "500"))
MAX_ROWS = int(os.getenv("MAX_ROWS", "0")) or None  # max agences pour CE run
MAX_SECONDS = int(os.getenv("MAX_SECONDS", str(5 * 3600 + 30 * 60)))  # arrêt propre avant les 6 h GitHub
RETRY_ERRORS = os.getenv("RETRY_ERRORS", "").lower() in ("1", "true", "yes")
DO_GIT = os.getenv("DO_GIT", "1") == "1"
GL = os.getenv("SERPER_GL", "fr")
HL = os.getenv("SERPER_HL", "fr")


class AllKeysExhausted(Exception):
    pass


# --------------------------------------------------------------------------- clés
class KeyPool:
    def __init__(self, keys, used):
        self.lock = threading.Lock()
        self.keys = {self.kid(k): k for k in keys}
        self.used = {kid: int(used.get(kid, 0)) for kid in self.keys}
        self.dead = {kid for kid in self.keys if self.used[kid] >= CREDITS_PER_KEY}
        self.rr = 0

    @staticmethod
    def kid(key):
        return hashlib.sha256(key.encode()).hexdigest()[:8]

    def get(self):
        with self.lock:
            alive = [k for k in self.keys if k not in self.dead]
            if not alive:
                raise AllKeysExhausted()
            kid = alive[self.rr % len(alive)]
            self.rr += 1
            return kid, self.keys[kid]

    def add_usage(self, kid, credits):
        with self.lock:
            self.used[kid] += credits
            if self.used[kid] >= CREDITS_PER_KEY:
                self.dead.add(kid)

    def kill(self, kid):
        with self.lock:
            self.dead.add(kid)
            self.used[kid] = max(self.used[kid], CREDITS_PER_KEY)

    def remaining(self):
        return sum(max(0, CREDITS_PER_KEY - u) for u in self.used.values())


def load_keys():
    raw = os.getenv("SERPER_API_KEYS", "")
    keys = [k.strip() for k in re.split(r"[\s,;]+", raw) if k.strip()]
    if not keys:
        sys.exit("Secret SERPER_API_KEYS vide : ajoute tes clés (une par ligne).")
    return list(dict.fromkeys(keys))


# --------------------------------------------------------------------------- données
def build_query(row):
    name = re.sub(r"[()]", " ", row["nom_complet"])
    seen, words = set(), []
    for w in name.split():  # dédoublonne "FAGOT IMMOBILIER (FAGOT IMMOBILIER) (FAGIM)"
        if w.lower() not in seen:
            seen.add(w.lower())
            words.append(w)
    return f"{' '.join(words)} {row['adresse']}".strip()


def load_rows():
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_progress():
    if PROGRESS_PATH.exists():
        return json.loads(PROGRESS_PATH.read_text())
    return {"next": 0, "done": False, "keys": {}}


def save_progress(p):
    PROGRESS_PATH.write_text(json.dumps(p, indent=2))


# --------------------------------------------------------------------------- appel API
def call_serper(pool, query):
    """Retourne (data|None, erreur|None). Lève AllKeysExhausted si plus aucune clé."""
    last_err = None
    for attempt in range(6):
        kid, key = pool.get()
        try:
            r = requests.post(
                API_URL,
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                json={"q": query, "gl": GL, "hl": HL},
                timeout=30,
            )
        except requests.RequestException as e:
            last_err = f"network: {e}"
            time.sleep(2 * (attempt + 1))
            continue

        if r.status_code == 200:
            data = r.json()
            pool.add_usage(kid, int(data.get("credits", 1) or 1))
            return data, None
        body = r.text[:300]
        if r.status_code in (401, 402, 403) or (r.status_code == 400 and "credit" in body.lower()):
            pool.kill(kid)  # clé invalide ou à court de crédits -> on passe à la suivante
            last_err = f"key {kid} out: {r.status_code} {body}"
            continue
        if r.status_code == 429 or r.status_code >= 500:
            last_err = f"{r.status_code} {body}"
            time.sleep(3 * (attempt + 1))
            continue
        return None, f"{r.status_code} {body}"  # 400 etc. : requête refusée, inutile de réessayer
    return None, last_err or "unknown"


def process_row(pool, idx, row):
    query = build_query(row)
    try:
        data, err = call_serper(pool, query)
    except AllKeysExhausted:
        return idx, None, None  # non traité : sera repris au prochain run
    if data is None:
        return idx, None, {"siren": row["siren"], "row": idx, "query": query, "error": err}
    rec = {
        "siren": row["siren"],
        "row": idx,
        "query": query,
        "source": row,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_places": len(data.get("places", [])),
        "places": data.get("places", []),
    }
    return idx, rec, None


# --------------------------------------------------------------------------- git
def git_push(msg):
    if not DO_GIT:
        return
    run = lambda *a: subprocess.run(a, cwd=ROOT, check=True)
    run("git", "config", "user.name", "github-actions[bot]")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("git", "add", "data/places.jsonl", "data/errors.jsonl", "data/progress.json")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return
    run("git", "commit", "-m", msg)
    for _ in range(4):
        subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=ROOT)
        if subprocess.run(["git", "push"], cwd=ROOT).returncode == 0:
            return
        time.sleep(5)
    sys.exit("git push a échoué")


# --------------------------------------------------------------------------- main
def main():
    rows = load_rows()
    total = len(rows)
    progress = load_progress()
    pool = KeyPool(load_keys(), progress.get("keys", {}))
    OUT_PATH.touch()
    ERR_PATH.touch()

    if RETRY_ERRORS:
        errs = [json.loads(l) for l in ERR_PATH.read_text().splitlines() if l.strip()]
        todo = sorted({e["row"] for e in errs})
        ERR_PATH.write_text("")
        print(f"Reprise de {len(todo)} lignes en erreur")
    else:
        if progress["done"]:
            print("Tout est déjà traité.")
            return
        start = progress["next"]
        end = min(total, start + MAX_ROWS) if MAX_ROWS else total
        todo = list(range(start, end))

    print(f"{total} agences | {len(todo)} à traiter | crédits restants ≈ {pool.remaining()} | {len(pool.keys)} clés")
    t0 = time.time()
    since_push = 0
    stopped = None

    for c in range(0, len(todo), 100):
        chunk = todo[c : c + 100]
        if time.time() - t0 > MAX_SECONDS:
            stopped = "limite de temps"
            break
        if pool.remaining() <= 0:
            stopped = "crédits épuisés"
            break
        with ThreadPoolExecutor(WORKERS) as ex:
            results = list(ex.map(lambda i: process_row(pool, i, rows[i]), chunk))

        # on ne garde que le préfixe traité (les lignes sans clé dispo seront reprises)
        cut = next((n for n, (_, rec, err) in enumerate(results) if rec is None and err is None), len(results))
        if cut < len(results):
            stopped = "crédits épuisés"
        results, chunk = results[:cut], chunk[:cut]
        with open(OUT_PATH, "a", encoding="utf-8") as fo, open(ERR_PATH, "a", encoding="utf-8") as fe:
            for idx, rec, err in results:
                if rec:
                    fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
                else:
                    fe.write(json.dumps(err, ensure_ascii=False) + "\n")
        if not chunk:
            break
        if not RETRY_ERRORS:
            progress["next"] = chunk[-1] + 1
        progress["keys"] = pool.used
        save_progress(progress)
        since_push += len(chunk)
        print(f"  {chunk[-1] + 1}/{total}  crédits restants ≈ {pool.remaining()}", flush=True)
        if since_push >= COMMIT_EVERY:
            git_push(f"data: {progress['next']}/{total} agences")
            since_push = 0

    if not RETRY_ERRORS and progress["next"] >= total:
        progress["done"] = True
    progress["keys"] = pool.used
    save_progress(progress)
    git_push(f"data: {progress['next']}/{total} agences" + (f" ({stopped})" if stopped else ""))
    print("Terminé" if progress["done"] else f"Arrêt : {stopped or 'MAX_ROWS atteint'}")


if __name__ == "__main__":
    main()
