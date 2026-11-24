# Private access with Cloudflare Tunnel and Access

This is the setup we recommend when the server sits on an internal network and your team is spread
around: no open ports, no VPN, and only people with a company email can open the board.

- **Cloudflare Tunnel** connects the server to your hostname from the inside. You don't need a public
  IP or open firewall ports.
- **Cloudflare Access** puts a login (email one-time PIN, Google, Okta, …) in front of the board. A
  second Access app lets the laptops' telemetry in with a service token.

Both are on Cloudflare's free Zero Trust plan for small teams. You need a domain on Cloudflare.

## 1. Run the server

Keep `LB_HOST=127.0.0.1` so the server is only reachable through the tunnel. Then start it with
`python3 server.py`, Docker, or one of the files in `deploy/`.

## 2. Create the tunnel

Zero Trust dashboard → Networks → Tunnels → Create a tunnel (Cloudflared).

1. Install the connector the dashboard shows, or:
   - **Docker:** put its token in `.env` as `TUNNEL_TOKEN=...` and run
     `docker compose --profile tunnel up -d`.
   - **No root:** put the token in `data/tunnel.token` (chmod 600) and run `run-tunnel.sh` from cron.
2. Add a public hostname, e.g. `leaderboard.example.com`, pointing to:
   - `http://127.0.0.1:8790` for a plain install
   - `http://leaderboard:8790` with Docker Compose

## 3. Protect the board (viewer app)

Access → Applications → Add → Self-hosted.

1. Domain: `leaderboard.example.com`, with no path, so it covers the whole site.
2. Policy "Employees": Allow, with *Emails ending in* `@example.com` (or your identity provider's group).
3. Login method: One-time PIN, or any identity provider you have.
4. Save, then copy the **Application Audience (AUD) tag**.

## 4. Let laptops send usage (ingest app)

Laptops can't do an interactive login, so they use a service token instead.

1. Access → Service Auth → Service Tokens → Create a service token. Copy its **Client ID** and
   **Client Secret**.
2. Access → Applications → Add → Self-hosted:
   - **Paths:** `leaderboard.example.com/v1/metrics` (this also covers `/v1/metrics/backfill`),
     `leaderboard.example.com/install.sh` and `leaderboard.example.com/install.ps1`.
   - **Policy:** action **Service Auth**, include that service token.

A more specific path wins in Access, so these paths use the service token while the rest of the site
still asks people to log in.

## 5. Tell the server

In `config.env`:

```
LB_PUBLIC_URL=https://leaderboard.example.com
LB_CF_TEAM_DOMAIN=yourteam.cloudflareaccess.com
LB_CF_VIEWER_AUD=<AUD tag from step 3>
LB_CF_CLIENT_ID=<service token client id>
LB_CF_CLIENT_SECRET=<service token secret>
LB_ADMIN_EMAILS=you@example.com
```

Restart the server. From then on:

- **Viewers** are identified by the verified `Cf-Access-Jwt-Assertion`, checked against your team's
  keys, the AUD tag and the expiry. People can link their name once with their token, then add a photo.
- **Admins** listed in `LB_ADMIN_EMAILS` open `/admin` without a passphrase.
- **Install commands** from the admin page include the service-token headers, so the installer
  download and the telemetry both get through Access.

## Check it

```bash
curl -sI https://leaderboard.example.com/                # 302 to the Access login
curl -s -X POST https://leaderboard.example.com/v1/metrics -d '{}' -H 'Content-Type: application/json'
                                                         # blocked without the service token
```

The service token expires (one year by default). Renew it before then and update `config.env`.
Participants who installed with the old token will need a fresh install command.
