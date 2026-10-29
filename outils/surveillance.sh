#!/usr/bin/env bash
# Surveillance du serveur (cron toutes les 5 min) : notification sur le téléphone (appli ntfy)
# quand un problème apparaît, puis quand il est résolu. Pas de répétition tant que rien ne change.
# --message TITRE TEXTE [PRIORITÉ] : envoie une notification (utilisé par sauvegarde.sh).
# --test : envoie une notification d'essai.
set -u
. /etc/serveur-maison.conf
CONF="$HOME/.surveillance"
SUJET=$(cat "$CONF/sujet" 2>/dev/null) || { echo "Sujet ntfy absent ($CONF/sujet)"; exit 1; }

notifier() { # titre, texte, priorité (min/low/default/high/urgent), étiquettes (emojis ntfy)
  curl -s -m 20 -H "Title: $1" -H "Priority: ${3:-default}" -H "Tags: ${4:-warning}" \
    --data-binary "$2" "https://ntfy.sh/$SUJET" >/dev/null || true
}

case "${1:-}" in
  --message) notifier "$2" "$3" "${4:-high}" "warning"; exit 0 ;;
  --test) notifier "Serveur maison" "Les alertes fonctionnent ✔" default "white_check_mark"; exit 0 ;;
esac

# Battement de cœur vers healthchecks.io (s'il est configuré) : c'est lui qui prévient si le serveur
# entier tombe (coupure, plantage, plus de Wi-Fi), puisqu'alors ce script ne peut plus rien envoyer.
[ -s "$CONF/healthchecks" ] && curl -fsS -m 15 --retry 2 -o /dev/null "$(cat "$CONF/healthchecks")"

# Une ligne par problème : « clé|priorité|message ».
problemes=""
ajouter() { problemes+="$1|$2|$3"$'\n'; }

pc=$(df --output=pcent / | tail -1 | tr -dc 0-9)
[ "$pc" -ge 85 ] && ajouter disque high "Disque presque plein : $pc % utilisé."
# Disque des médias (s'il est à part) : les torrents restent en partage, chaque fichier y est en double
if mountpoint -q "$MEDIAS_BRUT"; then
  pc=$(df --output=pcent "$MEDIAS_BRUT" | tail -1 | tr -dc 0-9)
  [ "$pc" -ge 90 ] && ajouter ssd high "Disque des médias presque plein : $pc % utilisé. Arrête des partages dans Transmission."
fi

for z in /sys/class/thermal/thermal_zone*; do
  [ "$(cat "$z/type")" = x86_pkg_temp ] && t=$(( $(cat "$z/temp") / 1000 ))
done
[ "${t:-0}" -ge 90 ] && ajouter chaleur high "Processeur à ${t} °C : le PC chauffe trop (aération bouchée ?)."
# Santé des disques (SMART) : tous les disques branchés. Une alerte avant la panne, pas après.
for dev in /dev/nvme?n1 /dev/sd?; do
  [ -b "$dev" ] || continue
  nom="Le disque ${dev#/dev/} ($(lsblk -dno MODEL "$dev" 2>/dev/null | xargs))"
  sante=$(sudo -n smartctl -j -H -A "$dev" 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit()
n = d.get("nvme_smart_health_information_log", {})
pb = []
if d.get("smart_status", {}).get("passed") is False: pb.append("test de santé échoué")
if n.get("critical_warning"): pb.append("avertissement critique")
if n.get("media_errors"): pb.append(str(n["media_errors"]) + " erreurs de données")
if n.get("percentage_used", 0) >= 80: pb.append("usé à " + str(n["percentage_used"]) + " %")
if n.get("available_spare", 100) <= n.get("available_spare_threshold", 0): pb.append("plus de réserve")
if d.get("temperature", {}).get("current", 0) >= 75: pb.append(str(d["temperature"]["current"]) + " °C")
print(", ".join(pb))')
  [ -n "$sante" ] && ajouter "smart-${dev##*/}" high "$nom montre des signes de faiblesse ($sante). Pense à sauvegarder ce qu'il contient."
done

# Mise à jour de sécurité (noyau…) installée par Ubuntu, qui ne s'applique qu'au redémarrage
[ -f /var/run/reboot-required ] && ajouter redemarrage default "Une mise à jour de sécurité attend un redémarrage du serveur. Choisis un moment sans partie ni film : sudo reboot (ou demande-le à Claude)."

B=/sys/class/power_supply/BAT0
if [ "$(cat $B/status 2>/dev/null)" = Discharging ]; then
  cap=$(cat $B/capacity)
  if [ "$cap" -le 20 ]; then
    ajouter batterie urgent "Batterie à $cap % : le serveur va s'éteindre. Rebranche le chargeur."
  else
    ajouter secteur high "Le PC tourne sur batterie ($cap %) : coupure de courant ou chargeur débranché."
  fi
fi

declare -A NOMS=([wg-quick@wg0]="Le VPN" [AdGuardHome]="Le bloqueur de pubs" [jellyfin]="Jellyfin" [smbd]="Le dossier partagé des médias" [transmission-daemon]="Transmission" [sonarr]="Sonarr" [radarr]="Radarr" [docker]="Docker (Seerr)" [prowlarr]="Prowlarr" [portail]="Le portail $NOM_PORTAIL" [serveur-maison-admin]="Le pare-feu des outils admin" [crowdsec]="Le détecteur d attaques (CrowdSec)" [crowdsec-firewall-bouncer]="Le bloqueur d attaques (CrowdSec)")
for s in "${!NOMS[@]}"; do
  systemctl is-enabled --quiet "$s" 2>/dev/null || continue  # composant pas installé ici
  systemctl is-active --quiet "$s" || ajouter "$s" high "${NOMS[$s]} ne tourne plus ($s)."
done

# Comparaison avec le passage précédent : on prévient des nouveautés et des retours à la normale.
avant=$(cat "$CONF/etat" 2>/dev/null || true)
while IFS='|' read -r cle prio msg; do
  [ -n "$cle" ] && ! grep -q "^$cle|" <<<"$avant" && notifier "Problème sur le serveur" "$msg" "$prio" warning
done <<<"$problemes"
while IFS='|' read -r cle prio msg; do
  [ -n "$cle" ] && ! grep -q "^$cle|" <<<"$problemes" && notifier "Résolu" "Résolu : $msg" low white_check_mark
done <<<"$avant"
printf '%s' "$problemes" >"$CONF/etat"
