#!/usr/bin/env bash
# Sauvegarde des mondes du serveur actif (et de Palworld) dans ~/sauvegardes (7 jours conservés).
# Sans argument (cron de 5 h) : annonce, sauvegarde, puis redémarrage de Minecraft pour le garder fluide.
# --manuel (bouton du panneau) : sauvegarde immédiate, sans annonce ni redémarrage.
set -e
ALERTE="$HOME/outils/surveillance.sh"
trap '[ -x "$ALERTE" ] && "$ALERTE" --message "Sauvegarde ratée" "La sauvegarde de la nuit a échoué (ligne $LINENO de sauvegarde.sh)."' ERR
D=$(readlink -f "$HOME/mc")
cd "$D"
MONDES=$(for x in */level.dat; do [ -e "$x" ] && dirname "$x"; done || true)
MC_ACTIF=$(systemctl is-active --quiet minecraft && echo oui || true)
if [ "${1:-}" != "--manuel" ] && [ -n "$MC_ACTIF" ]; then
  mc "say Sauvegarde et redémarrage du serveur dans 1 minute." >/dev/null
  sleep 60
fi
mkdir -p "$HOME/sauvegardes"
F="$HOME/sauvegardes/$(basename "$D")-$(date +%F-%Hh%M).tar.gz"
if [ -n "$MONDES" ]; then
  [ -z "$MC_ACTIF" ] || { mc "save-off" >/dev/null; mc "save-all flush" >/dev/null; sleep 10; }
  tar czf "$F" $MONDES 2>/dev/null || true
  [ -z "$MC_ACTIF" ] || mc "save-on" >/dev/null
fi
# Palworld enregistre lui-même régulièrement : on copie son dossier de parties s'il existe.
P="$HOME/palworld/Pal/Saved/SaveGames"
if [ -d "$P" ] && [ -n "$(ls -A "$P")" ]; then
  tar czf "$HOME/sauvegardes/palworld-$(date +%F-%Hh%M).tar.gz" -C "$HOME/palworld/Pal/Saved" SaveGames 2>/dev/null || true
fi
# Réglages de tous les services (Jellyfin, Radarr, Sonarr, Seerr, Immich, VPN…) : un échec ne bloque pas le reste
if ! "$HOME/outils/sauvegarde-config.sh" >/dev/null; then
  "$ALERTE" --message "Sauvegarde des réglages ratée" "Les mondes sont sauvegardés, mais pas les réglages des services."
fi
# Copie hors du PC sur Google Drive (si rclone est configuré) : 4 jours gardés là-bas pour ne pas
# remplir les 15 Go gratuits. rclone n'a accès qu'aux fichiers qu'il a lui-même créés (scope drive.file).
if rclone listremotes 2>/dev/null | grep -qx 'drive:'; then
  if ! { rclone copy "$HOME/sauvegardes" drive:sauvegardes-serveur --include '*.tar.gz' --max-age 2d -q &&
         rclone delete drive:sauvegardes-serveur --min-age 4d -q; }; then
    "$ALERTE" --message "Copie Google Drive ratée" "Les sauvegardes sont sur le serveur mais pas sur Google Drive."
  fi
fi
find "$HOME/sauvegardes" -maxdepth 1 -name '*.tar.gz' -mtime +7 -delete
# Pas de redémarrage si Minecraft est éteint (ex. Palworld tourne : relancer Minecraft le couperait).
[ "${1:-}" = "--manuel" ] || [ -z "$MC_ACTIF" ] || sudo systemctl restart minecraft
if [ -n "$MONDES" ]; then echo "Sauvegarde terminée : $(basename "$F")"; else echo "Aucun monde à sauvegarder."; fi
