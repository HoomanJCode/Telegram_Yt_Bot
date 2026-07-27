# Cloudflare SSL Setup Guide

> **Securing your Telegram bot's file server with Cloudflare**

This guide covers **three approaches** for serving HTTPS download links through Cloudflare. Choose the one that fits your deployment style.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Approach 1: Cloudflare Tunnel (Recommended)](#approach-1-cloudflare-tunnel-recommended)
- [Approach 2: Proxied DNS + Reverse Proxy](#approach-2-proxied-dns--reverse-proxy)
- [Approach 3: Native HTTPS with Origin CA](#approach-3-native-https-with-origin-ca)
- [Comparison Matrix](#comparison-matrix)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

Before starting, you need:

- A **domain name** with its nameservers pointed to Cloudflare
- The Telegram bot **already running** on your VPS (see [DEPLOYMENT.md](./DEPLOYMENT.md))
- The file server port (default: `8000`) accessible if **not** using the tunnel method

## Approach 1: Cloudflare Tunnel (Recommended)

**No open ports required.** The tunnel creates an outbound-only encrypted connection from your VPS to Cloudflare's edge. Works behind NAT, CGNAT, and strict firewalls.

### Step 1: Install cloudflared

```bash
# Download and install the cloudflared binary
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb

# Verify installation
cloudflared version
```

### Step 2: Authenticate cloudflared

```bash
cloudflared tunnel login
```

This opens a browser URL. Log into your Cloudflare account and select the domain for your tunnel. A certificate file (`~/.cloudflared/cert.pem`) is saved automatically.

### Step 3: Create a Tunnel

```bash
cloudflared tunnel create telegramytbot
```

This creates a tunnel named `telegramytbot` and generates a credentials file (e.g., `~/.cloudflared/<uuid>.json`). Note the tunnel UUID shown in the output.

### Step 4: Configure the Tunnel

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: <your-tunnel-uuid>
credentials-file: /root/.cloudflared/<uuid>.json

ingress:
  # Route downloads.yourdomain.com to the bot's file server
  - hostname: downloads.yourdomain.com
    service: http://localhost:8000
  # Catch-all: reject requests that don't match any hostname
  - service: http_status:404
```

### Step 5: Route DNS through the Tunnel

```bash
cloudflared tunnel route dns telegramytbot downloads.yourdomain.com
```

This creates a `CNAME` record pointing `downloads.yourdomain.com` to the tunnel.

### Step 6: Install as a System Service

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

### Step 7: Update Bot Configuration

Edit your `.env` file:

```env
BASE_DOWNLOAD_LINK=https://downloads.yourdomain.com
# Keep SSL_CERT_FILE and SSL_KEY_FILE commented out — Cloudflare
# handles TLS at the edge. The bot's file server stays on plain
# HTTP (localhost:8000), which is safe because only cloudflared
# can reach it.
```

> **⚠️ Important:** With Cloudflare Tunnel, the bot's file server listens on `0.0.0.0:8000` (all interfaces), but only `cloudflared` on the same machine connects to it via `127.0.0.1:8000`. For defence-in-depth, block external access to port 8000 at the firewall (`ufw deny 8000`). Set `BASE_DOWNLOAD_LINK` to `https://` (Cloudflare's edge) even though the origin is HTTP.

### Step 8: Restart the Bot

```bash
sudo systemctl restart telegramytbot
```

---

## Approach 2: Proxied DNS + Reverse Proxy

Use this approach when you prefer to manage your own reverse proxy (Nginx, Caddy) and want Cloudflare's CDN in front.

### Step 1: Set up a Reverse Proxy

#### Option A: Nginx

Install Nginx and create a site config:

```nginx
server {
    listen 80;
    server_name downloads.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Support for large file downloads
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_max_temp_file_size 0;
        client_max_body_size 0;
    }
}
```

Enable and start:

```bash
sudo ln -s /etc/nginx/sites-available/downloads /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

#### Option B: Caddy (Auto HTTPS)

```caddyfile
downloads.yourdomain.com {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy automatically provisions and renews Let's Encrypt certificates.

### Step 2: Configure Cloudflare DNS

In the Cloudflare Dashboard → **DNS**:

1. Add an **A record** pointing `downloads.yourdomain.com` to your server's public IP
2. Ensure the **Proxy status** (orange cloud ☁️) is **enabled** (proxied)

### Step 3: Set SSL/TLS Mode

In Cloudflare Dashboard → **SSL/TLS** → **Overview**:

- **Flexible** (default): Cloudflare → visitor is HTTPS, Cloudflare → your server is HTTP. Easy but less secure.
- **Full (Strict)** (recommended): End-to-end encryption. You'll need a valid certificate on your server (see Approach 3 for Origin CA).

### Step 4: Update Bot Configuration

```env
BASE_DOWNLOAD_LINK=https://downloads.yourdomain.com
```

### Step 5: Restart the Bot

```bash
sudo systemctl restart telegramytbot
```

---

## Approach 3: Native HTTPS with Origin CA

Use this when you want **end-to-end encryption** without a reverse proxy. The bot itself terminates TLS using a Cloudflare Origin CA certificate.

### Step 1: Generate an Origin CA Certificate

In Cloudflare Dashboard:

1. Go to **SSL/TLS** → **Origin Server**
2. Click **Create Certificate**
3. Choose:
   - **Hostname**: `downloads.yourdomain.com` (or `*.yourdomain.com` for wildcard)
   - **Private key type**: RSA (2048)
4. Click **Create**

Cloudflare shows you:

- **Origin Certificate** (`.pem`)
- **Private Key** (`.pem`)

**Copy both immediately** — Cloudflare does not store the private key.

### Step 2: Install the Certificate on Your VPS

SSH into your VPS and:

```bash
# Create a directory for SSL files
mkdir -p /opt/TelegramYtBot/ssl

# Save the certificate and key (paste the content from Cloudflare)
nano /opt/TelegramYtBot/ssl/origin-cert.pem
nano /opt/TelegramYtBot/ssl/origin-key.pem

# Secure the private key
chmod 600 /opt/TelegramYtBot/ssl/origin-key.pem
```

> **Tip:** You can also upload these as base64-encoded GitHub Secrets (`SSL_CERT_B64`, `SSL_KEY_B64`) for automated deployments. The CI pipeline will write them to disk automatically. See [DEPLOYMENT.md](./DEPLOYMENT.md).

### Step 3: Configure the Bot

Edit your `.env`:

```env
BASE_DOWNLOAD_LINK=https://downloads.yourdomain.com:8000
SSL_CERT_FILE=/opt/TelegramYtBot/ssl/origin-cert.pem
SSL_KEY_FILE=/opt/TelegramYtBot/ssl/origin-key.pem
```

> **Port note:** Keep the bot on port 8000 (non-privileged). If you need port 443:
> ```bash
> sudo setcap 'cap_net_bind_service=+ep' $(readlink -f $(which python3))
> ```
> Then change `BASE_DOWNLOAD_LINK=https://downloads.yourdomain.com:443`

### Step 4: Set Cloudflare SSL/TLS to Full (Strict)

In Cloudflare Dashboard → **SSL/TLS** → **Overview**:

- Select **Full (Strict)**

This ensures Cloudflare verifies your Origin CA certificate before forwarding traffic.

### Step 5: Configure Cloudflare DNS

1. Add an **A record** for `downloads.yourdomain.com` pointing to your server's public IP
2. Enable the **Proxy** (orange cloud ☁️)

### Step 6: Restart the Bot

```bash
sudo systemctl restart telegramytbot
```

Check the logs to confirm HTTPS is active:

```bash
journalctl -u telegramytbot -n 20 --no-pager | grep -i "https\|ssl\|file server"
```

Expected output:
```
INFO:yt_bot:File server on port 8000 (HTTPS)
```

### Step 7: Set Up Certificate Renewal

Cloudflare Origin CA certificates are valid for **15 years** — renewal is not a concern. However, if you ever regenerate the certificate, you must:

1. Copy the new cert + key to your VPS
2. Restart the bot: `sudo systemctl restart telegramytbot`

---

## Comparison Matrix

| Feature | Cloudflare Tunnel | Reverse Proxy | Native HTTPS + Origin CA |
|---------|:----------------:|:-------------:|:------------------------:|
| **Open ports required** | ❌ No | ✅ Yes | ✅ Yes |
| **Works behind NAT/CGNAT** | ✅ Yes | ❌ No | ❌ No |
| **Setup complexity** | Low | Medium | Medium |
| **End-to-end encryption** | ✅ Yes (tunnel) | ✅ Yes (when Strict) | ✅ Yes (Origin CA) |
| **Certificate management** | Automatic | Manual (or Caddy auto) | Manual (15yr validity) |
| **Bot code changes needed** | None | None | None |
| **Port 443 (standard HTTPS)** | Automatic | Via reverse proxy | Via `setcap` |
| **Firewall rules needed** | Outbound-only | Port 80/443 + 8000 open | Port 8000 (or 443) open |

---

## Troubleshooting

### "File server on port 8000 (HTTP)" instead of HTTPS

- **Cause**: `_build_ssl_context()` returned `None` because `SSL_CERT_FILE` / `SSL_KEY_FILE` are unset, empty, or the paths don't exist.
- **Fix**: Double-check `.env` paths and file permissions (`chmod 600` the key).
- **With Tunnel**: This is expected — Cloudflare handles TLS at the edge.

### "SSL configuration error" on bot startup

- **Cause**: Only one of `SSL_CERT_FILE` / `SSL_KEY_FILE` is set, or a certificate file doesn't exist.
- **Fix**: Set both variables or neither. Check file paths. The bot exits with code 78 (EX_CONFIG) so systemd won't restart-loop.

### Visitors see "ERR_TOO_MANY_REDIRECTS"

- **Cause**: Cloudflare's **Flexible** SSL mode + your origin also redirecting HTTP → HTTPS creates a redirect loop.
- **Fix**: Set SSL/TLS mode to **Full (Strict)** in Cloudflare Dashboard.

### Tunnel shows "failed to connect to origin"

- **Cause**: `cloudflared` cannot reach `http://localhost:8000`. The bot may be down or bound to `0.0.0.0` instead of `127.0.0.1`.
- **Fix**: Ensure the bot is running (`systemctl status telegramytbot`). If the bot's file server binds to all interfaces, restrict it by editing the firewall.

### Bot starts but download links don't load

- **Cause**: The port in `BASE_DOWNLOAD_LINK` doesn't match the bot's actual listening port.
- **Fix**: The bot parses the port from `BASE_DOWNLOAD_LINK`. Ensure it's correct.
- **With Tunnel**: `BASE_DOWNLOAD_LINK` should be `https://downloads.yourdomain.com` (no port needed — Cloudflare serves on 443).

---

**Next:** [Deployment Guide](./DEPLOYMENT.md) → [Configuration Reference](./CONFIGURATION.md) → [Back to README](../README.md)
