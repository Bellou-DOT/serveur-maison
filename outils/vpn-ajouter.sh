#!/usr/bin/env bash
# Ajoute un appareil au VPN : ~/outils/vpn-ajouter.sh telephone
# Crée ~/vpn/NOM.conf (à importer dans l'appli WireGuard) et ~/vpn/NOM.png (QR code à scanner).
# Retirer un appareil : supprimer sa section [Peer] dans /etc/wireguard/wg0.conf puis « sudo systemctl restart wg-quick@wg0 ».
set -euo pipefail
. /etc/serveur-maison.conf
PUBLIC=$(curl -s -m 10 https://api.ipify.org)
NOM=${1:?nom de l appareil}
[[ $NOM =~ ^[a-z0-9-]{1,20}$ ]] || { echo "Nom invalide"; exit 1; }
[ ! -e ~/vpn/$NOM.conf ] || { echo "Existe déjà : ~/vpn/$NOM.conf"; exit 1; }
umask 077
N=2; while sudo grep -q "10.8.0.$N/32" /etc/wireguard/wg0.conf; do N=$((N+1)); done
PRIV=$(wg genkey); PUB=$(echo "$PRIV" | wg pubkey); PSK=$(wg genpsk)
SPUB=$(sudo awk '/^PrivateKey/ {print $3}' /etc/wireguard/wg0.conf | wg pubkey)
printf '\n[Peer]\n# %s\nPublicKey = %s\nPresharedKey = %s\nAllowedIPs = 10.8.0.%s/32\n' "$NOM" "$PUB" "$PSK" "$N" | sudo tee -a /etc/wireguard/wg0.conf >/dev/null
# DNS = AdGuard Home sur le serveur : pubs bloquées partout. Seul le réseau du VPN passe par le tunnel.
cat > ~/vpn/$NOM.conf <<C
[Interface]
PrivateKey = $PRIV
Address = 10.8.0.$N/32
DNS = 10.8.0.1

[Peer]
PublicKey = $SPUB
PresharedKey = $PSK
Endpoint = $PUBLIC:51820
AllowedIPs = 10.8.0.0/24
PersistentKeepalive = 25
C
qrencode -o ~/vpn/$NOM.png -s 8 -m 2 < ~/vpn/$NOM.conf
sudo bash -c 'wg syncconf wg0 <(wg-quick strip wg0)'
echo "Appareil $NOM ajouté : 10.8.0.$N"
