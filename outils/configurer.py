#!/usr/bin/env python3
"""Réglages des services après l'installation, sans passer par leurs pages web (sauf la création des comptes).

À relancer autant de fois que nécessaire : ce qui est déjà fait est sauté. Chaque ligne affichée dit
« ✔ fait », « · déjà fait » ou « ✋ à faire par toi » (création de compte, etc.), puis on relance.
  - Jellyfin : bibliothèques Films et Séries (avant l'assistant de création du compte, ou avec JELLYFIN_CLE=...).
  - Radarr / Sonarr : dossier racine, Transmission (en pause), garde-fou des gros films, notification de fin.
  - Prowlarr : relié à Radarr et Sonarr (les sources de téléchargement restent à ajouter par l'utilisateur).
  - Seerr : relié à Radarr et Sonarr (une fois connecté à Jellyfin par l'utilisateur).
  - AdGuard Home : écoute sur IP_SERVEUR et nom HOTE -> IP_SERVEUR (une fois son assistant terminé).
  - Tailscale : vérifie que le serveur est connecté, que ses routes sont approuvées et que sa clé n'expire pas.
--test : vérifications sans réseau.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.expanduser('~/outils'))
from config import C  # noqa: E402

ACCUEIL = Path.home()
DOCKER = '172.17.0.1'  # le serveur vu depuis un conteneur Docker (Seerr)
ARR = {'radarr': (7878, 'v3', 'Film'), 'sonarr': (8989, 'v3', 'Séries'), 'prowlarr': (9696, 'v1', None)}
A_FAIRE = []


def dire(etat, texte):
    print({'fait': '✔', 'deja': '·', 'toi': '✋', 'erreur': '✖'}[etat], texte)
    if etat in ('toi', 'erreur'):
        A_FAIRE.append(texte)


def http(url, methode='GET', corps=None, entetes=None):
    req = urllib.request.Request(url, method=methode, headers={'Content-Type': 'application/json', **(entetes or {})},
                                 data=None if corps is None else json.dumps(corps).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        t = r.read()
        return json.loads(t) if t.strip() else None


def cle_arr(app):
    return re.search(r'<ApiKey>(\w+)', Path(f'/var/lib/{app}/config.xml').read_text()).group(1)


def arr(app, methode, chemin, corps=None):
    port, version, _ = ARR[app]
    return http(f'http://127.0.0.1:{port}/api/{version}/{chemin}', methode, corps, {'X-Api-Key': cle_arr(app)})


def remplir(modele, nom, champs, **autres):
    """Copie d'un modèle (« schema ») de Radarr/Sonarr/Prowlarr avec nos valeurs."""
    m = json.loads(json.dumps(modele))
    m.pop('id', None)
    m['name'] = nom
    for f in m['fields']:
        if f['name'] in champs:
            f['value'] = champs[f['name']]
    m.update(autres)
    return m


def ajouter(app, quoi, implementation, nom, champs, **autres):
    if any(x['name'] == nom for x in arr(app, 'GET', quoi)):
        return dire('deja', f'{app} : {nom}')
    modele = next(x for x in arr(app, 'GET', f'{quoi}/schema') if x['implementation'] == implementation)
    arr(app, 'POST', quoi, remplir(modele, nom, champs, **autres))
    dire('fait', f'{app} : {nom}')


def erreur_http(e):
    try:
        return e.read().decode()[:300]
    except Exception:
        return str(e)


def etape(nom, fonction):
    try:
        fonction()
    except urllib.error.HTTPError as e:
        dire('erreur', f'{nom} : {e.code} {erreur_http(e)}')
    except (OSError, ValueError, StopIteration, KeyError) as e:
        dire('erreur', f'{nom} : {e!r}')


# ---------- Jellyfin ----------

def jellyfin():
    base = 'http://127.0.0.1:8096'
    info = http(f'{base}/System/Info/Public')
    entetes = {}
    if info.get('StartupWizardCompleted'):
        cle = os.environ.get('JELLYFIN_CLE')
        if not cle:
            return dire('toi', 'Jellyfin : compte déjà créé, donc les bibliothèques sont à ajouter dans Tableau de bord → '
                        f"Bibliothèques (Films → {C['MEDIAS']}/Film, Séries → {C['MEDIAS']}/Séries), ou relance avec "
                        'JELLYFIN_CLE=<clé créée dans Tableau de bord → Clés API>')
        entetes = {'Authorization': f'MediaBrowser Token="{cle}"'}
    voulues = [('Films', 'movies', f"{C['MEDIAS']}/Film"), ('Séries', 'tvshows', f"{C['MEDIAS']}/Séries")]
    if C['DOSSIER_PRIVE']:
        voulues.append((C['DOSSIER_PRIVE'], 'movies', f"{C['MEDIAS']}/{C['DOSSIER_PRIVE']}"))
    existantes = {b['Name'] for b in http(f'{base}/Library/VirtualFolders', entetes=entetes)}
    for nom, type_, chemin in voulues:
        if nom in existantes:
            dire('deja', f'Jellyfin : bibliothèque {nom}')
            continue
        q = urllib.parse.urlencode({'name': nom, 'collectionType': type_, 'refreshLibrary': 'true'})
        http(f'{base}/Library/VirtualFolders?{q}', 'POST', {'LibraryOptions': {'PathInfos': [{'Path': chemin}],
             'PreferredMetadataLanguage': 'fr', 'MetadataCountryCode': 'FR'}}, entetes)
        dire('fait', f'Jellyfin : bibliothèque {nom} ({chemin})')
    if not info.get('StartupWizardCompleted'):
        dire('toi', f"Jellyfin : crée ton compte sur http://{C['IP_SERVEUR']}:8096 (les bibliothèques sont déjà là, passe "
             "cette étape de l'assistant), puis dans Tableau de bord → Utilisateurs → ton compte, coche "
             '« Autoriser la suppression de médias »')


# ---------- Radarr, Sonarr, Prowlarr ----------

def radarr_sonarr(app):
    dossier = f"{C['MEDIAS_BRUT']}/{ARR[app][2]}"
    if any(r['path'].rstrip('/') == dossier for r in arr(app, 'GET', 'rootfolder')):
        dire('deja', f'{app} : dossier {dossier}')
    else:
        arr(app, 'POST', 'rootfolder', {'path': dossier})
        dire('fait', f'{app} : dossier {dossier}')
    film = app == 'radarr'
    ajouter(app, 'downloadclient', 'Transmission', 'Transmission', {
        'host': '127.0.0.1', 'port': 9091, 'urlBase': '/transmission/', 'username': 'medias',
        'password': Path('/var/lib/medias/.transmission-mdp').read_text().strip(),
        ('movieCategory' if film else 'tvCategory'): 'films' if film else 'series', 'addPaused': True},
        enable=True, priority=1)
    # Les torrents arrivent en pause : le garde-fou lance les petits et demande l'accord pour les gros (> 10 Go)
    ajouter(app, 'notification', 'CustomScript', 'Garde-fou 10 Go', {'path': str(ACCUEIL / 'outils/garde-fou-film.sh')},
            onGrab=True)
    sujet = (ACCUEIL / '.surveillance' / 'sujet').read_text().strip()
    ajouter(app, 'notification', 'Ntfy', 'Téléchargement terminé', {'serverUrl': 'https://ntfy.sh', 'topics': [sujet]},
            **({'onDownload': True} if film else {'onImportComplete': True}))


def prowlarr():
    for app in ('radarr', 'sonarr'):
        port = ARR[app][0]
        ajouter('prowlarr', 'applications', app.capitalize(), app.capitalize(), {
            'prowlarrUrl': 'http://127.0.0.1:9696', 'baseUrl': f'http://127.0.0.1:{port}', 'apiKey': cle_arr(app)},
            syncLevel='fullSync')
    if not arr('prowlarr', 'GET', 'indexer'):
        dire('toi', f"Prowlarr : ajoute tes sources de téléchargement sur http://{C['IP_SERVEUR']}:9696 (Indexeurs → "
             'Ajouter) ; elles passent toutes seules dans Radarr et Sonarr')


# ---------- Seerr ----------

def seerr():
    reglages = json.loads(subprocess.run(['sudo', '-n', 'cat', '/var/lib/seerr/settings.json'], capture_output=True,
                                         text=True, check=True).stdout)
    if not reglages.get('public', {}).get('initialized'):
        return dire('toi', f"Seerr : ouvre http://{C['IP_SERVEUR']}:5055, choisis Jellyfin (adresse {DOCKER}, port 8096) "
                    'et connecte-toi avec ton compte Jellyfin ; à l\'étape Radarr/Sonarr, clique juste sur Terminer : '
                    'je les relie ensuite')
    cle = {'X-Api-Key': reglages['main']['apiKey']}
    for app in ('radarr', 'sonarr'):
        if reglages.get(app):
            dire('deja', f'Seerr : {app}')
            continue
        profil = arr(app, 'GET', 'qualityprofile')[0]
        dossier = f"{C['MEDIAS_BRUT']}/{ARR[app][2]}"
        corps = {'name': app.capitalize(), 'hostname': DOCKER, 'port': ARR[app][0], 'apiKey': cle_arr(app), 'useSsl': False,
                 'baseUrl': '', 'activeProfileId': profil['id'], 'activeProfileName': profil['name'],
                 'activeDirectory': dossier, 'is4k': False, 'isDefault': True, 'externalUrl': '', 'syncEnabled': True,
                 'preventSearch': False, 'tags': []}
        if app == 'radarr':
            corps['minimumAvailability'] = 'released'
        else:
            corps.update(seriesType='standard', animeSeriesType='anime', activeAnimeProfileId=profil['id'],
                         activeAnimeProfileName=profil['name'], activeAnimeDirectory=dossier, animeTags=[],
                         enableSeasonFolders=True)
        http(f'http://127.0.0.1:5055/api/v1/settings/{app}', 'POST', corps, cle)
        dire('fait', f'Seerr : relié à {app}')


# ---------- AdGuard Home ----------

ANCIEN_VPN = '10.8.0.1'  # adresse de l'ancien VPN WireGuard, retirée des installations d'avant Tailscale


def adguard_reglages(r, hote, ip):
    """Modifie les réglages d'AdGuard (dict du YAML) ; vrai s'il y a eu un changement.
    Il doit répondre sur l'adresse du serveur : c'est le DNS que Tailscale donne aux appareils, par le réseau de la maison."""
    change = False
    hotes = r.setdefault('dns', {}).setdefault('bind_hosts', [])
    if ANCIEN_VPN in hotes:
        hotes.remove(ANCIEN_VPN)
        change = True
    if '0.0.0.0' not in hotes and ip not in hotes:
        hotes.append(ip)
        change = True
    # rangement des réécritures : « filtering » dans les versions récentes, « dns » dans les anciennes
    section = r['filtering'] if 'rewrites' in r.get('filtering', {}) or 'filtering' in r else r['dns']
    reecritures = section.setdefault('rewrites', []) or []
    section['rewrites'] = reecritures
    for x in reecritures:
        if x.get('answer') == ANCIEN_VPN:
            x['answer'] = ip
            change = True
    for nom in (hote, f'{hote}.lan'):
        if not any(x.get('domain') == nom for x in reecritures):
            reecritures.append({'domain': nom, 'answer': ip, 'enabled': True})
            change = True
    if r.get('http', {}).get('address', '').startswith(ANCIEN_VPN + ':'):
        r['http']['address'] = r['http']['address'].replace(ANCIEN_VPN, ip, 1)
        change = True
    return change


def adguard():
    import yaml
    fichier = Path('/opt/AdGuardHome/AdGuardHome.yaml')
    if not subprocess.run(['sudo', '-n', 'test', '-f', str(fichier)]).returncode == 0:
        return dire('toi', f"AdGuard Home : ouvre http://{C['IP_SERVEUR']}:3000, choisis ton identifiant et ton mot de passe "
                    "(interfaces : laisse le choix proposé ; si le port 53 est refusé, choisis l'adresse "
                    f"{C['IP_SERVEUR']})")
    r = yaml.safe_load(subprocess.run(['sudo', '-n', 'cat', str(fichier)], capture_output=True, text=True, check=True).stdout)
    if not adguard_reglages(r, C['HOTE'], C['IP_SERVEUR']):
        return dire('deja', f"AdGuard Home : adresse du serveur et nom {C['HOTE']}")
    subprocess.run(['sudo', '-n', 'systemctl', 'stop', 'AdGuardHome'], check=True)
    subprocess.run(['sudo', '-n', 'tee', str(fichier)], input=yaml.safe_dump(r, allow_unicode=True, sort_keys=False),
                   text=True, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['sudo', '-n', 'systemctl', 'start', 'AdGuardHome'], check=True)
    dire('fait', f"AdGuard Home : écoute sur {C['IP_SERVEUR']}, http://{C['HOTE']} → serveur")


# ---------- Tailscale ----------

CONSOLE = 'https://login.tailscale.com/admin'


def tailscale_a_faire(s, reseau, ip):
    """Ce qui reste à régler à la main, d'après « tailscale status --json » (la console ne se règle pas sans clé d'API)."""
    if s.get('BackendState') != 'Running':
        lien = s.get('AuthURL') or 'le lien affiché par : sudo tailscale up'
        return [f'Tailscale : connecte le serveur à ton compte (crée-le si besoin, c\'est gratuit) en ouvrant {lien}']
    moi, faire = s.get('Self', {}), []
    if reseau not in (moi.get('PrimaryRoutes') or []) or not moi.get('ExitNodeOption'):
        faire.append(f'Tailscale : sur {CONSOLE}/machines, menu « … » du serveur → Edit route settings : coche {reseau} '
                     f'et « Use as exit node ». Puis sur {CONSOLE}/dns : Add nameserver → Custom → {ip}, '
                     'et active « Override DNS servers » (pubs bloquées partout)')
    if moi.get('KeyExpiry'):
        faire.append(f'Tailscale : sur {CONSOLE}/machines, menu « … » du serveur → Disable key expiry '
                     '(sinon il se déconnecte au bout de 6 mois)')
    return faire


def tailscale():
    r = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True)
    faire = tailscale_a_faire(json.loads(r.stdout or '{}'), C['RESEAU_MAISON'], C['IP_SERVEUR'])
    for t in faire:
        dire('toi', t)
    if not faire:
        dire('deja', f"Tailscale : connecté, accès à {C['RESEAU_MAISON']} et sortie internet approuvés")


def main():
    if C.get('AVEC_MEDIAS') == 'oui':
        etape('Jellyfin', jellyfin)
        for app in ('radarr', 'sonarr'):
            etape(app, lambda a=app: radarr_sonarr(a))
        etape('Prowlarr', prowlarr)
        etape('Seerr', seerr)
    etape('AdGuard Home', adguard)
    etape('Tailscale', tailscale)
    if C.get('AVEC_MEDIAS') == 'oui':
        A_FAIRE.append(f"Radarr (:7878), Sonarr (:8989), Prowlarr (:9696) : à la première ouverture de chacun sur "
                       f"http://{C['IP_SERVEUR']}, choisis ton identifiant et ton mot de passe")
    if C.get('AVEC_PHOTOS') == 'oui':
        A_FAIRE.append(f"Immich : crée le compte admin sur http://{C['IP_SERVEUR']}:2283, puis installe l'appli sur le téléphone")
    A_FAIRE.append('Partage réseau : choisis le mot de passe du dossier partagé avec : sudo smbpasswd -a $USER')
    print('\nReste à faire par toi (puis relance ce script) :' if A_FAIRE else '\nTout est réglé.')
    for i, t in enumerate(A_FAIRE, 1):
        print(f'  {i}. {t}')


def test():
    m = {'id': 3, 'name': '', 'fields': [{'name': 'host', 'value': 'x'}, {'name': 'port', 'value': 1}]}
    r = remplir(m, 'T', {'host': '127.0.0.1'}, enable=True)
    assert r['name'] == 'T' and r['fields'][0]['value'] == '127.0.0.1' and r['fields'][1]['value'] == 1
    assert 'id' not in r and r['enable'] and m['fields'][0]['value'] == 'x'  # le modèle n'est pas modifié
    a = {'dns': {'bind_hosts': ['127.0.0.1']}, 'filtering': {'rewrites': []}}
    ip = '192.168.1.20'
    assert adguard_reglages(a, 'monserv', ip) and a['dns']['bind_hosts'] == ['127.0.0.1', ip]
    assert [x['domain'] for x in a['filtering']['rewrites']] == ['monserv', 'monserv.lan']
    assert not adguard_reglages(a, 'monserv', ip)  # relancé : rien à changer
    b = {'dns': {'bind_hosts': ['0.0.0.0'], 'rewrites': None}}
    assert adguard_reglages(b, 'x', ip) and b['dns']['bind_hosts'] == ['0.0.0.0'] and len(b['dns']['rewrites']) == 2
    # installation d'avant Tailscale : tout ce qui visait l'ancien VPN passe sur l'adresse du serveur
    v = {'http': {'address': '10.8.0.1:3000'}, 'dns': {'bind_hosts': ['127.0.0.1', ip, '10.8.0.1']},
         'filtering': {'rewrites': [{'domain': 'monserv', 'answer': '10.8.0.1', 'enabled': True}]}}
    assert adguard_reglages(v, 'monserv', ip) and v['dns']['bind_hosts'] == ['127.0.0.1', ip]
    assert v['http']['address'] == ip + ':3000' and {x['answer'] for x in v['filtering']['rewrites']} == {ip}
    reseau = '192.168.1.0/24'
    assert 'https://login.tailscale.com/a/x' in tailscale_a_faire({'BackendState': 'NeedsLogin', 'AuthURL': 'https://login.tailscale.com/a/x'}, reseau, ip)[0]
    assert len(tailscale_a_faire({'BackendState': 'Running', 'Self': {'KeyExpiry': '2027-05-01'}}, reseau, ip)) == 2
    assert tailscale_a_faire({'BackendState': 'Running', 'Self': {'PrimaryRoutes': [reseau], 'ExitNodeOption': True}}, reseau, ip) == []
    print('test OK')


if __name__ == '__main__':
    test() if sys.argv[1:] == ['--test'] else main()
