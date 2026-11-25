# TubeDock

A private YouTube to MP3/MP4 converter running on my home lab.
Entirely vibe coded with Claude Code.

## How it works

Paste a link, pick MP3 or MP4. [yt-dlp](https://github.com/yt-dlp/yt-dlp) downloads,
FFmpeg converts, and the file is deleted one hour later.

- One conversion at a time, up to four waiting
- No open sign-up: single-use invitation links
- Single container with Docker Compose

## Run

```sh
docker compose up -d --build
docker compose exec -T converter cat /auth/setup-token
```

The service listens on `127.0.0.1:8080` and needs an HTTPS reverse proxy.
Open `https://YOUR-DOMAIN/setup#CODE` to set the admin password.

> The server fetches URLs given by users (SSRF risk): only open it to people you trust,
> and isolate the container from the rest of your network.

Only use it for content you are allowed to download. The interface is in French.

[Detailed notes (in French)](docs/README.fr.md)
