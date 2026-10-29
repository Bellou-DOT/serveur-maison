#!/usr/bin/env python3
"""Portail du serveur maison (http://HOTE, maison + VPN) et garde-fou des gros films.

  portail.py            sert le portail sur le port 80 (service « portail »)
  portail.py --grab     appelé par Radarr à chaque film envoyé à Transmission (ajouté en pause) :
                        ≤ 10 Go → lancé tout de suite ; plus gros → notification avec Télécharger / Annuler
  portail.py --test     vérifications sans réseau
"""
import getpass
import html
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from config import C

ACCUEIL = Path.home()
ATTENTE = ACCUEIL / '.films-en-attente.json'
LIMITE = 10 * 1024 ** 3
PAGE = Path(__file__).with_name('portail.html')
SERVICES = ['jellyfin', 'docker', 'radarr', 'sonarr', 'prowlarr', 'transmission-daemon']


def cle_api(app):
    return re.search(r'<ApiKey>(\w+)', Path(f'/var/lib/{app}/config.xml').read_text()).group(1)


def auth_transmission():
    mdp = (ACCUEIL / '.transmission-mdp').read_text().strip()
    return 'Basic ' + __import__('base64').b64encode(f'{getpass.getuser()}:{mdp}'.encode()).decode()


def transmission(methode, **arguments):
    """Appel RPC à Transmission (gère le jeton de session X-Transmission-Session-Id)."""
    auth = auth_transmission()
    corps = json.dumps({'method': methode, 'arguments': arguments}).encode()
    session = ''
    for _ in range(2):
        req = urllib.request.Request('http://127.0.0.1:9091/transmission/rpc', data=corps,
                                     headers={'Authorization': auth, 'X-Transmission-Session-Id': session})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 409:
                raise
            session = e.headers['X-Transmission-Session-Id']
    raise RuntimeError('Transmission ne répond pas')


PORTS_ARR = {'radarr': 7878, 'sonarr': 8989}


def arr(app, methode, chemin):
    req = urllib.request.Request(f'http://127.0.0.1:{PORTS_ARR[app]}/api/v3/{chemin}', method=methode,
                                 headers={'X-Api-Key': cle_api(app)})
    with urllib.request.urlopen(req, timeout=30) as r:
        t = r.read()
        return json.loads(t) if t else None


def notifier(titre, texte, actions=''):
    sujet = (ACCUEIL / '.surveillance' / 'sujet').read_text().strip()
    entetes = {'Title': titre.encode('utf-8'), 'Tags': b'movie_camera', 'Priority': b'high'}
    if actions:
        entetes['Actions'] = actions.encode('utf-8')
    req = urllib.request.Request(f'https://ntfy.sh/{sujet}', data=texte.encode(), headers=entetes)
    urllib.request.urlopen(req, timeout=20).read()


def lire_attente():
    try:
        return json.loads(ATTENTE.read_text())
    except (OSError, ValueError):
        return {}


def ecrire_attente(d):
    ATTENTE.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    ATTENTE.chmod(0o600)


def go(octets):
    return f'{octets / 1024 ** 3:.1f} Go'.replace('.', ',')


def decision_necessaire(taille):
    return taille > LIMITE


def qualite_basse(qualite):
    """Vrai sous le 1080p : « WEBDL-720p », « SDTV », « DVD »… (les noms de qualité de Radarr / Sonarr)."""
    m = re.search(r'(\d{3,4})p', qualite)
    return int(m.group(1)) < 1080 if m else qualite.upper() in ('SDTV', 'DVD', 'DVD-R')


def grab():
    """Crochet « On Grab » de Radarr (films) et Sonarr (séries) : le torrent vient d'être ajouté en pause."""
    app = 'sonarr' if 'sonarr_eventtype' in os.environ else 'radarr'
    env = lambda cle, defaut='': os.environ.get(f'{app}_{cle}') or defaut
    if env('eventtype') != 'Grab':
        return  # « Test » et autres événements
    taille = int(env('release_size', '0'))
    empreinte = env('download_id').lower()
    if app == 'radarr':
        titre, quoi = f"{env('movie_title', '?')} ({env('movie_year')})", 'Ce film'
    else:
        saison = env('release_seasonnumber')
        titre = env('series_title', '?') + (f' – saison {saison}' if saison else '')
        quoi = 'Cette saison' if env('release_episodenumbers').count(',') else 'Cet épisode'
    if qualite_basse(env('release_quality')):  # autorisé pour certaines séries anciennes (profil « basse déf »), mais signalé
        notifier(f'{titre} : qualité {env("release_quality")}', f"{quoi} n'existe pas en 1080p : version en dessous téléchargée.")
    if not decision_necessaire(taille):
        transmission('torrent-start', ids=[empreinte])
        return
    jeton = secrets.token_urlsafe(16)
    attente = lire_attente()
    attente[jeton] = {'empreinte': empreinte, 'titre': titre, 'taille': taille, 'app': app,
                      'version': env('release_title')}
    ecrire_attente(attente)
    base = f"http://{C['HOTE']}/film"
    notifier(f'{titre} : {go(taille)}', f"{quoi} dépasse 10 Go ({env('release_quality')}). "
             'Le téléchargement est en pause en attendant ta réponse.',
             f'view, Télécharger, {base}/ok?t={jeton}; view, Annuler, {base}/non?t={jeton}')


def decider(jeton, accepter):
    attente = lire_attente()
    film = attente.pop(jeton, None)
    if not film:
        return 'Cette demande a déjà été traitée (ou n\'existe plus).'
    if accepter:
        transmission('torrent-start', ids=[film['empreinte']])
        message = f"C'est parti : « {film['titre']} » ({go(film['taille'])}) se télécharge."
    else:
        app = film.get('app', 'radarr')
        file = arr(app, 'GET', 'queue?pageSize=200')['records']
        element = next((e for e in file if (e.get('downloadId') or '').lower() == film['empreinte']), None)
        if element:  # retiré, mis sur liste noire : Radarr / Sonarr cherchera une autre version
            arr(app, 'DELETE', f"queue/{element['id']}?removeFromClient=true&blocklist=true&skipRedownload=false")
        else:
            transmission('torrent-remove', ids=[film['empreinte']], **{'delete-local-data': True})
        message = f"Annulé : « {film['titre']} ». {app.capitalize()} va chercher une autre version."
    ecrire_attente(attente)
    return message


def annuler(tid):
    """Annule un téléchargement depuis le portail : retiré de Transmission (fichiers compris) et plus suivi par
    Radarr / Sonarr, pour qu'ils ne le relancent pas tout seuls."""
    r = transmission('torrent-get', ids=[tid], fields=['hashString', 'name'])
    t = r.get('arguments', r)['torrents']
    if not t:
        return
    empreinte = t[0]['hashString'].lower()
    for app in ('radarr', 'sonarr'):
        elements = [e for e in arr(app, 'GET', 'queue?pageSize=500')['records'] if (e.get('downloadId') or '').lower() == empreinte]
        if not elements:
            continue
        arr(app, 'DELETE', f"queue/{elements[0]['id']}?removeFromClient=true&blocklist=false")
        if app == 'radarr':
            film = arr(app, 'GET', f"movie/{elements[0]['movieId']}")
            arr_envoyer(app, 'PUT', f"movie/{film['id']}", dict(film, monitored=False))
        else:
            arr_envoyer(app, 'PUT', 'episode/monitor', {'episodeIds': [e['episodeId'] for e in elements if e.get('episodeId')], 'monitored': False})
        break
    else:
        transmission('torrent-remove', ids=[tid], **{'delete-local-data': True})
    attente = lire_attente()
    if any(f['empreinte'] == empreinte for f in attente.values()):
        ecrire_attente({j: f for j, f in attente.items() if f['empreinte'] != empreinte})


def arr_envoyer(app, methode, chemin, corps):
    req = urllib.request.Request(f'http://127.0.0.1:{PORTS_ARR[app]}/api/v3/{chemin}', method=methode, data=json.dumps(corps).encode(),
                                 headers={'X-Api-Key': cle_api(app), 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def seerr(methode, chemin, corps=None):
    cle = json.loads(Path('/var/lib/seerr/settings.json').read_text())['main']['apiKey']
    req = urllib.request.Request(f'http://127.0.0.1:5055/api/v1/{chemin}', method=methode,
                                 data=json.dumps(corps).encode() if corps is not None else None,
                                 headers={'X-Api-Key': cle, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b'null')


# Statuts Seerr : 2 en attente, 3 en cours, 4 en partie disponible, 5 disponible
STATUTS = {2: 'demande', 3: 'demande', 4: 'partiel', 5: 'dispo'}


def rechercher(q):
    """Films et séries trouvés par Seerr (titres en français), avec leur état chez nous."""
    d = seerr('GET', 'search?' + urllib.parse.urlencode({'query': q, 'page': 1, 'language': 'fr'}, quote_via=urllib.parse.quote))
    sortie = []
    for r in d.get('results', []):
        if r.get('mediaType') not in ('movie', 'tv'):
            continue
        info = r.get('mediaInfo') or {}
        sortie.append({'id': r['id'], 'type': r['mediaType'], 'titre': r.get('title') or r.get('name'),
                       'annee': (r.get('releaseDate') or r.get('firstAirDate') or '')[:4],
                       'affiche': r.get('posterPath'), 'resume': (r.get('overview') or '')[:220],
                       'etat': STATUTS.get(info.get('status'), 'absent'), 'jellyfin': info.get('jellyfinMediaId')})
    return sortie[:18]


FICHES = {}  # (type, tmdb) -> fiche Seerr en français (titre, affiche…), gardée en mémoire


def nouveautes():
    """Derniers films et séries arrivés dans Jellyfin (d'après Seerr). Recalculé à chaque fois avec Radarr / Sonarr :
    un titre supprimé disparaît tout de suite, et les films du dossier privé (DOSSIER_PRIVE) n'apparaissent jamais."""
    films = {m['tmdbId']: m for m in arr('radarr', 'GET', 'movie')}
    series = {s['tmdbId'] for s in arr('sonarr', 'GET', 'series') if s.get('statistics', {}).get('episodeFileCount')}

    def visible(m):
        if m['mediaType'] == 'movie':
            f = films.get(m['tmdbId'])
            prive = C['DOSSIER_PRIVE']
            return bool(f and f.get('hasFile') and not (prive and f'/{prive}/' in (f.get('path') or '')))
        return m['mediaType'] == 'tv' and m['tmdbId'] in series
    medias = [m for m in seerr('GET', 'media?take=30&skip=0&filter=allavailable&sort=mediaAdded')['results'] if visible(m)][:14]

    def fiche(m):
        cle = (m['mediaType'], m['tmdbId'])
        if cle not in FICHES:
            d = seerr('GET', f"{m['mediaType']}/{m['tmdbId']}?language=fr")
            FICHES[cle] = {'id': m['tmdbId'], 'type': m['mediaType'], 'titre': d.get('title') or d.get('name'),
                           'annee': (d.get('releaseDate') or d.get('firstAirDate') or '')[:4], 'affiche': d.get('posterPath'),
                           'resume': (d.get('overview') or '')[:220], 'etat': 'dispo'}
        return {**FICHES[cle], 'jellyfin': m.get('jellyfinMediaId')}
    with ThreadPoolExecutor(6) as pool:
        return list(pool.map(fiche, medias))


def demander(type_, id_):
    corps = {'mediaType': type_, 'mediaId': id_}
    if type_ == 'tv':
        corps['seasons'] = 'all'
    seerr('POST', 'request', corps)


def telechargements():
    """Torrents en cours (hors partage terminé) pour la section du portail."""
    champs = ['id', 'name', 'percentDone', 'status', 'rateDownload', 'eta', 'totalSize', 'error', 'errorString', 'hashString']
    liste = transmission('torrent-get', fields=champs)['arguments']['torrents']
    en_attente = {f['empreinte'] for f in lire_attente().values()}  # gros films qui attendent ta réponse (garde-fou 10 Go)
    # Statuts Transmission : 0 en pause, 1-2 vérification, 3-4 téléchargement (file / actif), 5-6 partage
    return [{'id': t['id'], 'nom': t['name'], 'pct': round(t['percentDone'] * 100, 1), 'pause': t['status'] == 0,
             'attente': t['hashString'].lower() in en_attente, 'vitesse': t['rateDownload'], 'eta': t['eta'],
             'taille': t['totalSize'], 'erreur': t['errorString'] if t['error'] else ''}
            for t in liste if t['percentDone'] < 1]


# PC à réveiller depuis sa mise en veille (paquet magique, Wake on LAN / WLAN) ; vide = tuile cachée
PC_MAC, PC_IP = C['PC_MAC'], C['PC_IP']
# vide tant que installer.sh n'a pas détecté le réseau : diffusion générale seulement
DIFFUSION = str(ipaddress.ip_network(C['RESEAU_MAISON'], strict=False).broadcast_address) if C['RESEAU_MAISON'] else '255.255.255.255'


def pc_allume():
    return bool(PC_IP) and subprocess.run(['ping', '-c', '1', '-W', '1', PC_IP], capture_output=True).returncode == 0


def reveiller_pc():
    magique = bytes([255]) * 6 + bytes.fromhex(PC_MAC.replace(':', '')) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):  # en Wi-Fi un paquet peut se perdre : envoyé plusieurs fois, en diffusion et directement
            for dest in (DIFFUSION, '255.255.255.255', PC_IP):
                for port in (9, 7):
                    s.sendto(magique, (dest, port))


# Mise en veille demandée depuis le portail : le petit agent du PC (pc-agent.ps1) vient la chercher toutes les 10 s
ORDRE_PC = {'veille': 0.0}


def ordre_pc():
    """Consommé une seule fois, et seulement dans les 2 minutes : au réveil, le PC ne se rendort pas tout seul."""
    demande, ORDRE_PC['veille'] = ORDRE_PC['veille'], 0.0
    return {'veille': time.time() - demande < 120}


def etat_services():
    actifs = subprocess.run(['systemctl', 'is-active', *SERVICES], capture_output=True, text=True).stdout.split()
    return dict(zip(SERVICES, actifs))


NOMS_PROCESSUS = {'jellyfin': 'Jellyfin', 'ffmpeg': 'Jellyfin (conversion)',
                  'immich': 'Immich', 'immich-api': 'Immich', 'python': 'Immich (IA)', 'postgres': 'Base Immich',
                  'transmission-da': 'Transmission', 'Radarr': 'Radarr', 'Sonarr': 'Sonarr', 'Prowlarr': 'Prowlarr', 'node': 'Seerr'}


def systeme():
    """Mesures brutes pour la page « Serveur en direct » (le navigateur calcule les % et débits entre deux relevés)."""
    cpu = [int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
    mem = {l.split(':')[0]: int(l.split()[1]) * 1024 for l in Path('/proc/meminfo').read_text().splitlines()}
    temp = None
    for z in Path('/sys/class/thermal').glob('thermal_zone*'):
        if (z / 'type').read_text().strip() == 'x86_pkg_temp':
            temp = int((z / 'temp').read_text()) / 1000
    bat = Path('/sys/class/power_supply/BAT0')
    disques = []
    for nom, chemin in (('Disque du serveur', '/'), ('Disque des médias', C['MEDIAS_BRUT'])):
        if os.path.ismount(chemin):
            d = os.statvfs(chemin)
            disques.append({'nom': nom, 'total': d.f_blocks * d.f_frsize, 'libre': d.f_bavail * d.f_frsize})
    reseau = [0, 0]
    for l in Path('/proc/net/dev').read_text().splitlines()[2:]:
        nom, v = l.split(':', 1)
        if nom.strip() == C['INTERFACE']:
            v = v.split()
            reseau[0] += int(v[0]); reseau[1] += int(v[8])
    # Deux relevés de top à 0,5 s d'écart : le second donne la conso CPU instantanée par programme
    sortie = subprocess.run(['top', '-b', '-n', '2', '-d', '0.5', '-w', '200', '-o', '%CPU'], capture_output=True, text=True).stdout
    lignes = sortie.split('PID USER')[-1].splitlines()[1:]
    gros = []
    for l in lignes:
        c = l.split(None, 11)
        if len(c) == 12 and float(c[8].replace(',', '.')) >= 1 and len(gros) < 6:
            nom = c[11].strip()
            if nom.startswith(('kworker', 'ksoftirq', 'rcu_')) or nom == 'top':  # noyau, et la mesure elle-même
                continue
            nom = 'Wi-Fi' if 'iwlwifi' in nom else NOMS_PROCESSUS.get(nom, nom)
            gros.append({'nom': nom, 'cpu': float(c[8].replace(',', '.'))})
    return {'cpu': cpu, 'coeurs': os.cpu_count(), 'temp': temp, 'charge': os.getloadavg(),
            'demarrage': float(Path('/proc/uptime').read_text().split()[0]),
            'ram': {'total': mem['MemTotal'], 'dispo': mem['MemAvailable']}, 'disques': disques, 'reseau': reseau,
            'batterie': {'pct': int((bat / 'capacity').read_text()), 'etat': (bat / 'status').read_text().strip()} if bat.exists() else None,
            'processus': gros, 'services': etat_services()}


PARE_FEU = Path('/etc/serveur-maison-admin.nft')  # même liste d'appareils admin que le pare-feu


def est_admin(ip):
    """Le serveur, le VPN, ou un appareil de la maison dont l'adresse MAC est dans la liste du pare-feu."""
    if ip == '127.0.0.1' or ip.startswith('10.8.0.'):
        return True
    try:
        macs = set(re.findall(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', PARE_FEU.read_text().lower()))
        for ligne in Path('/proc/net/arp').read_text().splitlines()[1:]:
            champs = ligne.split()
            if champs[0] == ip and champs[3].lower() in macs:
                return True
    except OSError:
        pass
    return False


def page(nom='portail.html'):
    t = PAGE.with_name(nom).read_text().replace('{{NOM}}', html.escape(C['NOM_PORTAIL']))
    # sans PC configuré, la tuile « Mon PC » est cachée (le premier attribut style l'emporte sur le second)
    return t if PC_MAC else t.replace('id="tuilePc"', 'style="display:none" id="tuilePc"', 1)


RELAIS = 'http://127.0.0.1:9091'  # Transmission interdit d'être affiché ailleurs que chez lui : on le sert depuis le portail
ENTETES_RELAYES = ('Authorization', 'Content-Type', 'X-Transmission-Session-Id', 'Accept', 'If-None-Match')
# Noms sous lesquels on joint le portail : tout autre nom (« DNS rebinding » depuis une page web piégée) est refusé
HOTES = {C['HOTE'], *(f"{C['HOTE']}.{x}" for x in ('lan', 'home', 'local')), C['IP_SERVEUR'], '10.8.0.1', 'localhost', '127.0.0.1'} - {''}


def hote_autorise(entete):
    return (entete or '').rsplit(':', 1)[0].lower() in HOTES


def rpc_autorise(corps):
    """Transmission peut lancer un programme en fin de téléchargement ou écrire n'importe où : par le portail, seuls
    les téléchargements dans le dossier des médias sont permis (sinon un appareil admin piraté = serveur piraté)."""
    try:
        d = json.loads(corps or b'{}')
    except ValueError:
        return False
    args = d.get('arguments') or {}
    if any(k.startswith('script-') for k in args):
        return False
    dossiers = [args[k] for k in ('download-dir', 'incomplete-dir', 'location') if k in args]
    return all(isinstance(x, str) and '..' not in x and x.startswith(C['MEDIAS_BRUT'] + '/') for x in dossiers)


class Portail(BaseHTTPRequestHandler):
    def relayer(self):
        corps = self.rfile.read(int(self.headers.get('Content-Length', 0) or 0)) if self.command == 'POST' else None
        if corps is not None and not rpc_autorise(corps):
            return self.envoyer(403, '{"result": "refusé par le portail"}', 'application/json')
        req = urllib.request.Request(RELAIS + self.path, data=corps, method=self.command,
                                     headers={**{h: self.headers[h] for h in ENTETES_RELAYES if self.headers.get(h)},
                                              # relais réservé aux appareils admin : le portail s'identifie à ta place
                                              'Authorization': auth_transmission()})
        try:
            r = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:  # 401 (mot de passe) et 409 (jeton de session) font partie du protocole
            r = e
        donnees = r.read()
        self.send_response(r.status if hasattr(r, 'status') else r.code)
        for h, v in r.headers.items():
            if h.lower() not in ('transfer-encoding', 'connection', 'content-length'):
                self.send_header(h, v)
        self.send_header('Content-Length', str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    def envoyer(self, code, corps, type_='text/html; charset=utf-8'):
        b = corps.encode()
        self.send_response(code)
        self.send_header('Content-Type', type_)
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not hote_autorise(self.headers.get('Host')):
            return self.envoyer(403, 'Adresse inconnue.')
        u = urlparse(self.path)
        admin = est_admin(self.client_address[0])
        if not admin and (u.path.startswith(('/transmission/', '/film/')) or u.path in ('/telechargements', '/serveur', '/systeme', '/pc')):
            return self.envoyer(403, 'Réservé à l\'administrateur.')
        if u.path.startswith('/transmission/'):
            return self.relayer()
        # Accueil : complet pour l'admin ; les autres appareils de la maison voient la version invité
        if u.path == '/' and admin:
            return self.envoyer(200, page())
        if u.path in ('/', '/invite', '/invite/'):
            return self.envoyer(200, page().replace('<body>', '<body class="invite">', 1))
        if u.path == '/serveur':
            return self.envoyer(200, page('serveur.html'))
        if u.path == '/systeme':
            return self.envoyer(200, json.dumps(systeme()), 'application/json')
        if u.path == '/etat':
            return self.envoyer(200, json.dumps(etat_services()), 'application/json')
        if u.path == '/pc':
            return self.envoyer(200, json.dumps({'allume': pc_allume()}), 'application/json')
        if u.path == '/pc/ordre' and PC_IP and self.client_address[0] == PC_IP:
            return self.envoyer(200, json.dumps(ordre_pc()), 'application/json')
        if u.path == '/nouveautes':
            try:
                return self.envoyer(200, json.dumps(nouveautes()), 'application/json')
            except Exception as e:
                return self.envoyer(502, json.dumps({'erreur': str(e)}), 'application/json')
        if u.path == '/telechargements':
            try:
                return self.envoyer(200, json.dumps(telechargements()), 'application/json')
            except Exception as e:
                return self.envoyer(502, json.dumps({'erreur': str(e)}), 'application/json')
        if u.path == '/recherche':
            q = parse_qs(u.query).get('q', [''])[0].strip()[:80]
            try:
                return self.envoyer(200, json.dumps(rechercher(q) if q else []), 'application/json')
            except Exception as e:
                return self.envoyer(502, json.dumps({'erreur': str(e)}), 'application/json')
        if u.path in ('/film/ok', '/film/non'):
            jeton = parse_qs(u.query).get('t', [''])[0]
            try:
                message = decider(jeton, u.path.endswith('ok')) if re.fullmatch(r'[\w-]{10,40}', jeton) else 'Lien invalide.'
            except Exception as e:  # Transmission ou Radarr injoignable
                message = f'Erreur : {e}'
            return self.envoyer(200, '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                                     '<body style="font:18px system-ui;background:#111;color:#eee;padding:24px">'
                                     f'<p>{html.escape(message)}</p><p><a style="color:#9cf" href="/">← {html.escape(C['NOM_PORTAIL'])}</a></p>')
        self.envoyer(404, 'Introuvable')

    def do_POST(self):
        if not hote_autorise(self.headers.get('Host')) or not est_admin(self.client_address[0]):
            return self.envoyer(403, '{}', 'application/json')
        if self.path.startswith('/transmission/'):
            return self.relayer()
        # Demande d'un film ou d'une série. Corps JSON + même origine : une page tierce ne peut pas la déclencher.
        origine = urlparse(self.headers.get('Origin', '')).hostname
        if self.path not in ('/demander', '/telechargement', '/pc') or origine != self.headers.get('Host', '').split(':')[0] \
                or not self.headers.get('Content-Type', '').startswith('application/json'):
            return self.envoyer(403, '{}', 'application/json')
        try:
            d = json.loads(self.rfile.read(min(int(self.headers.get('Content-Length', 0)), 1000)))
            if self.path == '/pc' and d.get('action') == 'veille':
                ORDRE_PC['veille'] = time.time()
                return self.envoyer(200, json.dumps({'ok': True}), 'application/json')
            if self.path == '/pc':
                reveiller_pc()
                return self.envoyer(200, json.dumps({'ok': True}), 'application/json')
            if self.path == '/telechargement':  # pause / reprise / annulation d'un téléchargement, ou pause / reprise de tous
                if d.get('action') == 'annuler' and isinstance(d.get('id'), int):
                    annuler(d['id'])
                    return self.envoyer(200, json.dumps({'ok': True}), 'application/json')
                if d.get('action') not in ('pause', 'reprendre') or not (isinstance(d.get('id'), int) or d.get('tous') is True):
                    return self.envoyer(400, json.dumps({'erreur': 'action invalide'}), 'application/json')
                if d.get('tous'):  # « tout reprendre » ne relance pas un gros film qui attend la réponse au garde-fou
                    ids = [t['id'] for t in telechargements() if d['action'] == 'pause' or not t['attente']]
                else:
                    ids = [d['id']]
                if ids:
                    transmission('torrent-stop' if d['action'] == 'pause' else 'torrent-start', ids=ids)
                return self.envoyer(200, json.dumps({'ok': True}), 'application/json')
            if d.get('type') not in ('movie', 'tv') or not isinstance(d.get('id'), int):
                return self.envoyer(400, json.dumps({'erreur': 'demande invalide'}), 'application/json')
            demander(d['type'], d['id'])
            return self.envoyer(200, json.dumps({'ok': True}), 'application/json')
        except urllib.error.HTTPError as e:
            return self.envoyer(502, json.dumps({'erreur': e.read().decode(errors='replace')[:200]}), 'application/json')
        except Exception as e:
            return self.envoyer(502, json.dumps({'erreur': str(e)}), 'application/json')

    def log_message(self, *a):
        pass


def test():
    assert not decision_necessaire(LIMITE) and decision_necessaire(LIMITE + 1)
    assert go(15 * 1024 ** 3) == '15,0 Go'
    assert qualite_basse('WEBDL-720p') and qualite_basse('SDTV') and qualite_basse('Bluray-480p')
    assert not qualite_basse('WEBDL-1080p') and not qualite_basse('Bluray-2160p') and not qualite_basse('Unknown')
    global ATTENTE
    ATTENTE = Path('/tmp/attente-test.json')
    ecrire_attente({'abcdefghijkl': {'empreinte': 'x', 'titre': 'T', 'taille': 1}})
    assert decider('inconnu-000000', True).startswith('Cette demande')
    assert est_admin('127.0.0.1') and est_admin('10.8.0.2') and not est_admin('192.168.1.250')
    ATTENTE.unlink()
    assert len(bytes([255]) * 6 + bytes.fromhex('aa:bb:cc:dd:ee:ff'.replace(':', '')) * 16) == 102
    assert hote_autorise('127.0.0.1:80') and hote_autorise('10.8.0.1') and not hote_autorise('pirate.example.com')
    assert not hote_autorise(None) and not hote_autorise('')
    ok = '{"method": "torrent-add", "arguments": {"filename": "x", "download-dir": "%s/Téléchargements"}}' % C['MEDIAS_BRUT']
    assert rpc_autorise(ok.encode()) and rpc_autorise(b'{"method": "torrent-get", "arguments": {"fields": ["id"]}}')
    assert not rpc_autorise(b'{"method": "session-set", "arguments": {"script-torrent-done-enabled": true}}')
    assert not rpc_autorise(b'{"method": "torrent-add", "arguments": {"download-dir": "/home/x/.ssh"}}')
    assert not rpc_autorise(('{"arguments": {"location": "%s/../../etc"}}' % C['MEDIAS_BRUT']).encode())
    assert not rpc_autorise(b'pas du json')
    ORDRE_PC['veille'] = time.time()
    assert ordre_pc() == {'veille': True} and ordre_pc() == {'veille': False}
    m = systeme()
    assert len(m['cpu']) >= 4 and m['ram']['total'] > m['ram']['dispo'] > 0 and m['disques'], m
    print('test OK')


if __name__ == '__main__':
    if sys.argv[1:] == ['--grab']:
        grab()
    elif sys.argv[1:] == ['--test']:
        test()
    else:
        ThreadingHTTPServer(('0.0.0.0', 80), Portail).serve_forever()
