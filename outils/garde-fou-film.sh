#!/bin/sh
# Crochet Radarr / Sonarr « On Grab » (lancé sous le compte medias) : garde-fou des films de plus de 10 Go (voir portail.py)
exec python3 "$(dirname "$0")/portail.py" --grab
