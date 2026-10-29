#!/usr/bin/env python3
"""Gestion du serveur Minecraft maison : mondes (instances), cartes, modpacks.

Chaque monde vit dans ~/instances/<nom>/ avec son lancer.sh ; ~/mc est un lien vers le monde actif
(le service systemd « minecraft » démarre ~/mc/lancer.sh). Appelé par le panneau via SSH :
    python3 ~/outils/gestion.py <commande> [arguments]
Les tâches longues (installations) tournent en arrière-plan ; « suivi » donne leur progression.
"""
import base64
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from config import C

ACCUEIL = Path.home()
INSTANCES = ACCUEIL / 'instances'
ACTIF = ACCUEIL / 'mc'
ENVOIS = INSTANCES / '.envois'
SAUVEGARDES = ACCUEIL / 'sauvegardes'
TACHE = INSTANCES / '.tache.json'
JOURNAL_TACHE = INSTANCES / '.tache.log'
UA = {'User-Agent': 'serveur-maison (panneau)'}
FTB = 'https://api.feed-the-beast.com/v1/modpacks/public/modpack'
FLAGS = ('-XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 -XX:+UnlockExperimentalVMOptions '
         '-XX:+DisableExplicitGC -XX:+AlwaysPreTouch -XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 '
         '-XX:G1HeapRegionSize=8M -XX:G1ReservePercent=20 -XX:InitiatingHeapOccupancyPercent=15')

NOM = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')
ENVOI = re.compile(r'^[0-9a-f]{32}\.zip$')
TITRE = re.compile(r'^[A-Za-z0-9._-]{1,60}$')
VALIDATION = {  # arguments acceptés par tâche (re-vérifiés ici même si le panneau valide déjà)
    'activer': [lambda a: NOM.match(a) and (INSTANCES / a).is_dir()],
    'supprimer': [lambda a: NOM.match(a) and (INSTANCES / a).is_dir() and a != instance_active()],
    'carte': [lambda a: ENVOI.match(a) and (ENVOIS / a).is_file()],
    'serverpack': [lambda a: ENVOI.match(a) and (ENVOIS / a).is_file(), TITRE.match],
    'modrinth': [re.compile(r'^[A-Za-z0-9_-]{1,64}$').match],
    'ftb': [re.compile(r'^\d{1,7}$').match],
    'palworld': [],
}
TITRES = {'activer': 'Changement de monde', 'supprimer': 'Suppression du monde','carte': 'Installation de la carte', 'serverpack': 'Import du server pack',
          'modrinth': 'Installation du modpack', 'ftb': 'Installation du modpack FTB',
          'palworld': 'Installation / mise à jour de Palworld'}

# Palworld : serveur dédié Steam (app 2394010), service « palworld » en conflit avec « minecraft »
PALWORLD = ACCUEIL / 'palworld'
STEAMCMD = ACCUEIL / 'steamcmd'
PAL_INI = PALWORLD / 'Pal/Saved/Config/LinuxServer/PalWorldSettings.ini'
# Réglages modifiables depuis le panneau : clé → validation (pas de " , ( ) qui casseraient la ligne OptionSettings)
PAL_REGLAGES = {
    'ServerName': re.compile(r'^[\w\' .!-]{1,40}$'),
    'ServerPassword': re.compile(r'^[A-Za-z0-9_-]{0,32}$'),
    'ServerPlayerMaxNum': re.compile(r'^([1-9]|[12]\d|3[0-2])$'),
}
# Options de jeu Palworld proposées dans le panneau (seules celles présentes dans le fichier du jeu sont affichées)
PAL_OPTIONS = {
    'ExpRate': ('float', 0.1, 20), 'PalCaptureRate': ('float', 0.5, 5), 'PalSpawnNumRate': ('float', 0.5, 3),
    'PalEggDefaultHatchingTime': ('float', 0, 240), 'WorkSpeedRate': ('float', 0.1, 10),
    'CollectionDropRate': ('float', 0.1, 10), 'EnemyDropItemRate': ('float', 0.1, 10),
    'DayTimeSpeedRate': ('float', 0.1, 5), 'NightTimeSpeedRate': ('float', 0.1, 5),
    'PlayerDamageRateAttack': ('float', 0.1, 10), 'PlayerDamageRateDefense': ('float', 0.1, 10),
    'PalDamageRateAttack': ('float', 0.1, 10), 'PalDamageRateDefense': ('float', 0.1, 10),
    'PlayerStomachDecreaceRate': ('float', 0.1, 10), 'PlayerStaminaDecreaceRate': ('float', 0.1, 10),
    'PalStomachDecreaceRate': ('float', 0.1, 10), 'BuildObjectDeteriorationDamageRate': ('float', 0, 10),
    'DeathPenalty': ('enum', ('None', 'Item', 'ItemAndEquipment', 'All')),
    'bEnableInvaderEnemy': ('bool',), 'bIsPvP': ('bool',), 'bEnablePlayerToPlayerDamage': ('bool',),
    'bEnableFriendlyFire': ('bool',), 'bEnableFastTravel': ('bool',), 'bHardcore': ('bool',), 'bPalLost': ('bool',),
    'BaseCampWorkerMaxNum': ('int', 1, 50), 'BaseCampMaxNumInGuild': ('int', 1, 10), 'GuildPlayerMaxNum': ('int', 1, 100),
}
PAL_ADMIN = ACCUEIL / '.palworld-admin'  # mot de passe admin (API locale du panneau et /AdminPassword en jeu)
PAL_API = 'http://127.0.0.1:8212/v1/api'

# Règles Minecraft : (clé du panneau, nom depuis 1.21.11 / 26.x, ancien nom, type). PvP ancien = server.properties.
REGLES_MC = [
    ('garder_inventaire', 'keep_inventory', 'keepInventory', 'bool'),
    ('cycle_jour', 'advance_time', 'doDaylightCycle', 'bool'),
    ('meteo', 'advance_weather', 'doWeatherCycle', 'bool'),
    ('apparition_mobs', 'spawn_mobs', 'doMobSpawning', 'bool'),
    ('destruction_mobs', 'mob_griefing', 'mobGriefing', 'bool'),
    ('regeneration', 'natural_health_regeneration', 'naturalRegeneration', 'bool'),
    ('phantoms', 'spawn_phantoms', 'doInsomnia', 'bool'),
    ('reapparition_immediate', 'immediate_respawn', 'doImmediateRespawn', 'bool'),
    ('degats_chute', 'fall_damage', 'fallDamage', 'bool'),
    ('pvp', 'pvp', None, 'bool'),
    ('messages_mort', 'show_death_messages', 'showDeathMessages', 'bool'),
    ('dormeurs', 'players_sleeping_percentage', 'playersSleepingPercentage', 'int'),
]
BOOLEEN = re.compile(r'^(true|false)$')
PROPRIETES_MC = {  # réglages de server.properties modifiables ; seuls difficulté et mode s'appliquent sans redémarrage
    'difficulty': re.compile(r'^(peaceful|easy|normal|hard)$'),
    'gamemode': re.compile(r'^(survival|creative|adventure|spectator)$'),
    'force-gamemode': BOOLEEN, 'hardcore': BOOLEEN, 'allow-flight': BOOLEEN,
    'max-players': re.compile(r'^([1-9]|[1-4]\d|50)$'),
    'view-distance': re.compile(r'^([4-9]|1[0-6])$'),
}
VALIDATION['nouveau-monde'] = [re.compile(r'^(normal|flat|large_biomes|amplified)$').match,
                               re.compile(r'^(aleatoire|-?[A-Za-z0-9_]{1,32})$').match]
TITRES['nouveau-monde'] = 'Création d\'une nouvelle carte'


def sortie(obj):
    print(json.dumps(obj, ensure_ascii=False))


def log(msg):
    print(msg, flush=True)


def api(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.loads(r.read())


def telecharger(url, dest, sha512=None, sha1=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + '.part')
    h512, h1 = hashlib.sha512(), hashlib.sha1()
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r, open(tmp, 'wb') as f:
        while bloc := r.read(1 << 16):
            f.write(bloc)
            h512.update(bloc)
            h1.update(bloc)
    if (sha512 and h512.hexdigest() != sha512) or (not sha512 and sha1 and h1.hexdigest() != sha1):
        tmp.unlink()
        raise RuntimeError(f'empreinte incorrecte pour {dest.name}')
    tmp.replace(dest)


def chemin_sur(base, rel):
    """Chemin d'un fichier d'archive, refusé s'il sort du dossier (ex. « ../../.ssh/authorized_keys »)."""
    p = (base / rel).resolve()
    if not str(p).startswith(str(base.resolve()) + os.sep):
        raise RuntimeError(f'chemin refusé dans l\'archive : {rel}')
    return p


def extraire(zf, base, prefixe=''):
    for m in zf.infolist():
        if m.is_dir() or not m.filename.startswith(prefixe) or m.filename == prefixe:
            continue
        cible = chemin_sur(base, m.filename[len(prefixe):])
        cible.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(m) as src, open(cible, 'wb') as dst:
            shutil.copyfileobj(src, dst)


def java_pour(mc):
    """Version de Java exigée par une version de Minecraft (1.16 → 8, 1.18 → 17, 1.21 → 21, 26.x → 25)."""
    m = re.match(r'^1\.(\d+)(?:\.(\d+))?', mc or '')
    if not m:
        return 25 if re.match(r'^(2[6-9]|[3-9]\d)\.', mc or '') else 21
    x, y = int(m[1]), int(m[2] or 0)
    if x <= 16:
        return 8
    if x < 20 or (x == 20 and y <= 4):
        return 17
    return 21


def assurer_java(n):
    d = Path(f'/usr/lib/jvm/java-{n}-openjdk-amd64/bin')
    if not (d / 'java').exists():
        log(f'Installation de Java {n}…')
        subprocess.run(['sudo', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', '-o', 'DPkg::Lock::Timeout=600',
                        'install', '-y', '-qq', f'openjdk-{n}-jre-headless'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return d


def ram_go():
    with open('/proc/meminfo') as f:
        return int(f.readline().split()[1]) // 1048576 * 2 // 3  # 2/3 de la mémoire, comme l'installation


def ecrire_lanceur(d, java_bin, mode, cible=''):
    args = f'-Xms{ram_go()}G -Xmx{ram_go()}G {FLAGS}'
    if mode == 'run.sh':  # Forge / NeoForge : la mémoire passe par user_jvm_args.txt (nos lignes en dernier gagnent)
        with open(d / 'user_jvm_args.txt', 'a') as f:
            f.write('\n# Ajouté par le panneau\n' + args.replace(' ', '\n') + '\n')
        cmd = 'exec bash ./run.sh nogui'
    elif mode == 'script':
        cmd = f'exec bash ./{cible}'
    else:
        cmd = f'exec java {args} -jar {cible} nogui'
    (d / 'lancer.sh').write_text('#!/usr/bin/env bash\n# Démarre ce monde (appelé par le service minecraft).\n'
                                 f'export PATH="{java_bin}:$PATH"\n{cmd}\n')
    (d / 'lancer.sh').chmod(0o755)


def lanceur_auto(d, java_bin):
    if (d / 'run.sh').exists():
        return ecrire_lanceur(d, java_bin, 'run.sh')
    for nom in ('fabric-server-launch.jar', 'fabric-server.jar', 'quilt-server-launch.jar', 'server.jar'):
        if (d / nom).exists():
            return ecrire_lanceur(d, java_bin, 'jar', nom)
    forge = sorted(p.name for p in d.glob('forge-*.jar') if 'installer' not in p.name)
    if forge:
        return ecrire_lanceur(d, java_bin, 'jar', forge[0])
    for nom in ('startserver.sh', 'start.sh', 'ServerStart.sh', 'start-server.sh', 'LaunchServer.sh'):
        if (d / nom).exists():
            return ecrire_lanceur(d, java_bin, 'script', nom)
    raise RuntimeError('impossible de trouver comment démarrer ce serveur (ni run.sh, ni .jar, ni script)')


def installer_forge(d, java_bin, installeur):
    log('Installation du modloader (une à deux minutes)…')
    subprocess.run([str(java_bin / 'java'), '-jar', installeur.name, '--installServer'], cwd=d, check=True,
                   stdout=sys.stdout, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=1800)
    installeur.unlink()
    for p in d.glob('*installer*.log'):
        p.unlink()


def installer_loader(d, dep, mc):
    java_bin = assurer_java(java_pour(mc))
    if 'fabric-loader' in dep:
        inst = next(v['version'] for v in api('https://meta.fabricmc.net/v2/versions/installer') if v['stable'])
        log('Installation de Fabric…')
        telecharger(f"https://meta.fabricmc.net/v2/versions/loader/{mc}/{dep['fabric-loader']}/{inst}/server/jar",
                    d / 'fabric-server.jar')
        return ecrire_lanceur(d, java_bin, 'jar', 'fabric-server.jar')
    if 'neoforge' in dep:
        v = dep['neoforge']
        url = f'https://maven.neoforged.net/releases/net/neoforged/neoforge/{v}/neoforge-{v}-installer.jar'
    elif 'forge' in dep:
        v = f"{mc}-{dep['forge']}"
        url = f'https://maven.minecraftforge.net/net/minecraftforge/forge/{v}/forge-{v}-installer.jar'
    else:
        raise RuntimeError('modloader non pris en charge (Quilt n\'est pas géré)')
    telecharger(url, d / 'installer.jar')
    installer_forge(d, java_bin, d / 'installer.jar')
    lanceur_auto(d, java_bin)


def ascii(texte):
    return texte.encode('ascii', 'ignore').decode().strip()


def ecrire_proprietes(d, regles):
    # default-server.properties : certains modpacks (mod « Default Server Properties ») le recopient au démarrage.
    for nom in ('server.properties', 'default-server.properties'):
        props = d / nom
        if nom != 'server.properties' and not props.exists():
            continue
        lignes = props.read_text(errors='replace').splitlines() if props.exists() else []
        vues = set()
        for i, ligne in enumerate(lignes):
            cle = ligne.split('=', 1)[0].strip()
            if cle in regles:
                lignes[i] = f'{cle}={regles[cle]}'
                vues.add(cle)
        lignes += [f'{k}={v}' for k, v in regles.items() if k not in vues]
        props.write_text('\n'.join(lignes) + '\n')


def lire_proprietes(d):
    props = d / 'server.properties'
    lignes = props.read_text(errors='replace').splitlines() if props.exists() else []
    return dict(l.split('=', 1) for l in lignes if '=' in l and not l.startswith('#'))


def finaliser(d, info):
    """Réglages communs à tout nouveau monde : EULA, liste blanche activée et reprise de celle du monde actif."""
    (d / 'eula.txt').write_text('eula=true\n')
    ecrire_proprietes(d, {'motd': ascii(f"{C['NOM_JEUX']} - {info['titre']}")[:59], 'white-list': 'true',
                          'enforce-whitelist': 'true', 'online-mode': 'true', 'server-port': '25565'})
    for f in ('whitelist.json', 'ops.json'):
        if (ACTIF / f).exists():
            shutil.copy(ACTIF / f, d / f)
    (d / 'console.in').touch()
    (d / '.info.json').write_text(json.dumps(info, ensure_ascii=False))


def nouvelle_instance(base):
    slug = re.sub(r'[^a-z0-9]+', '-', base.lower()).strip('-')[:32] or 'monde'
    nom, n = slug, 2
    while (INSTANCES / nom).exists():
        nom, n = f'{slug}-{n}', n + 1
    (INSTANCES / nom).mkdir(parents=True)
    return INSTANCES / nom


def lire_info(d):
    try:
        return json.loads((d / '.info.json').read_text())
    except (OSError, ValueError):
        return {'titre': d.name, 'type': 'inconnu'}


def instance_active():
    return ACTIF.resolve().name if ACTIF.is_symlink() else None


def nom_niveau(d):
    props = d / 'server.properties'
    for ligne in props.read_text(errors='replace').splitlines() if props.exists() else []:
        if ligne.startswith('level-name='):
            nom = ligne.split('=', 1)[1].strip()
            if re.match(r'^[A-Za-z0-9 _.-]{1,64}$', nom) and nom not in ('.', '..'):
                return nom
    return 'world'


def service(action):
    subprocess.run(['sudo', 'systemctl', action, 'minecraft'], check=True)


def journal_depuis(debut):
    return subprocess.run(['journalctl', '-u', 'minecraft', '--since', debut, '--no-pager', '-o', 'cat'],
                          capture_output=True, text=True).stdout


def demarrer_et_attendre(minutes=20):
    debut = time.strftime('%Y-%m-%d %H:%M:%S')
    log('Démarrage du serveur…')
    service('start')
    for i in range(minutes * 12):
        time.sleep(5)
        j = journal_depuis(debut)
        if 'Done (' in j:
            log('Serveur prêt.')
            return
        if i % 6 == 5:
            log(f'… démarrage en cours ({(i + 1) * 5} s)')
    raise RuntimeError('le serveur n\'a pas fini de démarrer. Dernières lignes :\n'
                       + '\n'.join(journal_depuis(debut).splitlines()[-15:]))


def sauvegarder_mondes(d, raison):
    mondes = sorted(p.name for p in d.iterdir() if p.is_dir() and (p / 'level.dat').exists())
    if not mondes:
        return
    SAUVEGARDES.mkdir(exist_ok=True)
    f = SAUVEGARDES / f"{raison}-{d.name}-{time.strftime('%Y-%m-%d-%Hh%M')}.tar.gz"
    log(f'Sauvegarde de l\'ancien monde ({", ".join(mondes)})…')
    subprocess.run(['tar', 'czf', str(f), '-C', str(d), *mondes], check=True)


# ---------- Tâches (arrière-plan) ----------

def basculer(nom):
    lien = ACCUEIL / 'mc.nouveau'
    if lien.is_symlink() or lien.exists():
        lien.unlink()
    lien.symlink_to(INSTANCES / nom)
    lien.replace(ACTIF)  # bascule atomique du lien


def tache_activer(nom):
    precedent = instance_active()
    log('Arrêt du serveur (le monde est sauvegardé)…')
    service('stop')
    basculer(nom)
    try:
        demarrer_et_attendre()
    except Exception:
        if precedent and precedent != nom:  # le nouveau monde ne démarre pas : on remet l'ancien en route
            log('Retour au monde précédent…')
            service('stop')
            basculer(precedent)
            service('start')
        raise
    with open(ACTIF / 'console.in', 'a') as console:  # filet de sécurité : jamais de serveur ouvert à tous
        console.write('whitelist on\n')
    return f"« {lire_info(INSTANCES / nom)['titre']} » est maintenant le monde actif."


# En solo, l'inventaire et la position de l'hôte sont dans level.dat (Data.Player) :
# un serveur ne lit que playerdata/<uuid>.dat, l'hôte arriverait donc les poches vides.

def nbt_fin(b, i, t):
    """Position juste après la charge utile d'un tag NBT de type t qui commence en i."""
    fixes = {1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8}
    if t in fixes:
        return i + fixes[t]
    if t == 8:
        return i + 2 + int.from_bytes(b[i:i + 2], 'big')
    if t in (7, 11, 12):
        n = int.from_bytes(b[i:i + 4], 'big', signed=True)
        return i + 4 + max(n, 0) * {7: 1, 11: 4, 12: 8}[t]
    if t == 9:
        st, n = b[i], int.from_bytes(b[i + 1:i + 5], 'big', signed=True)
        i += 5
        for _ in range(max(n, 0)):
            i = nbt_fin(b, i, st)
        return i
    if t == 10:
        while b[i] != 0:
            i = nbt_fin(b, i + 3 + int.from_bytes(b[i + 1:i + 3], 'big'), b[i])
        return i + 1
    raise ValueError(f'type NBT inconnu {t}')


def nbt_enfants(b, i):
    """Enfants {nom: (type, début, fin)} du compound dont la charge utile commence en i."""
    enfants = {}
    while b[i] != 0:
        t, n = b[i], int.from_bytes(b[i + 1:i + 3], 'big')
        nom, debut = b[i + 3:i + 3 + n].decode('utf-8', 'replace'), i + 3 + n
        i = nbt_fin(b, debut, t)
        enfants[nom] = (t, debut, i)
    return enfants


def joueur_solo(level_dat):
    """(uuid, contenu de playerdata/<uuid>.dat) du joueur hôte d'un monde solo, ou None."""
    b = gzip.decompress(level_dat.read_bytes())
    data = nbt_enfants(b, 3 + int.from_bytes(b[1:3], 'big')).get('Data')
    joueur = data and data[0] == 10 and nbt_enfants(b, data[1]).get('Player')
    if not joueur or joueur[0] != 10:
        return None
    c = nbt_enfants(b, joueur[1])
    if c.get('UUID', (0,))[0] == 11:
        brut = b[c['UUID'][1] + 4:c['UUID'][2]]
    elif 'UUIDMost' in c and 'UUIDLeast' in c:  # avant 1.16
        brut = b[c['UUIDMost'][1]:c['UUIDMost'][2]] + b[c['UUIDLeast'][1]:c['UUIDLeast'][2]]
    else:
        return None
    if len(brut) != 16:
        return None
    h = brut.hex()
    return f'{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}', gzip.compress(b'\x0a\x00\x00' + b[joueur[1]:joueur[2]])


def tache_carte(fichier):
    z, d = ENVOIS / fichier, INSTANCES / instance_active()
    niveau, tmp = nom_niveau(d), d / '.carte-tmp'
    try:
        with zipfile.ZipFile(z) as zf:
            dats = [n for n in zf.namelist() if n == 'level.dat' or n.endswith('/level.dat')]
            if not dats:
                raise RuntimeError('ce fichier ne contient pas de monde Minecraft (aucun level.dat)')
            racine = min(dats, key=lambda n: n.count('/'))[:-len('level.dat')]
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir()
            log('Extraction de la carte…')
            extraire(zf, tmp, racine)
    except zipfile.BadZipFile:
        raise RuntimeError('ce fichier n\'est pas un .zip valide')
    finally:
        z.unlink(missing_ok=True)
    try:
        hote = joueur_solo(tmp / 'level.dat')
    except Exception as e:
        hote = None
        log(f'Attention : inventaire du joueur solo illisible ({e}).')
    if hote:
        (tmp / 'playerdata').mkdir(exist_ok=True)
        (tmp / 'playerdata' / f'{hote[0]}.dat').write_bytes(hote[1])
        log(f'Monde solo : inventaire et position du joueur hôte récupérés ({hote[0]}).')
    log('Arrêt du serveur…')
    service('stop')
    sauvegarder_mondes(d, 'avant-carte')
    for n in (niveau, f'{niveau}_nether', f'{niveau}_the_end'):  # Paper recrée nether et End depuis la carte
        shutil.rmtree(d / n, ignore_errors=True)
    tmp.rename(d / niveau)
    (d / niveau / 'session.lock').unlink(missing_ok=True)
    carte_web_effacer(d)
    demarrer_et_attendre()
    carte_web_dessiner(d)
    return 'Carte installée ! L\'ancien monde est sauvegardé dans ~/sauvegardes.'


def tache_modrinth(projet):
    p = api(f'https://api.modrinth.com/v2/project/{projet}')
    if p.get('project_type') != 'modpack':
        raise RuntimeError('ce projet Modrinth n\'est pas un modpack')
    versions = api(f'https://api.modrinth.com/v2/project/{projet}/version')
    v = next((v for v in versions if any(f['filename'].endswith('.mrpack') for f in v['files'])), None)
    if not v:
        raise RuntimeError('aucune version installable de ce modpack')
    fic = next((f for f in v['files'] if f['filename'].endswith('.mrpack') and f.get('primary')),
               next(f for f in v['files'] if f['filename'].endswith('.mrpack')))
    d = nouvelle_instance(p['slug'])
    log(f"Modpack {p['title']} {v['version_number']}")
    try:
        mr = d / '.modpack.mrpack'
        telecharger(fic['url'], mr, sha512=fic['hashes'].get('sha512'))
        with zipfile.ZipFile(mr) as zf:
            index = json.loads(zf.read('modrinth.index.json'))
            fichiers = [f for f in index['files'] if (f.get('env') or {}).get('server') != 'unsupported']
            log(f'Téléchargement de {len(fichiers)} mods et fichiers…')

            def un(f):
                erreur = None
                for url in f['downloads']:
                    try:
                        return telecharger(url, chemin_sur(d, f['path']), f['hashes'].get('sha512'), f['hashes'].get('sha1'))
                    except Exception as e:  # miroir suivant
                        erreur = e
                raise RuntimeError(f"{f['path']} : {erreur}")

            with ThreadPoolExecutor(8) as ex:
                for i, _ in enumerate(ex.map(un, fichiers), 1):
                    if i % 25 == 0 or i == len(fichiers):
                        log(f'  {i}/{len(fichiers)}')
            extraire(zf, d, 'overrides/')
            extraire(zf, d, 'server-overrides/')
        mr.unlink()
        mc = index['dependencies']['minecraft']
        installer_loader(d, index['dependencies'], mc)
        finaliser(d, {'titre': p['title'], 'type': 'modrinth', 'version': f"{v['version_number']} · MC {mc}",
                      'icone': p.get('icon_url')})
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    tache_activer(d.name)
    return f"{p['title']} est installé et lancé ! Ton ancien monde est conservé."


def tache_ftb(pack):
    p = api(f'{FTB}/{pack}')
    versions = [v for v in p['versions'] if v.get('type') == 'release'] or p['versions']
    v = max(versions, key=lambda v: v.get('updated', 0))
    cibles = {t['name']: t['version'] for t in v.get('targets', [])}
    mc = cibles.get('minecraft', '')
    d = nouvelle_instance(p['name'])
    log(f"Modpack {p['name']} {v['name']} (Minecraft {mc})")
    try:
        java_bin = assurer_java(java_pour(mc))
        inst = d / '.installateur-ftb'
        log('Téléchargement de l\'installateur FTB…')
        telecharger(f"{FTB}/{pack}/{v['id']}/server/linux", inst)
        inst.chmod(0o755)
        log('Installation des mods et du modloader (plusieurs minutes)…')
        # -pack/-version obligatoires : sinon l'installateur les déduit de son nom de fichier ou les demande au clavier.
        subprocess.run([str(inst), '-auto', '-force', '-no-colours', '-no-java', '-accept-eula',
                        '-pack', str(pack), '-version', str(v['id']), '-dir', str(d)],
                       cwd=d, check=True, stdout=sys.stdout, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=3600,
                       env={**os.environ, 'PATH': f"{java_bin}:{os.environ['PATH']}"})
        inst.unlink()
        lanceur_auto(d, java_bin)
        icone = next((a['url'] for a in p.get('art', []) if a.get('type') == 'square'), None)
        finaliser(d, {'titre': p['name'], 'type': 'ftb', 'version': f"{v['name']} · MC {mc}", 'icone': icone})
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    tache_activer(d.name)
    return f"{p['name']} est installé et lancé ! Ton ancien monde est conservé."


def detecter_mc(d):
    for f in ('variables.txt', 'server-setup-config.yaml'):
        if (d / f).exists():
            m = re.search(r'(?:MINECRAFT_VERSION\s*=\s*|mcVersion:\s*)"?([0-9.]+)', (d / f).read_text(errors='replace'))
            if m:
                return m[1]
    for p in (d / 'libraries/net/neoforged/neoforge').glob('*'):
        m = re.match(r'^(\d+)\.(\d+)\.', p.name)  # NeoForge 21.1.x → Minecraft 1.21.1
        if m:
            return f'1.{m[1]}.{m[2]}' if m[2] != '0' else f'1.{m[1]}'
    for p in (d / 'libraries/net/minecraftforge/forge').glob('*'):
        return p.name.split('-')[0]
    for p in d.glob('*installer*.jar'):
        m = re.search(r'-(1\.\d+(?:\.\d+)?)-', p.name)
        if m:
            return m[1]
    return None


def tache_serverpack(fichier, titre):
    z = ENVOIS / fichier
    d = nouvelle_instance(titre)
    try:
        with zipfile.ZipFile(z) as zf:
            noms = [n for n in zf.namelist() if not n.endswith('/')]
            hauts = {n.split('/', 1)[0] for n in noms}
            prefixe = hauts.pop() + '/' if len(hauts) == 1 and all('/' in n for n in noms) else ''
            log('Extraction du server pack…')
            extraire(zf, d, prefixe)
        mc = detecter_mc(d)
        log(f'Version de Minecraft détectée : {mc or "inconnue (Java 21 par défaut)"}')
        java_bin = assurer_java(java_pour(mc) if mc else 21)
        installeur = next(iter(sorted(d.glob('*installer*.jar'))), None)
        if installeur and not (d / 'run.sh').exists():
            installer_forge(d, java_bin, installeur)
        lanceur_auto(d, java_bin)
        finaliser(d, {'titre': titre.replace('-', ' ').replace('_', ' '), 'type': 'serverpack',
                      'version': f'MC {mc}' if mc else 'version inconnue'})
    except zipfile.BadZipFile:
        shutil.rmtree(d, ignore_errors=True)
        raise RuntimeError('ce fichier n\'est pas un .zip valide')
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    finally:
        z.unlink(missing_ok=True)
    tache_activer(d.name)
    return f'Server pack « {titre} » installé et lancé ! Ton ancien monde est conservé.'


def service_actif(nom):
    return subprocess.run(['systemctl', 'is-active', '--quiet', nom]).returncode == 0


def service_etat(nom):
    return subprocess.run(['systemctl', 'is-active', nom], capture_output=True, text=True).stdout.strip()


def pal_lire(cles=PAL_REGLAGES, fichier=None):
    """Valeurs des clés demandées dans la ligne OptionSettings (les clés absentes du fichier sont omises)."""
    ligne = next((l for l in (fichier or PAL_INI).read_text(errors='replace').splitlines() if l.startswith('OptionSettings=')), '')
    return {k: m[1].strip('"') for k in cles if (m := re.search(rf'[(,]{k}=("[^"]*"|[^,)]*)', ligne))}


def pal_format(k, v):
    """Valeur telle qu'écrite dans PalWorldSettings.ini (texte entre guillemets, nombres, True/False)."""
    if k in PAL_OPTIONS:
        typ = PAL_OPTIONS[k][0]
        return f'{float(v):.6f}' if typ == 'float' else str(int(v)) if typ == 'int' else v
    return v if k in ('ServerPlayerMaxNum', 'RESTAPIPort', 'RESTAPIEnabled') else f'"{v}"'


def pal_option_valide(k, v):
    t = PAL_OPTIONS[k]
    try:
        if t[0] == 'bool':
            return v in ('True', 'False')
        if t[0] == 'enum':
            return v in t[1]
        n = float(v) if t[0] == 'float' else int(v)
        return t[1] <= n <= t[2]
    except ValueError:
        return False


def pal_admin():
    """Mot de passe admin Palworld (créé au besoin) ; active l'API REST locale utilisée par le panneau."""
    if not PAL_ADMIN.exists():
        PAL_ADMIN.write_text(secrets.token_hex(6))
        PAL_ADMIN.chmod(0o600)
    mdp = PAL_ADMIN.read_text().strip()
    if PAL_INI.exists() and pal_lire(['AdminPassword', 'RESTAPIEnabled']) != {'AdminPassword': mdp, 'RESTAPIEnabled': 'True'}:
        pal_ecrire({'AdminPassword': mdp, 'RESTAPIEnabled': 'True', 'RESTAPIPort': '8212'})
    return mdp


def pal_api(chemin, donnees=None):
    auth = base64.b64encode(f'admin:{pal_admin()}'.encode()).decode()
    req = urllib.request.Request(f'{PAL_API}/{chemin}', method='POST' if donnees is not None else 'GET',
                                 data=json.dumps(donnees).encode() if donnees is not None else None,
                                 headers={'Authorization': f'Basic {auth}', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=8) as r:
        corps = r.read()
    return json.loads(corps) if corps.strip().startswith(b'{') else {}


def pal_ecrire(reglages):
    texte = PAL_INI.read_text(errors='replace')
    for k, v in reglages.items():
        valeur = pal_format(k, v)
        texte, n = re.subn(rf'(?<=[(,]){k}=("[^"]*"|[^,)]*)', lambda _: f'{k}={valeur}', texte)
        if n != 1:
            raise RuntimeError(f'réglage {k} introuvable dans PalWorldSettings.ini')
    PAL_INI.write_text(texte)


def tache_palworld():
    if service_actif('palworld'):
        raise RuntimeError('arrête Palworld avant de le mettre à jour')
    if not (STEAMCMD / 'steamcmd.sh').exists():
        log('Installation de SteamCMD (outil officiel de Valve)…')
        telecharger('https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz', STEAMCMD / 'steamcmd.tgz')
        subprocess.run(['tar', 'xzf', 'steamcmd.tgz'], cwd=STEAMCMD, check=True)
        (STEAMCMD / 'steamcmd.tgz').unlink()
    log('Téléchargement du serveur Palworld (plusieurs Go, 5 à 15 minutes)…')
    # Le code de retour de SteamCMD n'est pas fiable (il se met à jour lui-même) : on vérifie le résultat.
    subprocess.run([str(STEAMCMD / 'steamcmd.sh'), '+force_install_dir', str(PALWORLD), '+login', 'anonymous',
                    '+app_update', '2394010', 'validate', '+quit'], stdout=sys.stdout, stderr=subprocess.STDOUT,
                   stdin=subprocess.DEVNULL, timeout=7200)
    if not (PALWORLD / 'PalServer.sh').exists():
        raise RuntimeError('le téléchargement de Palworld a échoué (voir le journal)')
    sdk = ACCUEIL / '.steam/sdk64'
    sdk.mkdir(parents=True, exist_ok=True)
    shutil.copy(STEAMCMD / 'linux64/steamclient.so', sdk / 'steamclient.so')
    if not PAL_INI.exists():  # premier lancement : réglages par défaut du jeu, à notre nom
        PAL_INI.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(PALWORLD / 'DefaultPalWorldSettings.ini', PAL_INI)
        pal_ecrire({'ServerName': C['NOM_JEUX'], 'ServerPlayerMaxNum': '16'})
    pal_admin()
    return 'Palworld est installé et à jour. Il reste éteint : lance-le depuis l\'onglet Palworld.'


def console(*lignes):
    with open(ACTIF / 'console.in', 'a') as f:
        f.write(''.join(l + '\n' for l in lignes))


def carte_web_effacer(d):
    """Retire les tuiles squaremap de l'ancien monde : sinon la carte web montre toujours l'ancien terrain."""
    sq = d / 'plugins' / 'squaremap'
    for p in [*(sq / 'web' / 'tiles').glob('minecraft_*'), *(sq / 'data').glob('minecraft_*')]:
        shutil.rmtree(p, ignore_errors=True)


def carte_web_dessiner(d, apres_chunky=False):
    """Rendu complet de la carte web. squaremap ne dessine pas les chunks pré-générés par Chunky :
    on attend alors la fin de la pré-génération (attente détachée, 4 h max) pour ne pas bloquer les tâches."""
    if not (d / 'plugins' / 'squaremap').is_dir():
        return
    cmd = 'echo "squaremap fullrender minecraft:overworld" >> console.in'
    if apres_chunky:
        cmd = f'journalctl -u minecraft -f -n0 -o cat | grep -q -m1 "\\[Chunky\\] Task finished" && {cmd}'
    subprocess.Popen(['timeout', '4h', 'bash', '-c', cmd], cwd=d, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def tache_nouveau_monde(type_monde, graine):
    d = INSTANCES / instance_active()
    niveau = nom_niveau(d)
    log('Arrêt du serveur…')
    service('stop')
    sauvegarder_mondes(d, 'avant-nouvelle-carte')
    for n in (niveau, f'{niveau}_nether', f'{niveau}_the_end'):
        shutil.rmtree(d / n, ignore_errors=True)
    carte_web_effacer(d)
    ecrire_proprietes(d, {'level-seed': '' if graine == 'aleatoire' else graine, 'level-type': f'minecraft:{type_monde}'})
    (d / '.regles.json').unlink(missing_ok=True)
    log(f'Génération d\'une nouvelle carte ({type_monde}, graine {graine})…')
    demarrer_et_attendre()
    console('whitelist on')
    if list((d / 'plugins').glob('Chunky*.jar')):  # pré-génération pour éviter le lag d'exploration
        console('chunky spawn', 'chunky radius 3000', 'chunky start')
        log('Pré-génération de la carte lancée (rayon 3000 blocs). La carte web sera dessinée ensuite.')
        carte_web_dessiner(d, apres_chunky=True)
    else:
        carte_web_dessiner(d)
    return 'Nouvelle carte créée ! L\'ancienne est sauvegardée dans ~/sauvegardes.'


def tache_supprimer(nom):
    d = INSTANCES / nom
    if nom == instance_active():  # revérifié : le monde a pu être activé entre-temps
        raise RuntimeError('impossible de supprimer le monde en cours')
    titre = lire_info(d)['titre']
    sauvegarder_mondes(d, 'supprime')  # filet : la carte reste récupérable 7 jours
    log('Suppression des fichiers…')
    shutil.rmtree(d)
    return f'« {titre} » est supprimé. Sa carte reste dans ~/sauvegardes pendant 7 jours.'


TACHES = {'activer': tache_activer, 'supprimer': tache_supprimer, 'carte': tache_carte, 'serverpack': tache_serverpack,
          'modrinth': tache_modrinth, 'ftb': tache_ftb, 'palworld': tache_palworld,
          'nouveau-monde': tache_nouveau_monde}


# ---------- Règles Minecraft (serveur allumé : lues et changées par la console) ----------

def interroger(*commandes):
    debut = time.strftime('%Y-%m-%d %H:%M:%S')
    console(*commandes)
    time.sleep(1.5)
    return journal_depuis(debut)


def variante_regles():
    """« nouveau » (26.x : keep_inventory) ou « ancien » (1.21 : keepInventory), mémorisé par monde."""
    f = ACTIF / '.regles.json'
    try:
        return json.loads(f.read_text())['variante']
    except (OSError, ValueError, KeyError):
        pass
    j = interroger('gamerule minecraft:keep_inventory', 'gamerule keepInventory')
    v = 'nouveau' if 'rule keep_inventory is' in j else 'ancien' if 'rule keepInventory is' in j else None
    if v:
        f.write_text(json.dumps({'variante': v}))
    return v


def cmd_mc_regles():
    proprietes = {k: v for k, v in lire_proprietes(ACTIF).items() if k in PROPRIETES_MC}
    if not service_actif('minecraft'):
        return sortie({'ok': False, 'proprietes': proprietes, 'message': 'Démarre Minecraft pour voir et changer les règles de jeu.'})
    v = variante_regles()
    if not v:
        return sortie({'ok': False, 'proprietes': proprietes, 'message': 'Le serveur ne répond pas encore, réessaie dans un instant.'})
    noms = {cle: (nouveau if v == 'nouveau' else ancien) for cle, nouveau, ancien, _ in REGLES_MC}
    vus = dict(re.findall(r'Game ?rule (?:minecraft:)?(\w+) is currently set to:? (\S+)',
                          interroger(*(f'gamerule {n}' for n in noms.values() if n))))
    regles = []
    for cle, _, _, typ in REGLES_MC:
        if noms[cle] in vus:
            regles.append({'cle': cle, 'type': typ, 'valeur': vus[noms[cle]]})
        elif cle == 'pvp' and v == 'ancien':
            regles.append({'cle': cle, 'type': typ, 'valeur': lire_proprietes(ACTIF).get('pvp', 'true'), 'redemarrage': True})
    sortie({'ok': True, 'regles': regles, 'proprietes': proprietes})


def cmd_mc_regle(args):
    regle = next((r for r in REGLES_MC if args and r[0] == args[0]), None)
    if not regle or len(args) != 2 or not (BOOLEEN.match(args[1]) if regle[3] == 'bool' else re.match(r'^(100|\d{1,2})$', args[1])):
        return sortie({'ok': False, 'message': 'règle invalide'})
    if not service_actif('minecraft'):
        return sortie({'ok': False, 'message': 'Démarre Minecraft pour changer les règles.'})
    v = variante_regles()
    nom = regle[1] if v == 'nouveau' else regle[2]
    if nom is None:  # PvP sur les anciennes versions : réglage du fichier, appliqué au redémarrage
        ecrire_proprietes(ACTIF, {'pvp': args[1]})
        return sortie({'ok': True, 'message': 'PvP modifié : redémarre le serveur pour l\'appliquer.'})
    reponse = interroger(f'gamerule {nom} {args[1]}')
    sortie({'ok': 'set to' in reponse or 'now set' in reponse, 'message': reponse.strip().splitlines()[-1] if reponse.strip() else ''})


def cmd_mc_proprietes():
    try:
        demande = json.loads(sys.stdin.read())
        changes = {k: str(demande[k]) for k in PROPRIETES_MC if k in demande}
    except (ValueError, TypeError, AttributeError):
        return sortie({'ok': False, 'message': 'réglages illisibles'})
    mauvais = [k for k, v in changes.items() if not PROPRIETES_MC[k].match(v)]
    if mauvais or not changes:
        return sortie({'ok': False, 'message': 'réglage invalide : ' + ', '.join(mauvais or ['aucun'])})
    avant = lire_proprietes(ACTIF)
    ecrire_proprietes(ACTIF, changes)
    if service_actif('minecraft'):  # ces deux-là s'appliquent tout de suite
        if 'difficulty' in changes:
            console(f"difficulty {changes['difficulty']}")
        if 'gamemode' in changes:  # defaultgamemode seul ne touche que les nouveaux joueurs
            console(f"defaultgamemode {changes['gamemode']}", f"gamemode {changes['gamemode']} @a")
    a_redemarrer = [k for k, v in changes.items() if k not in ('difficulty', 'gamemode') and avant.get(k) != v]
    sortie({'ok': True, 'redemarrage': a_redemarrer})


# ---------- Palworld : joueurs et administration (API REST locale, serveur allumé) ----------

def cmd_pal_joueurs():
    if not service_actif('palworld'):
        return sortie({'ok': False, 'admin': pal_admin() if PAL_INI.exists() else None, 'message': 'Palworld est arrêté.'})
    try:
        joueurs = pal_api('players').get('players', [])
    except Exception as e:  # API pas encore prête (démarrage) ou mot de passe pas encore pris en compte
        return sortie({'ok': False, 'admin': pal_admin(), 'message': f'Palworld ne répond pas encore ({e}). '
                       'Si tu viens d\'installer cette version du panneau, relance Palworld une fois.'})
    sortie({'ok': True, 'admin': pal_admin(), 'joueurs': [
        {'nom': j.get('name'), 'id': j.get('userId'), 'niveau': j.get('level'), 'ping': round(j.get('ping') or 0)}
        for j in joueurs]})


def cmd_pal_action(args):
    action = args[0] if args else ''
    if action in ('kick', 'ban', 'unban') and len(args) == 2 and re.match(r'^[A-Za-z0-9_-]{1,64}$', args[1]):
        donnees = {'userid': args[1], 'message': 'Décision de l\'administrateur'}
    elif action == 'announce':
        message = sys.stdin.read().strip()
        if not (0 < len(message) <= 200) or re.search(r'[\x00-\x1f]', message):
            return sortie({'ok': False, 'message': 'message invalide'})
        donnees = {'message': message}
    elif action == 'save':
        donnees = {}
    else:
        return sortie({'ok': False, 'message': 'action invalide'})
    try:
        pal_api(action, donnees)
    except Exception as e:
        return sortie({'ok': False, 'message': f'Palworld n\'a pas accepté la demande ({e}).'})
    sortie({'ok': True})


# ---------- Modern Warfare 2 (IW4x sous Wine, service « iw4x ») ----------
# Réglages du panneau dans ~/.iw4x-reglages.json, appliqués par userraw/panneau.cfg (exécuté après server.cfg).

IW4X = Path('/srv/iw4x')
MW2_REGLAGES = ACCUEIL / '.iw4x-reglages.json'
MW2_PORT = 28960
MODES_MW2 = {'war': 'Match à mort par équipe', 'dm': 'Chacun pour soi', 'dom': 'Domination',
             'sd': 'Recherche et destruction', 'sab': 'Sabotage', 'koth': 'Quartier général',
             'ctf': 'Capture du drapeau', 'dd': 'Démolition'}
CARTES_MW2 = {'afghan': 'Afghan', 'derail': 'Derail', 'estate': 'Estate', 'favela': 'Favela', 'highrise': 'Highrise',
              'invasion': 'Invasion', 'checkpoint': 'Karachi', 'quarry': 'Quarry', 'rundown': 'Rundown', 'rust': 'Rust',
              'boneyard': 'Scrapyard', 'nightshift': 'Skidrow', 'subbase': 'Sub Base', 'terminal': 'Terminal',
              'underpass': 'Underpass', 'brecourt': 'Wasteland',
              # Packs de cartes (DLC) inclus dans IW4x
              'complex': 'Bailout', 'crash': 'Crash', 'overgrown': 'Overgrown', 'compact': 'Salvage', 'storm': 'Storm',
              'abandon': 'Carnival', 'fuel2': 'Fuel', 'strike': 'Strike', 'trailerpark': 'Trailer Park', 'vacant': 'Vacant',
              # Classiques ajoutés par IW4x
              'nuked': 'Nuketown', 'cargoship': 'Wet Work', 'shipment': 'Shipment', 'killhouse': 'Killhouse',
              'backlot': 'Backlot', 'bloc': 'Bloc', 'cross_fire': 'Crossfire'}
MW2_DEFAUTS = {'nom': C['NOM_JEUX'], 'mdp': '', 'max': 12, 'mode': 'war', 'hardcore': False,
               'aleatoire': True, 'cartes': list(CARTES_MW2)[:16]}
NOM_MW2 = re.compile(r"^[A-Za-z0-9 _\-\[\]().!'#&+]{1,40}$")
MDP_MW2 = re.compile(r'^[A-Za-z0-9_\-]{0,24}$')


def mw2_reglages():
    try:
        r = {**MW2_DEFAUTS, **json.loads(MW2_REGLAGES.read_text())}
    except (OSError, ValueError):
        r = dict(MW2_DEFAUTS)
        mdp = ACCUEIL / '.iw4x-mdp'  # mot de passe créé à l'installation
        if mdp.exists():
            r['mdp'] = mdp.read_text().strip()
    return r


def mw2_valide(r):
    return (isinstance(r, dict) and NOM_MW2.match(str(r.get('nom', ''))) and MDP_MW2.match(str(r.get('mdp', '')))
            and isinstance(r.get('max'), int) and 2 <= r['max'] <= 18 and r.get('mode') in MODES_MW2
            and isinstance(r.get('hardcore'), bool) and isinstance(r.get('aleatoire'), bool)
            and isinstance(r.get('cartes'), list) and 1 <= len(r['cartes']) <= len(CARTES_MW2)
            and all(c in CARTES_MW2 for c in r['cartes']))


def mw2_cfg(r):
    """Contenu de userraw/panneau.cfg (les valeurs viennent de mw2_valide : pas de guillemet ni de « ; »)."""
    rotation = f"gametype {r['mode']} " + ' '.join(f'map mp_{c}' for c in r['cartes'])
    hc = '1' if r['hardcore'] else '0'
    return '\n'.join([
        '// Généré par le panneau : modifier depuis le panneau, pas ici.',
        f'set sv_hostname "^5{r["nom"]}"', f'set g_password "{r["mdp"]}"',
        f'set sv_maxclients "{r["max"]}"', f'set party_maxplayers "{r["max"]}"',
        f'set g_gametype "{r["mode"]}"', f'set sv_maprotation "{rotation}"',
        f'set sv_randomMapRotation "{1 if r["aleatoire"] else 0}"',
        f'set g_hardcore "{hc}"', f'set scr_hardcore "{hc}"', ''])


def mw2_requete(texte, timeout=3):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(b'\xff\xff\xff\xff' + texte.encode('latin-1') + b'\n', ('127.0.0.1', MW2_PORT))
        return s.recv(65535).decode('latin-1', 'replace')


def mw2_rcon(commande):
    return mw2_requete(f"rcon {(ACCUEIL / '.iw4x-rcon').read_text().strip()} {commande}")


SANS_COULEUR = re.compile(r'\^\d')


def mw2_statut():
    """Carte, mode et joueurs (nom, score, ping) via « getstatus », None si le serveur ne répond pas."""
    try:
        lignes = mw2_requete('getstatus').split('\n')
    except OSError:
        return None
    champs = lignes[1].split('\\')[1:]
    info = dict(zip(champs[::2], champs[1::2]))
    joueurs = []
    for l in lignes[2:]:
        m = re.match(r'^(-?\d+) (\d+) "(.*)"', l.strip())
        if m:
            joueurs.append({'nom': SANS_COULEUR.sub('', m.group(3)), 'score': int(m.group(1)), 'ping': int(m.group(2))})
    return {'carte': info.get('mapname', '').removeprefix('mp_'), 'mode': info.get('g_gametype'), 'joueurs': joueurs}


def cmd_mw2_etat():
    sortie({'installe': (IW4X / 'iw4x.exe').exists(), 'service': service_etat('iw4x'),
            'statut': mw2_statut() if service_actif('iw4x') else None, 'reglages': mw2_reglages(),
            'modes': MODES_MW2, 'cartes': CARTES_MW2, 'port': MW2_PORT})


def cmd_mw2_reglages():
    try:
        r = json.loads(sys.stdin.read(10000))
    except ValueError:
        r = None
    if not mw2_valide(r):
        return sortie({'ok': False, 'message': 'Réglages invalides.'})
    r = {k: r[k] for k in MW2_DEFAUTS}
    MW2_REGLAGES.write_text(json.dumps(r, ensure_ascii=False, indent=1))
    (IW4X / 'userraw' / 'panneau.cfg').write_text(mw2_cfg(r))
    relance = service_actif('iw4x')
    if relance:
        subprocess.run(['sudo', 'systemctl', 'restart', 'iw4x'], check=True)
    sortie({'ok': True, 'relance': relance})


def cmd_mw2_action():
    """{"action": carte | suivante | expulser | dire, "valeur": …} sur l'entrée : envoyé par RCON au serveur allumé."""
    try:
        d = json.loads(sys.stdin.read(2000))
        action, valeur = str(d.get('action', '')), str(d.get('valeur', ''))
    except (ValueError, AttributeError):
        return sortie({'ok': False, 'message': 'action invalide'})
    if action == 'carte' and valeur in CARTES_MW2:
        commande = f'map mp_{valeur}'
    elif action == 'suivante':
        commande = 'map_rotate'
    elif action == 'expulser' and re.match(r'^[^"\\;]{1,32}$', valeur):
        commande = f'kick "{valeur}"'
    elif action == 'dire' and re.match(r'^[^"\\;]{1,150}$', valeur):
        commande = f'say "{valeur}"'
    else:
        return sortie({'ok': False, 'message': 'action invalide'})
    if not service_actif('iw4x'):
        return sortie({'ok': False, 'message': 'Le serveur MW2 est arrêté.'})
    try:
        mw2_rcon(commande)
    except TimeoutError:
        pass  # commande partie : le serveur charge la nouvelle carte et ne répond qu'après
    except OSError as e:
        return sortie({'ok': False, 'message': f'Le serveur ne répond pas ({e}).'})
    sortie({'ok': True})


# ---------- Commandes ----------

def lire_tache():
    try:
        t = json.loads(TACHE.read_text())
    except (OSError, ValueError):
        return None
    if t.get('etat') == 'en_cours' and not Path(f"/proc/{t.get('pid')}").exists():
        t.update(etat='echec', message='la tâche s\'est arrêtée brutalement')
    return t


def cmd_etat():
    s = subprocess.run(['systemctl', 'is-active', 'minecraft'], capture_output=True, text=True).stdout.strip()
    with open('/proc/meminfo') as f:
        mem = {k: int(v.split()[0]) * 1024 for k, v in (l.split(':', 1) for l in f)}
    du = shutil.disk_usage(ACCUEIL)
    actif = instance_active()
    with open('/proc/uptime') as f:
        uptime = float(f.read().split()[0])
    sortie({'service': s, 'actif': actif, 'info': lire_info(INSTANCES / actif) if actif else None, 'uptime': uptime,
            'ram': [mem['MemTotal'] - mem['MemAvailable'], mem['MemTotal']], 'disque': [du.used, du.total],
            'charge': os.getloadavg()[0], 'coeurs': os.cpu_count(), 'tache': lire_tache(),
            'palworld': {'installe': (PALWORLD / 'PalServer.sh').exists(),
                         'service': subprocess.run(['systemctl', 'is-active', 'palworld'], capture_output=True,
                                                   text=True).stdout.strip()}})


def cmd_palworld_reglages():
    """Sans entrée : affiche les réglages. Avec un JSON sur l'entrée standard : les valide puis les enregistre."""
    if not PAL_INI.exists():
        return sortie({'ok': False, 'message': 'Palworld n\'est pas encore installé.'})
    brut = sys.stdin.read().strip()
    if brut:
        try:
            demande = json.loads(brut)
            reglages = {k: str(demande[k]).strip() for k in (*PAL_REGLAGES, *PAL_OPTIONS) if k in demande}
        except (ValueError, TypeError, AttributeError):
            return sortie({'ok': False, 'message': 'réglages illisibles'})
        presentes = pal_lire(PAL_OPTIONS)
        mauvais = [k for k, v in reglages.items()
                   if not (PAL_REGLAGES[k].match(v) if k in PAL_REGLAGES else k in presentes and pal_option_valide(k, v))]
        if mauvais or not reglages:
            return sortie({'ok': False, 'message': 'réglage invalide : ' + ', '.join(mauvais or ['aucun'])})
        pal_ecrire(reglages)
    pal_admin()
    sortie({'ok': True, 'reglages': pal_lire(), 'options': pal_lire(PAL_OPTIONS),
            'types': {k: list(t) for k, t in PAL_OPTIONS.items()},
            'defauts': pal_lire(PAL_OPTIONS, PALWORLD / 'DefaultPalWorldSettings.ini'), 'actif': service_actif('palworld')})


def cmd_instances():
    actif = instance_active()
    sortie([{'nom': d.name, **lire_info(d), 'actif': d.name == actif}
            for d in sorted(INSTANCES.iterdir()) if d.is_dir() and NOM.match(d.name)])


def cmd_amis():
    def noms(f):
        try:
            return [e['name'] for e in json.loads((ACTIF / f).read_text())]
        except (OSError, ValueError, KeyError, TypeError):
            return []
    sortie({'amis': noms('whitelist.json'), 'ops': noms('ops.json')})


def cmd_lancer(args):
    if not args or args[0] not in TACHES:
        return sortie({'ok': False, 'message': 'tâche inconnue'})
    regles = VALIDATION[args[0]]
    if len(args) - 1 != len(regles) or not all(r(a) for r, a in zip(regles, args[1:])):
        return sortie({'ok': False, 'message': 'paramètres invalides'})
    t = lire_tache()
    if t and t.get('etat') == 'en_cours':
        return sortie({'ok': False, 'message': f"une tâche est déjà en cours : {t['titre']}"})
    with open(JOURNAL_TACHE, 'w') as journal:
        p = subprocess.Popen([sys.executable, '-u', __file__, 'executer', *args], stdout=journal,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    TACHE.write_text(json.dumps({'titre': TITRES[args[0]], 'etat': 'en_cours', 'pid': p.pid, 'debut': time.time()}))
    sortie({'ok': True})


def cmd_executer(args):
    try:
        t = {'etat': 'ok', 'message': TACHES[args[0]](*args[1:])}
        log(t['message'])
    except Exception as e:
        log(f'ÉCHEC : {e}')
        t = {'etat': 'echec', 'message': str(e)}
        if not service_actif('palworld'):  # ne jamais laisser le serveur éteint (sans couper Palworld)
            subprocess.run(['sudo', 'systemctl', 'start', 'minecraft'])
    TACHE.write_text(json.dumps({**json.loads(TACHE.read_text()), **t, 'fin': time.time()}))


def cmd_suivi():
    texte = JOURNAL_TACHE.read_text(errors='replace') if JOURNAL_TACHE.exists() else ''
    lignes = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', texte).splitlines()[-40:]  # sans les codes couleur de SteamCMD
    sortie({**(lire_tache() or {}), 'journal': lignes})


def autotest():
    assert [java_pour(v) for v in ('1.12.2', '1.18.2', '1.20.1', '1.20.6', '1.21.1', '26.3', '')] == [8, 17, 17, 21, 21, 25, 21]
    r = {**MW2_DEFAUTS, 'mdp': 'abc_1', 'cartes': ['rust', 'nuked'], 'hardcore': True}
    assert mw2_valide(r) and 'sv_maprotation "gametype war map mp_rust map mp_nuked"' in mw2_cfg(r)
    assert 'g_hardcore "1"' in mw2_cfg(r)
    assert not mw2_valide({**r, 'nom': 'x"; quit'}) and not mw2_valide({**r, 'cartes': ['rust"']})
    assert not mw2_valide({**r, 'max': 40}) and not mw2_valide({**r, 'mode': 'gun'}) and not mw2_valide({**r, 'mdp': 'a b'})
    base = Path('/tmp')
    try:
        chemin_sur(base, '../etc/passwd')
        raise AssertionError('traversée acceptée')
    except RuntimeError:
        pass
    assert chemin_sur(base, 'a/b.txt') == Path('/tmp/a/b.txt')
    assert ascii('Serveur de Test - Pokémon') == 'Serveur de Test - Pokmon'
    assert VALIDATION['modrinth'][0]('abc-DEF_1') and not VALIDATION['modrinth'][0]('a;b')
    assert not VALIDATION['ftb'][0]('12a') and TITRE.match('StoneBlock-4_1.2') and not TITRE.match('a b')
    assert PAL_REGLAGES['ServerName'].match("Serveur de Test !") and not PAL_REGLAGES['ServerName'].match('a",x=(')
    assert PAL_REGLAGES['ServerPlayerMaxNum'].match('32') and not PAL_REGLAGES['ServerPlayerMaxNum'].match('33')
    assert PAL_REGLAGES['ServerPassword'].match('') and not PAL_REGLAGES['ServerPassword'].match('a b')
    def tag(t, nom, charge):
        return bytes([t]) + len(nom).to_bytes(2, 'big') + nom.encode() + charge
    joueur = (tag(11, 'UUID', (4).to_bytes(4, 'big') + bytes.fromhex('0123456789abcdef0011223344556677'))
              + tag(9, 'Inventory', b'\x0a' + bytes(4)) + tag(9, 'Pos', b'\x06' + (3).to_bytes(4, 'big') + bytes(24))
              + tag(8, 'Nom', b'\x00\x02ok') + b'\x00')
    ld = Path('/tmp/level-autotest.dat')
    ld.write_bytes(gzip.compress(tag(10, '', tag(10, 'Data', tag(3, 'version', bytes(4)) + tag(10, 'Player', joueur) + b'\x00') + b'\x00')))
    uuid, dat = joueur_solo(ld)
    assert uuid == '01234567-89ab-cdef-0011-223344556677' and gzip.decompress(dat) == b'\x0a\x00\x00' + joueur
    ld.write_bytes(gzip.compress(tag(10, '', tag(10, 'Data', tag(3, 'version', bytes(4)) + b'\x00') + b'\x00')))
    assert joueur_solo(ld) is None
    ld.unlink()
    assert not VALIDATION['supprimer'][0](instance_active() or 'x') and not VALIDATION['supprimer'][0]('../mc')
    assert pal_format('PalEggDefaultHatchingTime', '0') == '0.000000' and pal_format('bHardcore', 'True') == 'True'
    assert pal_format('ServerName', 'x') == '"x"' and pal_format('BaseCampWorkerMaxNum', '20') == '20'
    assert pal_option_valide('DeathPenalty', 'Item') and not pal_option_valide('DeathPenalty', 'x')
    assert pal_option_valide('ExpRate', '2.5') and not pal_option_valide('ExpRate', '100') and not pal_option_valide('ExpRate', 'abc')
    assert VALIDATION['nouveau-monde'][1]('aleatoire') and VALIDATION['nouveau-monde'][1]('-123') and not VALIDATION['nouveau-monde'][1]('a b')
    assert PROPRIETES_MC['difficulty'].match('hard') and not PROPRIETES_MC['max-players'].match('51')
    global PAL_INI
    vrai, PAL_INI = PAL_INI, Path('/tmp/pal-autotest.ini')
    PAL_INI.write_text('[x]\nOptionSettings=(Difficulty=None,ServerPlayerMaxNum=32,ServerName="Default Palworld Server",'
                       'AdminPassword="",ServerPassword="",PublicPort=8211)\n')
    pal_ecrire({'ServerName': 'Serveur de Test', 'ServerPlayerMaxNum': '16', 'ServerPassword': 'abc'})
    assert pal_lire() == {'ServerName': 'Serveur de Test', 'ServerPassword': 'abc', 'ServerPlayerMaxNum': '16'}
    assert 'AdminPassword=""' in PAL_INI.read_text()
    PAL_INI.unlink()
    PAL_INI = vrai
    print('autotest OK')


if __name__ == '__main__':
    INSTANCES.mkdir(exist_ok=True)
    ENVOIS.mkdir(exist_ok=True)
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else ''), sys.argv[2:]
    {'etat': cmd_etat, 'instances': cmd_instances, 'amis': cmd_amis, 'suivi': cmd_suivi, 'autotest': autotest,
     'palworld-reglages': cmd_palworld_reglages, 'mc-regles': cmd_mc_regles, 'mc-proprietes': cmd_mc_proprietes,
     'mc-regle': lambda: cmd_mc_regle(args), 'pal-joueurs': cmd_pal_joueurs, 'pal-action': lambda: cmd_pal_action(args),
     'mw2-etat': cmd_mw2_etat, 'mw2-reglages': cmd_mw2_reglages, 'mw2-action': cmd_mw2_action,
     'lancer': lambda: cmd_lancer(args), 'executer': lambda: cmd_executer(args)}.get(
        cmd, lambda: sortie({'erreur': f'commande inconnue : {cmd}'}))()
