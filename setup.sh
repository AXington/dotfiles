#!/usr/bin/env bash
set -euo pipefail

# Usage: ./setup.sh [--copilot]
#   --copilot   Also install GitHub Copilot CLI and bootstrap global instructions

# ── Argument parsing ─────────────────────────────────────────────────────────

SETUP_COPILOT=false

for arg in "$@"; do
    case "$arg" in
        --copilot|-c) SETUP_COPILOT=true ;;
        --help|-h)
            echo "Usage: $0 [--copilot]"
            echo "  --copilot, -c   Install GitHub Copilot CLI and bootstrap global instructions"
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

# ── Helpers ─────────────────────────────────────────────────────────────────

log()  { echo "==> $*"; }
warn() { echo "WARN: $*" >&2; }

command_exists() { command -v "$1" &>/dev/null; }

# ── OS / distro detection ────────────────────────────────────────────────────

detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    elif grep -qiE "microsoft|wsl" /proc/version 2>/dev/null || [[ -n "${WSL_DISTRO_NAME:-}" ]]; then
        echo "wsl"
    elif [[ "$OSTYPE" == "linux-gnu" || "$OSTYPE" == "linux-musl" ]]; then
        echo "linux"
    else
        echo "unknown"
    fi
}

detect_linux_distro() {
    if command_exists apt-get; then
        echo "debian"
    elif command_exists dnf; then
        echo "rhel"
    elif command_exists yum; then
        echo "rhel-old"
    elif command_exists pacman; then
        echo "arch"
    else
        echo "unknown"
    fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(detect_os)"

# ── Package installation ─────────────────────────────────────────────────────

install_packages_macos() {
    log "Installing Homebrew packages..."
    if ! command_exists brew; then
        log "Installing Homebrew..."
        bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi

    # Add Homebrew to PATH for this session (Apple Silicon or Intel)
    if [[ -x /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi

    brew update

    # Install each package, skipping already-installed ones
    while IFS= read -r pkg || [[ -n "$pkg" ]]; do
        [[ -z "$pkg" || "$pkg" == \#* ]] && continue
        # Strip inline option flags for the install check
        pkg_name="${pkg%% --*}"
        if brew list --formula "$pkg_name" &>/dev/null; then
            log "Already installed: $pkg_name"
        else
            # shellcheck disable=SC2086
            brew install $pkg
        fi
    done < "${SCRIPT_DIR}/brew_packages.txt"
}

install_packages_debian() {
    log "Installing apt packages..."
    sudo apt-get update -y
    # shellcheck disable=SC2046
    sudo apt-get install -y $(grep -v '^\s*#' "${SCRIPT_DIR}/apt-packages.txt" | xargs)
}

install_packages_rhel() {
    local mgr="dnf"
    command_exists dnf || mgr="yum"
    log "Installing packages via $mgr..."
    # shellcheck disable=SC2046
    sudo "$mgr" install -y $(grep -v '^\s*#' "${SCRIPT_DIR}/dnf-packages.txt" | xargs)
}

install_packages_arch() {
    log "Installing pacman packages..."
    # shellcheck disable=SC2046
    sudo pacman -S --needed --noconfirm $(grep -v '^\s*#' "${SCRIPT_DIR}/pacman-packages.txt" | xargs)
}

install_packages() {
    case "$OS" in
        macos) install_packages_macos ;;
        macos|wsl|linux)
            case "$(detect_linux_distro)" in
                debian)   install_packages_debian ;;
                rhel*)    install_packages_rhel ;;
                arch)     install_packages_arch ;;
                *) warn "Unsupported Linux distro — skipping package install" ;;
            esac
            ;;
    esac
}

# ── GNU tools symlink (macOS only) ───────────────────────────────────────────

setup_gnubin_macos() {
    log "Setting up ~/.gnubin symlinks for GNU tools..."
    mkdir -p "$HOME/.gnubin"

    # Homebrew prefix differs by architecture
    local brew_prefix
    brew_prefix="$(brew --prefix)"

    for dir in "${brew_prefix}/opt"/*/libexec/gnubin; do
        [[ -d "$dir" ]] || continue
        while IFS= read -r -d '' bin; do
            ln -sf "$bin" "$HOME/.gnubin/$(basename "$bin")"
        done < <(find "$dir" -maxdepth 1 -type f -print0)
    done
}

# ── Powerline fonts ──────────────────────────────────────────────────────────

install_powerline_fonts() {
    if fc-list 2>/dev/null | grep -qi "powerline\|MesloLGM\|Nerd Font"; then
        log "Powerline/Nerd fonts already installed, skipping."
        return
    fi
    log "Installing Powerline fonts..."
    local tmp_dir
    tmp_dir="$(mktemp -d)"
    git clone --depth=1 https://github.com/powerline/fonts.git "$tmp_dir/fonts"
    bash "$tmp_dir/fonts/install.sh"
    rm -rf "$tmp_dir"
}

# ── tmux ─────────────────────────────────────────────────────────────────────

setup_tmux() {
    log "Setting up tmux (gpakosz/.tmux)..."

    if [[ ! -d "$HOME/.tmux" ]]; then
        git clone https://github.com/gpakosz/.tmux.git "$HOME/.tmux"
    fi

    ln -sf "$HOME/.tmux/.tmux.conf" "$HOME/.tmux.conf"

    # Only copy the local config if it doesn't already exist
    if [[ ! -f "$HOME/.tmux.conf.local" ]]; then
        cp "$HOME/.tmux/.tmux.conf.local" "$HOME/.tmux.conf.local"
    fi

    local conf="$HOME/.tmux.conf.local"

    # Disable fancy separators
    sed -i.bak \
        -e 's/^tmux_conf_theme_left_separator/#tmux_conf_theme_left_separator/g' \
        -e 's/^tmux_conf_theme_right_separator/#tmux_conf_theme_right_separator/g' \
        -e "s/#tmux_conf_theme_left_separator_main=''/tmux_conf_theme_left_separator_main=''/" \
        -e "s/#tmux_conf_theme_left_separator_sub=''/tmux_conf_theme_left_separator_sub=''/" \
        -e "s/#tmux_conf_theme_right_separator_main=''/tmux_conf_theme_right_separator_main=''/" \
        -e "s/#tmux_conf_theme_right_separator_sub=''/tmux_conf_theme_right_separator_sub=''/" \
        -e 's/tmux_conf_copy_to_os_clipboard=false/tmux_conf_copy_to_os_clipboard=true/' \
        -e 's/#set -g mouse on/set -g mouse on/' \
        -e 's/# set -gu prefix2/set -gu prefix2/' \
        -e 's/# unbind C-a/unbind C-a/' \
        -e 's/# unbind C-b/unbind C-b/' \
        -e 's/# set -g prefix C-a/set -g prefix C-a/' \
        -e 's/# bind C-a send-prefix/bind C-a send-prefix/' \
        "$conf"
    rm -f "${conf}.bak"

    # Add window navigation bindings (idempotent)
    if ! grep -q "bind a last-window" "$conf"; then
        cat >> "$conf" << 'EOF'
bind a last-window
bind n next-window
EOF
    fi
}

# ── Oh My Zsh ────────────────────────────────────────────────────────────────

install_omz() {
    if [[ -d "$HOME/.oh-my-zsh" ]]; then
        log "Oh My Zsh already installed, skipping."
        return
    fi
    log "Installing Oh My Zsh..."
    # Non-interactive install: don't switch shell, don't start zsh
    RUNZSH=no CHSH=no sh -c \
        "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"
}

install_omz_plugins() {
    local plugin_dir="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins"
    if [[ ! -d "${plugin_dir}/zsh-syntax-highlighting" ]]; then
        log "Installing zsh-syntax-highlighting plugin..."
        git clone --depth=1 \
            https://github.com/zsh-users/zsh-syntax-highlighting.git \
            "${plugin_dir}/zsh-syntax-highlighting"
    fi
}

# ── .zshrc customizations ────────────────────────────────────────────────────

configure_zshrc() {
    local zshrc="$HOME/.zshrc"

    # Set theme and plugins in the OMZ-generated .zshrc
    if [[ -f "$zshrc" ]]; then
        sed -i.bak 's/ZSH_THEME="robbyrussell"/ZSH_THEME="agnoster"/' "$zshrc"
        sed -i.bak 's/^plugins=(git)$/plugins=(git zsh-syntax-highlighting)/' "$zshrc"
        rm -f "${zshrc}.bak"
    fi

    # Append dotfiles customizations block (idempotent)
    if grep -q "# >>> dotfiles customizations <<<" "$zshrc" 2>/dev/null; then
        log ".zshrc customizations already present, skipping."
        return
    fi

    log "Writing .zshrc customizations..."

    # macOS: prepend GNU tools and Homebrew to PATH
    local path_prefix='$HOME/.gnubin'
    if [[ "$OS" == "macos" ]]; then
        local brew_prefix
        brew_prefix="$(brew --prefix 2>/dev/null || echo '/opt/homebrew')"
        path_prefix="${brew_prefix}/bin:\$HOME/.gnubin"
    fi

    cat >> "$zshrc" << EOF

# >>> dotfiles customizations <<<

export PATH="${path_prefix}:\$PATH"

autoload -U +X bashcompinit && bashcompinit

# Prefer nvim locally, fall back to vim over SSH
if [[ -n "\$SSH_CONNECTION" ]]; then
    export EDITOR='vim'
else
    command -v nvim &>/dev/null && export EDITOR='nvim' || export EDITOR='vim'
fi

export SSH_KEY_PATH="\$HOME/.ssh/id_ed25519"

# Suppress agnoster user@host prompt when not over SSH
prompt_context() {}

export GPG_TTY=\$(tty)
bindkey '^R' history-incremental-search-backward

fpath+=\${ZDOTDIR:-~}/.zsh_functions

# Tool completions (loaded only if the tool is present)
command -v kubectl &>/dev/null && source <(kubectl completion zsh)
command -v helm    &>/dev/null && source <(helm completion zsh)

# fzf
[ -f "\$HOME/.fzf.zsh" ] && source "\$HOME/.fzf.zsh"

# pyenv
export PYENV_ROOT="\$HOME/.pyenv"
[[ -d "\$PYENV_ROOT/bin" ]] && export PATH="\$PYENV_ROOT/bin:\$PATH"
command -v pyenv &>/dev/null && eval "\$(pyenv init -)"

# Auto-attach to tmux (skip if already inside tmux or in a CI/non-interactive env)
if [[ -z "\$TMUX" && -z "\${CI:-}" && -t 1 ]]; then
    tmux attach 2>/dev/null || tmux new
fi

# <<< dotfiles customizations <<<
EOF
}

# ── Vim ──────────────────────────────────────────────────────────────────────

setup_vim() {
    if [[ ! -d "$HOME/.vim" ]]; then
        log "Cloning .vim config..."
        git clone https://github.com/AXington/.vim.git "$HOME/.vim"
    fi
    (cd "$HOME/.vim" && git checkout heavenly && git submodule init && git submodule update)
    ln -sf "$HOME/.vim/.vimrc" "$HOME/.vimrc"
    ln -sf "$HOME/.vim/.vimrc.local" "$HOME/.vimrc.local"
}

# ── Default shell ────────────────────────────────────────────────────────────

set_default_shell_zsh() {
    local zsh_path
    zsh_path="$(command -v zsh)"
    if [[ "$SHELL" != "$zsh_path" ]]; then
        log "Setting zsh as default shell..."
        # Add zsh to /etc/shells if not already there
        grep -qxF "$zsh_path" /etc/shells || echo "$zsh_path" | sudo tee -a /etc/shells
        sudo chsh -s "$zsh_path" "$USER"
    fi
}

# Linux: set vim as default editor via update-alternatives
set_default_editor_linux() {
    if command_exists update-alternatives && command_exists vim; then
        sudo update-alternatives --set editor "$(command -v vim)"
    fi
}

# ── Copilot CLI ──────────────────────────────────────────────────────────────

setup_copilot() {
    local script="${SCRIPT_DIR}/scripts/setup_copilot.sh"
    if [[ ! -f "$script" ]]; then
        warn "scripts/setup_copilot.sh not found — skipping Copilot setup."
        return
    fi
    log "Running Copilot CLI setup..."
    bash "$script"
}

# ── Main ─────────────────────────────────────────────────────────────────────

log "Detected OS: $OS"

install_packages

if [[ "$OS" == "macos" ]]; then
    setup_gnubin_macos
fi

install_powerline_fonts

setup_tmux
install_omz
install_omz_plugins
configure_zshrc
setup_vim

if [[ "$OS" == "linux" || "$OS" == "wsl" ]]; then
    set_default_editor_linux
fi

set_default_shell_zsh

if [[ "$SETUP_COPILOT" == "true" ]]; then
    setup_copilot
fi

log "Done! Start a new shell session (or run: exec zsh -l) to apply changes."
