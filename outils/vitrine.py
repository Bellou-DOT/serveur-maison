#!/usr/bin/env python3
"""Vitrine Jellyfin des médias (appelée par suppressions.py toutes les 5 min, au démarrage et après chaque import Radarr).

Radarr et Sonarr rangent dans MEDIAS_BRUT ; Jellyfin lit MEDIAS (réglages dans /etc/serveur-maison.conf) :
  MEDIAS/Séries -> lien vers MEDIAS_BRUT/Séries (idem DOSSIER_PRIVE, bibliothèque réservée à un compte)
  MEDIAS/Film/  -> un lien par fichier, sauf les vidéos presque vides (torrent inachevé :
                    la taille est réservée mais le contenu n'est que des zéros).
Un lien publié qui disparaît a été supprimé depuis Jellyfin : il n'est pas recréé et part dans « a_supprimer »
de l'état (~/.surveillance/vitrine.json), que suppressions.py traite (film, copie en partage, Radarr, Seerr).
--test : vérification sur un dossier temporaire.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

from config import C

BRUT, VITRINE = Path(C['MEDIAS_BRUT']), Path(C['MEDIAS'])
LIENS = ('Séries', *filter(None, [C['DOSSIER_PRIVE']]))
ETAT = Path.home() / '.surveillance' / 'vitrine.json'
VIDEOS = ('.mkv', '.mp4', '.avi', '.m4v', '.mov', '.ts', '.wmv')


def presque_vide(f, pas=64 << 20, bloc=1 << 18):
    """Vrai si au moins la moitié des blocs échantillonnés ne contiennent que des zéros."""
    vides = n = 0
    with open(f, 'rb') as h:
        for pos in range(0, f.stat().st_size, pas):
            h.seek(pos)
            b = h.read(bloc)
            n += 1
            vides += b.count(0) == len(b)
    return n > 0 and vides * 2 >= n


def lire_etat(etat):
    try:
        return json.loads(etat.read_text())
    except (OSError, ValueError, AttributeError):
        return {}


def synchroniser(brut, vitrine, etat=None, liens=LIENS):
    films = brut / 'Film'
    if not films.is_dir():  # disque absent : on ne touche à rien (Jellyfin garderait sinon une vitrine vide)
        return None
    e = lire_etat(etat)
    publies, pleins = set(e.get('publies', [])), e.get('pleins', {})
    a_supprimer = set(e.get('a_supprimer', []))
    (vitrine / 'Film').mkdir(parents=True, exist_ok=True)
    for nom in liens:
        if not (vitrine / nom).is_symlink():
            (vitrine / nom).symlink_to(brut / nom)
    voulus, masques = set(), []
    for racine, _, fichiers in os.walk(films):
        for nom in fichiers:
            source = Path(racine) / nom
            rel = source.relative_to(films)
            st = source.stat()
            signature = [st.st_size, int(st.st_mtime)]
            if nom.lower().endswith(VIDEOS) and pleins.get(str(rel)) != signature:  # déjà vérifié : pas de relecture
                if presque_vide(source):
                    masques.append(str(rel))
                    continue
                pleins[str(rel)] = signature
            lien = vitrine / 'Film' / rel
            if str(rel) in a_supprimer or (str(rel) in publies and not lien.is_symlink()):
                a_supprimer.add(str(rel))  # supprimé depuis Jellyfin : on ne le remet pas
                continue
            voulus.add(rel)
            if not lien.is_symlink():
                lien.parent.mkdir(parents=True, exist_ok=True)
                lien.symlink_to(source)
    for racine, dossiers, fichiers in os.walk(vitrine / 'Film', topdown=False):
        for nom in fichiers:
            lien = Path(racine) / nom
            if lien.is_symlink() and lien.relative_to(vitrine / 'Film') not in voulus:
                lien.unlink()
        for nom in dossiers:
            d = Path(racine) / nom
            if not any(d.iterdir()):
                d.rmdir()
    if etat:
        existants = {str(r) for r in voulus} | set(masques) | a_supprimer
        etat.write_text(json.dumps({'publies': sorted(str(r) for r in voulus), 'a_supprimer': sorted(a_supprimer),
                                    'pleins': {k: v for k, v in pleins.items() if k in existants}}, ensure_ascii=False))
    return masques


def test():
    with tempfile.TemporaryDirectory() as t:
        brut, vitrine = Path(t) / 'brut', Path(t) / 'vitrine'
        (brut / 'Film' / 'Saga').mkdir(parents=True)
        (brut / 'Séries').mkdir()
        (brut / 'Prive').mkdir()
        (brut / 'Film' / 'bon.mkv').write_bytes(b'\x1a' * 4096)
        (brut / 'Film' / 'vide.mkv').write_bytes(bytes(4096))
        (brut / 'Film' / 'Saga' / 'bon.srt').write_text('1')
        assert synchroniser(brut, vitrine, liens=('Séries', 'Prive')) == ['vide.mkv']
        assert sorted(str(p.relative_to(vitrine)) for p in vitrine.rglob('*') if p.is_symlink()) == \
            ['Prive', 'Film/Saga/bon.srt', 'Film/bon.mkv', 'Séries']
        (brut / 'Film' / 'bon.mkv').unlink()  # film retiré du disque : son lien disparaît
        synchroniser(brut, vitrine)
        assert not (vitrine / 'Film' / 'bon.mkv').is_symlink()
        assert synchroniser(Path(t) / 'absent', vitrine) is None
        # Suppression depuis Jellyfin : le lien disparaît, il n'est pas recréé et le film est signalé
        etat = Path(t) / 'etat.json'
        (brut / 'Film' / 'Film X').mkdir()
        (brut / 'Film' / 'Film X' / 'x.mkv').write_bytes(b'' * 4096)
        synchroniser(brut, vitrine, etat)
        (vitrine / 'Film' / 'Film X' / 'x.mkv').unlink()
        (vitrine / 'Film' / 'Film X').rmdir()
        synchroniser(brut, vitrine, etat)
        assert not (vitrine / 'Film' / 'Film X').exists() and json.loads(etat.read_text())['a_supprimer'] == ['Film X/x.mkv']
        assert (brut / 'Film' / 'Film X' / 'x.mkv').exists()  # le vrai fichier est supprimé par suppressions.py, pas ici
        # Un nouveau film (jamais publié) est bien publié
        (brut / 'Film' / 'Nouveau').mkdir()
        (brut / 'Film' / 'Nouveau' / 'n.mkv').write_bytes(b'' * 4096)
        synchroniser(brut, vitrine, etat)
        assert (vitrine / 'Film' / 'Nouveau' / 'n.mkv').is_symlink()
    print('test OK')


if __name__ == '__main__':
    if sys.argv[1:] == ['--test']:
        test()
    else:
        m = synchroniser(BRUT, VITRINE, ETAT)
        if m:
            print('Masqués (presque vides) :', *m, sep='\n  ')
