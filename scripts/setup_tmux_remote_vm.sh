#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <host> <user>" >&2
    exit 1
fi

HOST="$1"
REMOTE_USER="$2"
LOCAL_CONF="${SCRIPT_DIR}/../.tmux.conf.local"

if [[ ! -f "$LOCAL_CONF" ]]; then
    echo "Error: .tmux.conf.local not found at ${LOCAL_CONF}" >&2
    exit 1
fi

scp "$LOCAL_CONF" "${REMOTE_USER}@${HOST}:/home/${REMOTE_USER}/.tmux.conf.local"

ssh "${REMOTE_USER}@${HOST}" << 'REMOTE'
    git clone --depth=1 https://github.com/gpakosz/.tmux.git "$HOME/.tmux" 2>/dev/null \
        || (cd "$HOME/.tmux" && git pull)
    ln -sf "$HOME/.tmux/.tmux.conf" "$HOME/.tmux.conf"
REMOTE

echo "tmux configured on ${REMOTE_USER}@${HOST}"
