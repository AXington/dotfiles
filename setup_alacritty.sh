#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

setup_completions() {
    local zsh_fn_dir="${ZDOTDIR:-$HOME}/.zsh_functions"
    if [[ ! -f "${zsh_fn_dir}/_alacritty" ]]; then
        mkdir -p "$zsh_fn_dir"
        curl -fsSL -o "${zsh_fn_dir}/_alacritty" \
            https://raw.githubusercontent.com/alacritty/alacritty/master/extra/completions/_alacritty
    fi
}

setup_man_pages() {
    local man_path="/usr/local/share/man/man1"
    local man_page_file="${man_path}/alacritty.1.gz"
    if [[ ! -f "$man_page_file" ]]; then
        sudo mkdir -p "$man_path"
        local tmp
        tmp="$(mktemp)"
        curl -fsSL -o "$tmp" \
            https://raw.githubusercontent.com/alacritty/alacritty/master/extra/alacritty.man
        gzip -c "$tmp" | sudo tee "$man_page_file" > /dev/null
        rm -f "$tmp"
    fi
}

setup_terminfo() {
    local tmp
    tmp="$(mktemp)"
    curl -fsSL -o "$tmp" \
        https://raw.githubusercontent.com/alacritty/alacritty/master/extra/alacritty.info
    sudo tic -xe alacritty,alacritty-direct "$tmp"
    rm -f "$tmp"
}

install_mac() {
    if ! command -v brew &>/dev/null; then
        bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    # Add Homebrew to PATH (Apple Silicon or Intel)
    if [[ -x /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    brew install --cask alacritty
    command -v gzip &>/dev/null || brew install gzip
}

install_debian() {
    sudo apt-get update -y
    sudo apt-get install -y gzip alacritty
}

install_rhel() {
    local mgr="dnf"
    command -v dnf &>/dev/null || mgr="yum"
    sudo "$mgr" install -y gzip alacritty
}

install_arch() {
    sudo pacman -S --needed --noconfirm alacritty gzip
}

install_alacritty() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        install_mac
    elif [[ "$OSTYPE" == "linux-gnu" || "$OSTYPE" == "linux-musl" ]] \
         || grep -qiE "microsoft|wsl" /proc/version 2>/dev/null \
         || [[ -n "${WSL_DISTRO_NAME:-}" ]]; then
        if command -v apt-get &>/dev/null; then
            install_debian
        elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
            install_rhel
        elif command -v pacman &>/dev/null; then
            install_arch
        else
            echo "Unsupported distro — install alacritty manually." >&2
            exit 1
        fi
    else
        echo "Unsupported OS." >&2
        exit 1
    fi
}

install_alacritty
setup_man_pages
setup_completions
setup_terminfo

# Symlink alacritty config
mkdir -p "$HOME/.config/alacritty"
ln -sf "${SCRIPT_DIR}/terminal_configs/alacritty.toml" "$HOME/.config/alacritty/alacritty.toml"
echo "Alacritty config symlinked to ~/.config/alacritty/alacritty.toml"
