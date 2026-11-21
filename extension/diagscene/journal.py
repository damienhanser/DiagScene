# SPDX-License-Identifier: GPL-3.0-or-later
"""Journal de travail : une ligne par enregistrement et par analyse, écrite dans le fichier .blend.

Le journal permet de retracer l'histoire d'un fichier : quand il a été travaillé, sur quelle
machine (empreinte anonyme), et comment il a grossi. Il aide à retrouver le moment où un
problème est apparu, et sert d'indice d'authenticité dans un cadre d'évaluation.

Stockage : un bloc de texte « .diagscene_journal » (une ligne JSON par événement).
Chaque ligne porte l'empreinte (hash) de la précédente : une ligne modifiée ou supprimée
au milieu du journal se repère. Ce n'est pas une preuve : un utilisateur averti peut
réécrire tout le journal. C'est un indice, à croiser avec les rapports et l'oral.
"""

import datetime
import hashlib
import json
import os
import platform
import uuid

import bpy

NOM_TEXTE = ".diagscene_journal"
_SEL = "diagscene-journal-v1"
CHAMPS_HASH = ("n", "date", "evt", "fichier", "blender", "objets", "faces", "materiaux", "images",
               "poids_ko", "machine", "thematique", "config_sha", "bloquants", "avertissements",
               "conseils", "prec")


def empreinte_machine():
    """Empreinte anonyme et stable de la machine : ni nom d'utilisateur, ni nom d'ordinateur."""
    brut = f"{_SEL}|{uuid.getnode():012x}|{platform.system()}|{platform.machine()}"
    return hashlib.sha256(brut.encode()).hexdigest()[:10]


def _hash(entree):
    canon = json.dumps({k: entree.get(k) for k in CHAMPS_HASH}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _texte(creer=False):
    t = bpy.data.texts.get(NOM_TEXTE)
    if t is None and creer:
        t = bpy.data.texts.new(NOM_TEXTE)
        t.use_fake_user = True
    return t


def lire():
    """Liste des entrées (dict). Les lignes illisibles sont rendues avec la clé « illisible »."""
    t = _texte()
    if t is None:
        return []
    res = []
    for ligne in t.as_string().splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            res.append(json.loads(ligne))
        except ValueError:
            res.append({"illisible": ligne[:80]})
    return res


def mesures():
    """Mesures légères de la scène (données de base, sans évaluer les modificateurs)."""
    objets = [o for o in bpy.data.objects if o.library is None]
    faces = 0
    for o in objets:
        if o.type == "MESH" and o.data is not None:
            faces += len(o.data.polygons)
    poids = 0
    if bpy.data.filepath and os.path.isfile(bpy.data.filepath):
        poids = os.path.getsize(bpy.data.filepath) // 1024
    return {
        "objets": len(objets),
        "faces": faces,
        "materiaux": sum(1 for m in bpy.data.materials if m.library is None and m.users > 0),
        "images": sum(1 for i in bpy.data.images if i.library is None and i.users > 0),
        "poids_ko": poids,
    }


def ajouter(evt, **extra):
    """Ajoute une ligne au journal. Rien n'est écrit si le journal est désactivé dans les préférences."""
    if not actif():
        return None
    entrees = lire()
    valides = [e for e in entrees if "hash" in e]
    prec = valides[-1]["hash"] if valides else ""
    e = {
        "n": (valides[-1].get("n", 0) + 1) if valides else 1,
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evt": evt,
        "fichier": os.path.basename(bpy.data.filepath) or "(non enregistré)",
        "blender": bpy.app.version_string,
        "machine": empreinte_machine(),
        "prec": prec,
    }
    e.update(mesures())
    for k in ("thematique", "config_sha", "bloquants", "avertissements", "conseils"):
        e[k] = extra.get(k)
    e["hash"] = _hash(e)
    t = _texte(creer=True)
    contenu = t.as_string()
    if contenu and not contenu.endswith("\n"):
        contenu += "\n"
    t.from_string(contenu + json.dumps(e, ensure_ascii=False) + "\n")
    return e


def verifier(entrees=None):
    """Contrôle de la chaîne. Renvoie (ok, liste de problèmes lisibles)."""
    entrees = lire() if entrees is None else entrees
    problemes = []
    prec = ""
    attendu = 1
    for i, e in enumerate(entrees, 1):
        if "illisible" in e:
            problemes.append(f"ligne {i} illisible")
            continue
        if e.get("hash") != _hash(e):
            problemes.append(f"ligne {e.get('n', i)} modifiée après coup")
        if e.get("prec", "") != prec:
            problemes.append(f"chaîne rompue avant la ligne {e.get('n', i)} (ligne supprimée ou insérée)")
        if e.get("n") != attendu:
            problemes.append(f"numérotation : ligne {e.get('n')} au lieu de {attendu}")
        prec = e.get("hash", "")
        attendu = (e.get("n") or attendu) + 1
    return (not problemes), problemes


def synthese(entrees=None):
    """Chiffres clés du journal pour le rapport et les règles."""
    entrees = [e for e in (lire() if entrees is None else entrees) if "illisible" not in e]
    if not entrees:
        return None
    jours = sorted({e["date"][:10] for e in entrees if e.get("date")})
    machines = []
    for e in entrees:
        m = e.get("machine")
        if m and m not in machines:
            machines.append(m)
    enreg = [e for e in entrees if e.get("evt") == "enregistrement"]
    versions = sorted({e.get("blender", "") for e in entrees if e.get("blender")})
    ok, problemes = verifier(entrees)
    return {
        "nb": len(entrees),
        "enregistrements": len(enreg),
        "analyses": sum(1 for e in entrees if e.get("evt") == "analyse"),
        "jours": jours,
        "machines": machines,
        "machine_courante": empreinte_machine(),
        "versions": versions,
        "premiere": entrees[0],
        "derniere": entrees[-1],
        "integre": ok,
        "problemes": problemes,
    }


# --- Préférence et gestionnaire d'enregistrement ----------------------------

def actif():
    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
        return bool(prefs.journal_actif)
    except (KeyError, AttributeError):
        return True


@bpy.app.handlers.persistent
def _avant_enregistrement(*_args):
    try:
        ajouter("enregistrement")
    except Exception as e:  # le journal ne doit jamais empêcher d'enregistrer
        print(f"DiagScene : journal non mis à jour ({type(e).__name__} : {e})")


def register():
    if _avant_enregistrement not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_avant_enregistrement)


def unregister():
    if _avant_enregistrement in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_avant_enregistrement)
