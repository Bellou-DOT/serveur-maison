#!/usr/bin/env python3
"""Films et séries sortis il y a plus d'un an (cron toutes les heures).

Leurs profils « 1080p minimum » passent sur un profil « ancien » qui accepte aussi en dessous du 1080p
(la meilleure version trouvée reste prise d'abord). Pas de mise à niveau ensuite : ce qui est pris est gardé.
Les profils anciens sont créés au premier passage à partir des profils normaux.
--test : vérifications sans réseau.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.expanduser('~/outils'))
import portail  # noqa: E402  (arr, arr_envoyer : mêmes clés d'API que le portail)

# profil normal -> profil ancien (créé s'il manque)
CORRESPONDANCES = {
    'radarr': {'Films FR (1080p/4K)': 'Films anciens (1080p visé, moins si introuvable)'},
    'sonarr': {'Séries FR (1080p/4K)': 'Séries anciennes (1080p visé, moins si introuvable)',
               'Animés VOSTFR/MULTi (1080p/4K)': 'Animés anciens (tout, 1080p de préférence)'},
}
EXCLUES = ('Unknown', 'WORKPRINT', 'CAM', 'TELESYNC', 'TELECINE', 'REGIONAL', 'DVDSCR', 'Raw-HD', 'BR-DISK')


def nom_item(it):
    return it.get('quality', {}).get('name') or it.get('name') or ''


def id_item(it):
    return it.get('quality', {}).get('id') if 'quality' in it else it.get('id')


def version_ancienne(profil, nom):
    """Copie du profil : basses définitions autorisées (sauf versions filmées au cinéma), sans mise à niveau."""
    p = json.loads(json.dumps(profil))
    p.pop('id', None)
    p['name'] = nom
    for it in p['items']:
        if not any(x in nom_item(it) for x in ('Remux', *EXCLUES)):
            it['allowed'] = True
    p['upgradeAllowed'] = False
    p['cutoff'] = next(id_item(it) for it in p['items'] if it['allowed'] and '1080p' in nom_item(it))
    return p


def profils(app):
    """{id du profil normal: id du profil ancien}, en créant ou complétant les profils anciens."""
    tous = {p['name']: p for p in portail.arr(app, 'GET', 'qualityprofile')}
    corr = {}
    for normal, ancien in CORRESPONDANCES[app].items():
        if normal not in tous:
            continue
        voulu = version_ancienne(tous[normal], ancien)
        if ancien in tous:
            existant = tous[ancien]
            if existant['upgradeAllowed']:  # pas de mise à niveau : ce qui est pris est gardé
                existant['upgradeAllowed'] = False
                portail.arr_envoyer(app, 'PUT', f"qualityprofile/{existant['id']}", existant)
            corr[tous[normal]['id']] = existant['id']
        else:
            corr[tous[normal]['id']] = json.loads(portail.arr_envoyer(app, 'POST', 'qualityprofile', voulu))['id']
    return corr


def ancien(annee, aujourdhui=None):
    return 0 < (annee or 0) <= (aujourdhui or datetime.date.today()).year - 1


def main():
    corr = profils('radarr')
    films = [m for m in portail.arr('radarr', 'GET', 'movie')
             if m['monitored'] and not m.get('hasFile') and ancien(m.get('year')) and m['qualityProfileId'] in corr]
    for m in films:
        m['qualityProfileId'] = corr[m['qualityProfileId']]
        portail.arr_envoyer('radarr', 'PUT', f"movie/{m['id']}", m)
    if films:
        portail.arr_envoyer('radarr', 'POST', 'command', {'name': 'MoviesSearch', 'movieIds': [m['id'] for m in films]})
        print('Films passés en profil ancien :', ', '.join(m['title'] for m in films))

    corr = profils('sonarr')
    for s in portail.arr('sonarr', 'GET', 'series'):
        st = s.get('statistics', {})
        manque = st.get('episodeFileCount', 0) < st.get('episodeCount', 0)
        if s['monitored'] and manque and ancien(s.get('year')) and s['qualityProfileId'] in corr:
            s['qualityProfileId'] = corr[s['qualityProfileId']]
            portail.arr_envoyer('sonarr', 'PUT', f"series/{s['id']}", s)
            portail.arr_envoyer('sonarr', 'POST', 'command', {'name': 'SeriesSearch', 'seriesId': s['id']})
            print('Série passée en profil ancien :', s['title'])


def test():
    d = datetime.date(2026, 10, 25)
    assert ancien(2025, d) and ancien(1996, d) and not ancien(2026, d) and not ancien(0, d) and not ancien(None, d)
    profil = {'id': 7, 'name': 'X', 'upgradeAllowed': False, 'cutoff': 19, 'items': [
        {'quality': {'id': 25, 'name': 'CAM'}, 'allowed': False},
        {'quality': {'id': 1, 'name': 'SDTV'}, 'allowed': False},
        {'id': 1001, 'name': 'WEB 720p', 'allowed': False, 'items': []},
        {'quality': {'id': 9, 'name': 'HDTV-1080p'}, 'allowed': True},
        {'quality': {'id': 30, 'name': 'Remux-1080p'}, 'allowed': False},
        {'quality': {'id': 19, 'name': 'Bluray-2160p'}, 'allowed': True}]}
    a = version_ancienne(profil, 'Y')
    assert [it['allowed'] for it in a['items']] == [False, True, True, True, False, True]
    assert a['cutoff'] == 9 and not a['upgradeAllowed'] and 'id' not in a and profil['items'][1]['allowed'] is False
    print('test OK')


if __name__ == '__main__':
    test() if sys.argv[1:] == ['--test'] else main()
