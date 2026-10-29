# Consignes pour l'IA qui installe ce serveur

Tu aides quelqu'un à installer **serveur-maison**, depuis ce dossier.
Il te dit en général juste « installe le serveur » : tu fais tout, sauf ce qui est marqué ✋ plus bas.
Parle-lui en français simple, sans jargon : c'est souvent un débutant.

**Où tournes-tu ?**
- Sur **Windows** (le cas le plus courant : il t'a demandé de récupérer ce dépôt sur son PC) → tu prépares la
  **clé USB d'installation** (section suivante). Tout le reste se fera seul sur le futur serveur.
- Sur **Ubuntu 24.04**, sur la machine qui sera le serveur → passe directement aux [Étapes](#étapes).

## Sur Windows : préparer la clé USB

Le script `cle-usb/preparer-cle.ps1` fait tout : il télécharge Ubuntu Server, formate la clé et y met le dépôt et
les réglages. Le PC qui démarrera sur la clé s'installera ensuite tout seul (Ubuntu puis le serveur).

1. **Demande-lui dans la conversation**, en une fois : le nom du serveur (affiché sur le portail), le nom du compte
   Linux (minuscules, ex. `serveur`), s'il veut les films et séries (`oui`/`non`), la sauvegarde des photos
   (`oui`/`non`), le **nom** de son Wi-Fi s'il n'y a pas de câble pour le serveur (sur Windows :
   `netsh wlan show interfaces`, ligne SSID, pour lui proposer celui où il est). ✋ **Ne demande aucun mot de passe** : le script les lui demande lui-même.
2. Fais-lui brancher une clé USB d'au moins 8 Go, puis `powershell -ExecutionPolicy Bypass -File
   cle-usb\preparer-cle.ps1 -Lister`. Montre-lui la liste et fais-lui **confirmer laquelle** : elle sera effacée.
   Préviens-le aussi que le téléchargement fait **4 Go** (Ubuntu Server 24.04.5, depuis releases.ubuntu.com).
3. Lance (en arrière-plan si ton outil coupe les commandes longues : comptez 10 à 40 minutes) :
   ```
   powershell -ExecutionPolicy Bypass -File cle-usb\preparer-cle.ps1 -Nom "…" -Hote "…" -Utilisateur "…" `
     -Films oui -Photos oui -Wifi "…" -Cle <numéro>
   ```
   (`-Hote` : nom court en minuscules sans espace ; `-Wifi ""` si câble.)
   Dis-lui **avant** qu'une fenêtre va s'ouvrir : il accepte les droits administrateur, tape le mot de passe du
   compte (deux fois), celui du Wi-Fi, puis **EFFACER** pour confirmer. La fenêtre se ferme toute seule à la fin.
4. La commande affiche tout ce qui s'est passé dans cette fenêtre (aussi dans `cle-usb\preparation.log`).
   Si ça a échoué, lis l'erreur, corrige, relance. Sinon, donne-lui :
   - le **sujet ntfy** affiché : installer l'appli **ntfy** sur son téléphone et s'y abonner **maintenant** ;
   - la suite : brancher la clé sur le futur serveur, l'allumer en appuyant sur la touche du menu de démarrage
     (F12, F11, F10, Échap ou F2 selon la marque), choisir la clé, taper **yes** à l'unique question (⚠ le disque de
     ce PC est effacé), retirer la clé au redémarrage. Il recevra « Serveur prêt ✅ » avec l'adresse du portail ;
   - qu'il pourra ensuite te rappeler pour les comptes à créer (étape 4 ci-dessous, depuis le portail ou en SSH) ;
   - de reformater la clé après l'installation (elle contient les mots de passe).
   Supprime `cle-usb\preparation.log` à la fin.

## Règles

- ✋ **Les comptes et les mots de passe sont à lui.** Tu ne crées aucun compte et tu ne tapes aucun mot de passe à sa place
  (Jellyfin, Radarr, Sonarr, Prowlarr, Seerr, Immich, AdGuard, Samba, appli du téléphone). Tu lui dis exactement
  où cliquer, tu attends qu'il te dise « c'est fait », puis tu continues.
- ✋ **Les sources de téléchargement (indexeurs de Prowlarr)** : c'est lui qui les choisit et les ajoute. Ne les
  propose pas et ne les configure pas.
- **Rien d'irréversible sans son accord** : formater ou monter un disque, modifier `/etc/fstab`, effacer des fichiers.
  Montre ce que tu vas faire et attends un « oui ».
- Tout ce que l'installation crée de secret (mots de passe générés, clés du VPN) reste sur le serveur :
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
     (`AVEC_MEDIAS`) et les photos (`AVEC_PHOTOS`).
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
