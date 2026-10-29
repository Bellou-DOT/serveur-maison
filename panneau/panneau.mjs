// Panneau de gestion du serveur Minecraft.
// Page locale (127.0.0.1 uniquement) qui pilote le serveur par SSH : rien n'est exposé sur Internet.
// Côté serveur, tout passe par ~/outils/gestion.py (mondes, cartes, modpacks) et le service « minecraft ».
// Lancement : node panneau.mjs   (ouvre le navigateur tout seul)
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { readFileSync, existsSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const PORT = 8765;
// --ecoute=10.8.0.1 --ecoute=<IP du serveur> : panneau installé sur le serveur, joignable par le VPN et la maison
// (option répétable). Sur le serveur, il répond aussi au nom HOTE du portail (variables de /etc/serveur-maison.conf).
const ECOUTES = process.argv.filter((a) => /^--ecoute=\d{1,3}(\.\d{1,3}){3}$/.test(a)).map((a) => a.slice(9));
const ECOUTE = ECOUTES[0] || '127.0.0.1';
const ADRESSE = `http://${ECOUTE}:${PORT}`;
const HOTES = [`127.0.0.1:${PORT}`, `localhost:${PORT}`, ...ECOUTES.map((ip) => `${ip}:${PORT}`),
  ...(ECOUTES.length && process.env.HOTE ? [`${process.env.HOTE}:${PORT}`, `${process.env.HOTE}.lan:${PORT}`] : [])];
// Jeton aléatoire par lancement : un autre site ouvert dans le navigateur ne peut pas le lire,
// donc ne peut pas envoyer de commande au serveur à ta place.
const JETON = randomBytes(24).toString('hex');
const CLE_SSH = process.env.CLE_SSH || join(homedir(), '.ssh', 'oracle_mc');
const FICHIER_IP = new URL('../ip.txt', import.meta.url); // « utilisateur@IP » du serveur (ou « IP » pour Oracle)
const FICHIER_ADRESSE = new URL('../adresse.txt', import.meta.url); // adresse publique à donner aux amis
const NOM_JEUX = (process.env.NOM_JEUX || 'Serveur maison').replace(/[<>&"]/g, '');
const PAGE = readFileSync(new URL('./page.html', import.meta.url), 'utf8').replaceAll('__NOM__', NOM_JEUX);
const G = 'python3 ~/outils/gestion.py';
const TAILLE_MAX_ENVOI = 8 * 1024 ** 3;
const UA = { 'user-agent': 'serveur-maison (panneau)' };
const FTB = 'https://api.feed-the-beast.com/v1/modpacks/public/modpack';

const lire = (f) => (existsSync(f) ? readFileSync(f, 'utf8').trim() || null : null);
const lireIp = () => lire(FICHIER_IP);
const optionsSsh = (ip) => ['-i', CLE_SSH, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new',
  '-o', 'ConnectTimeout=10', ip.includes('@') ? ip : `ubuntu@${ip}`];

function ssh(ip, commande, entree = '') {
  return new Promise((ok) => {
    const p = spawn('ssh', [...optionsSsh(ip), commande]);
    let stdout = '', stderr = '';
    p.stdout.on('data', (d) => (stdout += d));
    p.stderr.on('data', (d) => (stderr += d));
    p.on('error', (e) => ok({ code: -1, sortie: e.message, stdout: '' }));
    p.on('close', (code) => ok({ code, sortie: (stdout + stderr).trim(), stdout }));
    p.stdin.end(entree);
  });
}

async function sshJson(ip, commande, entree = '') {
  const r = await ssh(ip, commande, entree);
  try {
    return { code: r.code, donnees: JSON.parse(r.stdout) };
  } catch {
    return { code: r.code || 1, sortie: r.sortie || 'Réponse illisible du serveur.' };
  }
}

// Validation : tout ce qui va sur la ligne SSH est vérifié ici (et revérifié par gestion.py).
// La seule saisie libre (commande console) passe par stdin, jamais par la ligne SSH.
export const pseudoValide = (p) => typeof p === 'string' && /^[A-Za-z0-9_]{3,16}$/.test(p);
// Joueur console / téléphone (Bedrock, via Geyser + Floodgate) : « .Gamertag », liste blanche gérée par Floodgate
export const gamertagValide = (p) => typeof p === 'string' && /^\.[A-Za-z0-9_]{1,16}$/.test(p);
const amiVersCommande = (action, p) => (gamertagValide(p) ? `fwhitelist ${action} ${p.slice(1)}`
  : pseudoValide(p) ? `whitelist ${action} ${p}` : null);
export const nomValide = (n) => typeof n === 'string' && /^[a-z0-9][a-z0-9-]{0,39}$/.test(n);
export const idModrinthValide = (i) => typeof i === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(i);
export const idFtbValide = (i) => typeof i === 'string' && /^\d{1,7}$/.test(i);
export function commandeValide(c) {
  if (typeof c !== 'string') return null;
  const nette = c.trim().replace(/^\//, '');
  return nette.length >= 1 && nette.length <= 256 && !/[\x00-\x1f\x7f]/.test(nette) ? nette : null;
}
// Réglages Palworld : mêmes règles que gestion.py (pas de " , ( ) qui casseraient PalWorldSettings.ini)
const REGLES_PAL = {
  ServerName: /^[\p{L}\p{N}_' .!-]{1,40}$/u,
  ServerPassword: /^[A-Za-z0-9_-]{0,32}$/,
  ServerPlayerMaxNum: /^([1-9]|[12]\d|3[0-2])$/,
};
// Options de jeu (taux, True/False, choix) : forme vérifiée ici, type et bornes exacts vérifiés par gestion.py.
const OPTION_PAL = { cle: /^[A-Za-z]{2,40}$/, valeur: /^[A-Za-z0-9.]{1,20}$/ };
export function reglagesPalValides(v) {
  if (!v || typeof v !== 'object') return null;
  const out = {};
  for (const [k, brut] of Object.entries(v)) {
    const val = String(brut).trim();
    if (k in REGLES_PAL ? !REGLES_PAL[k].test(val) : !(OPTION_PAL.cle.test(k) && OPTION_PAL.valeur.test(val))) return null;
    out[k] = val;
  }
  return Object.keys(out).length ? out : null;
}

// Actions sur un joueur Minecraft : traduites en commandes console, chaque morceau validé.
const MODES = ['survival', 'creative', 'adventure', 'spectator'];
const ENCHANTEMENTS = { sharpness: 5, protection: 4, efficiency: 5, unbreaking: 3, mending: 1, fortune: 3, looting: 3,
  silk_touch: 1, power: 5, infinity: 1, feather_falling: 4, respiration: 3, fire_aspect: 2, knockback: 2,
  sweeping_edge: 3, depth_strider: 3, thorns: 3, flame: 1, punch: 2, aqua_affinity: 1 };
const KITS = {
  fer: ['iron_helmet', 'iron_chestplate', 'iron_leggings', 'iron_boots', 'iron_sword', 'iron_pickaxe', 'iron_axe', 'shield', 'bread 32'],
  diamant: ['diamond_helmet', 'diamond_chestplate', 'diamond_leggings', 'diamond_boots', 'diamond_sword', 'diamond_pickaxe',
    'diamond_axe', 'diamond_shovel', 'shield', 'cooked_beef 32', 'torch 64'],
  netherite: ['netherite_helmet', 'netherite_chestplate', 'netherite_leggings', 'netherite_boots', 'netherite_sword',
    'netherite_pickaxe', 'netherite_axe', 'elytra', 'firework_rocket 64', 'golden_apple 16', 'totem_of_undying'],
};
export function commandesJoueur(v) {
  if (!v || typeof v !== 'object' || !pseudoValide(v.joueur)) return null;
  const p = v.joueur;
  const n = Number(v.nombre);
  switch (v.action) {
    case 'op': return [`op ${p}`];
    case 'deop': return [`deop ${p}`];
    case 'mode': return MODES.includes(v.mode) ? [`gamemode ${v.mode} ${p}`] : null;
    case 'donner': {
      const id = typeof v.objet === 'string' ? v.objet.trim().toLowerCase() : '';
      if (!/^([a-z0-9_.-]{1,32}:)?[a-z0-9_/.-]{1,64}$/.test(id) || !Number.isInteger(n) || n < 1 || n > 640) return null;
      return [`give ${p} ${id.includes(':') ? id : `minecraft:${id}`} ${n}`];
    }
    case 'kit': return Object.hasOwn(KITS, v.kit) ? KITS[v.kit].map((o) => `give ${p} minecraft:${o}`) : null;
    case 'enchanter': {
      const max = Object.hasOwn(ENCHANTEMENTS, v.enchantement) ? ENCHANTEMENTS[v.enchantement] : 0;
      return Number.isInteger(n) && n >= 1 && n <= max ? [`enchant ${p} minecraft:${v.enchantement} ${n}`] : null;
    }
    case 'xp': return Number.isInteger(n) && n >= 1 && n <= 1000 ? [`experience add ${p} ${n} levels`] : null;
    case 'soigner': return [`effect give ${p} minecraft:instant_health 1 10`, `effect give ${p} minecraft:saturation 1 10`];
    case 'expulser': return [`kick ${p}`];
    case 'tp': { // vers un autre joueur, ou vers des coordonnées « x y z »
      if (pseudoValide(v.cible)) return [`tp ${p} ${v.cible}`];
      const c = typeof v.cible === 'string' ? v.cible.trim() : '';
      return /^-?\d{1,8} -?\d{1,3} -?\d{1,8}$/.test(c) ? [`tp ${p} ${c}`] : null;
    }
    default: return null;
  }
}
// duree* : durée d'une journée complète (20 min en temps normal) ; « time rate » n'existe qu'à partir de Minecraft 26.
const COMMANDES_MONDE = { jour: 'time set day', nuit: 'time set night', beau: 'weather clear', pluie: 'weather rain',
  duree20: 'time rate 1', duree30: 'time rate 0.6667', duree40: 'time rate 0.5', duree60: 'time rate 0.3333' };
export const regleMcValide = (v) => v && /^[a-z_]{1,30}$/.test(v.cle) && /^(true|false|100|\d{1,2})$/.test(String(v.valeur));
export const nouveauMondeValide = (v) => v && ['normal', 'flat', 'large_biomes', 'amplified'].includes(v.type)
  && /^(|-?[A-Za-z0-9_]{1,32})$/.test(v.graine ?? '');
// « StoneBlock 4 Server Pack.zip » → « StoneBlock-4-Server-Pack »
export function titreDepuisFichier(nom) {
  if (typeof nom !== 'string') return null;
  const t = nom.replace(/\.zip$/i, '').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^[-.]+|-+$/g, '').slice(0, 60);
  return t || null;
}

const INVALIDE = { code: 1, sortie: 'Paramètre invalide.' };
const versConsole = (ip, ligne) => ssh(ip, 'cat >> ~/mc/console.in', ligne + '\n');
// Envoie des commandes puis renvoie la réponse du serveur (dernières lignes de sa console).
const consoleAvecReponse = (ip, lignes) => ssh(ip,
  `cat >> ~/mc/console.in; sleep 1; journalctl -u minecraft -n ${lignes.length} --no-pager -o cat | sed 's/^\\[[0-9:]* INFO\\]: //; s/^System chat: //'`,
  lignes.join('\n') + '\n');

const ACTIONS = {
  etat: (ip) => sshJson(ip, `${G} etat`),
  instances: (ip) => sshJson(ip, `${G} instances`),
  amis: (ip) => sshJson(ip, `${G} amis`),
  suivi: (ip) => sshJson(ip, `${G} suivi`),
  demarrer: (ip) => ssh(ip, 'sudo systemctl start minecraft && echo "Serveur en cours de démarrage."'),
  arreter: (ip) => ssh(ip, 'sudo systemctl stop minecraft && echo "Serveur arrêté (monde sauvegardé)."'),
  redemarrer: (ip) => ssh(ip, 'sudo systemctl restart minecraft && echo "Redémarrage lancé."'),
  sauvegarder: (ip) => ssh(ip, '~/outils/sauvegarde.sh --manuel'),
  joueurs: (ip) => versConsole(ip, 'list'),
  commande: (ip, { valeur }) => {
    const c = commandeValide(valeur);
    return c ? versConsole(ip, c) : { code: 1, sortie: 'Commande vide ou invalide.' };
  },
  ajouterAmi: (ip, { valeur }) => (amiVersCommande('add', valeur) ? versConsole(ip, amiVersCommande('add', valeur))
    : { code: 1, sortie: 'Pseudo invalide (3 à 16 caractères : lettres, chiffres, _ ; joueur console : .Gamertag).' }),
  retirerAmi: (ip, { valeur }) => (amiVersCommande('remove', valeur) ? versConsole(ip, amiVersCommande('remove', valeur)) : INVALIDE),
  activer: (ip, { valeur }) => (nomValide(valeur) ? sshJson(ip, `${G} lancer activer ${valeur}`) : INVALIDE),
  supprimer: (ip, { valeur }) => (nomValide(valeur) ? sshJson(ip, `${G} lancer supprimer ${valeur}`) : INVALIDE),
  modrinth: (ip, { valeur }) => (idModrinthValide(valeur) ? sshJson(ip, `${G} lancer modrinth ${valeur}`) : INVALIDE),
  ftb: (ip, { valeur }) => (idFtbValide(valeur) ? sshJson(ip, `${G} lancer ftb ${valeur}`) : INVALIDE),
  palworldDemarrer: (ip) => ssh(ip, 'sudo systemctl start palworld && echo "Palworld démarre (Minecraft a été arrêté)."'),
  palworldArreter: (ip) => ssh(ip, 'sudo systemctl stop palworld && echo "Palworld arrêté (partie enregistrée)."'),
  palworldMaj: (ip) => sshJson(ip, `${G} lancer palworld`),
  palworldRedemarrer: (ip) => ssh(ip, 'sudo systemctl restart palworld && echo "Palworld redémarre avec les nouveaux réglages (environ une minute)."'),
  palworldReglages: (ip, { valeur }) => {
    if (valeur == null) return sshJson(ip, `${G} palworld-reglages`);
    const v = reglagesPalValides(valeur);
    return v ? sshJson(ip, `${G} palworld-reglages`, JSON.stringify(v)) : INVALIDE;
  },
  palJoueurs: (ip) => sshJson(ip, `${G} pal-joueurs`),
  palAction: (ip, { valeur }) => {
    if (!valeur || typeof valeur !== 'object') return INVALIDE;
    if (valeur.action === 'announce') {
      const m = commandeValide(valeur.message || '');
      return m && m.length <= 200 ? sshJson(ip, `${G} pal-action announce`, m) : INVALIDE;
    }
    if (valeur.action === 'save') return sshJson(ip, `${G} pal-action save`);
    return ['kick', 'ban', 'unban'].includes(valeur.action) && /^[A-Za-z0-9_-]{1,64}$/.test(valeur.id || '')
      ? sshJson(ip, `${G} pal-action ${valeur.action} ${valeur.id}`) : INVALIDE;
  },
  // Modern Warfare 2 (IW4x) : gestion.py valide les réglages et les commandes (reçus en JSON sur l'entrée)
  mw2Etat: (ip) => sshJson(ip, `${G} mw2-etat`),
  mw2Demarrer: (ip) => ssh(ip, 'sudo systemctl start iw4x && echo "Le serveur MW2 démarre (environ 30 secondes)."'),
  mw2Arreter: (ip) => ssh(ip, 'sudo systemctl stop iw4x && echo "Serveur MW2 arrêté."'),
  mw2Redemarrer: (ip) => ssh(ip, 'sudo systemctl restart iw4x && echo "Le serveur MW2 redémarre."'),
  mw2Reglages: (ip, { valeur }) => (valeur && typeof valeur === 'object'
    ? sshJson(ip, `${G} mw2-reglages`, JSON.stringify(valeur)) : INVALIDE),
  mw2Action: (ip, { valeur }) => (valeur && typeof valeur === 'object' && typeof valeur.action === 'string'
    ? sshJson(ip, `${G} mw2-action`, JSON.stringify({ action: valeur.action, valeur: String(valeur.valeur ?? '') })) : INVALIDE),
  mcRegles: (ip) => sshJson(ip, `${G} mc-regles`),
  mcRegle: (ip, { valeur }) => (regleMcValide(valeur) ? sshJson(ip, `${G} mc-regle ${valeur.cle} ${valeur.valeur}`) : INVALIDE),
  mcProprietes: (ip, { valeur }) => (valeur && typeof valeur === 'object'
    ? sshJson(ip, `${G} mc-proprietes`, JSON.stringify(valeur)) : INVALIDE),
  nouveauMonde: (ip, { valeur }) => (nouveauMondeValide(valeur)
    ? sshJson(ip, `${G} lancer nouveau-monde ${valeur.type} ${valeur.graine || 'aleatoire'}`) : INVALIDE),
  joueurAction: (ip, { valeur }) => {
    const lignes = commandesJoueur(valeur);
    return lignes ? consoleAvecReponse(ip, lignes) : INVALIDE;
  },
  monde: (ip, { valeur }) => (Object.hasOwn(COMMANDES_MONDE, valeur) ? consoleAvecReponse(ip, [COMMANDES_MONDE[valeur]]) : INVALIDE),
};

async function getJson(url) {
  const r = await fetch(url, { headers: UA, signal: AbortSignal.timeout(15000) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function rechercher(source, q) {
  if (source === 'modrinth') {
    const p = new URLSearchParams({
      query: q, limit: '12', index: q ? 'relevance' : 'downloads',
      facets: JSON.stringify([['project_type:modpack'], ['server_side:required', 'server_side:optional']]),
    });
    const j = await getJson(`https://api.modrinth.com/v2/search?${p}`);
    return j.hits.map((h) => ({
      source: 'modrinth', id: h.project_id, titre: h.title, description: h.description, icone: h.icon_url || null,
      telechargements: h.downloads, mc: (h.versions || []).at(-1) || '',
      loaders: (h.categories || []).filter((c) => ['fabric', 'forge', 'neoforge', 'quilt'].includes(c)),
    }));
  }
  if (source === 'ftb') {
    if (!q) return [];
    const j = await getJson(`${FTB}/search/12?term=${encodeURIComponent(q)}`);
    const packs = await Promise.all((j.packs || []).slice(0, 12).map((id) => getJson(`${FTB}/${id}`).catch(() => null)));
    return packs.filter(Boolean).map((p) => {
      const v = (p.versions || []).filter((x) => x.type === 'release').sort((a, b) => b.updated - a.updated)[0] || {};
      const cibles = v.targets || [];
      return {
        source: 'ftb', id: String(p.id), titre: p.name, description: p.synopsis || '',
        icone: (p.art || []).find((a) => a.type === 'square')?.url || null, telechargements: p.installs || 0,
        mc: cibles.find((t) => t.name === 'minecraft')?.version || '',
        loaders: cibles.filter((t) => t.type === 'modloader').map((t) => t.name),
      };
    });
  }
  throw new Error('source inconnue');
}

function json(res, statut, corps) {
  res.writeHead(statut, { 'content-type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(corps));
}

const serveur = createServer(async (req, res) => {
  // Anti « DNS rebinding » : seul le nom local est accepté.
  if (!HOTES.includes(req.headers.host)) {
    res.writeHead(403).end();
    return;
  }
  const url = new URL(req.url, ADRESSE);
  const jetonOk = (req.headers['x-jeton'] || url.searchParams.get('jeton')) === JETON;

  if (req.method === 'GET' && url.pathname === '/') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
    res.end(PAGE.replace('__JETON__', JETON));
    return;
  }
  if (!jetonOk) return json(res, 403, { erreur: 'jeton' });
  const ip = lireIp();

  if (req.method === 'GET' && url.pathname === '/recherche') {
    const q = (url.searchParams.get('q') || '').replace(/[\x00-\x1f\x7f]/g, '').slice(0, 60).trim();
    try {
      return json(res, 200, { resultats: await rechercher(url.searchParams.get('source'), q) });
    } catch (e) {
      return json(res, 502, { erreur: `Recherche impossible (${e.message}).` });
    }
  }

  if (!ip) return json(res, 200, { pret: false });

  if (req.method === 'GET' && url.pathname === '/journal') {
    res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store', connection: 'keep-alive' });
    const service = url.searchParams.get('service') === 'palworld' ? 'palworld' : 'minecraft';
    const p = spawn('ssh', [...optionsSsh(ip), `journalctl -u ${service} -f -n 300 -o cat`]);
    let reste = '';
    p.stdout.on('data', (d) => {
      const lignes = (reste + d).split('\n');
      reste = lignes.pop();
      for (const l of lignes) res.write(`data: ${JSON.stringify(l)}\n\n`);
    });
    p.on('close', () => res.end());
    req.on('close', () => p.kill());
    return;
  }

  if (req.method === 'POST' && url.pathname === '/envoi') {
    const type = url.searchParams.get('type');
    const titre = titreDepuisFichier(url.searchParams.get('nom'));
    const taille = Number(req.headers['content-length']);
    if (!['carte', 'serverpack'].includes(type) || !titre) return json(res, 400, { erreur: 'Envoi invalide.' });
    if (!(taille > 0) || taille > TAILLE_MAX_ENVOI) return json(res, 413, { erreur: 'Fichier vide ou trop gros (8 Go max).' });
    const fichier = `${randomBytes(16).toString('hex')}.zip`;
    const p = spawn('ssh', [...optionsSsh(ip), `cat > ~/instances/.envois/${fichier}`]);
    req.pipe(p.stdin);
    req.on('close', () => { if (!req.complete) { p.kill(); ssh(ip, `rm -f ~/instances/.envois/${fichier}`); } });
    const code = await new Promise((ok) => { p.on('close', ok); p.on('error', () => ok(-1)); });
    if (code !== 0 || !req.complete) return json(res, 502, { erreur: 'L\'envoi vers le serveur a échoué.' });
    const r = await sshJson(ip, `${G} lancer ${type === 'carte' ? `carte ${fichier}` : `serverpack ${fichier} ${titre}`}`);
    return json(res, 200, { pret: true, ...r });
  }

  if (req.method === 'POST' && url.pathname === '/action') {
    let brut = '';
    for await (const morceau of req) {
      brut += morceau;
      if (brut.length > 4096) return json(res, 413, { erreur: 'trop long' });
    }
    let demande;
    try { demande = JSON.parse(brut); } catch { return json(res, 400, { erreur: 'JSON invalide' }); }
    const action = Object.hasOwn(ACTIONS, demande.action) ? ACTIONS[demande.action] : null;
    if (!action) return json(res, 400, { erreur: 'action inconnue' });
    const r = await action(ip, demande);
    const hote = ip.split('@').pop();
    return json(res, 200, { pret: true, adresse: lire(FICHIER_ADRESSE) || hote, ipLocale: hote, ...r, stdout: undefined });
  }

  res.writeHead(404).end();
});

serveur.on('error', (e) => {
  if (e.code === 'EADDRINUSE') { // panneau déjà ouvert : on rouvre juste la page
    ouvrirNavigateur();
    setTimeout(() => process.exit(0), 500);
  } else throw e;
});

function ouvrirNavigateur() {
  if (!process.argv.includes('--sans-navigateur')) spawn('cmd', ['/c', 'start', '', ADRESSE], { stdio: 'ignore', detached: true }).unref();
}

// Lancé directement (pas importé par un test) : on démarre le serveur local.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  serveur.listen(PORT, ECOUTE, () => {
    console.log(`Panneau Minecraft : ${ADRESSE}  (fermer cette fenêtre pour l'arrêter)`);
    ouvrirNavigateur();
  });
  // Adresses supplémentaires : même gestionnaire, un serveur d'écoute par adresse
  for (const ip of ECOUTES.slice(1)) {
    const autre = createServer((req, res) => serveur.emit('request', req, res));
    // Au démarrage du PC, le Wi-Fi peut ne pas avoir encore son adresse : on réessaie.
    autre.on('error', () => setTimeout(() => autre.listen(PORT, ip), 10000));
    autre.listen(PORT, ip);
  }
}
