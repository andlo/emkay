#!/bin/bash
# Update Emkay in place (as root on his machine): newest code, fresh knowledge, restart.
#   bash update.sh                # update
#   bash update.sh "question" ... # update, then ask him some questions
set -e
V=/home/emkay/.venv/bin
CFG=/home/emkay/.config/emkay/emkay.toml
sudo -u emkay $V/pip install -q --upgrade "git+https://github.com/andlo/emkay.git"   # new dependencies
sudo -u emkay $V/pip install -q --force-reinstall --no-deps "git+https://github.com/andlo/emkay.git"
systemctl restart emkay
cd /home/emkay
for q in "$@"; do
  echo "Q: $q"
  start=$(date +%s)
  sudo -u emkay $V/emkay -c $CFG ask "$q" 2>&1 | grep -viE "warn|migrate|new classes| INFO " || true
  echo "($(( $(date +%s) - start )) s)"
  echo
done
