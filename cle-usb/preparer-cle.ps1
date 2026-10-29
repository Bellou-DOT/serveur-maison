# Prépare une clé USB qui installe Ubuntu Server ET le serveur maison tout seuls.
# Clic droit sur ce fichier → « Exécuter avec PowerShell » (les droits administrateur seront demandés).
# ⚠ La clé est entièrement effacée. Le PC qui démarrera dessus aussi, après une seule question de confirmation.
$ErrorActionPreference = 'Stop'
$Depot = Split-Path -Parent $PSScriptRoot
$Iso = 'ubuntu-24.04.5-live-server-amd64.iso'
$IsoUrl = "https://releases.ubuntu.com/24.04/$Iso"
$IsoSha256 = '97f3d7ffb032c3eb3b23d2c8be9cc76e60c2c1f2c0146ba5ba9fe01cafae0fd8'

# Droits administrateur (nécessaires pour formater la clé) : relance de ce script élevé
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', "`"$PSCommandPath`""
  exit
}

function Demander($question, $motif, $defaut = '') {
  while ($true) {
    $r = Read-Host ($question + $(if ($defaut) { " [$defaut]" } else { '' }))
    if (-not $r) { $r = $defaut }
    if ($r -cmatch $motif) { return $r }
    Write-Host '  Réponse invalide, recommence.' -ForegroundColor Yellow
  }
}
function Secret($question) {
  $s = Read-Host $question -AsSecureString
  [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
}
function Yaml($texte) { "'" + $texte.Replace("'", "''") + "'" }  # chaîne YAML entre apostrophes
function Ecrire($chemin, $texte) {  # UTF-8 sans BOM, fins de ligne Linux
  [IO.File]::WriteAllText($chemin, $texte.Replace("`r`n", "`n"), (New-Object Text.UTF8Encoding $false))
}

Write-Host "`n=== Clé d'installation du serveur maison ===`n" -ForegroundColor Cyan
$Nom = Demander 'Nom du serveur (affiché sur le portail)' '^[^"\\`$]{1,40}$' 'MonServ'
$Hote = Demander 'Nom court, en minuscules sans espace (adresse http://nom)' '^[a-z][a-z0-9-]{0,30}$' ($Nom.ToLower() -replace '[^a-z0-9-]', '')
$Utilisateur = Demander 'Nom du compte sur le serveur (minuscules)' '^[a-z][a-z0-9-]{0,30}$' 'serveur'
do {
  $Mdp = Secret 'Mot de passe de ce compte'
  $Mdp2 = Secret 'Encore une fois'
  if ($Mdp -ne $Mdp2 -or $Mdp.Length -lt 8) { Write-Host '  Différents ou trop court (8 caractères minimum).' -ForegroundColor Yellow }
} until ($Mdp -eq $Mdp2 -and $Mdp.Length -ge 8)
$Wifi = Read-Host 'Nom du Wi-Fi (laisse vide si le serveur est branché par câble)'
$WifiMdp = if ($Wifi) { Secret 'Mot de passe du Wi-Fi' } else { '' }
$Films = Demander 'Installer les films et séries (Jellyfin…) ? oui/non' '^(oui|non)$' 'oui'
$Photos = Demander 'Installer la sauvegarde des photos (Immich) ? oui/non' '^(oui|non)$' 'oui'
$Domaine = Demander 'Adresse DuckDNS (ex. moi.duckdns.org), vide sinon' '^([a-z0-9-]+\.duckdns\.org)?$'
$Jeton = if ($Domaine) { Demander 'Jeton DuckDNS' '^[0-9a-f-]{36}$' } else { '' }

# Clé USB : uniquement les disques USB, jamais le disque de Windows
$Disques = @(Get-Disk | Where-Object { $_.BusType -eq 'USB' -and -not $_.IsBoot -and -not $_.IsSystem })
if (-not $Disques) { throw 'Aucune clé USB trouvée : branche-la (8 Go minimum) et relance.' }
Write-Host "`nClés USB branchées :"
$Disques | ForEach-Object { Write-Host ("  {0} : {1} ({2:N0} Go)" -f $_.Number, $_.FriendlyName, ($_.Size / 1GB)) }
$Numero = Demander 'Numéro de la clé à utiliser' ('^(' + (($Disques | ForEach-Object Number) -join '|') + ')$')
$Cle = $Disques | Where-Object Number -eq ([int]$Numero)
if ($Cle.Size -lt 7GB) { throw 'Clé trop petite : 8 Go minimum.' }
Write-Host ("`n⚠ Tout ce qui est sur « {0} » va être effacé." -f $Cle.FriendlyName) -ForegroundColor Red
if ((Read-Host 'Tape EFFACER pour continuer') -cne 'EFFACER') { Write-Host 'Annulé.'; exit }

# Image d'Ubuntu Server (téléchargée une fois, vérifiée)
$Image = Join-Path ([Environment]::GetFolderPath('UserProfile')) "Downloads\$Iso"
if (-not (Test-Path $Image) -or (Get-FileHash $Image -Algorithm SHA256).Hash -ne $IsoSha256) {
  Write-Host "`nTéléchargement d'Ubuntu Server (4 Go)…" -ForegroundColor Cyan
  curl.exe -L --fail -o $Image $IsoUrl
  if ((Get-FileHash $Image -Algorithm SHA256).Hash -ne $IsoSha256) { Remove-Item $Image; throw 'Image téléchargée abîmée : relance le script.' }
}

Write-Host "`nFormatage de la clé…" -ForegroundColor Cyan
$Cle | Clear-Disk -RemoveData -RemoveOEM -Confirm:$false
$Cle | Initialize-Disk -PartitionStyle GPT
# FAT32 (lisible par le démarrage UEFI) : Windows ne sait pas formater plus de 32 Go dans ce format
$Partition = $Cle | New-Partition -Size ([Math]::Min($Cle.Size - 2MB, 32GB)) -AssignDriveLetter
$Volume = $Partition | Format-Volume -FileSystem FAT32 -NewFileSystemLabel 'SERVEUR' -Confirm:$false
$U = "$($Volume.DriveLetter):\"

Write-Host 'Copie d''Ubuntu sur la clé (quelques minutes)…' -ForegroundColor Cyan
$Monte = Mount-DiskImage -ImagePath $Image -PassThru
try {
  $Source = "$(($Monte | Get-Volume).DriveLetter):\"
  robocopy $Source $U /E /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "Copie d'Ubuntu ratée (robocopy $LASTEXITCODE)." }
} finally { Dismount-DiskImage -ImagePath $Image | Out-Null }

Write-Host 'Ajout du serveur maison et de tes réglages…' -ForegroundColor Cyan
robocopy $Depot (Join-Path $U 'serveur-maison') /E /XD .git /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copie du dépôt ratée (robocopy $LASTEXITCODE)." }

$Conf = [IO.File]::ReadAllText((Join-Path $Depot 'config.exemple'))
$Valeurs = @{ NOM_PORTAIL = $Nom; HOTE = $Hote; AVEC_MEDIAS = $Films; AVEC_PHOTOS = $Photos; DOMAINE = $Domaine; DUCKDNS_JETON = $Jeton }
foreach ($k in $Valeurs.Keys) { $Conf = [regex]::Replace($Conf, "(?m)^$k=`"[^`"]*`"", "$k=`"$($Valeurs[$k])`"") }
Ecrire (Join-Path $U 'serveur-maison.conf') $Conf

$WifiYaml = ''
if ($Wifi) {
  $WifiYaml = "    wifis:`n      wifi:`n        match:`n          name: `"wl*`"`n        dhcp4: true`n        optional: true`n" +
              "        access-points:`n          $(Yaml $Wifi):`n            password: $(Yaml $WifiMdp)"
}
$Auto = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'autoinstall.modele.yaml'))
$Auto = $Auto.Replace('@WIFI@', $WifiYaml).Replace('@HOTE@', $Hote).Replace('@UTILISATEUR@', $Utilisateur)
Ecrire (Join-Path $U 'autoinstall.yaml') $Auto
Ecrire (Join-Path $U 'compte.txt') "${Utilisateur}:$Mdp`n"

$Unite = [IO.File]::ReadAllText((Join-Path $Depot 'systeme\serveur-maison-installation.service'))
Ecrire (Join-Path $U 'serveur-maison-installation.service') $Unite.Replace('@UTILISATEUR@', $Utilisateur).Replace('@ACCUEIL@', "/home/$Utilisateur")
# Sujet des notifications créé dès maintenant : tu t'y abonnes avant l'installation, et tu sais quand c'est fini
$Sujet = 'serveur-maison-' + -join ((1..12) | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) })
Ecrire (Join-Path $U 'sujet-ntfy.txt') "$Sujet`n"

Write-Host @"

✅ Clé prête.

1. Sur ton téléphone, installe l'appli ntfy et abonne-toi au sujet :
      $Sujet
   (garde-le secret : quiconque le connaît peut lire les notifications du serveur)
2. Branche la clé sur le PC qui deviendra le serveur, allume-le en appuyant sur la touche du menu
   de démarrage (souvent F12, F11, F10, Échap ou F2 selon la marque) et choisis la clé USB.
3. Une seule question s'affiche : « Continue with autoinstall? » → tape yes puis Entrée.
   ⚠ Le disque de ce PC est alors entièrement effacé.
4. Ubuntu s'installe (environ 15 minutes) puis le PC redémarre : retire la clé à ce moment-là.
   Si la question réapparaît, ne tape PAS yes : retire la clé et redémarre le PC.
   La mise en place des services continue toute seule (15 à 30 minutes). Tu reçois une
   notification « Serveur prêt ✅ » avec l'adresse du portail et ce qui reste à faire.

⚠ La clé contient les mots de passe du Wi-Fi et du compte : reformate-la après l'installation.
"@ -ForegroundColor Green
