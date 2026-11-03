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

4. Go to **Config**:
   - **Indexers:** in Jackett, open an indexer, choose "Copy Torznab Feed", and paste the URL with a name. Blank rows are ignored. Or use Jackett's "all" feed (`/api/v2.0/indexers/all/results/torznab/?apikey=KEY`) to add one row for every indexer.
   - **Transmission:** the RPC URL, such as `http://<server-ip>:9091`, plus the username and password from Transmission's RPC settings if RPC auth is on. Use **Test** to check each connection before saving.

Settings are stored in a Docker volume named `mediasearch-data`, which keeps them across restarts and updates.

## Security

- Every page requires a login (HTTP Basic auth). Use HTTPS through your tunnel, because Basic auth sends the password with each request.
- Cross-site form posts are rejected, so another website cannot trigger downloads or change settings through your browser.
- The settings file holds your Jackett API keys and Transmission password. It is saved owner-only (`600`).
- The Transmission password is never shown back in the browser. Leave the field blank to keep the saved one.
- API docs pages are disabled.

## Notes

- Transmission and Jackett are not part of this compose file. Run them separately.
- Searches only return book and audiobook categories (Torznab 3030 and 7000).
