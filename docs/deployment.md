# Deployment

## Docker

> [!TIP]
> The `-v ~/.silver-research-bot:/home/silver-research-bot/.silver-research-bot` flag mounts your local config directory into the container, so your config and workspace persist across container restarts.
> The container runs as user `silver-research-bot` (UID 1000). If you get **Permission denied**, fix ownership on the host first: `sudo chown -R 1000:1000 ~/.silver-research-bot`, or pass `--user $(id -u):$(id -g)` to match your host UID. Podman users can use `--userns=keep-id` instead.

### Docker Compose

```bash
docker compose run --rm silver-research-bot-cli onboard   # first-time setup
vim ~/.silver-research-bot/config.json                     # add API keys
docker compose up -d silver-research-bot-gateway           # start gateway
```

```bash
docker compose run --rm silver-research-bot-cli agent -m "Hello!"   # run CLI
docker compose logs -f silver-research-bot-gateway                   # view logs
docker compose down                                      # stop
```

### Docker

```bash
# Build the image
docker build -t silver-research-bot .

# Initialize config (first time only)
docker run -v ~/.silver-research-bot:/home/silver-research-bot/.silver-research-bot --rm silver-research-bot onboard

# Edit config on host to add API keys
vim ~/.silver-research-bot/config.json

# Run gateway (connects to enabled channels, e.g. Telegram/Discord/Mochat)
docker run -v ~/.silver-research-bot:/home/silver-research-bot/.silver-research-bot -p 18790:18790 silver-research-bot gateway

# Or run a single command
docker run -v ~/.silver-research-bot:/home/silver-research-bot/.silver-research-bot --rm silver-research-bot agent -m "Hello!"
docker run -v ~/.silver-research-bot:/home/silver-research-bot/.silver-research-bot --rm silver-research-bot status
```

## Linux Service

Run the gateway as a systemd user service so it starts automatically and restarts on failure.

**1. Find the silver-research-bot binary path:**

```bash
which silver-research-bot   # e.g. /home/user/.local/bin/silver-research-bot
```

**2. Create the service file** at `~/.config/systemd/user/silver-research-bot-gateway.service` (replace `ExecStart` path if needed):

```ini
[Unit]
Description=silver-research-bot Gateway
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/silver-research-bot gateway
Restart=always
RestartSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=%h

[Install]
WantedBy=default.target
```

**3. Enable and start:**

```bash
systemctl --user daemon-reload
systemctl --user enable --now silver-research-bot-gateway
```

**Common operations:**

```bash
systemctl --user status silver-research-bot-gateway        # check status
systemctl --user restart silver-research-bot-gateway       # restart after config changes
journalctl --user -u silver-research-bot-gateway -f        # follow logs
```

If you edit the `.service` file itself, run `systemctl --user daemon-reload` before restarting.

> **Note:** User services only run while you are logged in. To keep the gateway running after logout, enable lingering:
>
> ```bash
> loginctl enable-linger $USER
> ```
