# TubeDock

Convertisseur personnel de liens pris en charge par [yt-dlp](https://github.com/yt-dlp/yt-dlp) → MP3 ou MP4, à héberger chez soi. Une conversion à la fois, jusqu'à quatre demandes simultanées dans la file. Les fichiers sont supprimés une heure après la fin de la conversion ; une vérification a lieu toutes les 30 secondes. Un redémarrage efface les conversions précédentes, même avant leur expiration.

## Lancer avec Docker Compose

```sh
docker compose up -d --build
```

Le service écoute sur `127.0.0.1:8080`. Placer un reverse proxy **HTTPS** devant le service, en conservant l'en-tête `Host` public. La connexion locale utilise des cookies `Secure` : l'interface doit être ouverte via son adresse HTTPS, pas directement via le port HTTP. Aucun compte public ne peut s'inscrire sans invitation. Les téléchargements sont réservés au compte qui a lancé la conversion.

## Compte administrateur et invitations

Au premier démarrage, le compte administrateur `tlacassi` existe mais n'a pas encore de mot de passe. Sur le serveur, lire le code de configuration avec :

```sh
docker compose exec -T converter cat /auth/setup-token
```

Ouvrir `https://VOTRE-DOMAINE/setup#CODE` dans son navigateur et choisir son propre mot de passe (8 à 128 caractères, sans autre règle de composition). Le code est secret, utilisable une seule fois, et expire après une heure. Il n'est pas écrit dans les logs. Ne pas partager ce lien. Avant configuration, aucune session admin ne peut être ouverte. Si le code expire, un opérateur disposant de la console du serveur peut le renouveler avec `docker compose exec -T converter python auth.py renew-setup` ; cette commande refuse de remplacer le mot de passe d'un admin déjà configuré.

La page `/admin` permet de créer des invitations, de révoquer celles encore disponibles et de révoquer les comptes. Une invitation expire après 7 jours, ne peut être utilisée qu'une fois et permet au destinataire de choisir son identifiant et son mot de passe. Le lien secret n'est affiché qu'à sa création. Révoquer un compte invalide immédiatement toutes ses sessions pour les requêtes suivantes ; un téléchargement déjà commencé ou une conversion déjà en cours peut se terminer. Révoquer une invitation déjà utilisée ne révoque pas le compte créé. Le compte admin ne peut pas se révoquer lui-même.

Les comptes et sessions sont persistants dans le volume **`converter-auth`** (`/auth`), distinct des médias temporaires. Sauvegarder ce volume et ne pas utiliser `docker compose down -v` pour un arrêt normal. Les mots de passe sont hachés avec scrypt et un sel aléatoire ; les jetons de session et d'invitation sont hachés en base. Les sessions expirent après 12 heures. Les mutations exigent un jeton CSRF. Dix tentatives d'authentification sont autorisées par adresse IP sur 15 minutes ; le tunnel doit remplacer de manière fiable `CF-Connecting-IP`. Sans cet en-tête, l'adresse du pair est utilisée et la limite peut être partagée derrière un autre proxy. Les calculs scrypt concurrents sont bornés à deux.

Pour tester : `python -m unittest discover -s tests -v`.

Pour arrêter : `docker compose down`. Les données temporaires sont dans le volume Docker `converter-data`. L'espace n'est pas limité strictement : les flux et fichiers intermédiaires peuvent être volumineux. Prévoir suffisamment d'espace et, si nécessaire, un quota sur le volume. La limite de 2 Go de yt-dlp s'applique aux fichiers source de taille connue, pas à l'espace total ni nécessairement à la taille finale. Conversion limitée à 30 minutes.

Pour mettre à jour yt-dlp et l'image : `docker compose build --no-cache && docker compose up -d`. Les sites modifient régulièrement leurs services : certains contenus peuvent échouer, notamment celles privées, soumises à restrictions ou nécessitant une authentification. Pas de cookies, playlists ou téléchargements en masse dans cette version.

**Sécurité réseau :** le service accepte des URL externes fournies par ses utilisateurs et peut suivre des redirections. Le filtrage des adresses locales évidentes ne constitue pas une protection complète contre les requêtes vers le réseau interne (SSRF). Réserver l’accès à des personnes de confiance et isoler le conteneur des services internes sensibles ; un VPN ou une authentification ne rend pas ces URL sûres en soi.

## Fonctionnement

L'interface envoie le lien au serveur. yt-dlp télécharge le média, puis FFmpeg produit le format demandé. Le lien n'est disponible qu'une fois le traitement terminé et ne survit pas au redémarrage. Les informations de progression sont conservées en mémoire uniquement. Il faut un seul processus et une seule instance : ne pas mettre plusieurs réplicas derrière un répartiteur.

N'utilisez l'outil que pour les contenus que vous êtes autorisé à télécharger et conformément aux conditions applicables.

## Publication privée sur GitHub

Ne pas ajouter de cookies, identifiants, bases de comptes, codes de configuration ou fichiers téléchargés au dépôt. Les réglages propres à une installation peuvent être conservés dans un `compose.override.yaml` local. Si ce fichier remplace la liste des volumes avec `!override`, conserver **les deux** montages `/data` et `/auth`.
