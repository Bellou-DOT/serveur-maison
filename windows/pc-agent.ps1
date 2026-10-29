# Agent du PC pour le portail du serveur maison : toutes les 10 s, demande au serveur si une mise en veille
# a été demandée (tuile « 💻 Mon PC »). À lancer caché à l'ouverture de session (voir README) : powershell -WindowStyle Hidden -File pc-agent.ps1 -Serveur 192.168.1.20
param([Parameter(Mandatory)][string]$Serveur)  # adresse IP du serveur (IP_SERVEUR)
Add-Type -AssemblyName System.Windows.Forms
while ($true) {
  try {
    $ordre = Invoke-RestMethod -Uri "http://$Serveur/pc/ordre" -TimeoutSec 5
    if ($ordre.veille) {
      # Veille classique (S3) : le Wi-Fi reste à l'écoute pour le réveil à distance
      [System.Windows.Forms.Application]::SetSuspendState([System.Windows.Forms.PowerState]::Suspend, $false, $false) | Out-Null
    }
  } catch { }  # serveur injoignable (Wi-Fi qui se reconnecte au réveil…) : on réessaie
  Start-Sleep -Seconds 10
}
