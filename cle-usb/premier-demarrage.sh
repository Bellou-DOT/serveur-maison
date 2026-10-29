#!/usr/bin/env bash
# Premier démarrage d'un serveur installé avec la clé USB (service serveur-maison-installation) :
# lance installer.sh, puis prévient sur le téléphone (ntfy) avec ce qui reste à faire. En cas d'échec,
# il réessaie au démarrage suivant (le drapeau /var/lib/serveur-maison-installe n'est posé qu'en cas de succès).
set -u
cd "$(dirname "$0")/.."
SUJET=$(cat "$HOME/.surveillance/sujet")
ntfy() { curl -s -m 20 -H "Title: $1" --data-binary "$2" "https://ntfy.sh/$SUJET" >/dev/null || true; }
for _ in $(seq 60); do curl -fs -m 5 -o /dev/null https://ntfy.sh && break; sleep 10; done  # le Wi-Fi se connecte
ntfy "Installation du serveur" "Ubuntu est installé. Mise en place des services en cours (15 à 30 minutes)…"
if ./installer.sh > "$HOME/installation.log" 2>&1; then
  sudo touch /var/lib/serveur-maison-installe
  python3 "$HOME/outils/configurer.py" > "$HOME/reste-a-faire.txt" 2>&1
  . /etc/serveur-maison.conf
  ntfy "Serveur prêt ✅" "Portail : http://$IP_SERVEUR — réserve cette adresse dans ta box.
$(sed -n '/Reste à faire/,$p' "$HOME/reste-a-faire.txt")"
else
  ntfy "Installation du serveur ratée" "$(tail -n 15 "$HOME/installation.log")

Branche un écran ou connecte-toi en SSH, puis : cd ~/serveur-maison && ./installer.sh"
fi
