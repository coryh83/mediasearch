# mediasearch

A simple search page for torrents. Search your Jackett indexers, click Download, and the torrent is sent to Transmission.

## Setup

1. Create a `.env` file next to `docker-compose.yml` with a long, unique password. The app refuses to start without one:

   ```sh
   APP_USER=admin
   APP_PASSWORD=replace-with-a-long-random-password
   ```

2. Copy the project to your server and run:

   ```sh
   docker compose up -d --build
   ```

3. Open `http://localhost:8080` on the server and log in with the user and password from `.env`. The port is bound to `127.0.0.1`, so it is not reachable from other machines directly. Put your tunnel in front of it.

## Security

- Every page requires a login (HTTP Basic auth). Use HTTPS through your tunnel, because Basic auth sends the password with each request.
- Cross-site form posts are rejected, so another website cannot trigger downloads or change settings through your browser.
- `data/config.json` holds your Jackett API keys and Transmission password. It is saved owner-only (`600`). Keep `data/` out of backups you share.
- The Transmission password is never shown back in the browser. Leave the field blank to keep the saved one.
- API docs pages are disabled.

3. Go to **Config**:
   - **Indexers:** in Jackett, open an indexer, choose "Copy Torznab Feed", and paste the URL with a name. Blank rows are ignored.
   - **Transmission:** the RPC URL, usually `http://<host>:9091`, plus a username and password if you have RPC auth enabled.

Settings are stored in `./data/config.json`.

## Notes

- Transmission and Jackett are not part of this compose file. Run them separately, or add them to `docker-compose.yml`.
- If Jackett and Transmission run in the same Docker network, use their service names in the URLs (e.g. `http://jackett:9117`).
