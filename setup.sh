#!/usr/bin/env bash
set -euo pipefail

# Bootstrap a new machine with all personal dotfiles and tooling.
#
# Usage:
#   ./setup.sh                               # run all sections (copilot excluded by default)
#   ./setup.sh --all                         # run everything, including copilot
#   ./setup.sh --only zsh vim alacritty      # run only the listed sections
#   ./setup.sh --skip packages fonts         # skip the listed sections, run the rest
#   ./setup.sh --copilot                     # include copilot in the standard run
#
# Sections: packages gnubin fonts tmux zsh vim alacritty wsl copilot

# ── Helpers ───────────────────────────────────────────────────────────────────

log()  { printf '\n\e[1;34m==> %s\e[0m\n' "$*"; }
ok()   { printf '\e[1;32m    ✓ %s\e[0m\n' "$*"; }
warn() { printf '\e[1;33mWARN: %s\e[0m\n' "$*" >&2; }

command_exists() { command -v "$1" &>/dev/null; }

# ── OS / distro detection ─────────────────────────────────────────────────────

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
    if command_exists apt-get;   then echo "debian"
    elif command_exists dnf;     then echo "rhel"
    elif command_exists yum;     then echo "rhel-old"
    elif command_exists pacman;  then echo "arch"
    else                              echo "unknown"
    fi
}

# Detect OS and script location before argument parsing so defaults can be
# set correctly (e.g. gnubin defaults to enabled on macOS).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(detect_os)"

# ── Argument parsing ──────────────────────────────────────────────────────────

ALL_SECTIONS=(packages gnubin fonts tmux zsh vim alacritty wsl copilot)
declare -A RUN
for s in "${ALL_SECTIONS[@]}"; do RUN[$s]=true; done
RUN[copilot]=false                                    # opt-in; use --copilot or --all
[[ "$OS" == "macos" ]] && RUN[gnubin]=true || RUN[gnubin]=false  # macOS-only
[[ "$OS" == "wsl"   ]] && RUN[wsl]=true    || RUN[wsl]=false     # WSL-only

usage() {
    cat << EOF
Usage: $0 [options]

Options:
  --only <s> [s...]   Run only the listed sections
  --skip <s> [s...]   Skip the listed sections, run the rest
  --copilot           Include Copilot CLI setup (off by default)
  --all               Run all sections including copilot
  --help              Show this help

Sections: ${ALL_SECTIONS[*]}
EOF
    exit 0
}

# Collect a space-separated list of section names after a flag, stopping at
# the next flag or end of args.  Sets COLLECTED (array) and SHIFT_BY (count).
collect_list() {
    COLLECTED=()
    SHIFT_BY=0
    while [[ $# -gt 0 && "$1" != --* ]]; do
        COLLECTED+=("$1")
        SHIFT_BY=$(( SHIFT_BY + 1 ))   # avoid (( )) with set -e when value is 0
        shift
    done
    [[ ${#COLLECTED[@]} -gt 0 ]] || { echo "Flag requires at least one section name." >&2; usage; }
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --only)
            shift
            collect_list "$@"
            for s in "${ALL_SECTIONS[@]}"; do RUN[$s]=false; done
            for s in "${COLLECTED[@]}";    do RUN[$s]=true;  done
            shift "$SHIFT_BY"
            ;;
        --skip)
            shift
            collect_list "$@"
            for s in "${COLLECTED[@]}"; do RUN[$s]=false; done
            shift "$SHIFT_BY"
            ;;
        --copilot) RUN[copilot]=true;                                    shift ;;
        --all)     for s in "${ALL_SECTIONS[@]}"; do RUN[$s]=true; done; shift ;;
        --help|-h) usage ;;
        *)         echo "Unknown option: $1" >&2; usage ;;
    esac
done

should_run() { [[ "${RUN[${1}]:-false}" == "true" ]]; }

# ── 1. Packages ───────────────────────────────────────────────────────────────

install_packages_macos() {
    if ! command_exists brew; then
        log "Installing Homebrew..."
        bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    if [[ -x /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    brew update
    while IFS= read -r pkg || [[ -n "$pkg" ]]; do
        [[ -z "$pkg" || "$pkg" == \#* ]] && continue
        pkg_name="${pkg%% *}"
        if brew list --formula "$pkg_name" &>/dev/null 2>&1 \
           || brew list --cask "$pkg_name" &>/dev/null 2>&1; then
            ok "Already installed: $pkg_name"
        else
            brew install "$pkg_name"
        fi
    done < "${SCRIPT_DIR}/brew_packages.txt"
}

install_packages_debian() {
    sudo apt-get update -y
    # shellcheck disable=SC2046
    sudo apt-get install -y $(grep -v '^\s*#' "${SCRIPT_DIR}/apt-packages.txt" | xargs)
}

install_packages_rhel() {
    local mgr; command_exists dnf && mgr="dnf" || mgr="yum"
    # shellcheck disable=SC2046
    sudo "$mgr" install -y $(grep -v '^\s*#' "${SCRIPT_DIR}/dnf-packages.txt" | xargs)
}

install_packages_arch() {
    # shellcheck disable=SC2046
    sudo pacman -S --needed --noconfirm $(grep -v '^\s*#' "${SCRIPT_DIR}/pacman-packages.txt" | xargs)
}

section_packages() {
    log "Installing packages..."
    case "$OS" in
        macos) install_packages_macos ;;
        linux|wsl)
            case "$(detect_linux_distro)" in
                debian)  install_packages_debian ;;
                rhel*)   install_packages_rhel ;;
                arch)    install_packages_arch ;;
                *) warn "Unsupported distro — skipping package install" ;;
            esac ;;
        *) warn "Unsupported OS — skipping package install" ;;
    esac
}

# ── 2. GNU tools (macOS only) ─────────────────────────────────────────────────

section_gnubin() {
    if [[ "$OS" != "macos" ]]; then
        warn "gnubin is macOS-only, skipping on $OS."
        return
    fi
    log "Symlinking GNU tools into ~/.gnubin..."
    mkdir -p "$HOME/.gnubin"
    local brew_prefix
    brew_prefix="$(brew --prefix)"
    for dir in "${brew_prefix}/opt"/*/libexec/gnubin; do
        [[ -d "$dir" ]] || continue
        while IFS= read -r -d '' bin; do
            ln -sf "$bin" "$HOME/.gnubin/$(basename "$bin")"
        done < <(find "$dir" -maxdepth 1 -type f -print0)
    done
    ok "GNU tools linked in ~/.gnubin"
}

# ── 3. Powerline fonts ────────────────────────────────────────────────────────

section_fonts() {
    log "Installing Powerline fonts..."

    # fc-list is Linux (fontconfig); on macOS check font dirs directly
    local has_fonts=false
    if command_exists fc-list && fc-list 2>/dev/null | grep -qi "powerline\|MesloLGM\|Nerd Font"; then
        has_fonts=true
    elif [[ "$OS" == "macos" ]] && find ~/Library/Fonts /Library/Fonts -name "*Powerline*" -o \
         -name "*MesloLGM*" -o -name "*NerdFont*" 2>/dev/null | grep -q .; then
        has_fonts=true
    fi

    if [[ "$has_fonts" == "true" ]]; then
        ok "Powerline/Nerd fonts already installed."
        return
    fi

    local tmp_dir
    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' EXIT

    git clone --depth=1 https://github.com/powerline/fonts.git "$tmp_dir/fonts"
    bash "$tmp_dir/fonts/install.sh"
    rm -rf "$tmp_dir"
    trap - EXIT
}

# ── 4. tmux ───────────────────────────────────────────────────────────────────

section_tmux() {
    log "Setting up tmux..."
    if [[ ! -d "$HOME/.tmux" ]]; then
        git clone https://github.com/gpakosz/.tmux.git "$HOME/.tmux"
    fi
    # Per gpakosz/.tmux instructions: symlink main conf, copy local conf
    ln -sf "$HOME/.tmux/.tmux.conf" "$HOME/.tmux.conf"
    if [[ ! -f "$HOME/.tmux.conf.local" ]]; then
        cp "$HOME/.tmux/.tmux.conf.local" "$HOME/.tmux.conf.local"
    fi

    local conf="$HOME/.tmux.conf.local"

    # Enable Powerline/Nerd Font separators (idempotent: skip if already applied).
    # Two-pass sed: first comment out the plain/empty separator lines that ship in
    # upstream, then uncomment the \uE0Bx lines that upstream ships but leaves
    # commented. Uses GNU sed (-i without backup suffix) which is guaranteed in
    # PATH on macOS because gnu-sed is in brew_packages.txt and gnubin is prepended
    # to PATH before this section runs.
    if ! grep -q "uE0B0" "$conf" || grep -q '^tmux_conf_theme_left_separator_main=""' "$conf"; then
        sed -i \
            -e 's@^tmux_conf_theme_left_separator_main=""$@#tmux_conf_theme_left_separator_main=""@' \
            -e 's@^tmux_conf_theme_left_separator_sub="|"$@#tmux_conf_theme_left_separator_sub="|"@' \
            -e 's@^tmux_conf_theme_right_separator_main=""$@#tmux_conf_theme_right_separator_main=""@' \
            -e 's@^tmux_conf_theme_right_separator_sub="|"$@#tmux_conf_theme_right_separator_sub="|"@' \
            "$conf"
        sed -i \
            -e "s@^#\(tmux_conf_theme_left_separator_main='\\\\uE0B0'.*\)@\1@" \
            -e "s@^#\(tmux_conf_theme_left_separator_sub='\\\\uE0B1'.*\)@\1@" \
            -e "s@^#\(tmux_conf_theme_right_separator_main='\\\\uE0B2'.*\)@\1@" \
            -e "s@^#\(tmux_conf_theme_right_separator_sub='\\\\uE0B3'.*\)@\1@" \
            "$conf"
    fi

    # Use C-a as sole prefix; unbind C-b so it passes through to remote/nested
    # tmux sessions which reliably use C-b.
    if ! grep -q "set -g prefix C-a" "$conf"; then
        cat >> "$conf" << 'TMUX_PREFIX'

# Use C-a as the sole prefix; C-b is freed for remote/nested tmux sessions
set -gu prefix2
unbind C-b
set -g prefix C-a
bind C-a send-prefix
TMUX_PREFIX
    fi

    # Window navigation bindings
    if ! grep -q "bind a last-window" "$conf"; then
        printf '\nbind a last-window\nbind n next-window\n' >> "$conf"
    fi
    ok "tmux configured."
}

# ── 5. ZSH / Oh My Zsh ───────────────────────────────────────────────────────

section_zsh() {
    log "Setting up Zsh + Oh My Zsh..."

    if [[ ! -d "$HOME/.oh-my-zsh" ]]; then
        RUNZSH=no CHSH=no sh -c \
            "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"
    else
        ok "Oh My Zsh already installed."
    fi

    local plugin_dir="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting"
    if [[ ! -d "$plugin_dir" ]]; then
        git clone --depth=1 \
            https://github.com/zsh-users/zsh-syntax-highlighting.git \
            "$plugin_dir"
    fi

    local zshrc="$HOME/.zshrc"

    if [[ -f "$zshrc" ]]; then
        sed -i.bak 's/ZSH_THEME="robbyrussell"/ZSH_THEME="agnoster"/' "$zshrc"
        sed -i.bak 's/^plugins=(git)$/plugins=(git zsh-syntax-highlighting)/' "$zshrc"
        rm -f "${zshrc}.bak"
    fi

    if grep -q "# >>> dotfiles customizations <<<" "$zshrc" 2>/dev/null; then
        ok ".zshrc customizations already present."
    else
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

if [[ -n "\$SSH_CONNECTION" ]]; then
    export EDITOR='vim'
else
    command -v nvim &>/dev/null && export EDITOR='nvim' || export EDITOR='vim'
fi

export SSH_KEY_PATH="\$HOME/.ssh/id_ed25519"
prompt_context() {}
export GPG_TTY=\$(tty)
bindkey '^R' history-incremental-search-backward
fpath+=\${ZDOTDIR:-~}/.zsh_functions

command -v kubectl &>/dev/null && source <(kubectl completion zsh)
command -v helm    &>/dev/null && source <(helm completion zsh)

[ -f "\$HOME/.fzf.zsh" ] && source "\$HOME/.fzf.zsh"

export PYENV_ROOT="\$HOME/.pyenv"
[[ -d "\$PYENV_ROOT/bin" ]] && export PATH="\$PYENV_ROOT/bin:\$PATH"
command -v pyenv &>/dev/null && eval "\$(pyenv init -)"

if [[ -z "\$TMUX" && -z "\${CI:-}" && -t 1 ]]; then
    tmux attach 2>/dev/null || tmux new
fi

# <<< dotfiles customizations <<<
EOF
    fi

    local zsh_path
    zsh_path="$(command -v zsh)"
    if [[ "$SHELL" != "$zsh_path" ]]; then
        grep -qxF "$zsh_path" /etc/shells || echo "$zsh_path" | sudo tee -a /etc/shells
        sudo chsh -s "$zsh_path" "$USER"
    fi

    if command_exists update-alternatives && command_exists vim; then
        sudo update-alternatives --set editor "$(command -v vim)"
    fi

    ok "Zsh configured."
}

# ── 6. Vim ────────────────────────────────────────────────────────────────────

section_vim() {
    log "Setting up Vim..."
    if [[ ! -d "$HOME/.vim" ]]; then
        git clone https://github.com/AXington/.vim.git "$HOME/.vim"
    fi
    (cd "$HOME/.vim" && git checkout Divine && git submodule init && git submodule update)
    ln -sf "$HOME/.vim/.vimrc" "$HOME/.vimrc"
    # .vimrc.local is machine-specific (WSL patches it at runtime); copy rather than
    # symlink so changes don't propagate back into the .vim git repo.
    if [[ ! -f "$HOME/.vimrc.local" ]]; then
        cp "$HOME/.vim/.vimrc.local" "$HOME/.vimrc.local" 2>/dev/null || touch "$HOME/.vimrc.local"
    fi
    ok "Vim configured."
}

# ── 7. Alacritty ─────────────────────────────────────────────────────────────

_alacritty_install_mac() {
    command_exists brew || bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    [[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
    brew install --cask alacritty
    command_exists gzip || brew install gzip
}

_alacritty_install_debian() {
    sudo apt-get update -y
    # alacritty is available via snap on Ubuntu; fall back to cargo build on Debian
    if command_exists snap; then
        sudo snap install alacritty --classic
    else
        warn "alacritty not in default apt repos. Install via cargo or your distro's method."
    fi
    command_exists gzip || sudo apt-get install -y gzip
}

_alacritty_install_rhel() {
    local m; command_exists dnf && m=dnf || m=yum
    # alacritty is not in standard RHEL/Fedora/CentOS repos; use flatpak if available
    if command_exists flatpak; then
        flatpak install -y flathub io.github.alacritty.Alacritty
    elif command_exists cargo; then
        warn "alacritty not in dnf repos. Building from source via cargo (slow)..."
        sudo "$m" install -y cmake freetype-devel fontconfig-devel libxcb-devel \
            libxkbcommon-devel g++ gzip
        cargo install alacritty
    else
        warn "alacritty not in dnf repos and flatpak/cargo not available."
        warn "Install flatpak first: sudo $m install -y flatpak && flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo"
        return 1
    fi
    command_exists gzip || sudo "$m" install -y gzip
}

_alacritty_install_arch() {
    sudo pacman -S --needed --noconfirm alacritty gzip
}

section_alacritty() {
    log "Setting up Alacritty..."

    case "$OS" in
        macos)    _alacritty_install_mac ;;
        linux|wsl)
            case "$(detect_linux_distro)" in
                debian)  _alacritty_install_debian ;;
                rhel*)   _alacritty_install_rhel ;;
                arch)    _alacritty_install_arch ;;
                *) warn "Unsupported distro — install alacritty manually." ;;
            esac ;;
        *) warn "Unsupported OS — install alacritty manually." ;;
    esac

    # Man page
    local man_path="/usr/local/share/man/man1"
    if [[ ! -f "${man_path}/alacritty.1.gz" ]]; then
        sudo mkdir -p "$man_path"
        local man_tmp
        man_tmp="$(mktemp)"
        curl -fsSL -o "$man_tmp" \
            https://raw.githubusercontent.com/alacritty/alacritty/master/extra/alacritty.man
        gzip -c "$man_tmp" | sudo tee "${man_path}/alacritty.1.gz" > /dev/null
        rm -f "$man_tmp"
    fi

    # Zsh completions
    local zsh_fn_dir="${ZDOTDIR:-$HOME}/.zsh_functions"
    if [[ ! -f "${zsh_fn_dir}/_alacritty" ]]; then
        mkdir -p "$zsh_fn_dir"
        curl -fsSL -o "${zsh_fn_dir}/_alacritty" \
            https://raw.githubusercontent.com/alacritty/alacritty/master/extra/completions/_alacritty
    fi

    # terminfo
    local terminfo_tmp
    terminfo_tmp="$(mktemp)"
    curl -fsSL -o "$terminfo_tmp" \
        https://raw.githubusercontent.com/alacritty/alacritty/master/extra/alacritty.info
    sudo tic -xe alacritty,alacritty-direct "$terminfo_tmp"
    rm -f "$terminfo_tmp"

    # Symlink config
    mkdir -p "$HOME/.config/alacritty"
    ln -sf "${SCRIPT_DIR}/terminal_configs/alacritty.toml" \
           "$HOME/.config/alacritty/alacritty.toml"

    ok "Alacritty configured."
}

# ── 8. WSL2 ──────────────────────────────────────────────────────────────────

section_wsl() {
    if [[ "$OS" != "wsl" ]]; then
        warn "WSL section is WSL-only, skipping on $OS."
        return
    fi
    log "Configuring WSL2 environment..."

    # wslu provides wslview (open URLs/files in Windows), wslpath, etc.
    if ! command_exists wslview; then
        sudo apt-get install -y wslu
    else
        ok "wslu already installed."
    fi

    # win32yank.exe — bidirectional clipboard, handles CRLF automatically.
    # Better than clip.exe (write-only) + powershell paste (slow).
    if ! command_exists win32yank.exe; then
        log "Installing win32yank for clipboard integration..."
        local winy_tmp
        winy_tmp="$(mktemp)"
        curl -fsSL -o "$winy_tmp" \
            "https://github.com/equalsraf/win32yank/releases/latest/download/win32yank-x64.zip"
        unzip -p "$winy_tmp" win32yank.exe | sudo tee /usr/local/bin/win32yank.exe > /dev/null
        sudo chmod +x /usr/local/bin/win32yank.exe
        rm -f "$winy_tmp"
        ok "win32yank installed at /usr/local/bin/win32yank.exe"
    else
        ok "win32yank already installed."
    fi

    # /etc/wsl.conf — enable systemd, lock in the default user.
    # Only written if the file doesn't exist; never overwrites existing config.
    if [[ ! -f /etc/wsl.conf ]]; then
        log "Writing /etc/wsl.conf (systemd + interop settings)..."
        sudo tee /etc/wsl.conf > /dev/null << EOF
[boot]
systemd=true

[interop]
# Keep clip.exe, explorer.exe, etc. available inside WSL
appendWindowsPath=true

[user]
default=${USER}
EOF
        ok "/etc/wsl.conf written. Run 'wsl --shutdown' from PowerShell to apply."
    else
        ok "/etc/wsl.conf already exists — not overwriting."
    fi

    # ~/.tmux.conf.local — true color + win32yank clipboard (idempotent)
    local tmux_conf="$HOME/.tmux.conf.local"
    if [[ -f "$tmux_conf" ]] && ! grep -q "# >>> WSL config <<<" "$tmux_conf"; then
        log "Patching ~/.tmux.conf.local for WSL2 (true color + clipboard)..."
        cat >> "$tmux_conf" << 'TMUX_WSL'

# >>> WSL config <<<

# True color passthrough — required for termguicolors in vim to render correctly
# in Windows Terminal. Must match what Windows Terminal reports as TERM.
set -g default-terminal "tmux-256color"
set -ga terminal-overrides ",xterm-256color:Tc"
set -ga terminal-overrides ",*256col*:Tc"

# Clipboard via win32yank — bidirectional, strips CRLF on paste automatically.
# Overrides gpakosz framework's xsel/xclip path which requires X11.
if -b 'command -v win32yank.exe > /dev/null 2>&1' {
    set -s copy-command 'win32yank.exe -i --crlf'
    bind -T copy-mode-vi y     send -X copy-pipe-and-cancel 'win32yank.exe -i --crlf'
    bind -T copy-mode-vi Enter send -X copy-pipe-and-cancel 'win32yank.exe -i --crlf'
    bind -T copy-mode    y     send -X copy-pipe-and-cancel 'win32yank.exe -i --crlf'
    bind -T copy-mode    Enter send -X copy-pipe-and-cancel 'win32yank.exe -i --crlf'
}

# <<< WSL config <<<
TMUX_WSL
        ok "~/.tmux.conf.local patched."
    else
        ok "tmux WSL config already present."
    fi

    # ~/.vimrc.local — true color + win32yank clipboard (idempotent)
    local vimrc_local="$HOME/.vimrc.local"
    if ! grep -q "\" >>> WSL config <<<" "$vimrc_local" 2>/dev/null; then
        log "Patching ~/.vimrc.local for WSL2 (true color + clipboard)..."
        cat >> "$vimrc_local" << 'VIM_WSL'

" >>> WSL config <<<

" True color — vim is compiled with +termguicolors; Windows Terminal supports it.
" t_8f/t_8b sequences are required when not running a true GUI vim.
if has('termguicolors')
  let &t_8f = "\<Esc>[38;2;%lu;%lu;%lum"
  let &t_8b = "\<Esc>[48;2;%lu;%lu;%lum"
  set termguicolors
endif

" Bidirectional clipboard via win32yank.
" --crlf on copy: Windows apps expect CRLF.
" --lf on paste:  strip CRLF so pasting into vim doesn't leave ^M on every line.
if executable('win32yank.exe')
  let g:clipboard = {
    \ 'name': 'win32yank',
    \ 'copy':  { '+': 'win32yank.exe -i --crlf', '*': 'win32yank.exe -i --crlf' },
    \ 'paste': { '+': 'win32yank.exe -o --lf',   '*': 'win32yank.exe -o --lf'   },
    \ 'cache_enabled': 0,
    \ }
  set clipboard=unnamedplus
endif

" <<< WSL config <<<
VIM_WSL
        ok "~/.vimrc.local patched."
    else
        ok "vim WSL config already present."
    fi

    # Print the Windows-side steps that can't be scripted from inside WSL
    printf '\n'
    printf '  \e[1;33m┌─ Windows-side steps (run these in PowerShell) ──────────────────────────┐\e[0m\n'
    printf '  \e[1;33m│\e[0m                                                                          \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m  1. Install MesloLGM Nerd Font:                                         \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m     Invoke-WebRequest -Uri "https://github.com/ryanoasis/nerd-fonts/    \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m       releases/latest/download/Meslo.zip" -OutFile "$env:TEMP\Meslo.zip"\e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m     Expand-Archive "$env:TEMP\Meslo.zip" "$env:TEMP\Meslo" -Force       \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m     # Then right-click each .ttf → Install for all users               \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m                                                                          \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m  2. Copy wslconfig.template → %%USERPROFILE%%\\.wslconfig               \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m     (adjust memory/cpu values for your machine)                         \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m                                                                          \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m  3. Import Windows Terminal color scheme from:                          \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m     terminal_configs/windows-terminal-settings.json                     \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m     (Settings → Open JSON → merge "schemes" + "profiles.defaults")      \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m                                                                          \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m  4. Apply /etc/wsl.conf: run  wsl --shutdown  then reopen              \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m                                                                          \e[1;33m│\e[0m\n'
    printf '  \e[1;33m└──────────────────────────────────────────────────────────────────────────┘\e[0m\n'
}

# ── 9. GitHub Copilot CLI ─────────────────────────────────────────────────────

section_copilot() {
    log "Setting up GitHub Copilot CLI..."

    if ! command_exists copilot; then
        case "$OS" in
            macos)
                brew install copilot-cli ;;
            linux|wsl)
                curl -fsSL https://gh.io/copilot-install | bash ;;
            *)
                warn "Unsupported OS. Install manually: https://gh.io/copilot-install"
                return 1 ;;
        esac
    else
        ok "Copilot CLI already installed."
    fi

    local instructions_dir="$HOME/.copilot"
    local instructions_file="${instructions_dir}/copilot-instructions.md"
    mkdir -p "$instructions_dir"

    if [[ -f "$instructions_file" ]]; then
        ok "Global instructions already exist at ${instructions_file}."
    else
        log "Writing global Copilot instructions..."
        cat > "$instructions_file" << 'INSTRUCTIONS'
# Global Copilot Instructions

## Assistant Preference

- The assistant prefers to be referred to as `Sam`.
- The assistant prefers they/them pronouns.

## User Preference

- The user's name is Alice (Ali). Use she/her pronouns when referring to her.

## Context

The user is a DevOps/Site Reliability engineer. Apply that lens to all responses — prefer operational clarity, reliability, and maintainability.

## General Expertise

You are an expert in:
- Cloud infrastructure (AWS, Azure, GCP) — with deepest focus on AWS
- AWS services: EC2, EKS, IAM, Identity Center/SSO, CloudFormation, S3, RDS, Route53, VPC, and related
- AWS CLI and SDK tooling
- Kubernetes and container orchestration
- Linux/Unix systems and command-line tooling
- Infrastructure as Code principles and practices
- DevOps and SRE practices: observability, reliability, CI/CD, incident response
- Git and version control workflows
- Shell scripting (bash/zsh)
- Python scripting for automation and tooling

## Coding Rules

- Follow the naming conventions of the language and repository in use.
- Prefer shorter, concise, efficient code by default.
- Only comment code that genuinely needs clarification — do not over-comment.

## Quality Rules

- Prioritize accuracy over speed.
- Never guess. Only provide answers that can be verified.
- Base answers on the latest stable version of the technology being discussed.
INSTRUCTIONS
        ok "Global instructions written to ${instructions_file}."
    fi

    log "To authenticate, run: copilot /login"
}

# ── Main ──────────────────────────────────────────────────────────────────────

log "Detected OS: ${OS}"
log "Sections to run:$(for s in "${ALL_SECTIONS[@]}"; do [[ "${RUN[$s]}" == "true" ]] && printf ' %s' "$s"; done)"

should_run packages  && section_packages
should_run gnubin    && section_gnubin

# On macOS, prepend all Homebrew GNU tool paths into PATH for this session so
# that gnu-sed (and friends) are used in subsequent sections rather than BSD tools.
if [[ "$OS" == "macos" ]] && command_exists brew; then
    _brew_prefix="$(brew --prefix)"
    for _gnu_dir in "${_brew_prefix}/opt"/*/libexec/gnubin; do
        [[ -d "$_gnu_dir" ]] && PATH="$_gnu_dir:$PATH"
    done
    export PATH
    unset _brew_prefix _gnu_dir
fi

should_run fonts     && section_fonts
should_run tmux      && section_tmux
should_run zsh       && section_zsh
should_run vim       && section_vim
should_run alacritty && section_alacritty
should_run wsl       && section_wsl
should_run copilot   && section_copilot

log "Done! Start a new shell session (or run: exec zsh -l) to apply changes."
