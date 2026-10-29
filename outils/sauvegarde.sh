#!/usr/bin/env bash
# Sauvegarde de nuit (cron de 5 h) : réglages de tous les services dans ~/sauvegardes (7 jours conservés),
# puis copie sur Google Drive si rclone est configuré.
set -e
ALERTE="$HOME/outils/surveillance.sh"
trap '[ -x "$ALERTE" ] && "$ALERTE" --message "Sauvegarde ratée" "La sauvegarde de la nuit a échoué (ligne $LINENO de sauvegarde.sh)."' ERR
mkdir -p "$HOME/sauvegardes"
"$HOME/outils/sauvegarde-config.sh" >/dev/null
# Copie hors du serveur sur Google Drive (remote rclone « drive ») : 4 jours gardés là-bas pour ne pas remplir
# les 15 Go gratuits. Avec le scope drive.file, rclone n'a accès qu'aux fichiers qu'il a lui-même créés.
if rclone listremotes 2>/dev/null | grep -qx 'drive:'; then
  if ! { rclone copy "$HOME/sauvegardes" drive:sauvegardes-serveur --include '*.tar.gz' --max-age 2d -q &&
         rclone delete drive:sauvegardes-serveur --min-age 4d -q; }; then
    "$ALERTE" --message "Copie Google Drive ratée" "Les sauvegardes sont sur le serveur mais pas sur Google Drive."
  fi
fi
find "$HOME/sauvegardes" -maxdepth 1 -name '*.tar.gz' -mtime +7 -delete
echo "Sauvegarde terminée."
