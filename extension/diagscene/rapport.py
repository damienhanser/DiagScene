# SPDX-License-Identifier: GPL-3.0-or-later
"""Export du rapport en Markdown, avec un en-tête YAML qui permet de suivre l'évolution."""

import datetime
import glob
import json
import os
import re

import bpy

from . import journal
from .regles import NIVEAUX

TITRES = {"bloquant": "Bloquants", "avertissement": "Avertissements", "conseil": "Conseils", "acquis": "Acquis"}
_ENTETE = re.compile(r"^---\n(.*?)\n---", re.S)


def _base(stem):
    """Nom du fichier sans le suffixe de version : les rapports de toutes les versions se suivent."""
    return re.sub(r"_v\d+$", "", stem)


def _cible_lisible(a):
    if a["cible_type"] == "NODE":
        return " › ".join(a["cible"].split("\t"))
    if a["cible_type"] in ("OBJETS", "MATERIAUX", "SCENE"):
        return ""
    return a["cible"]


def _historique(dossier, base):
    lignes = []
    for f in glob.glob(os.path.join(dossier, "*.md")):
        try:
            with open(f, encoding="utf-8") as fh:
                m = _ENTETE.match(fh.read(2000))
        except OSError:
            continue
        if not m:
            continue
        champs = {}
        for ligne in m.group(1).splitlines():
            if ":" in ligne:
                k, v = ligne.split(":", 1)
                champs[k.strip()] = v.strip().strip('"')
        if champs.get("type") == "rapport-diagnostic" and champs.get("fichier_base") == base:
            lignes.append(champs)
    lignes.sort(key=lambda c: c.get("date", ""))
    return lignes


def _section_journal(max_lignes=20):
    entrees = journal.lire()
    s = journal.synthese(entrees)
    l = ["## Journal de travail (historique du fichier)", ""]
    if s is None:
        etat = "désactivé dans les préférences" if not journal.actif() else "vide (il commence au prochain enregistrement)"
        return l + [f"Journal {etat}.", ""]
    l += [f"- **{s['enregistrements']} enregistrement(s)** et {s['analyses']} analyse(s), "
          f"du {s['premiere']['date'][:16]} au {s['derniere']['date'][:16]}, sur {len(s['jours'])} jour(s).",
          f"- **Machines** (empreintes anonymes) : {', '.join(s['machines'])} ; machine actuelle : {s['machine_courante']}.",
          f"- **Versions de Blender** : {', '.join(s['versions'])}.",
          f"- **Intégrité** : " + ("chaîne intacte." if s["integre"] else "journal altéré : " + " ; ".join(s["problemes"]) + "."),
          f"- **Dernière empreinte** : `{s['derniere'].get('hash', '')}` (à comparer avec le rapport suivant).", ""]
    l += ["| N° | Date | Événement | Faces | Objets | Matériaux | Images | Poids (Ko) | Machine | Thématique |",
          "| ---: | :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- | :--- |"]
    lisibles = [e for e in entrees if "illisible" not in e]
    if len(lisibles) > max_lignes:
        l.append(f"| … | | {len(lisibles) - max_lignes} ligne(s) plus ancienne(s) | | | | | | | |")
    for e in lisibles[-max_lignes:]:
        th = (e.get("thematique") or "").replace("|", "/")
        if e.get("config_sha"):
            th += f" ({e['config_sha']})"
        l.append(f"| {e.get('n', '')} | {e.get('date', '')[:16]} | {e.get('evt', '')} | {e.get('faces', '')} "
                 f"| {e.get('objets', '')} | {e.get('materiaux', '')} | {e.get('images', '')} | {e.get('poids_ko', '')} "
                 f"| {e.get('machine', '')} | {th} |")
    l += ["", "*Poids : taille du fichier sur le disque au moment de l'événement, avant l'enregistrement en cours.*", ""]
    return l


def ecrire(alertes, config, version_extension):
    chemin = bpy.data.filepath
    if not chemin:
        raise ValueError("Enregistrez d'abord le fichier .blend : le rapport est rangé à côté de lui.")
    dossier = os.path.join(os.path.dirname(chemin), "rapports")
    os.makedirs(dossier, exist_ok=True)
    stem = os.path.splitext(os.path.basename(chemin))[0]
    base = _base(stem)
    maintenant = datetime.datetime.now()
    compte = {n: sum(1 for a in alertes if a["niveau"] == n) for n in NIVEAUX}
    meta = config.get("meta", {})
    precedents = _historique(dossier, base)

    q = json.dumps
    l = ["---",
         "type: rapport-diagnostic",
         f"fichier: {q(os.path.basename(chemin), ensure_ascii=False)}",
         f"fichier_base: {q(base, ensure_ascii=False)}",
         f"date: {maintenant:%Y-%m-%d %H:%M}",
         f"blender: {bpy.app.version_string}",
         f"extension: {version_extension}",
         f"thematique: {q(meta.get('nom', ''), ensure_ascii=False)}",
         f"thematique_version: {q(str(meta.get('version', '')), ensure_ascii=False)}",
         f"thematique_empreinte: {q(meta.get('sha', ''))}",
         f"bloquants: {compte['bloquant']}",
         f"avertissements: {compte['avertissement']}",
         f"conseils: {compte['conseil']}",
         f"acquis: {compte['acquis']}",
         "---", "",
         f"# DiagScene — {os.path.basename(chemin)}", "",
         f"{maintenant:%d/%m/%Y à %H:%M} · thématique : {meta.get('nom', '')} · Blender {bpy.app.version_string}", "",
         f"**{compte['bloquant']} bloquant(s), {compte['avertissement']} avertissement(s), {compte['conseil']} conseil(s), "
         f"{compte['acquis']} acquis.**", ""]
    if not [a for a in alertes if a["niveau"] != "acquis"]:
        l += ["Aucun problème détecté pour cette thématique.", ""]
    for niveau in NIVEAUX:
        lot = [a for a in alertes if a["niveau"] == niveau]
        if not lot:
            continue
        l += [f"## {TITRES[niveau]}", ""]
        titre_courant = None
        for a in lot:
            if a["titre"] != titre_courant:
                if titre_courant is not None:
                    l.append("")
                titre_courant = a["titre"]
                l += [f"### {a['titre']}", ""]
                if a["pourquoi"] and niveau != "acquis":
                    l.append(f"*Pourquoi :* {a['pourquoi']}  ")
                if a["comment"] and niveau != "acquis":
                    l.append(f"*Comment corriger :* {a['comment']}  ")
                if a["lien"]:
                    l.append(f"*En savoir plus :* {a['lien']}")
                l.append("")
            cible = _cible_lisible(a)
            l.append(f"- **{cible}** : {a['detail']}" if cible else f"- {a['detail']}")
        l.append("")

    l += _section_journal()
    l += ["## Évolution", "",
          "| Date | Fichier | Bloquants | Avertissements | Conseils | Acquis |",
          "| :--- | :--- | ---: | ---: | ---: | ---: |"]
    for c in precedents:
        l.append(f"| {c.get('date', '')} | {c.get('fichier', '').replace('|', '/')} | {c.get('bloquants', '')} "
                 f"| {c.get('avertissements', '')} | {c.get('conseils', '')} | {c.get('acquis', '')} |")
    l.append(f"| **{maintenant:%Y-%m-%d %H:%M}** | **{os.path.basename(chemin).replace('|', '/')}** "
             f"| **{compte['bloquant']}** | **{compte['avertissement']}** | **{compte['conseil']}** | **{compte['acquis']}** |")
    l += ["", f"*Rapport produit par l'extension DiagScene {version_extension}. "
              f"Elle ne corrige rien ; elle ajoute seulement une ligne au journal de travail du fichier.*", ""]

    nom = f"{maintenant:%Y-%m-%d_%H%M}_{stem}.md"
    sortie = os.path.join(dossier, nom)
    with open(sortie, "w", encoding="utf-8") as f:
        f.write("\n".join(l))
    return sortie
