"""Retire d'agences_uniques.csv les agences qui ont une enseigne (réseaux).

Usage : python retirer_reseaux.py                  (retire toute société avec une enseigne)
        python retirer_reseaux.py --min 2          (seulement les enseignes sur 2 sociétés ou plus
                                                    + les réseaux de reseaux.txt)
        python retirer_reseaux.py --avec-nom       (retire aussi celles dont le NOM contient un
                                                    réseau de reseaux.txt : plus bruyant)

agences_uniques.csv n'est PAS modifié. Sorties :
  agences_sans_reseau.csv        la liste gardée
  agences_reseau_retirees.csv    les sociétés retirées, avec la raison (à vérifier)

Les sociétés dont l'enseigne est [Non-Diffusible] sont aussi retirées.
"""
import csv
import re
import sys
import unicodedata
from collections import Counter


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte or "")
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", texte.lower()).strip()


def enseignes_de(ligne):
    """Enseignes réelles de la société (sans [Non-Diffusible])."""
    out = []
    for e in (ligne.get("enseigne") or "").split(" | "):
        e = e.strip()
        if e and "non-diffusible" not in e.lower():
            out.append(e)
    return out


def main():
    args = sys.argv[1:]
    mini = 1
    if "--min" in args:
        i = args.index("--min")
        mini = int(args[i + 1])
        del args[i:i + 2]
    avec_nom = "--avec-nom" in args
    args = [a for a in args if not a.startswith("--")]
    entree = args[0] if args else "agences_uniques.csv"

    reseaux = []
    with open("reseaux.txt", encoding="utf-8") as f:
        for l in f:
            nom, cle = l.strip(), normaliser(l)
            if cle:
                reseaux.append((nom, cle))

    with open(entree, newline="", encoding="utf-8") as f:
        lecteur = csv.DictReader(f)
        champs = lecteur.fieldnames
        lignes = list(lecteur)

    freq = Counter()   # nb de sociétés par enseigne normalisée
    for l in lignes:
        for cle in {normaliser(e) for e in enseignes_de(l)}:
            freq[cle] += 1

    gardees, retirees = [], []
    raisons = Counter()
    nom_non_retire = 0
    for l in lignes:
        raison = ""
        if "non-diffusible" in (l.get("enseigne") or "").lower():
            raison = "enseigne masquée : [Non-Diffusible]"
        for e in ([] if raison else enseignes_de(l)):
            e_n = " " + normaliser(e) + " "
            e_c = e_n.replace(" ", "")
            connu = next((n for n, c in reseaux
                          if f" {c} " in e_n or e_c == c.replace(" ", "")), None)
            if connu:
                raison = f"enseigne réseau connu : {connu}"
                break
            if freq[normaliser(e)] >= mini:
                raison = f"enseigne : {e}"
                break
        nom_n = " " + normaliser(l.get("nom")) + " "
        reseau_nom = next((n for n, c in reseaux if f" {c} " in nom_n), None)
        if not raison and reseau_nom:
            if avec_nom:
                raison = f"nom contient : {reseau_nom}"
            else:
                nom_non_retire += 1
        if raison:
            retirees.append((l, raison))
            raisons[raison.split(" :")[0]] += 1
        else:
            gardees.append(l)

    with open("agences_sans_reseau.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=champs)
        w.writeheader()
        w.writerows(gardees)
    with open("agences_reseau_retirees.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=champs + ["raison"])
        w.writeheader()
        for l, r in retirees:
            w.writerow({**l, "raison": r})

    print(f"{len(lignes)} sociétés lues")
    print(f"{len(retirees)} retirées : " +
          ", ".join(f"{n} {k}" for k, n in raisons.items()) if retirees else "0 retirée")
    print(f"{len(gardees)} gardées -> agences_sans_reseaux.csv")
    if not avec_nom:
        print(f"{nom_non_retire} gardées alors que leur nom contient un réseau "
              f"connu (relance avec --avec-nom pour les retirer)")
    print("Détail des retirées -> agences_reseau_retirees.csv")


if __name__ == "__main__":
    main()
