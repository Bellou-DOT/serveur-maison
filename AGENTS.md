# Consignes pour l'IA qui installe ce serveur

Tu aides quelqu'un à installer **serveur-maison** sur son PC sous Ubuntu 24.04, depuis ce dossier.
Il te dit en général juste « installe le serveur » : tu fais tout, sauf ce qui est marqué ✋ plus bas.
Parle-lui en français simple, sans jargon : c'est souvent un débutant.

## Règles

- ✋ **Les comptes et les mots de passe sont à lui.** Tu ne crées aucun compte et tu ne tapes aucun mot de passe à sa place
  (Jellyfin, Radarr, Sonarr, Prowlarr, Seerr, Immich, AdGuard, Samba, appli du téléphone). Tu lui dis exactement
  où cliquer, tu attends qu'il te dise « c'est fait », puis tu continues.
- ✋ **Les sources de téléchargement (indexeurs de Prowlarr)** : c'est lui qui les choisit et les ajoute. Ne les
  propose pas et ne les configure pas.
- **Rien d'irréversible sans son accord** : formater ou monter un disque, modifier `/etc/fstab`, effacer des fichiers.
  Montre ce que tu vas faire et attends un « oui ».
- Tout ce que l'installation crée de secret (mots de passe générés, clés du VPN, jeton DuckDNS) reste sur le serveur :
  ne l'affiche pas en entier sans raison.

## Étapes

1. **Vérifier la machine** : `lsb_release -d` (Ubuntu 24.04 attendu), `free -h` (8 Go minimum), `df -h /`, accès Internet.
   Le compte doit pouvoir utiliser `sudo`.

2. **Remplir les réglages.** Lance `./installer.sh` une première fois : il crée `/etc/serveur-maison.conf`.
   Remplis-le toi-même (avec `sudo`) en devinant tout ce qui peut l'être, puis montre-lui le résultat :
   - `IP_SERVEUR`, `INTERFACE`, `RESEAU_MAISON` : `ip -4 -br addr`, `ip route` (interface de la route par défaut).
     Dis-lui de **réserver cette adresse dans sa box** (bail DHCP statique) et de **rediriger le port UDP 51820**
     vers elle (VPN). Ne continue pas tant qu'il n'a pas confirmé, ou explique que le VPN ne marchera pas depuis dehors.
   - Ne lui demande que : le nom de son serveur (`NOM_PORTAIL`, `HOTE` en minuscules sans espace), s'il veut les films
     (`AVEC_MEDIAS`) et les photos (`AVEC_PHOTOS`), et s'il a une adresse DuckDNS (`DOMAINE`, `DUCKDNS_JETON` :
     c'est lui qui crée le compte sur duckdns.org et te donne le jeton).
   - `ADMINS_MAC` : l'adresse MAC du PC ou téléphone d'où il administrera sans VPN (sur Windows : `getmac /v`,
     sur iPhone : Réglages → Wi-Fi → (i), en désactivant l'adresse privée pour ce réseau). Peut rester vide.
   - `PC_MAC` / `PC_IP` seulement s'il veut réveiller son PC à distance (voir README).
   - **Disque externe pour les films** : `lsblk -f`. S'il en a un, propose une ligne `/etc/fstab` (modèle dans le
     README, avec `nofail`), attends son accord, monte-le, puis mets le dossier dans `MEDIAS_BRUT`.

3. **Installer** : `./installer.sh` (15 à 30 minutes ; lance-le en arrière-plan si ton outil coupe les commandes
   longues, et suis la sortie). Il finit par lancer `~/outils/configurer.py`.
   Si une étape échoue : lis le message, corrige la cause (paquet, réseau, réglage), relance `./installer.sh`.
   Il est fait pour être relancé.

4. **Comptes à créer par lui** : `~/outils/configurer.py` affiche la liste « Reste à faire par toi ». Donne-lui les
   étapes **une par une**, dans cet ordre, en attendant sa confirmation à chaque fois :
   1. Jellyfin (assistant : il crée son compte, les bibliothèques sont déjà là) ;
   2. Radarr, Sonarr, Prowlarr (identifiant et mot de passe à la première ouverture) ;
   3. Prowlarr : ses sources de téléchargement ✋ ;
   4. Seerr (connexion avec son compte Jellyfin ; adresse de Jellyfin : `172.17.0.1`, port `8096`) ;
   5. AdGuard Home (assistant sur le port 3000) ;
   6. Immich (compte admin) ;
   7. `sudo smbpasswd -a $USER` pour le dossier partagé (il tape lui-même le mot de passe).

   Après chaque étape, relance `~/outils/configurer.py` : il relie ce qui vient de devenir possible
   (Seerr ↔ Radarr/Sonarr, AdGuard ↔ VPN…).

5. **Téléphone** :
   - appli **ntfy** : abonnement au sujet de `~/.surveillance/sujet` (envoie une notification d'essai avec
     `~/outils/surveillance.sh --test`) ;
   - appli **WireGuard** : il scanne `~/vpn/telephone.png`. Pour l'afficher dans le terminal :
     `qrencode -t ansiutf8 < ~/vpn/telephone.conf`. Autre appareil : `~/outils/vpn-ajouter.sh <nom>`.

6. **Vérifier** : `systemctl --failed`, `python3 ~/outils/portail.py --test`, ouvre `http://IP_SERVEUR` (curl), et
   `~/outils/configurer.py` doit n'avoir plus que des « déjà fait ». Termine par un récapitulatif : les adresses
   utiles, ce qui a été fait, ce qui reste éventuellement.

## Pour dépanner

- Réglages : `/etc/serveur-maison.conf` (relancer `./installer.sh` après un changement).
- Journaux : `journalctl -u <service> -n 50` ; tâches automatiques : `~/.surveillance/*.log` ; `crontab -l`.
- Services : `portail`, `jellyfin`, `radarr`, `sonarr`, `prowlarr`, `transmission-daemon`, `docker` (Seerr, Immich),
  `wg-quick@wg0`, `AdGuardHome`, `smbd`, `crowdsec`, `serveur-maison-admin` (pare-feu des outils d'admin).
- Un outil d'admin injoignable depuis la maison : c'est normal hors VPN si l'appareil n'est pas dans `ADMINS_MAC`.
- Chaque script a ses tests : `--test` (portail, vitrine, suppressions, mises-a-jour, configurer, profils-anciens).
