#!/bin/sh
# Remplit la liste « lan6 » du pare-feu avec le réseau IPv6 de la maison (préfixe annoncé par la box) : seules ces
# adresses peuvent ouvrir une connexion vers le serveur en IPv6. Lancé au démarrage du pare-feu et toutes les 5 min.
set -e
. /etc/serveur-maison.conf
P=$(ip -6 route show dev "$INTERFACE" proto ra | awk '$1 ~ /\/[0-9]+$/ {print $1}' | paste -sd, -)
nft flush set inet serveur_maison_admin lan6
nft add element inet serveur_maison_admin lan6 "{ fe80::/10${P:+, $P} }"
