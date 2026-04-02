#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <host> <user>" >&2
    exit 1
fi

HOST="$1"
REMOTE_USER="$2"
LOCAL_CONF="$HOME/.tmux.conf.local"

if [[ ! -f "$LOCAL_CONF" ]]; then
    echo "Error: ~/.tmux.conf.local not found. Run setup.sh --only tmux first." >&2
    exit 1
fi

scp "$LOCAL_CONF" "${REMOTE_USER}@${HOST}:/home/${REMOTE_USER}/.tmux.conf.local"

ssh "${REMOTE_USER}@${HOST}" << 'REMOTE'
    git clone --depth=1 https://github.com/gpakosz/.tmux.git "$HOME/.tmux" 2>/dev/null \
        || (cd "$HOME/.tmux" && git pull)
    ln -sf "$HOME/.tmux/.tmux.conf" "$HOME/.tmux.conf"
REMOTE

echo "tmux configured on ${REMOTE_USER}@${HOST}"
