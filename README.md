# serveur-maison

Un vieux PC portable transformé en serveur pour toute la maison, piloté depuis un portail web et le téléphone.
Tout est en français et prévu pour **Ubuntu 24.04** sur un PC dédié (8 Go de mémoire minimum).

Pas besoin de savoir installer Linux : depuis Windows, une **clé USB d'installation automatique** installe Ubuntu
Server sur le futur serveur (⚠ son disque est effacé), puis tout le reste, sans écran ni clavier. Ubuntu déjà installé ?
L'installateur marche aussi seul.

## Ce qu'il y a dedans

| | |
|---|---|
| 🏠 **Portail** | Une page d'accueil (`http://IP-du-serveur`) avec une tuile par service, les nouveautés, les téléchargements en cours, l'état du serveur en direct, le réveil et la mise en veille du PC. Les appareils qui ne sont pas admin voient une version simplifiée. |
| 🎬 **Films et séries** | Jellyfin pour regarder, Seerr pour demander un film, Radarr/Sonarr/Prowlarr pour aller le chercher, Transmission pour le télécharger. Un film supprimé dans Jellyfin est supprimé partout (fichier, Radarr, Seerr, téléchargement). Au-delà de 10 Go, le téléchargement attend ton accord par notification. |
| 📸 **Photos** | Immich : sauvegarde automatique des photos du téléphone. |
| 🔒 **Accès** | VPN Tailscale (gratuit, aucun port à ouvrir dans la box) pour tout utiliser depuis l'extérieur, et le serveur comme sortie internet sur un Wi-Fi public ; AdGuard Home qui bloque les pubs, même dehors ; CrowdSec qui bloque les attaques. Les outils d'admin ne répondent qu'à Tailscale et aux appareils déclarés. |
| 🔔 **Surveillance** | Notifications sur le téléphone (appli ntfy) : disque plein, surchauffe, disque en mauvaise santé, service en panne, coupure de courant, téléchargement fini. Mises à jour des services installées toutes seules après vérification, avec un seul résumé par jour. Sauvegarde des réglages chaque nuit. |

## Installation

### Le plus simple : demander à une IA de préparer la clé USB

Sur ton PC Windows, avec un assistant de code (par exemple [Claude Code](https://claude.com/claude-code)), dis-lui :

> Récupère `https://github.com/Bellou-DOT/serveur-maison.git` avec git et installe le serveur.

Il te pose quelques questions, prépare la clé USB (tu tapes toi-même tes mots de passe dans la fenêtre qui s'ouvre)
et te dit quoi faire ensuite : brancher la clé sur le futur serveur, l'allumer, taper **yes**. Tout le reste se
fait seul. Ses consignes sont dans [AGENTS.md](AGENTS.md).

### La clé USB à la main

Sur un PC Windows, avec une clé USB d'au moins 8 Go :

1. Clic droit sur [cle-usb/preparer-cle.ps1](cle-usb/preparer-cle.ps1) → **Exécuter avec PowerShell**. Il pose quelques
   questions (nom du serveur, compte, Wi-Fi, films et photos oui/non), télécharge Ubuntu Server (4 Go) et prépare la clé.
2. Abonne-toi dans l'appli **ntfy** au sujet qu'il affiche.
3. Démarre le futur serveur sur la clé (touche du menu de démarrage : F12, F11, F10, Échap ou F2 selon la marque),
   tape **yes** à l'unique question. ⚠ Le disque de ce PC est entièrement effacé.
4. Retire la clé quand le PC redémarre. Tout le reste se fait seul : tu reçois une notification « Serveur prêt ✅ »
   avec l'adresse du portail et ce qui reste à faire (créer tes comptes).

La clé contient les mots de passe du Wi-Fi et du compte : reformate-la ensuite.

### Ubuntu déjà installé

Il faut un PC dédié avec **Ubuntu 24.04** installé, branché sur la box. Ensuite, deux façons de faire.

#### Demander à une IA

Installe un assistant de code en ligne de commande sur le serveur (par exemple [Claude Code](https://claude.com/claude-code)),
ouvre-le dans le dossier du dépôt et dis-lui simplement **« installe le serveur »**.
Il suit les consignes de [AGENTS.md](AGENTS.md) : il remplit les réglages, installe tout, relie les services entre eux,
et ne te demande que ce qui doit venir de toi (créer tes comptes, choisir tes mots de passe, réglages de ta box).

```bash
git clone https://github.com/Bellou-DOT/serveur-maison.git serveur-maison
cd serveur-maison
claude          # puis : « installe le serveur »
```

#### À la main

```bash
git clone https://github.com/Bellou-DOT/serveur-maison.git serveur-maison
cd serveur-maison
./installer.sh                         # crée /etc/serveur-maison.conf
sudo nano /etc/serveur-maison.conf     # tes réglages : noms, adresse IP, ce qu'il faut installer…
./installer.sh                         # installe et règle tout (15 à 30 minutes)
```

Avant de commencer :
- **Réserve une adresse IP fixe** pour le serveur dans ta box (bail DHCP statique), et mets-la dans `IP_SERVEUR`.
- **Crée un compte [Tailscale](https://tailscale.com)** (gratuit) : le serveur et ton téléphone s'y connecteront.
  Aucun port n'est à ouvrir dans la box.

Le fichier de réglages est commenté ligne par ligne (modèle : [config.exemple](config.exemple)). On peut relancer
`./installer.sh` autant de fois qu'on veut : rien de ce qui existe n'est écrasé.

À la fin, l'installation a déjà relié les services entre eux (bibliothèques Jellyfin, Transmission dans Radarr et Sonarr,
Prowlarr, garde-fou des gros films, notifications…) et affiche **la liste de ce qui reste à faire par toi** :
surtout créer tes comptes. Après chaque étape, relance `~/outils/configurer.py` : il termine ce qui vient de devenir
possible (Seerr, AdGuard, Tailscale). Il reste aussi, côté téléphone :

- **Notifications** : appli **ntfy**, abonne-toi au sujet affiché à la fin de l'installation.
- **VPN** : appli **Tailscale**, connectée au même compte que le serveur. Depuis dehors, les adresses sont les mêmes
  qu'à la maison (`http://IP-du-serveur`…). Pour passer par la connexion de la maison sur un Wi-Fi public :
  Exit node → le serveur. Un autre appareil : installe Tailscale dessus avec le même compte.
- **Copie des sauvegardes sur Google Drive** (facultatif) : `rclone config`, remote nommé `drive`.

### Disque externe pour les films

Monte-le de façon permanente (ligne dans `/etc/fstab`, avec `nofail` pour que le serveur démarre même s'il est débranché), puis mets son dossier dans `MEDIAS_BRUT`. Exemple pour un disque exFAT :

```
UUID=XXXX-XXXX /mnt/medias-brut exfat rw,nofail,x-systemd.automount,uid=TON_COMPTE,gid=jellyfin,dmask=0002,fmask=0113 0 0
```

### Réveil du PC à distance

Remplis `PC_MAC` et `PC_IP`, active le réveil par le réseau (Wake on LAN / WLAN) dans le BIOS et dans Windows. Pour le bouton « mettre en veille », copie [windows/pc-agent.ps1](windows/pc-agent.ps1) sur le PC et lance-le caché à l'ouverture de session (Planificateur de tâches) :

```
powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File pc-agent.ps1 -Serveur 192.168.1.20
```

## Fonctionnement

- Les scripts vivent dans `~/outils`.
- Tous les scripts lisent `/etc/serveur-maison.conf`. Chacun a ses vérifications : `python3 ~/outils/portail.py --test`, `vitrine.py --test`, `mises-a-jour.py --test`…
- Tâches automatiques (crontab) : surveillance toutes les 5 min, suppressions toutes les 5 min, mises à jour toutes les heures, sauvegarde à 5 h.
- Jellyfin ne lit pas directement les fichiers téléchargés mais une « vitrine » de liens (`MEDIAS`) : c'est ce qui permet de savoir qu'un film a été supprimé depuis l'appli, et de cacher les fichiers pas encore finis.

## Sécurité

- Le compte qui installe peut utiliser `sudo` sans mot de passe (nécessaire aux tâches de nuit) : utilise un PC dédié au serveur.
  Aucun service joignable par le réseau ne tourne sous ce compte : le portail, Transmission, Radarr, Sonarr et Prowlarr
  tournent sous le compte `medias`, qui n'a aucun droit d'administration.
- Rien n'est ouvert sur Internet, y compris en IPv6 (le pare-feu du serveur n'accepte que le réseau de la maison,
  même si la box laisse passer) : Tailscale passe sans port ouvert. Les outils d'admin (Radarr, Sonarr, Prowlarr,
  Transmission, AdGuard) sont réservés à Tailscale et aux adresses MAC de `ADMINS_MAC`.
- Tailscale : seuls les appareils de ton compte entrent. La société Tailscale met les appareils en relation, mais ne
  voit pas ce qui passe dans le tunnel (chiffré de bout en bout).
- Le portail refuse les adresses qui ne sont pas les siennes (protection contre le « DNS rebinding ») et, pour
  Transmission, tout ordre qui lancerait un programme ou écrirait hors du dossier des médias.
- Les mots de passe générés sont dans des fichiers lisibles par ton compte et le compte `medias` seuls
  (`/var/lib/medias/.transmission-mdp`…), et dans la sauvegarde des réglages.
- Ce dépôt installe des logiciels. Ce que tu télécharges avec reste sous ta responsabilité.

## Licence

MIT : voir [LICENSE](LICENSE). Tu peux utiliser, modifier et partager ce projet librement.
