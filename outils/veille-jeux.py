#!/usr/bin/env python3
"""Veille des serveurs de jeu (service « veille-jeux »).

- Minecraft, Palworld ou MW2 allumé sans joueur pendant 5 h : il est arrêté (RAM et processeur libérés).
- Aucun jeu allumé : ce programme écoute à leur place sur leurs ports.
    Minecraft (TCP 25565) : la liste des serveurs affiche « en veille » ; se connecter le réveille
    (le joueur est déconnecté avec un message, il revient une minute plus tard).
    Palworld (UDP 8211) : le premier essai de connexion le réveille.
    Bedrock via Geyser (UDP 19132-19133, TCP 19132) : le premier contact réveille Minecraft.
    Modern Warfare 2 / IW4x (UDP 28960) : le premier essai de connexion le réveille (il tourne à côté des autres).
- Dès qu'un jeu démarre (réveil, panneau, sauvegarde de nuit), les ports lui sont rendus.
--test : vérifications du protocole Minecraft, sans réseau.
"""
import json
import select
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gestion  # noqa: E402  (pal_api, lire_tache : mêmes réglages que le panneau)

DELAI = 5 * 3600
MC_PORT, PAL_PORT = 25565, 8211
BEDROCK_PORT, NETHERNET_PORT = 19132, 19133  # Geyser : joueurs console / téléphone


# ---------- Protocole Minecraft (juste ce qu'il faut pour la liste des serveurs et la connexion) ----------

def varint(n):
    n &= 0xFFFFFFFF
    out = b''
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def lire_varint(f):
    n = 0
    for i in range(5):
        b = f(1)
        if not b:
            raise EOFError
        n |= (b[0] & 0x7F) << (7 * i)
        if not b[0] & 0x80:
            return n - (1 << 32) if n & (1 << 31) else n
    raise ValueError('varint trop long')


def chaine(s):
    b = s.encode()
    return varint(len(b)) + b


def paquet(pid, corps=b''):
    d = varint(pid) + corps
    return varint(len(d)) + d


def lire_paquet(f):
    taille = lire_varint(f)
    if not 0 < taille < 65536:
        raise ValueError('paquet invalide')
    d = f(taille)
    pos = [0]

    def lit(n):
        r = d[pos[0]:pos[0] + n]
        pos[0] += n
        return r
    return lire_varint(lit), lit


def lecteur(sock):
    def lit(n):
        d = b''
        while len(d) < n:
            x = sock.recv(n - len(d))
            if not x:
                raise EOFError
            d += x
        return d
    return lit


def mc_joueurs():
    """Nombre de joueurs connectés (ping de la liste des serveurs), None si le serveur ne répond pas encore."""
    try:
        with socket.create_connection(('127.0.0.1', MC_PORT), timeout=5) as s:
            s.sendall(paquet(0, varint(-1) + chaine('127.0.0.1') + struct.pack('>H', MC_PORT) + varint(1)) + paquet(0))
            pid, lit = lire_paquet(lecteur(s))
            return json.loads(lit(lire_varint(lit)))['players']['online']
    except Exception:
        return None


def pal_joueurs():
    try:
        return len(gestion.pal_api('players').get('players', []))
    except Exception:
        return None


def repondre_mc(client, reveiller):
    """Joue le serveur Minecraft endormi auprès d'un client : statut « en veille » ou réveil à la connexion."""
    client.settimeout(5)
    f = lecteur(client)
    pid, lit = lire_paquet(f)
    if pid != 0:
        return
    protocole = lire_varint(lit)
    lit(lire_varint(lit))  # adresse
    lit(2)  # port
    etat = lire_varint(lit)
    if etat == 1:  # liste des serveurs
        lire_paquet(f)
        statut = {'version': {'name': 'En veille', 'protocol': protocole}, 'players': {'max': 5, 'online': 0},
                  'description': {'text': '💤 Serveur en veille', 'color': 'gold',
                                  'extra': [{'text': '\nConnecte-toi pour le réveiller (1 minute)', 'color': 'gray'}]}}
        client.sendall(paquet(0, chaine(json.dumps(statut))))
        try:
            pid, lit = lire_paquet(f)
            if pid == 1:
                client.sendall(paquet(1, lit(8)))
        except Exception:
            pass
    elif etat in (2, 3):  # connexion
        try:
            lire_paquet(f)  # pseudo du joueur : lu, sinon fermer la connexion l'interromprait avant le message
        except Exception:
            pass
        message = reveiller()
        client.sendall(paquet(0, chaine(json.dumps({'text': message}))))
        client.shutdown(socket.SHUT_WR)
        try:
            client.recv(1024)  # laisse le client lire le message et fermer de son côté
        except OSError:
            pass


# ---------- Services ----------

IW4X_PORT = 28960  # Modern Warfare 2 (IW4x)
# Jeux d'un même groupe ne tournent jamais ensemble (Minecraft et Palworld sont en conflit) : les ports du groupe
# ne sont gardés que si aucun de ses jeux ne tourne. IW4x, léger, tourne à côté des autres.
GROUPES = {'minecraft': ('minecraft', 'palworld'), 'palworld': ('minecraft', 'palworld'), 'iw4x': ('iw4x',)}
JEUX = ('minecraft', 'palworld', 'iw4x')


def iw4x_joueurs():
    """Joueurs connectés au serveur MW2 (requête « getstatus » du protocole Quake 3), None s'il ne répond pas."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(3)
            s.sendto(b'\xff\xff\xff\xffgetstatus\n', ('127.0.0.1', IW4X_PORT))
            lignes = s.recv(65535).decode('latin-1').split('\n')
        return len([l for l in lignes[2:] if l.strip()])
    except Exception:
        return None


JOUEURS = {'minecraft': lambda: mc_joueurs(), 'palworld': lambda: pal_joueurs(), 'iw4x': iw4x_joueurs}


def etats():
    r = subprocess.run(['systemctl', 'show', '-p', 'ActiveState', '--value', *JEUX], capture_output=True, text=True).stdout.split()
    return dict(zip(JEUX, r))


def demarrer(jeu):
    subprocess.run(['sudo', 'systemctl', 'start', '--no-block', jeu])


def tache_en_cours():
    t = gestion.lire_tache()
    return bool(t and t.get('etat') == 'en_cours')


def udp(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('0.0.0.0', port))
    return s


def ouvrir_ecoutes(groupe):
    """Sockets gardés à la place des jeux endormis d'un groupe : socket -> jeu à réveiller."""
    if groupe == ('iw4x',):
        return {udp(IW4X_PORT): 'iw4x'}
    return {socket.create_server(('0.0.0.0', MC_PORT)): 'minecraft', udp(PAL_PORT): 'palworld',
            # Bedrock (Geyser) : RakNet en UDP 19132, signalisation NetherNet en TCP 19132, NetherNet en UDP 19133
            udp(BEDROCK_PORT): 'minecraft', socket.create_server(('0.0.0.0', BEDROCK_PORT)): 'minecraft',
            udp(NETHERNET_PORT): 'minecraft'}


def boucle():
    ecoutes = {}  # groupe -> {socket: jeu}
    derniere_activite = {}  # jeu -> heure du dernier joueur vu (ou du démarrage)
    prochaine_mesure = 0
    reveil = {'jeu': None, 'quand': 0}

    def reveiller(jeu):
        def f():
            if time.time() - reveil['quand'] > 120:
                reveil.update(jeu=jeu, quand=time.time())
            return '⏰ Le serveur se réveille ! Reconnecte-toi dans une minute.'
        return f

    def fermer(groupe):
        for s in ecoutes.pop(groupe, {}):
            s.close()

    while True:
        e = etats()
        allume = [j for j, s in e.items() if s in ('active', 'activating', 'reloading')]
        for groupe in set(GROUPES.values()):
            occupe = any(j in allume for j in groupe)
            # Un jeu du groupe démarre (réveil, panneau, sauvegarde) : on lui rend ses ports tout de suite
            if occupe or reveil['jeu'] in groupe:
                fermer(groupe)
            if reveil['jeu'] in groupe and not occupe:
                demarrer(reveil['jeu'])
                reveil['jeu'] = None
            elif not occupe and groupe not in ecoutes:
                try:
                    ecoutes[groupe] = ouvrir_ecoutes(groupe)
                except OSError:  # port encore pris par le jeu qui s'arrête : on réessaie
                    pass

        sockets = {s: j for d in ecoutes.values() for s, j in d.items()}
        prets = select.select(list(sockets), [], [], 1)[0] if sockets else time.sleep(1) or []
        for s in prets:
            jeu = sockets[s]
            if s.type == socket.SOCK_DGRAM:
                donnees, _ = s.recvfrom(2048)
                # MW2 : la liste des serveurs l'interroge sans arrêt (getinfo / getstatus) ; seul un joueur qui se
                # connecte demande un « challenge »
                if jeu == 'iw4x' and b'getchallenge' not in donnees and b'connect' not in donnees:
                    continue
                if not reveil['jeu']:
                    reveil.update(jeu=jeu, quand=time.time())
                continue
            client, _ = s.accept()
            try:
                if s.getsockname()[1] == MC_PORT:
                    repondre_mc(client, reveiller('minecraft'))
                elif not reveil['jeu']:
                    reveil.update(jeu=jeu, quand=time.time())
            except Exception:
                pass
            finally:
                client.close()

        for jeu in list(derniere_activite):
            if jeu not in allume:
                del derniere_activite[jeu]
        for jeu in allume:
            derniere_activite.setdefault(jeu, time.time())
        if time.time() < prochaine_mesure:
            continue
        prochaine_mesure = time.time() + 60
        for jeu in allume:
            n = JOUEURS[jeu]()
            if n != 0 or e[jeu] != 'active' or tache_en_cours():  # joueurs, démarrage en cours ou tâche du panneau
                derniere_activite[jeu] = time.time()
            elif time.time() - derniere_activite[jeu] >= DELAI:
                print(f'{jeu} : personne depuis 5 h, mise en veille', flush=True)
                subprocess.run(['sudo', 'systemctl', 'stop', jeu])


def test():
    for n in (0, 1, 127, 128, 25565, -1, 2 ** 31 - 1):
        tampon = [varint(n)]
        assert lire_varint(lambda k: _lit(tampon, k)) == n, n
    # Un client demande le statut, puis se connecte : le réveil est demandé
    a, b = socket.socketpair()
    a.sendall(paquet(0, varint(770) + chaine('x') + struct.pack('>H', 25565) + varint(1)) + paquet(0) + paquet(1, b'12345678'))
    repondre_mc(b, lambda: 'non')
    pid, lit = lire_paquet(lecteur(a))
    statut = json.loads(lit(lire_varint(lit)))
    assert pid == 0 and statut['version']['protocol'] == 770 and statut['players']['online'] == 0
    assert lire_paquet(lecteur(a))[1](8) == b'12345678'
    a, b = socket.socketpair()
    reveils = []
    a.sendall(paquet(0, varint(770) + chaine('x') + struct.pack('>H', 25565) + varint(2)) + paquet(0, chaine('Testeur')))
    repondre_mc(b, lambda: reveils.append(1) or 'réveil')
    pid, lit = lire_paquet(lecteur(a))
    assert pid == 0 and json.loads(lit(lire_varint(lit)))['text'] == 'réveil' and reveils == [1]
    print('test OK')


def _lit(tampon, k):
    r, tampon[0] = tampon[0][:k], tampon[0][k:]
    return r


if __name__ == '__main__':
    test() if sys.argv[1:] == ['--test'] else boucle()
