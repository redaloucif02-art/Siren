"""Enseignes + réseaux connus en UNE seule sortie : enseignes_reseaux.csv

Usage : python analyse_enseignes.py
        python analyse_enseignes.py entree.csv reseaux.txt

Pour chaque société de agences_uniques.csv :
  - si une de ses enseignes correspond à un réseau de reseaux.txt, elle est
    rangée sous le nom du réseau (« Century21 » et « Century 21 » = un seul) ;
  - sinon elle est rangée sous son enseigne telle quelle ;
  - si la société n'a pas d'enseigne mais que son NOM contient un réseau
    connu, elle est comptée à part dans la colonne via_nom (résultat plus
    bruyant : un nom de famille peut tomber sur une société sans rapport).

Sorties :
  societes_reseaux.csv  la LISTE des sociétés dont l'enseigne OU le nom contient
                        un réseau connu (une ligne par société, réseau et champ)
  enseignes_reseaux.csv le résumé, une ligne par réseau ou enseigne :
  nom | type (reseau_connu / hors_liste) | via_enseigne | via_nom | total
Triée par total décroissant. Les lignes « hors_liste » avec beaucoup de
sociétés sont probablement des réseaux à ajouter dans reseaux.txt.
"""
import csv
import re
import sys
import unicodedata
from collections import Counter, defaultdict


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte or "")
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", texte.lower()).strip()


def main():
    entree = sys.argv[1] if len(sys.argv) > 1 else "agences_uniques.csv"
    fichier_reseaux = sys.argv[2] if len(sys.argv) > 2 else "reseaux.txt"

    reseaux, vus = [], set()
    with open(fichier_reseaux, encoding="utf-8") as f:
        for ligne in f:
            nom = ligne.strip()
            cle = normaliser(nom)
            if cle and cle not in vus:
                vus.add(cle)
                reseaux.append((nom, cle))

    via_enseigne, via_nom = Counter(), Counter()
    formes = defaultdict(Counter)       # enseigne hors liste : écritures vues
    connus = {nom for nom, _ in reseaux}
    total = avec_enseigne = 0
    detail = open("societes_reseaux.csv", "w", newline="", encoding="utf-8")
    wd = csv.writer(detail)
    wd.writerow(["siren", "nom", "enseigne", "ville", "reseau", "champ"])

    with open(entree, newline="", encoding="utf-8") as f:
        for ligne in csv.DictReader(f):
            total += 1
            groupes = set()
            ecrits = set()   # évite d'écrire deux fois le même (réseau, champ)
            for e in (ligne.get("enseigne") or "").split(" | "):
                e = e.strip()
                if not e:
                    continue
                e_n = " " + normaliser(e) + " "
                e_c = e_n.replace(" ", "")
                trouve = [nom for nom, cle in reseaux
                          if f" {cle} " in e_n or e_c == cle.replace(" ", "")]
                if trouve:
                    groupes.update(trouve)
                    for r in trouve:
                        if (r, "enseigne") in ecrits:
                            continue
                        ecrits.add((r, "enseigne"))
                        wd.writerow([ligne.get("siren"), ligne.get("nom"),
                                     ligne.get("enseigne"), ligne.get("ville"),
                                     r, "enseigne"])
                else:
                    cle = normaliser(e)
                    if cle:
                        formes[cle][e] += 1
                        groupes.add("\0" + cle)   # marque « hors liste »
            if groupes:
                avec_enseigne += 1
            for g in groupes:
                via_enseigne[g] += 1

            nom_n = " " + normaliser(ligne.get("nom")) + " "
            for nom, cle in reseaux:
                if f" {cle} " in nom_n:
                    wd.writerow([ligne.get("siren"), ligne.get("nom"),
                                 ligne.get("enseigne"), ligne.get("ville"),
                                 nom, "nom"])
                    if nom not in groupes:   # pas de double compte dans le résumé
                        via_nom[nom] += 1

    detail.close()

    lignes = []
    for g in set(via_enseigne) | set(via_nom):
        if g.startswith("\0"):
            affichage = formes[g[1:]].most_common(1)[0][0]
            lignes.append((affichage, "hors_liste", via_enseigne[g], 0))
        else:
            lignes.append((g, "reseau_connu", via_enseigne[g], via_nom[g]))
    for nom, _ in reseaux:               # réseaux de la liste jamais trouvés
        if nom not in via_enseigne and nom not in via_nom:
            lignes.append((nom, "reseau_connu", 0, 0))
    lignes.sort(key=lambda x: (-(x[2] + x[3]), x[0].lower()))

    with open("enseignes_reseaux.csv", "w", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        w.writerow(["nom", "type", "via_enseigne", "via_nom", "total"])
        for nom, type_, a, b in lignes:
            w.writerow([nom, type_, a, b, a + b])

    trouves = [l for l in lignes if l[1] == "reseau_connu" and l[2] + l[3] > 0]
    hors = [l for l in lignes if l[1] == "hors_liste"]
    hors_gros = [l for l in hors if l[2] >= 5]
    print(f"{total} sociétés, dont {avec_enseigne} avec une enseigne")
    print(f"{len(trouves)} réseaux de ta liste trouvés sur {len(reseaux)}")
    print(f"{len(hors)} enseignes hors liste "
          f"({len(hors_gros)} sur 5 sociétés ou plus : réseaux possibles à ajouter)")
    print("Top 15 :")
    for nom, type_, a, b in lignes[:15]:
        marque = "" if type_ == "reseau_connu" else "  [hors liste]"
        print(f"  {nom} : {a} enseigne + {b} nom{marque}")
    print("Hors liste les plus fréquentes :")
    for nom, _, a, _ in hors[:10]:
        print(f"  {nom} : {a}")
    print("Écrit dans enseignes_reseaux.csv (résumé) et societes_reseaux.csv (liste)")


if __name__ == "__main__":
    main()
