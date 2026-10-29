#!/usr/bin/env bash
# Installe le serveur maison sur un PC dédié sous Ubuntu 24.04.
# Usage (avec ton compte habituel, pas root ; sudo sera demandé) :
#   ./installer.sh            1er lancement : crée /etc/serveur-maison.conf à remplir, puis relance
#   ./installer.sh            ensuite : installe ou met à jour tout ce qui est activé dans ce fichier
# Peut être relancé sans casse : chaque étape vérifie ce qui existe déjà. Les mots de passe et clés
# déjà créés ne sont jamais remplacés.
set -euo pipefail
DEPOT=$(cd "$(dirname "$0")" && pwd)
CONF=/etc/serveur-maison.conf

[ "$(id -u)" -ne 0 ] || { echo "Lance-le avec ton compte habituel (pas root) : il utilisera sudo."; exit 1; }
. /etc/os-release
[ "$ID $VERSION_ID" = "ubuntu 24.04" ] || echo "⚠ Prévu pour Ubuntu 24.04 (ici : $PRETTY_NAME). On continue quand même."
sudo -v
if [ ! -f "$CONF" ]; then
  sudo install -m 640 -o root -g "$(id -gn)" "$DEPOT/config.exemple" "$CONF"
  echo "Réglages créés dans $CONF : remplis-les (sudo nano $CONF), puis relance ./installer.sh"
  exit 0
fi
. "$CONF"
if [ -z "$IP_SERVEUR" ] || [ -z "$INTERFACE" ] || [ -z "$RESEAU_MAISON" ]; then
  # Réseau détecté depuis la route par défaut (carte, adresse, réseau), puis écrit dans les réglages
  INTERFACE=${INTERFACE:-$(ip route show default | awk '{print $5; exit}')}
  CIDR=$(ip -4 -o addr show dev "$INTERFACE" | awk '{print $4; exit}')
  [ -n "$CIDR" ] || { echo "Pas de réseau détecté : branche le serveur à la box, ou remplis IP_SERVEUR, INTERFACE et RESEAU_MAISON dans $CONF"; exit 1; }
  IP_SERVEUR=${IP_SERVEUR:-${CIDR%/*}}
  RESEAU_MAISON=${RESEAU_MAISON:-$(python3 -c "import ipaddress, sys; print(ipaddress.ip_interface(sys.argv[1]).network)" "$CIDR")}
  sudo sed -i -e "s#^IP_SERVEUR=\"[^\"]*\"#IP_SERVEUR=\"$IP_SERVEUR\"#" -e "s#^INTERFACE=\"[^\"]*\"#INTERFACE=\"$INTERFACE\"#"     -e "s#^RESEAU_MAISON=\"[^\"]*\"#RESEAU_MAISON=\"$RESEAU_MAISON\"#" "$CONF"
  echo "Réseau détecté : $IP_SERVEUR sur $INTERFACE ($RESEAU_MAISON). Pense à réserver cette adresse dans ta box."
fi
U=$(id -un)
H=$HOME

etape() { printf '\n== %s\n' "$*"; }
oui() { [ "${!1:-non}" = oui ]; }
paquets() { sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq -o DPkg::Lock::Timeout=600 "$@" >/dev/null; }
modele() { sed -e "s#@UTILISATEUR@#$U#g" -e "s#@ACCUEIL@#$H#g" -e "s#@IP@#$IP_SERVEUR#g" "$DEPOT/systeme/$1"; }
unite() { modele "$1" | sudo tee "/etc/systemd/system/${2:-$1}" >/dev/null; }
mdp() { [ -s "$1" ] || (umask 077; head -c 18 /dev/urandom | base64 | tr -d '/+=' > "$1"); }
cron() { # ajoute (ou remplace) la ligne de crontab qui contient $1
  { crontab -l 2>/dev/null | grep -vF "$1" || true; echo "$2"; } | crontab -
}

etape "Paquets de base"
sudo apt-get update -qq
paquets curl ca-certificates python3 python3-yaml nftables wireguard qrencode smartmontools
sudo timedatectl set-timezone "$FUSEAU"
# Les scripts de nuit (cron) relancent des services et lisent les bases : sudo sans mot de passe pour ce compte.
echo "$U ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/serveur-maison >/dev/null
sudo chmod 440 /etc/sudoers.d/serveur-maison

etape "Outils du serveur (~/outils) et portail http://$HOTE"
getent group jellyfin >/dev/null || sudo groupadd --system jellyfin
sudo usermod -aG jellyfin "$U"
mkdir -p "$H/outils" "$H/sauvegardes" "$H/.surveillance"
chmod 700 "$H/.surveillance"
cp "$DEPOT"/outils/* "$H/outils/"
chmod +x "$H"/outils/*.py "$H"/outils/*.sh
# Sujet ntfy secret : quiconque le connaît peut lire les notifications. Créé une seule fois.
[ -s "$H/.surveillance/sujet" ] || echo "serveur-maison-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')" > "$H/.surveillance/sujet"
ELEMENTS=""
[ -n "$ADMINS_MAC" ] && ELEMENTS="elements = { $(echo "$ADMINS_MAC" | xargs | sed 's/ /, /g') }"
modele serveur-maison-admin.nft | sed -e "s#@ELEMENTS@#$ELEMENTS#" -e "s#@INTERFACE@#$INTERFACE#" | sudo tee /etc/serveur-maison-admin.nft >/dev/null
unite serveur-maison-admin.service
unite portail.service
sudo systemctl daemon-reload
sudo systemctl enable --now serveur-maison-admin portail >/dev/null
sudo systemctl restart serveur-maison-admin portail
sudo systemctl enable --now smartd >/dev/null 2>&1 || sudo systemctl enable --now smartmontools >/dev/null
cron outils/surveillance.sh "*/5 * * * * $H/outils/surveillance.sh"
cron outils/sauvegarde.sh "0 5 * * * $H/outils/sauvegarde.sh"
cron outils/mises-a-jour.py "23 * * * * $H/outils/mises-a-jour.py >> $H/.surveillance/mises-a-jour.log 2>&1"

etape "VPN WireGuard (10.8.0.0/24, port UDP 51820)"
if ! sudo test -f /etc/wireguard/wg0.conf; then
  CLE=$(wg genkey)
  printf '# VPN maison : chaque appareil a sa section [Peer] (ajout : ~/outils/vpn-ajouter.sh NOM)\n[Interface]\nAddress = 10.8.0.1/24\nListenPort = 51820\nPrivateKey = %s\n' "$CLE" \
    | sudo tee /etc/wireguard/wg0.conf >/dev/null
  sudo chmod 600 /etc/wireguard/wg0.conf
fi
sudo systemctl enable --now wg-quick@wg0 >/dev/null
mkdir -p "$H/vpn"
[ -e "$H/vpn/telephone.conf" ] || "$H/outils/vpn-ajouter.sh" telephone

etape "AdGuard Home (bloqueur de pubs, et nom « $HOTE » pour le VPN)"
if [ ! -x /opt/AdGuardHome/AdGuardHome ]; then
  curl -fsSL https://raw.githubusercontent.com/AdguardTeam/AdGuardHome/master/scripts/install.sh | sudo sh -s -- -o /opt >/dev/null
fi

if oui AVEC_MEDIAS; then
  etape "Jellyfin (films et séries : http://$IP_SERVEUR:8096)"
  if [ ! -f /etc/apt/sources.list.d/jellyfin.sources ]; then  # dépôt officiel Jellyfin
    sudo install -d /etc/apt/keyrings
    curl -fsSL https://repo.jellyfin.org/jellyfin_team.gpg.key | sudo gpg --dearmor --yes -o /etc/apt/keyrings/jellyfin.gpg
    sudo tee /etc/apt/sources.list.d/jellyfin.sources >/dev/null <<EOF
Types: deb
URIs: https://repo.jellyfin.org/ubuntu
Suites: $VERSION_CODENAME
Components: main
Architectures: amd64
Signed-By: /etc/apt/keyrings/jellyfin.gpg
EOF
    sudo apt-get update -qq
  fi
  paquets jellyfin
  sudo usermod -aG jellyfin "$U"
  sudo usermod -aG "$(id -gn)" jellyfin

  etape "Dossiers des médias ($MEDIAS_BRUT → vitrine $MEDIAS)"
  for d in "$MEDIAS_BRUT" "$MEDIAS_BRUT/Film" "$MEDIAS_BRUT/Séries" "$MEDIAS_BRUT/Téléchargements" "$MEDIAS"; do
    [ -d "$d" ] || sudo install -d -o "$U" -g jellyfin -m 2775 "$d"
  done
  [ -z "$DOSSIER_PRIVE" ] || [ -d "$MEDIAS_BRUT/$DOSSIER_PRIVE" ] || sudo install -d -o "$U" -g jellyfin -m 2775 "$MEDIAS_BRUT/$DOSSIER_PRIVE"
  (umask 002; python3 "$H/outils/vitrine.py")  # dossiers modifiables par Jellyfin (suppression depuis l'appli)
  cron outils/suppressions.py "*/5 * * * * $H/outils/suppressions.py >> $H/.surveillance/suppressions.log 2>&1"
  cron outils/profils-anciens.py "17 * * * * $H/outils/profils-anciens.py >> $H/.surveillance/profils-anciens.log 2>&1"

  etape "Radarr, Sonarr, Prowlarr"
  paquets libicu74 sqlite3
  declare -A URLS=(
    [Radarr]="https://radarr.servarr.com/v1/update/master/updatefile?os=linux&runtime=netcore&arch=x64"
    [Sonarr]="https://services.sonarr.tv/v1/download/main/latest?version=4&os=linux&arch=x64"
    [Prowlarr]="https://prowlarr.servarr.com/v1/update/master/updatefile?os=linux&runtime=netcore&arch=x64")
  for APP in Radarr Sonarr Prowlarr; do
    app=${APP,,}
    if [ ! -x "/opt/$APP/$APP" ]; then
      curl -fsSL -o "/tmp/$app.tar.gz" "${URLS[$APP]}"
      sudo tar xzf "/tmp/$app.tar.gz" -C /opt && rm "/tmp/$app.tar.gz"
      sudo chown -R "$U:jellyfin" "/opt/$APP"
    fi
    [ -d "/var/lib/$app" ] || sudo install -d -o "$U" -g jellyfin "/var/lib/$app"
    modele arr.service | sed -e "s#@APP@#$APP#g" -e "s#@app@#$app#g" | sudo tee "/etc/systemd/system/$app.service" >/dev/null
  done
  sudo systemctl daemon-reload
  sudo systemctl enable --now radarr sonarr prowlarr >/dev/null

  etape "Transmission (téléchargements, page servie par le portail)"
  paquets transmission-daemon
  sudo systemctl stop transmission-daemon
  sudo mkdir -p /etc/systemd/system/transmission-daemon.service.d
  modele transmission-utilisateur.conf | sudo tee /etc/systemd/system/transmission-daemon.service.d/utilisateur.conf >/dev/null
  mkdir -p "$H/.config/transmission-daemon"
  mdp "$H/.transmission-mdp"
  # Réglages imposés (le reste garde les valeurs par défaut ou ce que tu as changé) ; Transmission chiffre le mot de passe.
  python3 - "$H/.config/transmission-daemon/settings.json" "$U" "$(cat "$H/.transmission-mdp")" "$MEDIAS_BRUT/Téléchargements" <<'EOF'
import json, sys
from pathlib import Path
f, u, mdp, dl = Path(sys.argv[1]), *sys.argv[2:]
s = json.loads(f.read_text()) if f.exists() else {}
s.update({'rpc-authentication-required': True, 'rpc-username': u, 'download-dir': dl, 'umask': 2, 'ratio-limit-enabled': False})
if not str(s.get('rpc-password', '')).startswith('{'):  # pas encore chiffré : on (re)pose le nôtre
    s['rpc-password'] = mdp
f.write_text(json.dumps(s, indent=4))
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now transmission-daemon >/dev/null && sudo systemctl restart transmission-daemon

  etape "Docker et Seerr (demandes de films : http://$IP_SERVEUR:5055)"
  paquets docker.io docker-compose-v2
  sudo systemctl enable --now docker >/dev/null
  [ -d /var/lib/seerr ] || sudo install -d -o 1000 -g 1000 /var/lib/seerr
  if ! sudo docker inspect seerr >/dev/null 2>&1; then
    sudo docker run -d --name seerr --restart unless-stopped -p 5055:5055 -e TZ="$FUSEAU" \
      -v /var/lib/seerr:/app/config ghcr.io/seerr-team/seerr:latest >/dev/null
  fi

  etape "Partage réseau des médias (Samba : \\\\$IP_SERVEUR\\medias)"
  paquets samba
  if ! grep -q '^\[medias\]' /etc/samba/smb.conf; then
    printf '\n[medias]\n   path = %s\n   valid users = %s\n   read only = no\n   force group = jellyfin\n   create mask = 0664\n   directory mask = 2775\n   hosts allow = 127.0.0.1 %s 10.8.0.0/24\n   hosts deny = 0.0.0.0/0\n' \
      "$MEDIAS_BRUT" "$U" "$RESEAU_MAISON" | sudo tee -a /etc/samba/smb.conf >/dev/null
    sudo systemctl restart smbd
  fi
fi

if oui AVEC_PHOTOS; then
  etape "Immich (photos : http://$IP_SERVEUR:2283)"
  paquets docker.io docker-compose-v2
  sudo systemctl enable --now docker >/dev/null
  if [ ! -f /opt/immich/docker-compose.yml ]; then
    sudo mkdir -p /opt/immich /srv/immich/photos /srv/immich/postgres
    sudo curl -fsSL -o /opt/immich/docker-compose.yml https://github.com/immich-app/immich/releases/latest/download/docker-compose.yml
    sudo curl -fsSL -o /opt/immich/.env https://github.com/immich-app/immich/releases/latest/download/example.env
    sudo sed -i -e "s#^UPLOAD_LOCATION=.*#UPLOAD_LOCATION=/srv/immich/photos#" -e "s#^DB_DATA_LOCATION=.*#DB_DATA_LOCATION=/srv/immich/postgres#" \
      -e "s#^\# *TZ=.*#TZ=$FUSEAU#" -e "s#^DB_PASSWORD=.*#DB_PASSWORD=$(head -c 18 /dev/urandom | base64 | tr -dc A-Za-z0-9)#" /opt/immich/.env
    sudo chmod 600 /opt/immich/.env
  fi
  (cd /opt/immich && sudo docker compose up -d >/dev/null)
fi

if oui AVEC_SECURITE; then
  etape "CrowdSec (bloque les adresses qui attaquent le serveur)"
  if ! command -v cscli >/dev/null; then
    curl -fsSL https://install.crowdsec.net | sudo sh >/dev/null
    paquets crowdsec crowdsec-firewall-bouncer-nftables
  fi
  modele crowdsec-maison.yaml | sed "s#@RESEAU@#$RESEAU_MAISON#" | sudo tee /etc/crowdsec/parsers/s02-enrich/maison-whitelist.yaml >/dev/null
  sudo systemctl restart crowdsec crowdsec-firewall-bouncer
fi

etape "Vérifications"
python3 "$H/outils/portail.py" --test
python3 "$H/outils/vitrine.py" --test
"$H/outils/surveillance.sh" --test
python3 "$H/outils/configurer.py" --test

etape "Réglages des services (liens entre eux, bibliothèques, VPN…)"
if oui AVEC_MEDIAS; then  # au premier démarrage, Jellyfin et les *arr mettent un moment à répondre
  for url in http://127.0.0.1:8096/health http://127.0.0.1:7878/ping http://127.0.0.1:8989/ping http://127.0.0.1:9696/ping; do
    for _ in $(seq 90); do curl -fs -m 2 -o /dev/null "$url" && break; sleep 2; done
  done
fi
python3 "$H/outils/configurer.py"

cat <<EOF

✅ Installation terminée. Fais les étapes « Reste à faire par toi » ci-dessus, puis relance ~/outils/configurer.py.
   Portail       : http://$IP_SERVEUR  (et http://$HOTE par le VPN)
   Notifications : appli ntfy, abonne-toi au sujet « $(cat "$H/.surveillance/sujet") » (une notification d'essai vient de partir)
   VPN           : $H/vpn/telephone.png (QR code à scanner dans l'appli WireGuard)
EOF
