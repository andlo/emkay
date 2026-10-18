#!/bin/bash
# Install Emkay on a fresh Debian/Ubuntu machine or LXC:
#
#   sudo bash install.sh
#
# Tor, Ollama and the model, a user `emkay` with a venv, a vault with a random
# passphrase, the café groups, and Emkay as a systemd service. Running it again
# updates Emkay and keeps the vault, key and state.
set -euo pipefail

MODEL="${MODEL:-qwen2.5:7b}"
REPO="${REPO:-https://github.com/andlo/emkay.git}"
HOME_DIR=/home/emkay
ENV_FILE=/etc/emkay/env
CONFIG=$HOME_DIR/.config/emkay/emkay.toml

apt-get update -qq
apt-get install -y -qq tor git python3-venv curl zstd ca-certificates >/dev/null
systemctl disable --now tor >/dev/null 2>&1 || true      # the Unseenet session starts its own Tor

if ! command -v ollama >/dev/null; then
  # not ollama's install.sh: in an LXC on a host with a GPU it tries to install
  # NVIDIA drivers and kernel headers, which fails - Emkay runs on the CPU
  curl -fsSL https://ollama.com/download/ollama-linux-amd64.tar.zst | tar --zstd -x -C /usr
  id ollama >/dev/null 2>&1 || useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama
  cat > /etc/systemd/system/ollama.service <<EOF
[Unit]
Description=Ollama
After=network-online.target

[Service]
ExecStart=/usr/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
fi
systemctl enable --now ollama
for i in $(seq 1 30); do ollama list >/dev/null 2>&1 && break; sleep 1; done
ollama pull "$MODEL"

id emkay >/dev/null 2>&1 || useradd -m -s /bin/bash emkay
sudo -u emkay bash -s <<EOF
set -e
cd $HOME_DIR
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q --upgrade "git+$REPO"
mkdir -p .config/emkay .local/state/emkay
EOF

mkdir -p /etc/emkay
[ -f "$ENV_FILE" ] || echo "UNSEENET_VAULT_PASSPHRASE=$(head -c 32 /dev/urandom | base64 | tr -d '/+=')" > "$ENV_FILE"
chown root:emkay "$ENV_FILE"; chmod 640 "$ENV_FILE"

if [ ! -f "$CONFIG" ]; then
  cat > "$CONFIG" <<EOF
[bot]
vault = "$HOME_DIR/.config/emkay/emkay.vault"
state_file = "$HOME_DIR/.local/state/emkay/state.json"
poll_seconds = 30
chat_quiet_seconds = 90
talk_quiet_minutes = 10
max_per_hour = 30
max_per_day = 200
max_per_thread = 6

[github]
owners = ["unseenet", "OpenVoiceOS"]   # README and description of their public repositories
refresh_hours = 12
cache_file = "$HOME_DIR/.local/state/emkay/repos.json"

[world]
wikipedia = true
duckduckgo = true

[model]
base_url = "http://127.0.0.1:11434/v1"
model = "$MODEL"
timeout = 600
EOF
  chown emkay:emkay "$CONFIG"
fi

sudo -u emkay env "$(cat "$ENV_FILE")" $HOME_DIR/.venv/bin/emkay -c "$CONFIG" sync
sudo -u emkay env "$(cat "$ENV_FILE")" $HOME_DIR/.venv/bin/emkay -c "$CONFIG" init

cat > /etc/systemd/system/emkay.service <<EOF
[Unit]
Description=Emkay, the robot café bot
After=network-online.target ollama.service
Wants=network-online.target

[Service]
User=emkay
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
ExecStart=$HOME_DIR/.venv/bin/emkay -c $CONFIG run
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable emkay
systemctl restart emkay
echo
echo "Emkay runs. Logs: journalctl -u emkay -f"
echo "Back up: $HOME_DIR/.config/emkay/ (vault, config) and $ENV_FILE (passphrase)"
