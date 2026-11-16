# SPDX-License-Identifier: GPL-3.0-or-later
"""Lecture des fichiers de configuration (un fichier TOML par thématique)."""

import glob
import os
import tomllib

DOSSIER = os.path.join(os.path.dirname(__file__), "configs")


def charger(chemin):
    with open(chemin, "rb") as f:
        data = tomllib.load(f)
    meta = data.setdefault("meta", {})
    meta.setdefault("nom", os.path.splitext(os.path.basename(chemin))[0])
    meta.setdefault("version", "")
    meta.setdefault("ordre", 50)
    meta["fichier"] = chemin
    data.setdefault("regles", {})
    return data


def lister():
    """Configurations livrées avec l'extension : [(chemin, meta)] triées par ordre puis nom."""
    res = []
    for chemin in glob.glob(os.path.join(DOSSIER, "*.toml")):
        try:
            meta = charger(chemin)["meta"]
        except Exception as e:  # fichier mal écrit : on le montre quand même
            meta = {"nom": os.path.basename(chemin) + " (illisible)", "ordre": 99, "erreur": str(e)}
        res.append((chemin, meta))
    res.sort(key=lambda x: (x[1].get("ordre", 50), x[1].get("nom", "")))
    return res
