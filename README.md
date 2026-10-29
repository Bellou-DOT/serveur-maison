# serveur-maison

Un vieux PC portable transformé en serveur pour toute la maison, piloté depuis un portail web et le téléphone.
Tout est en français et prévu pour **Ubuntu 24.04** sur un PC dédié (8 Go de mémoire minimum).

## Ce qu'il y a dedans

| | |
|---|---|
| 🏠 **Portail** | Une page d'accueil (`http://IP-du-serveur`) avec une tuile par service, les nouveautés, les téléchargements en cours, l'état du serveur en direct, le réveil et la mise en veille du PC. Les appareils qui ne sont pas admin voient une version simplifiée. |
| 🎬 **Films et séries** | Jellyfin pour regarder, Seerr pour demander un film, Radarr/Sonarr/Prowlarr pour aller le chercher, Transmission pour le télécharger. Un film supprimé dans Jellyfin est supprimé partout (fichier, Radarr, Seerr, téléchargement). Au-delà de 10 Go, le téléchargement attend ton accord par notification. |
| 📸 **Photos** | Immich : sauvegarde automatique des photos du téléphone. |
| 🔒 **Accès** | VPN WireGuard (QR code à scanner) pour tout utiliser depuis l'extérieur, AdGuard Home qui bloque les pubs sur le VPN, CrowdSec qui bloque les attaques. Les outils d'admin ne répondent qu'au VPN et aux appareils déclarés. |
| 🔔 **Surveillance** | Notifications sur le téléphone (appli ntfy) : disque plein, surchauffe, disque en mauvaise santé, service en panne, coupure de courant, téléchargement fini. Mises à jour des services installées toutes seules après vérification, avec un seul résumé par jour. Sauvegarde des réglages chaque nuit. |

## Installation

```bash
git clone <adresse-du-dépôt> serveur-maison
cd serveur-maison
./installer.sh                         # crée /etc/serveur-maison.conf
sudo nano /etc/serveur-maison.conf     # tes réglages : noms, adresse IP, ce qu'il faut installer…
./installer.sh                         # installe tout (compter 15 à 30 minutes)
```

Le fichier de réglages est commenté ligne par ligne (modèle : [config.exemple](config.exemple)). On peut relancer `./installer.sh` autant de fois qu'on veut, par exemple après avoir activé un composant : rien de ce qui existe n'est écrasé.

Avant de commencer :
- **Réserve une adresse IP fixe** pour le serveur dans ta box (bail DHCP statique), et mets-la dans `IP_SERVEUR`.
- **Ouvre les ports** dans la box vers le serveur : UDP 51820 (VPN), c'est tout.
- Une **adresse fixe sur Internet** est conseillée (gratuite sur [duckdns.org](https://www.duckdns.org)) : `DOMAINE` et `DUCKDNS_JETON`.

## Après l'installation

Quelques réglages passent forcément par les pages web des services (création de comptes, choix personnels) :

1. **Notifications** : installe l'appli **ntfy** sur le téléphone et abonne-toi au sujet affiché à la fin de l'installation.
2. **VPN** : scanne `~/vpn/telephone.png` dans l'appli **WireGuard**. Un autre appareil : `~/outils/vpn-ajouter.sh portable`.
3. **AdGuard Home** (`http://IP:3000`) : à l'assistant, choisis l'interface **wg0 (10.8.0.1)** pour le DNS. Puis *Filtres → Réécritures DNS* : `HOTE` → `10.8.0.1` (remplace HOTE par ta valeur), pour que `http://HOTE` marche par le VPN.
4. **Jellyfin** (`http://IP:8096`) : crée ton compte, puis les bibliothèques **Films** → `MEDIAS/Film` et **Séries** → `MEDIAS/Séries`. Pour autoriser la suppression depuis l'appli : *Tableau de bord → Utilisateurs → ton compte → Autoriser la suppression de médias*.
5. **Transmission** : rien à faire, il s'ouvre depuis le portail (mot de passe dans `~/.transmission-mdp`).
6. **Prowlarr** (`:9696`), **Radarr** (`:7878`), **Sonarr** (`:8989`) : crée les comptes, ajoute tes sources dans Prowlarr et relie-le à Radarr et Sonarr. Dans Radarr et Sonarr :
   - client de téléchargement **Transmission** (`127.0.0.1:9091`, ton nom d'utilisateur, mot de passe de `~/.transmission-mdp`), option **Ajouter en pause** cochée ;
   - dossier racine `MEDIAS_BRUT/Film` (Radarr) et `MEDIAS_BRUT/Séries` (Sonarr) ;
   - *Connexions → Script personnalisé*, déclencheur **À la récupération**, chemin `~/outils/garde-fou-film.sh` : c'est lui qui lance les petits téléchargements et demande ton accord pour les gros.
7. **Seerr** (`:5055`) : connecte-le à Jellyfin, Radarr et Sonarr.
8. **Immich** (`:2283`) : crée le compte admin, puis installe l'appli sur le téléphone avec l'adresse `http://IP:2283`.
9. **Partage réseau** : `sudo smbpasswd -a $USER` pour choisir le mot de passe du dossier partagé `\\IP\medias`.
10. **Copie des sauvegardes sur Google Drive** (facultatif) : `rclone config`, remote nommé `drive`. La sauvegarde de la nuit l'utilise toute seule.

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
- Rien d'autre que le VPN n'est ouvert sur Internet. Les outils d'admin (Radarr, Sonarr, Prowlarr, Transmission, AdGuard) sont réservés au VPN et aux adresses MAC de `ADMINS_MAC`.
- Les mots de passe générés sont dans des fichiers lisibles par ton compte seul (`~/.transmission-mdp`…), et dans la sauvegarde des réglages.
- Ce dépôt installe des logiciels. Ce que tu télécharges avec reste sous ta responsabilité.
