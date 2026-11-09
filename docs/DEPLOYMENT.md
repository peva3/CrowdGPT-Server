# Deploying an orchestrator

The node is stateless outside `/data`: SQLite WAL, the node key, the object
cache, checkpoints, and public records live there. Back up that directory.

## Docker (recommended)

```bash
cp .env.example .env
# set CROWDGPT_ADMIN_SECRET and CROWDGPT_PUBLIC_URL
docker compose up -d --build
docker compose logs -f orchestrator
curl -fsS http://127.0.0.1:5006/cluster/health
```

The default compose file uses the named volume `crowdgpt-data`. A host bind
mount must be writable by UID 10001:

```bash
mkdir -p data && sudo chown -R 10001:10001 data
```

The image's own `/data` is owned by UID 10001; a root-owned bind mount makes
SQLite fail with `unable to open database file`.

Put a TLS reverse proxy in front of port 5006. Set
`CROWDGPT_TRUSTED_PROXIES` to the proxy range. Do not publish 5006 directly to
the internet.

The image runs as UID 10001 and writes only `/data`.

## systemd (no Docker)

```bash
sudo useradd --system --home /var/lib/crowdgpt --shell /usr/sbin/nologin crowdgpt
sudo mkdir -p /opt/crowdgpt /var/lib/crowdgpt /etc/crowdgpt
sudo rsync -a --exclude data --exclude .git ./ /opt/crowdgpt/
sudo python3.12 -m venv /opt/crowdgpt/.venv
sudo /opt/crowdgpt/.venv/bin/pip install -r /opt/crowdgpt/requirements.lock
sudo cp deploy/crowdgpt-orchestrator.service /etc/systemd/system/
sudo tee /etc/crowdgpt/orchestrator.env >/dev/null <<'ENV'
CROWDGPT_ADMIN_SECRET=change-me
CROWDGPT_PUBLIC_URL=https://node.example
CROWDGPT_ORCHESTRATOR_DB=/var/lib/crowdgpt/orchestrator_state.sqlite3
CROWDGPT_OBJECT_DIR=/var/lib/crowdgpt/objects
CROWDGPT_PEERS_FILE=/var/lib/crowdgpt/peers.json
CROWDGPT_TRUSTED_PROXIES=127.0.0.1/32
ENV
sudo chown -R crowdgpt:crowdgpt /var/lib/crowdgpt
sudo systemctl daemon-reload
sudo systemctl enable --now crowdgpt-orchestrator
sudo -u crowdgpt /opt/crowdgpt/.venv/bin/python /opt/crowdgpt/tools/operator_selfcheck.py
```

## CLI

```bash
python tools/orchestrator_cli.py init --dir /var/lib/crowdgpt \
  --public-url https://node.example
python tools/orchestrator_cli.py doctor --dir /var/lib/crowdgpt --probe
python tools/orchestrator_cli.py invite --dir /var/lib/crowdgpt \
  --endpoint https://node.example --out invite.json
python tools/orchestrator_cli.py join --dir /var/lib/crowdgpt \
  --token invite.json --trusted-pubkey <issuer-hex> \
  --public-url https://me.example
python tools/orchestrator_cli.py status --dir /var/lib/crowdgpt
python tools/orchestrator_cli.py verify --dir /var/lib/crowdgpt
```

`join` requires the issuer public key out of band. A self-signed invite is not
trusted.

## Reachability

Read `docs/NG_NETWORKING.md`. Trainers need only outbound HTTPS. Orchestrators
need a public endpoint, reverse proxy, tunnel, or private mesh. There is no
hole punching or relay.

## Upgrade and backup

```bash
python tools/orchestrator_cli.py backup --dir /var/lib/crowdgpt --backup /backup/node
python tools/orchestrator_cli.py restore --dir /var/lib/crowdgpt --backup /backup/node
python tools/orchestrator_cli.py verify --dir /var/lib/crowdgpt
```

Run `tools/operator_selfcheck.py` before starting a new image and after a
restore. The node key is identity: losing it loses the node id.
