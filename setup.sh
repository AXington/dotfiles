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
# Sections: packages gnubin fonts tmux zsh vim alacritty wsl python copilot

# -- Helpers -------------------------------------------------------------------

log()  { printf '\n\e[1;34m==> %s\e[0m\n' "$*"; }
ok()   { printf '\e[1;32m    ✓ %s\e[0m\n' "$*"; }
warn() { printf '\e[1;33mWARN: %s\e[0m\n' "$*" >&2; }

command_exists() { command -v "$1" &>/dev/null; }

DRY_RUN=false
CHECK_ONLY=false
_check_failed=false

# In --dry-run mode, print command instead of executing it.
run() {
    if [[ "$DRY_RUN" == "true" ]]; then
        printf '\e[2;37m  [dry] %s\e[0m\n' "$*"
    else
        "$@"
    fi
}

# --verify helpers
pass()       { printf '\e[1;32m  ✓ %s\e[0m\n' "$*"; }
fail()       { printf '\e[1;31m  ✗ %s\e[0m\n' "$*"; _check_failed=true; }
skip_check() { printf '\e[2;37m  – %s\e[0m\n' "$*"; }

# -- OS / distro detection -----------------------------------------------------

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

# -- Argument parsing ----------------------------------------------------------

ALL_SECTIONS=(packages gnubin fonts tmux zsh vim alacritty wsl python copilot)
declare -A RUN
for s in "${ALL_SECTIONS[@]}"; do RUN[$s]=true; done
RUN[copilot]=false                                    # opt-in; use --copilot or --all
WORK_SETUP=false                                      # opt-in; use --work for work-context instructions
UPDATE_INSTRUCTIONS=false                             # opt-in; use --update-instructions to force-rewrite
if [[ "$OS" == "macos" ]]; then RUN[gnubin]=true; else RUN[gnubin]=false; fi  # macOS-only
if [[ "$OS" == "wsl"   ]]; then RUN[wsl]=true;   else RUN[wsl]=false;   fi   # WSL-only

usage() {
    cat << EOF
Usage: $0 [options]

Options:
  --only <s> [s...]   Run only the listed sections
  --skip <s> [s...]   Skip the listed sections, run the rest
  --copilot           Include Copilot CLI setup (off by default)
  --work              Write work-context rules into Copilot instructions (requires --copilot or --only copilot)
  --update-instructions  Force-rewrite Copilot instructions even if file already exists (implies --copilot)
  --all               Run all sections including copilot
  --dry-run           Simulate: print what would be done without making changes
  --verify            Check post-conditions for each section (acts as test suite)
  --help              Show this help

Sections: ${ALL_SECTIONS[*]}
EOF
    exit "${1:-0}"
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
    [[ ${#COLLECTED[@]} -gt 0 ]] || { echo "Flag requires at least one section name." >&2; usage 1; }
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
        --work)    WORK_SETUP=true;                                      shift ;;
        --update-instructions) UPDATE_INSTRUCTIONS=true; RUN[copilot]=true; shift ;;
        --all)     for s in "${ALL_SECTIONS[@]}"; do RUN[$s]=true; done; shift ;;
        --dry-run)  DRY_RUN=true;    shift ;;
        --verify)   CHECK_ONLY=true; shift ;;
        --help|-h) usage ;;
        *)         echo "Unknown option: $1" >&2; usage 1 ;;
    esac
done

should_run() { [[ "${RUN[${1}]:-false}" == "true" ]]; }

# -- 1. Packages ---------------------------------------------------------------

install_packages_macos() {
    if ! command_exists brew; then
        if [[ "$DRY_RUN" == "true" ]]; then
            printf '\e[2;37m  [dry] install Homebrew\e[0m\n'
        else
            log "Installing Homebrew..."
            bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        fi
    fi
    if [[ -x /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    run brew update
    while IFS= read -r pkg || [[ -n "$pkg" ]]; do
        [[ -z "$pkg" || "$pkg" == \#* ]] && continue
        pkg_name="${pkg%% *}"
        if brew list --formula "$pkg_name" &>/dev/null \
           || brew list --cask "$pkg_name" &>/dev/null; then
            ok "Already installed: $pkg_name"
        else
            run brew install "$pkg_name"
        fi
    done < "${SCRIPT_DIR}/brew_packages.txt"
}

install_packages_debian() {
    sudo apt-get update -y
    # shellcheck disable=SC2046
    run sudo apt-get install -y $(grep -v '^\s*#' "${SCRIPT_DIR}/apt-packages.txt" | xargs)
}

install_packages_rhel() {
    local mgr; command_exists dnf && mgr="dnf" || mgr="yum"
    # shellcheck disable=SC2046
    run sudo "$mgr" install -y $(grep -v '^\s*#' "${SCRIPT_DIR}/dnf-packages.txt" | xargs)
}

install_packages_arch() {
    # shellcheck disable=SC2046
    run sudo pacman -S --needed --noconfirm $(grep -v '^\s*#' "${SCRIPT_DIR}/pacman-packages.txt" | xargs)
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
                *) warn "Unsupported distro  - skipping package install" ;;
            esac ;;
        *) warn "Unsupported OS  - skipping package install" ;;
    esac
}

# -- 2. GNU tools (macOS only) -------------------------------------------------

section_gnubin() {
    if [[ "$OS" != "macos" ]]; then
        warn "gnubin is macOS-only, skipping on $OS."
        return
    fi
    log "Symlinking GNU tools into ~/.gnubin..."
    run mkdir -p "$HOME/.gnubin"
    local brew_prefix
    brew_prefix="$(brew --prefix)"
    for dir in "${brew_prefix}/opt"/*/libexec/gnubin; do
        [[ -d "$dir" ]] || continue
        while IFS= read -r -d '' bin; do
            run ln -sf "$bin" "$HOME/.gnubin/$(basename "$bin")"
        done < <(find "$dir" -maxdepth 1 -type f -print0)
    done
    ok "GNU tools linked in ~/.gnubin"
}

# -- 3. Powerline fonts --------------------------------------------------------

section_fonts() {
    log "Installing Powerline fonts..."

    # fc-list is Linux (fontconfig); on macOS check font dirs directly
    local has_fonts=false
    if command_exists fc-list && fc-list 2>/dev/null | grep -qi "powerline\|MesloLGM\|Nerd Font"; then
        has_fonts=true
    elif [[ "$OS" == "macos" ]] && \
         find ~/Library/Fonts /Library/Fonts \
              \( -name "*Powerline*" -o -name "*MesloLGM*" -o -name "*NerdFont*" \) \
              -print 2>/dev/null | grep -q .; then
        has_fonts=true
    fi

    if [[ "$has_fonts" == "true" ]]; then
        ok "Powerline/Nerd fonts already installed."
        return
    fi

    local tmp_dir
    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' EXIT

    run git clone --depth=1 https://github.com/powerline/fonts.git "$tmp_dir/fonts"
    run bash "$tmp_dir/fonts/install.sh"
    rm -rf "$tmp_dir"
    trap - EXIT
}

# -- 4. tmux -------------------------------------------------------------------

section_tmux() {
    log "Setting up tmux..."
    if [[ ! -d "$HOME/.tmux" ]]; then
        run git clone https://github.com/gpakosz/.tmux.git "$HOME/.tmux"
    fi
    # Per gpakosz/.tmux instructions: symlink main conf, copy local conf
    run ln -sf "$HOME/.tmux/.tmux.conf" "$HOME/.tmux.conf"
    if [[ ! -f "$HOME/.tmux.conf.local" ]]; then
        run cp "$HOME/.tmux/.tmux.conf.local" "$HOME/.tmux.conf.local"
    fi

    local conf="$HOME/.tmux.conf.local"

    # Enable Powerline/Nerd Font separators (idempotent: skip if already applied).
    # Two-pass sed: first comment out the plain/empty separator lines that ship in
    # upstream, then uncomment the \uE0Bx lines that upstream ships but leaves
    # commented. Uses GNU sed (-i without backup suffix) which is guaranteed in
    # PATH on macOS because gnu-sed is in brew_packages.txt and gnubin is prepended
    # to PATH before this section runs.
    if ! grep -q "uE0B0" "$conf" || grep -q '^tmux_conf_theme_left_separator_main=""' "$conf"; then
        run sed -i \
            -e 's@^tmux_conf_theme_left_separator_main=""$@#tmux_conf_theme_left_separator_main=""@' \
            -e 's@^tmux_conf_theme_left_separator_sub="|"$@#tmux_conf_theme_left_separator_sub="|"@' \
            -e 's@^tmux_conf_theme_right_separator_main=""$@#tmux_conf_theme_right_separator_main=""@' \
            -e 's@^tmux_conf_theme_right_separator_sub="|"$@#tmux_conf_theme_right_separator_sub="|"@' \
            "$conf"
        run sed -i \
            -e "s@^#\(tmux_conf_theme_left_separator_main='\\\\uE0B0'.*\)@\1@" \
            -e "s@^#\(tmux_conf_theme_left_separator_sub='\\\\uE0B1'.*\)@\1@" \
            -e "s@^#\(tmux_conf_theme_right_separator_main='\\\\uE0B2'.*\)@\1@" \
            -e "s@^#\(tmux_conf_theme_right_separator_sub='\\\\uE0B3'.*\)@\1@" \
            "$conf"
    fi

    # Use C-a as sole prefix; unbind C-b so it passes through to remote/nested
    # tmux sessions which reliably use C-b.
    if ! grep -q "^set -g prefix C-a" "$conf"; then
        if [[ "$DRY_RUN" == "true" ]]; then
            printf '\e[2;37m  [dry] append prefix config to %s\e[0m\n' "$conf"
        else
            cat >> "$conf" << 'TMUX_PREFIX'

# Use C-a as the sole prefix; C-b is freed for remote/nested tmux sessions
set -gu prefix2
unbind C-b
set -g prefix C-a
bind C-a send-prefix
TMUX_PREFIX
        fi
    fi

    # Window navigation bindings + manual focus-events toggle (C-a F)
    # The toggle is a fallback for tools outside the ssh/aws wrappers.
    if ! grep -q "bind a last-window" "$conf"; then
        if [[ "$DRY_RUN" == "true" ]]; then
            printf '\e[2;37m  [dry] append bindings to %s\e[0m\n' "$conf"
        else
            cat >> "$conf" << 'TMUX_BINDINGS'

bind a last-window
bind n next-window
# Toggle focus-events on/off with <prefix>+F (fallback for non-ssh flows)
bind F run-shell "tmux set focus-events $(tmux show -gv focus-events | grep -q on && echo off || echo on) && tmux display-message 'focus-events: #{focus-events}'"
TMUX_BINDINGS
        fi
    fi
    ok "tmux configured."
}

# -- 5. ZSH / Oh My Zsh -------------------------------------------------------

section_zsh() {
    log "Setting up Zsh + Oh My Zsh..."

    if [[ ! -d "$HOME/.oh-my-zsh" ]]; then
        if [[ "$DRY_RUN" != "true" ]]; then
            RUNZSH=no CHSH=no sh -c \
                "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"
        else
            printf '\e[2;37m  [dry] install oh-my-zsh\e[0m\n'
        fi
    else
        ok "Oh My Zsh already installed."
    fi

    local plugin_dir="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting"
    if [[ ! -d "$plugin_dir" ]]; then
        run git clone --depth=1 \
            https://github.com/zsh-users/zsh-syntax-highlighting.git \
            "$plugin_dir"
    fi

    local zshrc="$HOME/.zshrc"

    if [[ -f "$zshrc" ]]; then
        run sed -i.bak \
            -e 's/ZSH_THEME="robbyrussell"/ZSH_THEME="agnoster"/' \
            -e 's/^plugins=(git)$/plugins=(git zsh-syntax-highlighting)/' \
            "$zshrc"
        run rm -f "${zshrc}.bak"

        # Fix bare unguarded 'tmux attach || tmux new' left by older setup runs.
        # Must use Python  - sed chokes on || in the match pattern.
        if command_exists python3; then
            python3 - "$zshrc" << 'PYFIX'
import sys
path = sys.argv[1]
with open(path) as f:
    content = f.read()
bare = 'tmux attach || tmux new\n'
guarded = 'if [[ -z "$TMUX" && -z "${CI:-}" && -t 1 ]]; then tmux attach 2>/dev/null || tmux new; fi\n'
changed = False
if bare in content and guarded not in content:
    content = content.replace(bare, guarded, 1)
    changed = True
    print('  fixed: bare tmux attach line guarded')

# Remove legacy literal-\n uv-virtualenvwrapper line written by older setups.
bad = r'\n# uv-virtualenvwrapper\nsource "$HOME/.local/bin/uv-virtualenvwrapper.sh"'
if bad in content:
    content = content.replace(bad, '')
    changed = True
    print('  fixed: removed literal-\\n uv-virtualenvwrapper line')

if changed:
    with open(path, 'w') as f:
        f.write(content)
PYFIX
        else
            warn "python3 not found  - skipping legacy zshrc cleanup (check for bare 'tmux attach || tmux new' manually)"
        fi
    fi

    if grep -q "# >>> dotfiles customizations <<<" "$zshrc" 2>/dev/null; then
        ok ".zshrc customizations already present."
    else
        if [[ "$DRY_RUN" == "true" ]]; then
            printf '\e[2;37m  [dry] append dotfiles customizations to %s\e[0m\n' "$zshrc"
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
    if command -v nvim &>/dev/null; then
        export EDITOR='nvim'
    else
        export EDITOR='vim'
    fi
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

# uv
[ -f "\$HOME/.local/bin/env" ] && . "\$HOME/.local/bin/env"
export WORKON_HOME="\$HOME/.venvs"
[ -f "\$HOME/.local/bin/uv-virtualenvwrapper.sh" ] && source "\$HOME/.local/bin/uv-virtualenvwrapper.sh"

if [[ -z "\$TMUX" && -z "\${CI:-}" && -t 1 ]]; then
    tmux attach 2>/dev/null || tmux new
fi

# Disable tmux focus-events during SSH to prevent garbage characters (e.g. [[;)
# injected by terminal focus escape sequences when browser windows open for
# SSO/SSM authentication flows.
ssh() {
    if [[ -n "\$TMUX" ]]; then
        tmux set -g focus-events off
        command ssh "\$@"
        local _ret=\$?
        tmux set -g focus-events on
        return \$_ret
    else
        command ssh "\$@"
    fi
}

# Same guard for 'aws sso login' which also opens a browser popup.
# All other aws subcommands pass through unchanged.
aws() {
    if [[ -n "\$TMUX" && "\$1" == "sso" && "\$2" == "login" ]]; then
        tmux set -g focus-events off
        command aws "\$@"
        local _ret=\$?
        tmux set -g focus-events on
        return \$_ret
    else
        command aws "\$@"
    fi
}

# <<< dotfiles customizations <<<
EOF
        fi
    fi

    local zsh_path
    zsh_path="$(command -v zsh || true)"
    if [[ -z "$zsh_path" ]]; then
        warn "zsh not found in PATH  - skipping default shell change"
    elif [[ "$SHELL" != "$zsh_path" ]]; then
        grep -qxF "$zsh_path" /etc/shells || {
            if [[ "$DRY_RUN" == "true" ]]; then
                printf '\e[2;37m  [dry] add %s to /etc/shells\e[0m\n' "$zsh_path"
            else
                echo "$zsh_path" | sudo tee -a /etc/shells
            fi
        }
        run sudo chsh -s "$zsh_path" "$USER" \
            || warn "chsh failed  - run manually: chsh -s $zsh_path"
    fi

    if command_exists update-alternatives && command_exists vim; then
        local vim_path
        vim_path="$(command -v vim)"
        # Register vim in the alternatives system before selecting it.
        # --install is idempotent; without this, --set fails if vim was never registered.
        run sudo update-alternatives --install /usr/bin/editor editor "$vim_path" 50 \
            || warn "update-alternatives --install editor failed (non-fatal)"
        run sudo update-alternatives --set editor "$vim_path" \
            || warn "update-alternatives --set editor failed (non-fatal)"
    fi

    ok "Zsh configured."
}

# -- 6. Vim --------------------------------------------------------------------

section_vim() {
    log "Setting up Vim..."
    if [[ ! -d "$HOME/.vim" ]]; then
        run git clone https://github.com/AXington/.vim.git "$HOME/.vim"
    fi
    if [[ "$DRY_RUN" == "true" ]]; then
        printf '\e[2;37m  [dry] checkout Divine branch and update submodules in ~/.vim\e[0m\n'
    else
        (cd "$HOME/.vim" \
            && { git symbolic-ref --short HEAD 2>/dev/null | grep -qx "Divine" || git checkout Divine; } \
            && git submodule update --init --recursive)
    fi
    run ln -sf "$HOME/.vim/.vimrc" "$HOME/.vimrc"
    # .vimrc.local is machine-specific (WSL patches it at runtime); copy rather than
    # symlink so changes don't propagate back into the .vim git repo.
    if [[ ! -f "$HOME/.vimrc.local" ]]; then
        run cp "$HOME/.vim/.vimrc.local" "$HOME/.vimrc.local" 2>/dev/null || run touch "$HOME/.vimrc.local"
    fi
    ok "Vim configured."
}

# -- 7. Alacritty -------------------------------------------------------------

_alacritty_install_mac() {
    command_exists brew || bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [[ -x /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    run brew install --cask alacritty
    command_exists gzip || run brew install gzip
}

_alacritty_install_debian() {
    sudo apt-get update -y
    # alacritty is available via snap on Ubuntu; fall back to cargo build on Debian
    if command_exists snap; then
        run sudo snap install alacritty --classic
    else
        warn "alacritty not in default apt repos. Install via cargo or your distro's method."
    fi
    command_exists gzip || run sudo apt-get install -y gzip
}

_alacritty_install_rhel() {
    local m; command_exists dnf && m=dnf || m=yum
    # alacritty is not in standard RHEL/Fedora/CentOS repos; use flatpak if available
    if command_exists flatpak; then
        run flatpak install --user -y flathub io.github.alacritty.Alacritty
    elif command_exists cargo; then
        warn "alacritty not in dnf repos. Building from source via cargo (slow)..."
        run sudo "$m" install -y cmake freetype-devel fontconfig-devel libxcb-devel \
            libxkbcommon-devel g++ gzip
        run cargo install alacritty
    else
        warn "alacritty not in dnf repos and flatpak/cargo not available."
        warn "Install flatpak first: sudo $m install -y flatpak && flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo"
        return 1
    fi
    command_exists gzip || run sudo "$m" install -y gzip
}

_alacritty_install_arch() {
    run sudo pacman -S --needed --noconfirm alacritty gzip
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
                *) warn "Unsupported distro  - install alacritty manually." ;;
            esac ;;
        *) warn "Unsupported OS  - install alacritty manually." ;;
    esac

    # Bail out early if alacritty still isn't available (install step warned already)
    if ! command_exists alacritty; then
        warn "alacritty not found after install attempt  - skipping man page, completions, and terminfo"
        return 1
    fi

    # Man page
    local man_path="/usr/local/share/man/man1"
    if [[ ! -f "${man_path}/alacritty.1.gz" ]]; then
        run sudo mkdir -p "$man_path"
        local man_tmp
        man_tmp="$(mktemp)"
        trap 'rm -f "$man_tmp"' RETURN
        run curl -fsSL -o "$man_tmp" \
            https://raw.githubusercontent.com/alacritty/alacritty/master/extra/alacritty.man
        if [[ "$DRY_RUN" == "true" ]]; then
            printf '\e[2;37m  [dry] download and install man page to %s\e[0m\n' "${man_path}/alacritty.1.gz"
        else
            gzip -c "$man_tmp" | sudo tee "${man_path}/alacritty.1.gz" > /dev/null
        fi
        rm -f "$man_tmp"
        trap - RETURN
    fi

    # Zsh completions
    local zsh_fn_dir="${ZDOTDIR:-$HOME}/.zsh_functions"
    if [[ ! -f "${zsh_fn_dir}/_alacritty" ]]; then
        run mkdir -p "$zsh_fn_dir"
        run curl -fsSL -o "${zsh_fn_dir}/_alacritty" \
            https://raw.githubusercontent.com/alacritty/alacritty/master/extra/completions/_alacritty
    fi

    # terminfo  - requires tic (ncurses); present on macOS and most Linux distros
    if command_exists tic; then
        local terminfo_tmp
        terminfo_tmp="$(mktemp)"
        trap 'rm -f "$terminfo_tmp"' RETURN
        run curl -fsSL -o "$terminfo_tmp" \
            https://raw.githubusercontent.com/alacritty/alacritty/master/extra/alacritty.info
        run sudo tic -xe alacritty,alacritty-direct "$terminfo_tmp"
        rm -f "$terminfo_tmp"
        trap - RETURN
    else
        warn "tic not found  - skipping alacritty terminfo install (run: sudo tic -xe alacritty,alacritty-direct alacritty.info)"
    fi

    # Symlink config
    run mkdir -p "$HOME/.config/alacritty"
    run ln -sf "${SCRIPT_DIR}/terminal_configs/alacritty.toml" \
           "$HOME/.config/alacritty/alacritty.toml"

    ok "Alacritty configured."
}

# -- 8. WSL2 ------------------------------------------------------------------

section_wsl() {
    if [[ "$OS" != "wsl" ]]; then
        warn "WSL section is WSL-only, skipping on $OS."
        return
    fi
    log "Configuring WSL2 environment..."

    # wslu provides wslview (open URLs/files in Windows), wslpath, etc.
    if ! command_exists wslview; then
        run sudo apt-get install -y wslu
    else
        ok "wslu already installed."
    fi

    # win32yank.exe  - bidirectional clipboard, handles CRLF automatically.
    # Better than clip.exe (write-only) + powershell paste (slow).
    if ! command_exists win32yank.exe; then
        log "Installing win32yank for clipboard integration..."
        command_exists unzip || run sudo apt-get install -y unzip
        local winy_tmp
        winy_tmp="$(mktemp)"
        run curl -fsSL -o "$winy_tmp" \
            "https://github.com/equalsraf/win32yank/releases/latest/download/win32yank-x64.zip"
        if [[ "$DRY_RUN" == "true" ]]; then
            printf '\e[2;37m  [dry] extract win32yank.exe to /usr/local/bin/win32yank.exe\e[0m\n'
        else
            unzip -p "$winy_tmp" win32yank.exe | sudo tee /usr/local/bin/win32yank.exe > /dev/null
        fi
        run sudo chmod +x /usr/local/bin/win32yank.exe
        rm -f "$winy_tmp"
        ok "win32yank installed at /usr/local/bin/win32yank.exe"
    else
        ok "win32yank already installed."
    fi

    # /etc/wsl.conf  - enable systemd, lock in the default user.
    # Only written if the file doesn't exist; never overwrites existing config.
    if [[ ! -f /etc/wsl.conf ]]; then
        log "Writing /etc/wsl.conf (systemd + interop settings)..."
        if [[ "$DRY_RUN" == "true" ]]; then
            printf '\e[2;37m  [dry] write /etc/wsl.conf\e[0m\n'
        else
            sudo tee /etc/wsl.conf > /dev/null << EOF
[boot]
systemd=true

[interop]
# Keep clip.exe, explorer.exe, etc. available inside WSL
appendWindowsPath=true

[user]
default=${USER}
EOF
        fi
        ok "/etc/wsl.conf written. Run 'wsl --shutdown' from PowerShell to apply."
    else
        ok "/etc/wsl.conf already exists  - not overwriting."
    fi

    # ~/.tmux.conf.local  - true color + win32yank clipboard (idempotent)
    local tmux_conf="$HOME/.tmux.conf.local"
    if [[ -f "$tmux_conf" ]] && ! grep -q "# >>> WSL config <<<" "$tmux_conf"; then
        log "Patching ~/.tmux.conf.local for WSL2 (true color + clipboard)..."
        if [[ "$DRY_RUN" == "true" ]]; then
            printf '\e[2;37m  [dry] append WSL config to %s\e[0m\n' "$tmux_conf"
        else
            cat >> "$tmux_conf" << 'TMUX_WSL'

# >>> WSL config <<<

# True color passthrough  - required for termguicolors in vim to render correctly
# in Windows Terminal. Must match what Windows Terminal reports as TERM.
set -g default-terminal "tmux-256color"
set -ga terminal-overrides ",xterm-256color:Tc"
set -ga terminal-overrides ",*256col*:Tc"

# Clipboard via win32yank  - bidirectional, strips CRLF on paste automatically.
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
        fi
        ok "~/.tmux.conf.local patched."
    else
        ok "tmux WSL config already present."
    fi

    # ~/.vimrc.local  - true color + win32yank clipboard (idempotent)
    local vimrc_local="$HOME/.vimrc.local"
    if ! grep -q "\" >>> WSL config <<<" "$vimrc_local" 2>/dev/null; then
        log "Patching ~/.vimrc.local for WSL2 (true color + clipboard)..."
        if [[ "$DRY_RUN" == "true" ]]; then
            printf '\e[2;37m  [dry] append WSL config to %s\e[0m\n' "$vimrc_local"
        else
            cat >> "$vimrc_local" << 'VIM_WSL'

" >>> WSL config <<<

" True color  - vim is compiled with +termguicolors; Windows Terminal supports it.
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
        fi
        ok "~/.vimrc.local patched."
    else
        ok "vim WSL config already present."
    fi

    # Print the Windows-side steps that can't be scripted from inside WSL
    printf '\n'
    printf '  \e[1;33m┌- Windows-side steps (run these in PowerShell) --------------------------┐\e[0m\n'
    printf '  \e[1;33m│\e[0m                                                                          \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m  1. Install MesloLGM Nerd Font:                                         \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m     Invoke-WebRequest -Uri "https://github.com/ryanoasis/nerd-fonts/    \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m       releases/latest/download/Meslo.zip" -OutFile "$env:TEMP\Meslo.zip"\e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m     Expand-Archive "$env:TEMP\Meslo.zip" "$env:TEMP\Meslo" -Force       \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m     # Then right-click each .ttf -> Install for all users               \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m                                                                          \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m  2. Copy wslconfig.template -> %%USERPROFILE%%\\.wslconfig               \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m     (adjust memory/cpu values for your machine)                         \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m                                                                          \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m  3. Import Windows Terminal color scheme from:                          \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m     terminal_configs/windows-terminal-settings.json                     \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m     (Settings -> Open JSON -> merge "schemes" + "profiles.defaults")      \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m                                                                          \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m  4. Apply /etc/wsl.conf: run  wsl --shutdown  then reopen              \e[1;33m│\e[0m\n'
    printf '  \e[1;33m│\e[0m                                                                          \e[1;33m│\e[0m\n'
    printf '  \e[1;33m└--------------------------------------------------------------------------┘\e[0m\n'
}

# -- 9. Python (uv + uv-virtualenvwrapper + base virtualenv) ------------------

section_python() {
    log "Setting up Python (uv + uv-virtualenvwrapper + base virtualenv)..."

    # Install uv via the official installer (works on all platforms)
    if ! command_exists uv; then
        if [[ "$DRY_RUN" == "true" ]]; then
            printf '\e[2;37m  [dry] install uv via curl | sh\e[0m\n'
        else
            curl -LsSf https://astral.sh/uv/install.sh | sh
            export PATH="$HOME/.local/bin:$PATH"
        fi
    else
        ok "uv already installed: $(uv --version)"
    fi

    # uv-virtualenvwrapper provides a shell script; install as a uv tool
    if [[ ! -f "$HOME/.local/bin/uv-virtualenvwrapper.sh" ]]; then
        run uv tool install uv-virtualenvwrapper
    else
        ok "uv-virtualenvwrapper already installed."
    fi

    # Create the base virtualenv (acts as a system-level scripting environment)
    local venv_home="${WORKON_HOME:-$HOME/.venvs}"
    mkdir -p "$venv_home"
    local base_venv="$venv_home/base"
    if [[ ! -d "$base_venv" ]]; then
        run uv venv "$base_venv"
        ok "Created base virtualenv at $base_venv"
    else
        ok "Base virtualenv already exists at $base_venv"
    fi

    log "Installing packages into base virtualenv..."
    if [[ "$DRY_RUN" == "true" ]]; then
        printf '\e[2;37m  [dry] uv pip install packages into %s\e[0m\n' "$base_venv"
    else
        uv pip install --python "$base_venv/bin/python" \
            `# REPL / debugging` \
            ipython ipdb pexpect \
            `# HTTP / networking` \
            requests httpx paramiko fabric dnspython \
            `# CLI / TUI` \
            click typer rich tqdm tabulate prettytable \
            `# Data / config parsing` \
            pydantic python-dotenv PyYAML jinja2 \
            lxml "beautifulsoup4[lxml]" jsonpath-ng \
            `# PDF / document generation` \
            reportlab fpdf2 weasyprint pypdf \
            `# AWS / cloud` \
            boto3 \
            `# Kubernetes` \
            kubernetes \
            `# System utilities` \
            psutil sh watchdog \
            `# Database clients` \
            psycopg2-binary pymysql \
            `# Security / crypto` \
            cryptography \
            `# Monitoring / observability` \
            prometheus-client
    fi

    ok "Python base virtualenv configured."
}

# -- 10. GitHub Copilot CLI ----------------------------------------------------

section_copilot() {
    log "Setting up GitHub Copilot CLI..."

    if ! command_exists copilot; then
        case "$OS" in
            macos)
                run brew install copilot-cli ;;
            linux|wsl)
                if [[ "$DRY_RUN" == "true" ]]; then
                    printf '\e[2;37m  [dry] install Copilot CLI via curl | bash\e[0m\n'
                else
                    curl -fsSL https://gh.io/copilot-install | bash
                fi ;;
            *)
                warn "Unsupported OS. Install manually: https://gh.io/copilot-install"
                return 1 ;;
        esac
    else
        ok "Copilot CLI already installed."
    fi

    local instructions_dir="$HOME/.copilot"
    local instructions_file="${instructions_dir}/copilot-instructions.md"
    run mkdir -p "$instructions_dir"

    if [[ -f "$instructions_file" ]]; then
        if [[ "$UPDATE_INSTRUCTIONS" != "true" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                printf '\e[2;37m  [dry] instructions already exist -- skipping (use --update-instructions to overwrite)\e[0m\n'
            else
                ok "Global instructions already exist at ${instructions_file} -- skipping (machine-specific, never overwritten)."
            fi
            log "To authenticate, run: copilot /login"
            return 0
        fi
        log "Overwriting existing instructions (--update-instructions)..."
    fi

    log "Writing global Copilot instructions..."
    if [[ "$DRY_RUN" == "true" ]]; then
        printf '\e[2;37m  [dry] write Copilot instructions to %s\e[0m\n' "$instructions_file"
        [[ "$WORK_SETUP" == "true" ]] && printf '\e[2;37m  [dry] append work-context section\e[0m\n'
    else
            cat > "$instructions_file" << 'INSTRUCTIONS'
# Global Copilot Instructions

## User Preference

- The user's name is Alice (Ali). Address her as Ali. Use she/her pronouns.

## Repository Instructions

Repository-level Copilot instructions (.github/copilot-instructions.md) provide
context specific to that repository. They must enhance and work within the rules
and intentions defined here. They may add repo-specific conventions, directory
structure, key commands, and tooling notes. They must not contradict or weaken
any rule defined in these global instructions.

## Coding Rules

- Follow the naming conventions of the language and repository in use.
- Correctness is the highest priority. Clarity comes second. Conciseness is last.
  Never sacrifice correctness or clarity for brevity.
- Reduce complexity wherever possible. Simple, obvious solutions are preferred
  over clever ones.
- Only comment code that genuinely needs clarification. Do not over-comment.
- Never hardcode secrets, credentials, IPs, URLs, or environment-specific values.
  Use variables, config files, or secret stores appropriate to the stack.
- Write idempotent code wherever the stack supports it. Operations must be safe
  to re-run without side effects.
- Always handle failure cases explicitly. Fail loudly with a clear error rather
  than silently continuing in a broken state.
- Do not modify unrelated code. When fixing a specific issue, stay in scope.
  Scope creep in automated changes is a reliability risk.
- All source files must use ASCII-safe encoding. Do not introduce any non-ASCII
  characters (Unicode codepoints above U+007F) anywhere in code, comments,
  configs, or scripts. This includes curly quotes, smart apostrophes,
  non-breaking spaces, ellipses, em-dashes, and any other Unicode typography.
  Use plain ASCII equivalents at all times. Non-ASCII characters cause silent,
  hard-to-diagnose failures in shells, parsers, and cross-platform tooling.
- In prose and plain language output, avoid em-dashes entirely. Do not substitute
  them with hyphens or double hyphens used as punctuation. Rewrite the sentence
  instead. This applies especially to output written in Ali's voice.

## Testing and Linting

Linting is always required. Run it before declaring any task complete.

| Stack     | Linting                           | Testing                                                              |
|-----------|-----------------------------------|----------------------------------------------------------------------|
| Python    | flake8 (PEP8 enforced)            | pytest; new behavior requires tests; bug fixes need regression tests |
| Shell     | shellcheck                        | Manual dry-run; test in non-prod first                               |
| YAML/JSON | Schema validation where available | N/A                                                                  |

If the repository has an existing test suite, run it before and after any code
change to establish a baseline and confirm nothing regressed. Do not declare a
task done without verifying the expected outcome.

If a repo has no linting setup, note it as tech debt but do not block the current
task on it.

## Quality Rules

- Safety and security come first, above all else including task completion. An
  answer that introduces a vulnerability or causes an unrecoverable change is worse
  than no answer at all.
- Never guess. Only provide answers that can be verified. Be ready to cite sources
  when asked.
- For anything version-sensitive (API syntax, tool behavior, config options, CLI
  flags): verify against current documentation before answering. Training data goes
  stale; docs do not.
- Do not report success until the outcome is confirmed by checking the exit code,
  API response, or resource state.
- State assumptions explicitly when they significantly affect the outcome. Wrong
  silent assumptions cause incidents.
- When a request is ambiguous or has multiple valid approaches with meaningfully
  different tradeoffs, ask before proceeding. State the options, give a
  recommendation, and let Ali decide.
- When proposing changes that could be difficult or impossible to reverse, surface
  the risks, blast radius, and rollback options before proceeding.

## Safety and Security

Safety and security are non-negotiable and take priority over completing the task.

- Never assume context is safe, correct, or complete. Verify explicitly.
- Before any mutating action, verify and state the active environment.
- Request only the permissions, access, and scope needed for the task.
- When two approaches achieve the same goal, prefer the one that can be undone:
  soft deletes, backups before overwrites, snapshots before resizes.
- Before any change to shared infrastructure, identify how to reverse it. If
  reversal is not possible, say so before acting.
- Before any destructive operation, stop and get explicit confirmation from Ali.
  State the blast radius first: what breaks, what is lost, what cannot be undone.
- If credentials are missing or authentication fails, stop and report clearly.
  Do not fall back to a different credential source without telling Ali.
- Do not disable, weaken, or work around security controls for convenience. If a
  control is blocking legitimate work, surface it and find an approved path.

## Code Review and Commits

- Before pushing any code to a remote repository, perform a code review. Check
  for correctness, security issues, unintended side effects, and scope creep.
  Present the review summary and wait for Ali's confirmation before pushing.
- Use Conventional Commits: <type>: <description>. Types: feat, fix, docs,
  refactor, test, chore. Keep the subject line under 72 characters. Use the
  commit body to explain what changed and why.

## Updating These Instructions

Before writing any new or modified instruction to any Copilot instructions file
(including ~/.copilot/copilot-instructions.md and any repo-level
.github/copilot-instructions.md):

1. Draft the proposed text and show it to Ali for review.
2. Use precise, actionable language.
3. Wait for explicit approval before writing to the file.
INSTRUCTIONS

            if [[ "$WORK_SETUP" == "true" ]]; then
                cat >> "$instructions_file" << 'WORK_INSTRUCTIONS'

## Work Context

Ali is a DevOps/Site Reliability engineer. Apply that lens to all responses.
Prefer operational clarity, reliability, and maintainability.

## Work: Additional Testing and Linting

| Stack     | Linting                        | Testing                                            |
|-----------|--------------------------------|----------------------------------------------------|
| Ansible   | ansible-lint                   | --check --diff dry-run; molecule where it exists   |
| Terraform | tflint, terraform validate     | terraform plan; terratest where it exists          |

## Work: Infrastructure Safety

Before any mutating action on cloud or shared infrastructure, verify and state
the active environment explicitly:
- AWS: account ID, region, and profile
- Kubernetes: active cluster context and namespace
- Terraform: workspace and backend

Treat prod, staging, and dev as distinct trust zones with separate credentials.
An action safe in dev is not automatically safe in prod.

Before executing any operation that deletes data, modifies production, or is
irreversible: stop and get explicit confirmation from Ali. State the blast radius
first. Never run terraform destroy, kubectl delete, DROP TABLE, or
aws s3 rm --recursive without a clear, affirmative go-ahead for that specific
action.
WORK_INSTRUCTIONS
                ok "Work-context section appended."
            fi
            ok "Global instructions written to ${instructions_file}."
    fi

    log "To authenticate, run: copilot /login"
}

# -- Verification (--verify mode) ---------------------------------------------

verify_packages() {
    case "$OS" in
        macos)
            command_exists brew \
                && pass "Homebrew installed" \
                || fail "Homebrew not installed" ;;
        linux|wsl)
            case "$(detect_linux_distro)" in
                debian) command_exists apt-get && pass "apt-get available" || fail "apt-get not available" ;;
                rhel*)  { command_exists dnf || command_exists yum; } && pass "dnf/yum available" || fail "no package manager found" ;;
                arch)   command_exists pacman && pass "pacman available" || fail "pacman not available" ;;
                *)      skip_check "unknown distro  - cannot verify packages" ;;
            esac ;;
        *) skip_check "unknown OS  - cannot verify packages" ;;
    esac
}

verify_gnubin() {
    if [[ "$OS" != "macos" ]]; then skip_check "gnubin is macOS-only"; return; fi
    [[ -d "$HOME/.gnubin" ]]        && pass "~/.gnubin directory exists"       || fail "~/.gnubin directory missing"
    [[ -L "$HOME/.gnubin/sed" ]]    && pass "GNU sed linked in ~/.gnubin"      || fail "GNU sed not linked in ~/.gnubin"
    [[ -L "$HOME/.gnubin/find" ]]   && pass "GNU find linked in ~/.gnubin"     || fail "GNU find not linked in ~/.gnubin"
}

verify_fonts() {
    if command_exists fc-list && fc-list 2>/dev/null | grep -qi "powerline\|MesloLGM\|Nerd Font"; then
        pass "Powerline/Nerd fonts installed"
    elif [[ "$OS" == "macos" ]] && \
         find ~/Library/Fonts /Library/Fonts \
              \( -name "*Powerline*" -o -name "*MesloLGM*" -o -name "*NerdFont*" \) \
              -print 2>/dev/null | grep -q .; then
        pass "Powerline/Nerd fonts installed"
    else
        fail "No Powerline/Nerd fonts found"
    fi
}

verify_tmux() {
    [[ -d "$HOME/.tmux" ]]             && pass "~/.tmux cloned"                    || fail "~/.tmux not cloned"
    [[ -L "$HOME/.tmux.conf" ]]        && pass "~/.tmux.conf is a symlink"         || fail "~/.tmux.conf is not a symlink"
    [[ -f "$HOME/.tmux.conf.local" ]]  && pass "~/.tmux.conf.local exists"         || fail "~/.tmux.conf.local missing"
    local conf="$HOME/.tmux.conf.local"
    grep -q "uE0B0"              "$conf" 2>/dev/null && pass "Powerline separators configured"  || fail "Powerline separators not configured"
    grep -q "^set -g prefix C-a" "$conf" 2>/dev/null && pass "C-a prefix configured"           || fail "C-a prefix not configured"
    grep -q "^unbind C-b"        "$conf" 2>/dev/null && pass "C-b unbound"                     || fail "C-b not unbound"
    grep -q "^bind a last-window" "$conf" 2>/dev/null && pass "bind a last-window set"         || fail "bind a last-window not set"
    grep -q "^bind n next-window" "$conf" 2>/dev/null && pass "bind n next-window set"         || fail "bind n next-window not set"
    grep -q "^bind F "           "$conf" 2>/dev/null && pass "bind F focus-events toggle set"  || fail "bind F focus-events toggle not set"
}

verify_zsh() {
    [[ -d "$HOME/.oh-my-zsh" ]]         && pass "Oh My Zsh installed"                          || fail "Oh My Zsh not installed"
    local zshrc="$HOME/.zshrc"
    [[ -f "$zshrc" ]]                   && pass "~/.zshrc exists"                              || { fail "~/.zshrc missing"; return; }
    grep -q 'ZSH_THEME="agnoster"' "$zshrc"               && pass "agnoster theme set"         || fail "agnoster theme not set"
    grep -q "zsh-syntax-highlighting"   "$zshrc"           && pass "zsh-syntax-highlighting present" || fail "zsh-syntax-highlighting missing"
    grep -q "# >>> dotfiles customizations <<<" "$zshrc"   && pass "customization block present"     || fail "customization block missing"
    grep -q "^ssh()"    "$zshrc"  && pass "ssh() focus-events wrapper present"   || fail "ssh() focus-events wrapper missing"
    grep -q "^aws()"    "$zshrc"  && pass "aws() focus-events wrapper present"   || fail "aws() focus-events wrapper missing"
    grep -q "WORKON_HOME" "$zshrc" && pass "WORKON_HOME set in .zshrc"           || fail "WORKON_HOME not set in .zshrc"
    grep -q 'uv-virtualenvwrapper.sh' "$zshrc" && pass "uv-virtualenvwrapper sourced in .zshrc" || fail "uv-virtualenvwrapper not sourced in .zshrc"
}

verify_vim() {
    [[ -d "$HOME/.vim" ]]               && pass "~/.vim cloned"                                || fail "~/.vim not cloned"
    [[ -L "$HOME/.vimrc" ]]             && pass "~/.vimrc symlinked"                           || fail "~/.vimrc not symlinked"
    [[ -f "$HOME/.vimrc.local" ]]       && pass "~/.vimrc.local exists"                        || fail "~/.vimrc.local missing"
    local branch
    branch="$(cd "$HOME/.vim" 2>/dev/null && git symbolic-ref --short HEAD 2>/dev/null || true)"
    [[ "$branch" == "Divine" ]]         && pass "~/.vim on Divine branch"                      || fail "~/.vim not on Divine branch (got: ${branch:-none})"
    local uninit
    uninit="$(cd "$HOME/.vim" 2>/dev/null && { git submodule status 2>/dev/null | { grep '^-' || true; } | wc -l | tr -d ' '; } || echo 0)"
    [[ "$uninit" -eq 0 ]]               && pass "All vim submodules initialized"               || fail "$uninit vim submodule(s) not initialized"
}

verify_alacritty() {
    command_exists alacritty           && pass "alacritty installed"                           || fail "alacritty not installed"
    local cfg="$HOME/.config/alacritty/alacritty.toml"
    [[ -L "$cfg" ]]                    && pass "alacritty.toml symlinked"                      || fail "alacritty.toml not symlinked"
    local target; target="$(readlink "$cfg" 2>/dev/null || true)"
    [[ "$target" == *"terminal_configs/alacritty.toml" ]] \
                                        && pass "alacritty.toml points to dotfiles"            || fail "alacritty.toml symlink target unexpected: $target"
}

verify_wsl() {
    if [[ "$OS" != "wsl" ]]; then skip_check "WSL section not applicable on $OS"; return; fi
    command_exists wslview              && pass "wslu installed"                                || fail "wslu not installed"
    command_exists win32yank.exe        && pass "win32yank installed"                          || fail "win32yank not installed"
    [[ -f /etc/wsl.conf ]]             && pass "/etc/wsl.conf present"                        || fail "/etc/wsl.conf missing"
    grep -q "# >>> WSL config <<<" "$HOME/.tmux.conf.local" 2>/dev/null \
                                        && pass "tmux WSL config present"                      || fail "tmux WSL config not in ~/.tmux.conf.local"
    grep -q "\" >>> WSL config <<<" "$HOME/.vimrc.local" 2>/dev/null \
                                        && pass "vim WSL config present"                       || fail "vim WSL config not in ~/.vimrc.local"
}

verify_python() {
    local uv_bin="${HOME}/.local/bin/uv"
    { command_exists uv || [[ -x "$uv_bin" ]]; } \
                                        && pass "uv installed"                                 || fail "uv not installed"
    [[ -f "$HOME/.local/bin/uv-virtualenvwrapper.sh" ]] \
                                        && pass "uv-virtualenvwrapper.sh present"              || fail "uv-virtualenvwrapper.sh missing"
    local venv="${WORKON_HOME:-$HOME/.venvs}/base"
    [[ -d "$venv" ]]                   && pass "base virtualenv exists"                        || { fail "base virtualenv missing ($venv)"; return; }
    [[ -x "$venv/bin/python" ]]        && pass "base venv python executable"                  || fail "base venv python not executable"
    local uv_bin="${HOME}/.local/bin/uv"
    local pkg
    for pkg in requests boto3 kubernetes rich ipython weasyprint cryptography prometheus_client paramiko; do
        "$uv_bin" pip show "$pkg" --python "$venv/bin/python" &>/dev/null \
                                        && pass "package: $pkg"                                || fail "package missing: $pkg"
    done
}

verify_copilot() {
    command_exists copilot              && pass "Copilot CLI installed"                        || fail "Copilot CLI not installed"
    [[ -f "$HOME/.copilot/copilot-instructions.md" ]] \
                                        && pass "Copilot instructions written"                 || fail "Copilot instructions missing"
}

# -- Main ----------------------------------------------------------------------

log "Detected OS: ${OS}"
if [[ "$DRY_RUN" == "true" ]]; then
    log "Mode: DRY RUN  - no changes will be made"
elif [[ "$CHECK_ONLY" == "true" ]]; then
    log "Mode: VERIFY  - checking post-conditions only"
fi
log "Sections:$(for s in "${ALL_SECTIONS[@]}"; do [[ "${RUN[$s]}" == "true" ]] && printf ' %s' "$s"; done)"

should_run packages && { [[ "$CHECK_ONLY" == "true" ]] && verify_packages || section_packages; }
should_run gnubin   && { [[ "$CHECK_ONLY" == "true" ]] && verify_gnubin   || section_gnubin;   }

# On macOS, prepend all Homebrew GNU tool paths into PATH for this session so
# that gnu-sed (and friends) are used in subsequent sections rather than BSD tools.
# Skip in verify mode (read-only checks don't need GNU sed).
if [[ "$OS" == "macos" ]] && [[ "$CHECK_ONLY" != "true" ]] && command_exists brew; then
    _brew_prefix="$(brew --prefix)"
    for _gnu_dir in "${_brew_prefix}/opt"/*/libexec/gnubin; do
        [[ -d "$_gnu_dir" ]] && PATH="$_gnu_dir:$PATH"
    done
    export PATH
    unset _brew_prefix _gnu_dir
fi

should_run fonts     && { [[ "$CHECK_ONLY" == "true" ]] && verify_fonts     || section_fonts;     }
should_run tmux      && { [[ "$CHECK_ONLY" == "true" ]] && verify_tmux      || section_tmux;      }
should_run zsh       && { [[ "$CHECK_ONLY" == "true" ]] && verify_zsh       || section_zsh;       }
should_run vim       && { [[ "$CHECK_ONLY" == "true" ]] && verify_vim       || section_vim;       }
should_run alacritty && { [[ "$CHECK_ONLY" == "true" ]] && verify_alacritty || section_alacritty; }
should_run wsl       && { [[ "$CHECK_ONLY" == "true" ]] && verify_wsl       || section_wsl;       }
should_run python    && { [[ "$CHECK_ONLY" == "true" ]] && verify_python    || section_python;    }
should_run copilot   && { [[ "$CHECK_ONLY" == "true" ]] && verify_copilot   || section_copilot;   }

if [[ "$CHECK_ONLY" == "true" ]]; then
    if [[ "$_check_failed" == "true" ]]; then
        log "Verify complete  - some checks FAILED"
        exit 1
    else
        log "Verify complete  - all checks passed"
    fi
else
    log "Done! Start a new shell (or run: exec zsh -l) to apply changes."
fi
