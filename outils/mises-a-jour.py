#!/usr/bin/env python3
"""Mises à jour automatiques et prudentes (cron toutes les heures).

Pour chaque service : nouvelle version ? -> vérification d'impact -> installation -> contrôle -> notification.
- Retenue (notification, rien d'installé) : changement de version majeure, ou notes de version qui annoncent
  des changements cassants (Immich). à regarder à la main.
- Reportée (silencieusement, à l'heure suivante) : quelqu'un regarde un film (Jellyfin).
- Si le service ne repart pas après la mise à jour : retour à l'ancienne version quand c'est possible, et alerte.
Les mises à jour de sécurité d'Ubuntu restent gérées par unattended-upgrades.
--test : vérifications sans réseau.  --verifier : affiche ce qui est disponible, sans rien installer.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.expanduser('~/outils'))
import portail  # noqa: E402  (arr, arr_envoyer, notifier, ACCUEIL)

ETAT = portail.ACCUEIL / '.surveillance' / 'maj-connues.json'
UA = {'User-Agent': 'serveur-maison (mises a jour)'}


class Retenue(Exception):
    """Risque détecté : on n'installe pas, on prévient."""


class Reportee(Exception):
    """Pas le bon moment (quelqu'un joue ou regarde) : on réessaie à l'heure suivante."""


def json_web(url, entetes=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **(entetes or {})}), timeout=30) as r:
        return json.loads(r.read())


def telecharger(url, sha256, destination):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
        donnees = r.read()
    if hashlib.sha256(donnees).hexdigest() != sha256:
        raise Retenue('fichier téléchargé corrompu (empreinte différente)')
    destination.write_bytes(donnees)


def nombres(v):
    return [int(x) for x in re.findall(r'\d+', v.split('-')[0])]


def version_plus_recente(a, b):
    return nombres(a) > nombres(b)


def meme_majeure(a, b):
    return nombres(a)[:1] == nombres(b)[:1]


def actif(service):
    return subprocess.run(['systemctl', 'is-active', '--quiet', service]).returncode == 0


def sudo(*cmd, **kw):
    return subprocess.run(['sudo', '-n', *cmd], check=True, capture_output=True, text=True, **kw)


# ---------- Radarr, Sonarr, Prowlarr : leur propre mise à jour intégrée ----------

def arr_liste(app):
    if app == 'prowlarr':
        cle = re.search(r'<ApiKey>(\w+)', Path('/var/lib/prowlarr/config.xml').read_text()).group(1)
        return json_web('http://127.0.0.1:9696/api/v1/update', {'X-Api-Key': cle}), cle
    return portail.arr(app, 'GET', 'update'), None


def arr_dispo(app):
    liste, _ = arr_liste(app)
    derniere = next((u for u in liste if u.get('latest')), None)
    return None if not derniere or derniere.get('installed') else derniere['version']


def arr_appliquer(app, v):
    liste, cle = arr_liste(app)
    installee = next((u['version'] for u in liste if u.get('installed')), None)
    if installee and not meme_majeure(v, installee):
        raise Retenue(f'nouvelle version majeure ({installee} → {v}) : à vérifier ensemble')
    if app == 'prowlarr':
        req = urllib.request.Request('http://127.0.0.1:9696/api/v1/command', data=b'{"name":"ApplicationUpdate"}',
                                     method='POST', headers={'X-Api-Key': cle, 'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=30).read()
    else:
        portail.arr_envoyer(app, 'POST', 'command', {'name': 'ApplicationUpdate'})
    for _ in range(36):  # le programme s'arrête, se met à jour et redémarre tout seul
        time.sleep(10)
        try:
            if arr_dispo(app) is None:
                return f'{app.capitalize()} {v}'
        except Exception:
            pass  # en plein redémarrage
    raise Retenue(f'{app.capitalize()} ne confirme pas la mise à jour vers {v} : à vérifier')


# ---------- Jellyfin (paquet Ubuntu) : appliqué quand personne ne regarde ----------

def jellyfin_dispo():
    sudo('apt-get', 'update', '-qq')
    sortie = subprocess.run(['apt', 'list', '--upgradable'], capture_output=True, text=True).stdout
    m = re.search(r'^jellyfin-server/\S+ (\S+)', sortie, re.M)
    return m.group(1) if m else None


def jellyfin_version():
    return json_web('http://127.0.0.1:8096/System/Info/Public')['Version']


def quelqu_un_regarde():
    """Connexions à Jellyfin venant d'un appareil (pas du serveur lui-même ni de Seerr dans Docker)."""
    sortie = subprocess.run(['ss', '-Htn', 'state', 'established', '( sport = :8096 )'], capture_output=True, text=True).stdout
    for ligne in sortie.splitlines():
        pair = ligne.split()[-1].rsplit(':', 1)[0].strip('[]').removeprefix('::ffff:')
        if not pair.startswith(('127.', '172.', '::1')):
            return True
    return False


def jellyfin_appliquer(v):
    avant = jellyfin_version()
    if not meme_majeure(v, avant):
        raise Retenue(f'nouvelle version majeure ({avant} → {v}) : à vérifier ensemble (extensions, applis)')
    if quelqu_un_regarde():
        raise Reportee('quelqu\'un regarde')
    sudo('env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', 'install', '-y', '-q', '--only-upgrade',
         'jellyfin', 'jellyfin-server', 'jellyfin-web')
    for _ in range(30):
        time.sleep(5)
        try:
            return f'Jellyfin {jellyfin_version()}'
        except Exception:
            pass
    raise Retenue('Jellyfin ne répond plus après la mise à jour : à vérifier tout de suite')


# ---------- Immich (Docker) ----------

IMMICH_ENV = Path('/opt/immich/.env')


def immich_dispo():
    actuelle = re.search(r'^IMMICH_VERSION=(\S+)', IMMICH_ENV.read_text(), re.M).group(1)
    derniere = json_web('https://api.github.com/repos/immich-app/immich/releases/latest')['tag_name']
    return derniere if version_plus_recente(derniere, actuelle) else None


def immich_appliquer(v):
    texte = IMMICH_ENV.read_text()
    actuelle = re.search(r'^IMMICH_VERSION=(\S+)', texte, re.M).group(1)
    if not meme_majeure(v, actuelle):
        raise Retenue(f'nouvelle version majeure ({actuelle} → {v}) : à vérifier ensemble')
    notes = json_web(f'https://api.github.com/repos/immich-app/immich/releases/tags/{v}').get('body') or ''
    if re.search(r'breaking change', notes, re.I):
        raise Retenue(f'les notes de {v} annoncent des changements cassants : à vérifier ensemble')
    IMMICH_ENV.write_text(re.sub(r'^IMMICH_VERSION=\S+', f'IMMICH_VERSION={v}', texte, flags=re.M))
    try:
        sudo('docker', 'compose', 'pull', '-q', cwd='/opt/immich', timeout=1800)
        sudo('docker', 'compose', 'up', '-d', cwd='/opt/immich', timeout=600)
        for _ in range(60):
            time.sleep(5)
            try:
                if json_web('http://127.0.0.1:2283/api/server/ping').get('res') == 'pong':
                    return f'Immich {v}'
            except Exception:
                pass
    except subprocess.SubprocessError:
        pass
    IMMICH_ENV.write_text(texte)  # retour à la version d'avant
    sudo('docker', 'compose', 'up', '-d', cwd='/opt/immich', timeout=600)
    raise Retenue(f'Immich {v} ne démarrait pas : version {actuelle} remise')


SERVICES = {
    'Jellyfin': (jellyfin_dispo, jellyfin_appliquer),
    'Immich (photos)': (immich_dispo, immich_appliquer),
    'Radarr': (lambda: arr_dispo('radarr'), lambda v: arr_appliquer('radarr', v)),
    'Sonarr': (lambda: arr_dispo('sonarr'), lambda v: arr_appliquer('sonarr', v)),
    'Prowlarr': (lambda: arr_dispo('prowlarr'), lambda v: arr_appliquer('prowlarr', v)),
}


HEURE_RESUME = 19


def resume_du_jour(annonce, dernier, aujourdhui, heure):
    """(titre, texte) du résumé quotidien, ou None si rien à dire, déjà envoyé aujourd'hui, ou trop tôt."""
    if not (annonce['faites'] or annonce['retenues']) or dernier == aujourdhui or heure < HEURE_RESUME:
        return None
    parties = []
    if annonce['faites']:
        parties.append('Installées et vérifiées :\n' + '\n'.join('• ' + f for f in dict.fromkeys(annonce['faites'])))
    if annonce['retenues']:
        parties.append('Retenues (rien d\'installé, demande à Claude) :\n' + '\n'.join(dict.fromkeys(annonce['retenues'])))
    titre = 'Mises à jour du jour ✅' if not annonce['retenues'] else 'Mises à jour du jour (à regarder)'
    return titre, '\n\n'.join(parties)


def main(installer=True):
    try:
        etat = json.loads(ETAT.read_text())
    except (OSError, ValueError):
        etat = {}
    retenues = etat.setdefault('retenues', {})
    faites, nouvelles_retenues, erreurs = [], [], []
    for nom, (dispo, appliquer) in SERVICES.items():
        try:
            v = dispo()
            if not v:
                retenues.pop(nom, None)
                continue
            if not installer:
                print(f'{nom} : {v} disponible')
                continue
            if retenues.get(nom) == v:
                continue  # déjà signalée, en attente d'une décision avec Claude
            faites.append(appliquer(v))
        except Reportee:
            continue
        except Retenue as e:
            retenues[nom] = v
            nouvelles_retenues.append(f'• {nom} {v} : {e}')
        except Exception as e:  # site injoignable, etc. : on réessaiera à l'heure suivante
            erreurs.append(f'{nom} ({e})')
    # une seule notification par jour : on accumule, puis résumé à HEURE_RESUME
    annonce = etat.setdefault('a_annoncer', {'faites': [], 'retenues': []})
    annonce['faites'] += faites
    annonce['retenues'] += nouvelles_retenues
    message = resume_du_jour(annonce, etat.get('dernier_resume'), time.strftime('%F'), time.localtime().tm_hour)
    if message:
        portail.notifier(*message)
        etat['dernier_resume'] = time.strftime('%F')
        etat['a_annoncer'] = {'faites': [], 'retenues': []}
    ETAT.write_text(json.dumps(etat, ensure_ascii=False, indent=1))
    print(time.strftime('%F %R'), '| installées :', faites or '-', '| retenues :', nouvelles_retenues or '-', '| erreurs :', erreurs or '-')


def test():
    assert version_plus_recente('v3.4.0', 'v3.3.5') and not version_plus_recente('v3.3.5', 'v3.3.5')
    assert version_plus_recente('2.12.1-SNAPSHOT', '2.12.0-SNAPSHOT') and not version_plus_recente('2.11.9', '2.12.0')
    assert version_plus_recente('v3.10.0', 'v3.9.9')  # comparaison numérique, pas alphabétique
    assert meme_majeure('6.5.0.1', '6.4.4.10685') and not meme_majeure('7.0.0', '6.4.4') and meme_majeure('v3.4.0', 'v3.3.5')
    assert meme_majeure('12.3.1', '12.3.0') and not meme_majeure('13.0.0', '12.3.0')
    a = {'faites': ['Radarr 1', 'Radarr 1'], 'retenues': []}
    assert resume_du_jour(a, None, '2026-10-29', 18) is None  # trop tôt
    assert resume_du_jour(a, '2026-10-29', '2026-10-29', 20) is None  # déjà envoyé
    assert resume_du_jour(a, '2026-10-28', '2026-10-29', 19)[1].count('Radarr 1') == 1  # doublons fusionnés
    assert resume_du_jour({'faites': [], 'retenues': []}, None, '2026-10-29', 20) is None
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / 'x'
        try:
            telecharger('data:,bonjour', '0' * 64, f)
        except (Retenue, Exception) as e:
            assert isinstance(e, Retenue) or 'data' in str(e).lower() or 'unknown url' in str(e).lower()
    print('test OK')


if __name__ == '__main__':
    if sys.argv[1:] == ['--test']:
        test()
    else:
        main(installer=sys.argv[1:] != ['--verifier'])
