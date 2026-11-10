# TubeDock

Convertisseur personnel de liens pris en charge par [yt-dlp](https://github.com/yt-dlp/yt-dlp) → MP3 ou MP4, à héberger chez soi. Une conversion à la fois, jusqu'à quatre demandes simultanées dans la file. Les fichiers sont supprimés une heure après la fin de la conversion ; une vérification a lieu toutes les 30 secondes. Un redémarrage efface les conversions précédentes, même avant leur expiration.

## Lancer avec Docker Compose

```sh
docker compose up -d --build
```

Ouvrir <http://localhost:8080>. Le port est lié à `127.0.0.1` : pour accéder depuis un autre appareil, placer un reverse proxy authentifié devant le service ou configurer un accès privé (VPN). L'application ne comporte **pas d'authentification** : ne pas l'exposer directement sur Internet. Toute personne ayant accès au service peut lancer des conversions ; toute personne possédant un lien peut télécharger le fichier pendant sa durée de vie.

Pour arrêter : `docker compose down`. Les données temporaires sont dans le volume Docker `converter-data`. L'espace n'est pas limité strictement : les flux et fichiers intermédiaires peuvent être volumineux. Prévoir suffisamment d'espace et, si nécessaire, un quota sur le volume. La limite de 2 Go de yt-dlp s'applique aux fichiers source de taille connue, pas à l'espace total ni nécessairement à la taille finale. Conversion limitée à 30 minutes.

Pour mettre à jour yt-dlp et l'image : `docker compose build --no-cache && docker compose up -d`. Les sites modifient régulièrement leurs services : certains contenus peuvent échouer, notamment celles privées, soumises à restrictions ou nécessitant une authentification. Pas de cookies, playlists ou téléchargements en masse dans cette version.

**Sécurité réseau :** le service accepte des URL externes fournies par ses utilisateurs et peut suivre des redirections. Le filtrage des adresses locales évidentes ne constitue pas une protection complète contre les requêtes vers le réseau interne (SSRF). Réserver l’accès à des personnes de confiance et isoler le conteneur des services internes sensibles ; un VPN ou une authentification ne rend pas ces URL sûres en soi.

## Fonctionnement

L'interface envoie le lien au serveur. yt-dlp télécharge le média, puis FFmpeg produit le format demandé. Le lien n'est disponible qu'une fois le traitement terminé et ne survit pas au redémarrage. Les informations de progression sont conservées en mémoire uniquement. Il faut un seul processus et une seule instance : ne pas mettre plusieurs réplicas derrière un répartiteur.

N'utilisez l'outil que pour les contenus que vous êtes autorisé à télécharger et conformément aux conditions applicables.

## Publication privée sur GitHub

Depuis ce dossier, initialiser Git, créer un dépôt privé sur GitHub puis ajouter son adresse comme `origin` et pousser la branche. Ne pas ajouter de cookies, identifiants ou fichiers téléchargés au dépôt.
