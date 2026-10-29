"""Réglages communs (/etc/serveur-maison.conf, format CLE="valeur" partagé avec bash et systemd).

SERVEUR_MAISON_CONF remplace le chemin (tests) ; à défaut, config.exemple du dépôt sert de repli.
"""
import os
import re
from pathlib import Path

CHEMINS = [os.environ.get('SERVEUR_MAISON_CONF', ''), '/etc/serveur-maison.conf',
           Path(__file__).resolve().parent.parent / 'config.exemple']


def lire(texte):
    return {m[1]: m[2] for m in re.finditer(r'^([A-Z_]+)="([^"]*)"', texte, re.M)}


C = lire(next(Path(p).read_text() for p in CHEMINS if p and Path(p).is_file()))


def oui(cle):
    return C.get(cle, 'non') == 'oui'


if __name__ == '__main__':
    assert lire('A="b c"  # note\n#B="x"\nC=""') == {'A': 'b c', 'C': ''}
    print('test OK', sorted(C)[:3])
