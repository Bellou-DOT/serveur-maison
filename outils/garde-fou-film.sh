#!/bin/sh
# Crochet Radarr « On Grab » : garde-fou des films de plus de 10 Go (voir portail.py)
exec python3 "$HOME/outils/portail.py" --grab
