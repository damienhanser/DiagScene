# SPDX-License-Identifier: GPL-3.0-or-later
"""Lecture des fichiers de configuration (un fichier TOML par thématique)."""

import glob
import hashlib
import os
import tomllib

DOSSIER = os.path.join(os.path.dirname(__file__), "configs")
# Version de cours : une grille unique, intégrée et non modifiable depuis le panneau.
# Présente seulement dans les paquets construits pour un cours (jamais dans la version publique).
VERROU = os.path.join(DOSSIER, "verrou.toml")


def verrouille():
    return os.path.isfile(VERROU)


def charger(chemin):
    with open(chemin, "rb") as f:
        brut = f.read()
    data = tomllib.loads(brut.decode("utf-8"))
    meta = data.setdefault("meta", {})
    meta.setdefault("nom", os.path.splitext(os.path.basename(chemin))[0])
    meta.setdefault("version", "")
    meta.setdefault("ordre", 50)
    meta["fichier"] = chemin
    meta["sha"] = hashlib.sha256(brut).hexdigest()[:12]  # empreinte : repère un fichier modifié
    data.setdefault("regles", {})
    return data


def lister():
    """Configurations livrées avec l'extension : [(chemin, meta)] triées par ordre puis nom."""
    if verrouille():
        return [(VERROU, charger(VERROU)["meta"])]
    res = []
    for chemin in glob.glob(os.path.join(DOSSIER, "*.toml")):
        try:
            meta = charger(chemin)["meta"]
        except Exception as e:  # fichier mal écrit : on le montre quand même
            meta = {"nom": os.path.basename(chemin) + " (illisible)", "ordre": 99, "erreur": str(e)}
        res.append((chemin, meta))
    res.sort(key=lambda x: (x[1].get("ordre", 50), x[1].get("nom", "")))
    return res
