// Vérifications du panneau : node test.mjs  (le panneau doit tourner sur le port 8765)
import assert from 'node:assert/strict';
import { request } from 'node:http';
import { pseudoValide, gamertagValide, commandeValide, nomValide, idModrinthValide, idFtbValide, titreDepuisFichier, reglagesPalValides,
  commandesJoueur, regleMcValide, nouveauMondeValide } from './panneau.mjs';

// Validation des saisies (tout ce qui finit sur la ligne SSH)
assert.ok(pseudoValide('Joueur_42'));
assert.ok(!pseudoValide('ab'));
assert.ok(gamertagValide('.Joueur_42') && !gamertagValide('Joueur') && !gamertagValide('.a b') && !gamertagValide('.x;stop'));
assert.ok(!pseudoValide('a'.repeat(17)));
assert.ok(!pseudoValide('x; rm -rf ~'));
assert.equal(commandeValide('/say Salut'), 'say Salut');
assert.equal(commandeValide('   '), null);
assert.equal(commandeValide('say a\nop pirate'), null); // pas d'injection de seconde ligne
assert.equal(commandeValide('x'.repeat(257)), null);
assert.ok(nomValide('vanilla') && nomValide('ftb-stoneblock-4'));
assert.ok(!nomValide('../mc') && !nomValide('a b') && !nomValide('-x') && !nomValide('Vanilla'));
assert.ok(idModrinthValide('1KVo5zza') && !idModrinthValide('a;reboot') && !idModrinthValide(''));
assert.ok(idFtbValide('130') && !idFtbValide('130 && reboot') && !idFtbValide('12345678'));
assert.equal(titreDepuisFichier('StoneBlock 4 Server Pack.zip'), 'StoneBlock-4-Server-Pack');
assert.equal(titreDepuisFichier('$(reboot);.zip'), 'reboot'); // caractères spéciaux retirés
assert.equal(titreDepuisFichier('--rm.zip'), 'rm');
assert.deepEqual(reglagesPalValides({ ServerName: 'Serveur de Joueur !', ServerPassword: '', ServerPlayerMaxNum: '16', Autre: 1 }),
  { ServerName: 'Serveur de Joueur !', ServerPassword: '', ServerPlayerMaxNum: '16', Autre: '1' }); // clé inconnue : écartée par gestion.py
assert.equal(reglagesPalValides({ ServerName: 'a",AdminPassword="x' }), null, 'injection dans OptionSettings refusée');
assert.equal(reglagesPalValides({ ServerPlayerMaxNum: '33' }), null);
assert.equal(reglagesPalValides({}), null);

// Fiche joueur : chaque commande console est construite à partir de morceaux validés
assert.deepEqual(commandesJoueur({ joueur: 'Joueur', action: 'donner', objet: 'diamond', nombre: 64 }), ['give Joueur minecraft:diamond 64']);
assert.deepEqual(commandesJoueur({ joueur: 'Joueur', action: 'donner', objet: 'mekanism:atomic_disassembler', nombre: 1 }), ['give Joueur mekanism:atomic_disassembler 1']);
assert.equal(commandesJoueur({ joueur: 'Joueur', action: 'donner', objet: 'diamond 64\nop pirate', nombre: 1 }), null, 'pas de seconde commande');
assert.equal(commandesJoueur({ joueur: 'Joueur', action: 'donner', objet: 'diamond', nombre: 9999 }), null);
assert.equal(commandesJoueur({ joueur: 'x; op y', action: 'op' }), null);
assert.deepEqual(commandesJoueur({ joueur: 'Joueur', action: 'enchanter', enchantement: 'sharpness', nombre: 5 }), ['enchant Joueur minecraft:sharpness 5']);
assert.equal(commandesJoueur({ joueur: 'Joueur', action: 'enchanter', enchantement: 'sharpness', nombre: 6 }), null, 'niveau max respecté');
assert.equal(commandesJoueur({ joueur: 'Joueur', action: 'mode', mode: 'god' }), null);
assert.equal(commandesJoueur({ joueur: 'Joueur', action: 'kit', kit: 'diamant' }).length, 11);
assert.ok(regleMcValide({ cle: 'garder_inventaire', valeur: 'true' }) && !regleMcValide({ cle: 'a b', valeur: 'true' }) && !regleMcValide({ cle: 'pvp', valeur: 'oui' }));
assert.ok(nouveauMondeValide({ type: 'flat', graine: '' }) && nouveauMondeValide({ type: 'normal', graine: '-42' }));
assert.ok(!nouveauMondeValide({ type: 'normal', graine: 'a b' }) && !nouveauMondeValide({ type: 'void', graine: '' }));
assert.deepEqual(reglagesPalValides({ PalEggDefaultHatchingTime: '0', bHardcore: 'False' }), { PalEggDefaultHatchingTime: '0', bHardcore: 'False' });
assert.equal(reglagesPalValides({ ExpRate: '1",AdminPassword="x' }), null, 'injection dans une option refusée');

// Protections HTTP
const appel = (methode, chemin, entetes = {}, corps) => new Promise((ok, ko) => {
  const r = request({ host: '127.0.0.1', port: 8765, method: methode, path: chemin, headers: entetes }, (res) => {
    let d = ''; res.on('data', (c) => (d += c)); res.on('end', () => ok({ statut: res.statusCode, corps: d }));
  });
  r.on('error', ko);
  r.end(corps);
});

assert.equal((await appel('GET', '/', { host: 'pirate.example:8765' })).statut, 403, 'hôte étranger refusé');
const page = await appel('GET', '/');
assert.equal(page.statut, 200);
const jeton = page.corps.match(/const JETON = "([0-9a-f]{48})"/)[1];
assert.equal((await appel('POST', '/action', {}, '{"action":"etat"}')).statut, 403, 'sans jeton refusé');
assert.equal((await appel('POST', '/action', { 'x-jeton': 'faux' }, '{"action":"etat"}')).statut, 403, 'mauvais jeton refusé');
assert.equal((await appel('GET', '/journal?jeton=faux')).statut, 403, 'flux sans jeton refusé');
assert.equal((await appel('GET', '/recherche?source=modrinth&q=x')).statut, 403, 'recherche sans jeton refusée');
assert.equal((await appel('POST', '/envoi?type=carte&nom=a.zip', { 'content-length': '1' }, 'x')).statut, 403, 'envoi sans jeton refusé');
assert.equal((await appel('POST', '/envoi?type=autre&nom=a.zip', { 'x-jeton': jeton, 'content-length': '1' }, 'x')).statut, 400, 'type d\'envoi inconnu refusé');
const piege = JSON.parse((await appel('POST', '/action', { 'x-jeton': jeton }, '{"action":"activer","valeur":"vanilla; reboot"}')).corps);
assert.equal(piege.code, 1, 'nom de monde piégé refusé');
assert.deepEqual(commandesJoueur({ joueur: 'Bob', action: 'tp', cible: 'Alice' }), ['tp Bob Alice']);
assert.deepEqual(commandesJoueur({ joueur: 'Bob', action: 'tp', cible: '100 64 -200' }), ['tp Bob 100 64 -200']);
assert.equal(commandesJoueur({ joueur: 'Bob', action: 'tp', cible: '1 2 3; op x' }), null);
const piegeMonde = JSON.parse((await appel('POST', '/action', { 'x-jeton': jeton }, '{"action":"monde","valeur":"duree99"}')).corps);
assert.equal(piegeMonde.code, 1, 'durée inconnue refusée');
const piegeSuppr = JSON.parse((await appel('POST', '/action', { 'x-jeton': jeton }, '{"action":"supprimer","valeur":"../mc"}')).corps);
assert.equal(piegeSuppr.code, 1, 'suppression hors des mondes refusée');

const etat = JSON.parse((await appel('POST', '/action', { 'x-jeton': jeton }, '{"action":"etat"}')).corps);
if (etat.pret) {
  assert.doesNotMatch(etat.ipLocale, /@/, "l'utilisateur SSH n'est pas affiché");
  assert.equal(etat.code, 0, 'gestion.py répond');
  assert.ok(['active', 'activating', 'inactive', 'failed'].includes(etat.donnees.service));
}
console.log('Toutes les vérifications passent.');
console.log(etat.pret ? `Serveur : ${etat.adresse} (${etat.ipLocale}), monde actif : ${etat.donnees.actif}` : 'Aucun serveur configuré.');
