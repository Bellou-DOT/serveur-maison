#!/usr/bin/env bash
# Sauvegarde des réglages de tous les services (appelée par sauvegarde.sh chaque nuit, avant la copie Google Drive).
# Bases SQLite copiées à chaud proprement (API de sauvegarde SQLite), sans les affiches ni les caches retéléchargeables.
# Contient des secrets (clés WireGuard, mots de passe, clés d'API) : archive lisible par le propriétaire seul, Drive privé.
# Restauration : sudo tar xzf config-….tar.gz -C /   (les bases sont dans bases/, à remettre à la main).
set -e
SORTIE="$HOME/sauvegardes/config-$(date +%F-%Hh%M).tar.gz"
T=$(mktemp -d)
trap 'sudo rm -rf "$T"' EXIT
mkdir -p "$T/bases"

# Copie cohérente d'une base SQLite en cours d'utilisation
base() { sudo -n python3 -I -c 'import sqlite3, sys; s = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True); d = sqlite3.connect(sys.argv[2]); s.backup(d); d.close()' "$1" "$T/bases/$2"; }
base /var/lib/jellyfin/data/jellyfin.db jellyfin.db
base /var/lib/radarr/radarr.db radarr.db
base /var/lib/sonarr/sonarr.db sonarr.db
base /var/lib/prowlarr/prowlarr.db prowlarr.db
base /var/lib/seerr/db/db.sqlite3 seerr.db
# Immich fait lui-même une copie de sa base chaque nuit : on prend la plus récente
DUMP=$(ls -t /srv/immich/photos/backups/immich-db-backup-*.sql.gz 2>/dev/null | head -1)
[ -n "$DUMP" ] && sudo -n cp "$DUMP" "$T/bases/"

FICHIERS=(
  /etc/jellyfin /var/lib/jellyfin/root /var/lib/jellyfin/plugins/configurations
  /var/lib/radarr/config.xml /var/lib/sonarr/config.xml /var/lib/prowlarr/config.xml /var/lib/seerr/settings.json
  /opt/immich/.env /opt/immich/docker-compose.yml /opt/immich/docker-compose.override.yml
  /opt/AdGuardHome/AdGuardHome.yaml /etc/wireguard /etc/samba/smb.conf /etc/serveur-maison-admin.nft /etc/serveur-maison.conf
  /etc/crowdsec/config.yaml /etc/crowdsec/local_api_credentials.yaml /etc/crowdsec/parsers/s02-enrich/maison-whitelist.yaml
  /etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml
  /etc/systemd/system/{AdGuardHome,serveur-maison-admin,iw4x,minecraft,palworld,panneau,portail,prowlarr,radarr,sonarr,veille-jeux}.service
  /etc/systemd/system/transmission-daemon.service.d
  "$HOME/outils" "$HOME/panneau-web" "$HOME/.config/transmission-daemon/settings.json" "$HOME/.config/rclone"
  "$HOME/.surveillance" "$HOME/duckdns" "$HOME/vpn"
  /srv/iw4x/userraw/server.cfg /srv/iw4x/userraw/panneau.cfg
)
for f in "$HOME"/.transmission-mdp "$HOME"/.medias-mdp "$HOME"/.palworld-admin "$HOME"/.iw4x-* ; do [ -e "$f" ] && FICHIERS+=("$f"); done
crontab -l > "$T/crontab.txt"
# Chemins absents ignorés (un service désinstallé ne doit pas faire échouer la sauvegarde de la nuit)
EXISTANTS=()
for f in "${FICHIERS[@]}"; do sudo -n test -e "$f" && EXISTANTS+=("$f"); done
sudo -n tar czf "$SORTIE" --exclude='__pycache__' --exclude='*.log' -C / "${EXISTANTS[@]#/}" -C "$T" bases crontab.txt 2>/dev/null
sudo -n chown "$(id -u):$(id -g)" "$SORTIE"
chmod 600 "$SORTIE"
echo "Réglages sauvegardés : $(basename "$SORTIE") ($(du -h "$SORTIE" | cut -f1))"
