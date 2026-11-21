# SPDX-License-Identifier: GPL-3.0-or-later
# DiagScene : pointe ce qui ne va pas dans un fichier Blender, sans rien corriger,
# et tient un journal de travail dans le fichier.

from . import journal, ui


def register():
    ui.register()
    journal.register()


def unregister():
    journal.unregister()
    ui.unregister()
