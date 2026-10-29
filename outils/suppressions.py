#!/usr/bin/env python3
"""Suppression complète de ce qui est effacé dans Jellyfin (cron toutes les 5 min).

- Films (bibliothèque Films, vue à travers la vitrine de liens) : la vitrine signale les liens effacés depuis Jellyfin.
- Films du dossier privé (DOSSIER_PRIVE) et épisodes de séries : Jellyfin efface directement le vrai fichier.
Pour un film : fichiers sur le disque, copie en partage dans Transmission, fiche Radarr et demande Seerr sont supprimés.
Pour des épisodes : ils ne sont plus suivis par Sonarr (pas de retéléchargement) ; si toute la série est effacée,
elle est supprimée comme un film (Sonarr, copies en partage, Seerr).
Garde-fous : rien si le disque des médias est absent, ni si trop d'éléments disparaissent d'un coup (alerte à la place).
--test : vérifications sans réseau.
"""
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.expanduser('~/outils'))
import portail  # noqa: E402  (arr, arr_envoyer, transmission, seerr, notifier)

OUTILS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('vitrine', OUTILS / 'vitrine.py')
vitrine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vitrine)

MAX_FILMS, MAX_EPISODES = 5, 40  # au-delà, c'est sûrement un incident (dossier renommé…), pas des suppressions


def alerte(texte):
    portail.notifier('Suppression bloquée', texte)


def oublier_torrents(hashes):
    for h in hashes:
        try:
            portail.transmission('torrent-remove', ids=[h], **{'delete-local-data': True})
        except Exception:
            pass  # déjà retiré


def oublier_seerr(type_, tmdb):
    try:
        info = portail.seerr('GET', f'{type_}/{tmdb}').get('mediaInfo') or {}
        if info.get('id'):
            portail.seerr('DELETE', f"media/{info['id']}")
    except Exception:
        pass  # Seerr ne connaît pas ce titre


def hashes_historique(app, chemin):
    return {h['downloadId'].lower() for h in portail.arr(app, 'GET', chemin)
            if h.get('eventType') == 'grabbed' and h.get('downloadId')}


def supprimer_film(m):
    hashes = hashes_historique('radarr', f"history/movie?movieId={m['id']}")
    portail.arr('radarr', 'DELETE', f"movie/{m['id']}?deleteFiles=true&addImportExclusion=false")
    oublier_torrents(hashes)
    oublier_seerr('movie', m['tmdbId'])
    return m['title']


def supprimer_serie(s):
    hashes = hashes_historique('sonarr', f"history/series?seriesId={s['id']}")
    portail.arr('sonarr', 'DELETE', f"series/{s['id']}?deleteFiles=true&addImportListExclusion=false")
    oublier_torrents(hashes)
    oublier_seerr('tv', s['tmdbId'])
    return s['title']


def dossiers(rels):
    """Regroupe les fichiers effacés par film (premier dossier sous Film/, ou le fichier s'il est seul)."""
    groupes = {}
    for r in rels:
        groupes.setdefault(r.split('/')[0], []).append(r)
    return groupes


def main():
    os.umask(0o002)  # dossiers de la vitrine modifiables par Jellyfin (groupe jellyfin)
    films_dir = vitrine.BRUT / 'Film'
    if not films_dir.is_dir():
        return  # disque débranché : surtout ne rien conclure
    vitrine.synchroniser(vitrine.BRUT, vitrine.VITRINE, vitrine.ETAT)
    etat = vitrine.lire_etat(vitrine.ETAT)
    supprimes = []

    # 1. Films effacés dans Jellyfin à travers la vitrine
    groupes = dossiers(etat.get('a_supprimer', []))
    if len(groupes) > MAX_FILMS:
        alerte(f'{len(groupes)} films semblent effacés d\'un coup : rien n\'a été supprimé, ils réapparaissent dans Jellyfin.')
        etat['a_supprimer'] = []
    else:
        films = {m.get('path'): m for m in portail.arr('radarr', 'GET', 'movie')}
        for dossier, rels in groupes.items():
            m = films.get(str(films_dir / dossier))
            if m:
                supprimes.append(supprimer_film(m))
            else:  # film copié à la main (pas dans Radarr) : on efface seulement ses fichiers
                for r in rels:
                    (films_dir / r).unlink(missing_ok=True)
                d = films_dir / dossier
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
                supprimes.append(dossier)
        etat['a_supprimer'] = []
    vitrine.ETAT.write_text(json.dumps(etat, ensure_ascii=False))

    # 2. Films dont Jellyfin a effacé le vrai fichier (dossier privé)
    disparus = [m for m in portail.arr('radarr', 'GET', 'movie')
                if m.get('hasFile') and not Path(m['movieFile']['path']).exists()]
    if len(disparus) > MAX_FILMS:
        alerte(f'{len(disparus)} films ont perdu leur fichier d\'un coup : rien n\'a été supprimé, vérifie le disque des médias.')
    else:
        supprimes += [supprimer_film(m) for m in disparus]

    # 3. Épisodes effacés dans Jellyfin
    series = {s['id']: s for s in portail.arr('sonarr', 'GET', 'series')}
    perdus = []
    for sid in series:
        perdus += [f for f in portail.arr('sonarr', 'GET', f'episodefile?seriesId={sid}') if not Path(f['path']).exists()]
    if len(perdus) > MAX_EPISODES:
        alerte(f'{len(perdus)} épisodes ont perdu leur fichier d\'un coup : rien n\'a été supprimé, vérifie le disque des médias.')
        perdus = []
    touchees = set()
    for f in perdus:
        episodes = [e['id'] for e in portail.arr('sonarr', 'GET', f"episode?episodeFileId={f['id']}")]
        if episodes:
            portail.arr_envoyer('sonarr', 'PUT', 'episode/monitor', {'episodeIds': episodes, 'monitored': False})
        portail.arr('sonarr', 'DELETE', f"episodefile/{f['id']}")
        touchees.add(f['seriesId'])
    for sid in touchees:
        restants = portail.arr('sonarr', 'GET', f'episodefile?seriesId={sid}')
        if restants:
            supprimes.append(f"{series[sid]['title']} (épisodes effacés, plus suivis)")
        else:
            supprimes.append(supprimer_serie(series[sid]))

    if supprimes:
        portail.notifier('Supprimé pour de bon', '\n'.join('🗑 ' + s for s in supprimes) +
                         '\n\nFichiers, copies en partage, Radarr / Sonarr et demandes Seerr nettoyés.')
        print('supprimés :', supprimes)


def test():
    assert dossiers(['A (2020)/a.mkv', 'A (2020)/a.srt', 'seul.mkv']) == {'A (2020)': ['A (2020)/a.mkv', 'A (2020)/a.srt'],
                                                                        'seul.mkv': ['seul.mkv']}
    print('test OK')


if __name__ == '__main__':
    test() if sys.argv[1:] == ['--test'] else main()
