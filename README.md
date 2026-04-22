# dotfiles

Personal developer environment bootstrap for macOS, Ubuntu/Debian, Arch Linux,
RHEL/Fedora, and WSL2 on Windows. Gets a new machine to a consistent, productive
state with a single command.

## Quick Start

```bash
git clone git@github.com:alistardust/dotfiles.git ~/dotfiles
cd ~/dotfiles
./setup.sh
```

For WSL2 on Windows, include the WSL-specific setup:

```bash
./setup.sh --skip alacritty   # Windows Terminal is your GUI emulator on WSL2; no need to install Alacritty inside WSL
```

## What `setup.sh` Does

Setup is divided into independent sections. By default all sections run on the
current platform. Exceptions: `gnubin` runs on macOS only, `wsl` on WSL2 only,
and `copilot`, `chatgpt`, plus `shellgpt` are opt-in.

| Section | What it sets up |
|---------|----------------|
| `packages` | Homebrew (macOS), apt, dnf/yum, or pacman packages from the relevant package list |
| `gnubin` | *(macOS only)* Symlinks all GNU tool binaries into `~/.gnubin` so they shadow BSD tools |
| `fonts` | Powerline fonts cloned and installed *(skipped on Arch — fonts managed via pacman)* |
| `tmux` | [gpakosz/.tmux](https://github.com/gpakosz/.tmux) framework, symlinked config, customisations applied to `~/.tmux.conf.local` |
| `zsh` | Oh My Zsh (non-interactive install), zsh-syntax-highlighting plugin, agnoster theme, dotfiles customisation block appended to `~/.zshrc` |
| `vim` | [alistardust/.vim](https://github.com/alistardust/.vim) on the `Divine` branch, submodules initialised, `.vimrc` symlinked; `.vimrc.local` copied (not symlinked — it's machine-specific and patched by the WSL section) |
| `alacritty` | Alacritty terminal installed, config symlinked from `terminal_configs/alacritty.toml`, man page, terminfo, and zsh completions set up |
| `wsl` | *(WSL2 only)* `wslu`, `win32yank.exe`, `/etc/wsl.conf`, clipboard + true-color patches to `~/.tmux.conf.local` and `~/.vimrc.local` |
| `python` | `uv`, `uv-virtualenvwrapper`, a base virtualenv at `~/.venvs/base`, and a preinstalled package set for local scripting/research |
| `keyd` | *(Linux only)* Installs and enables [keyd](https://github.com/rvaiya/keyd); deploys `configs/keyd.conf` which remaps Meta+C/V/X/Z/A to Ctrl equivalents system-wide for Mac-like muscle memory |
| `copilot` | *(opt-in)* GitHub Copilot CLI installed, global instructions bootstrapped to `~/.copilot/copilot-instructions.md` |
| `chatgpt` | *(opt-in)* Official OpenAI Codex CLI installed via npm (`codex` command, ChatGPT/API key sign-in on first run) |
| `shellgpt` | *(opt-in)* Unofficial open-source ShellGPT installed via `uv tool install shell-gpt` (`sgpt` command, requires `OPENAI_API_KEY`) |

### Selective Runs

```bash
./setup.sh --only zsh vim          # run only the listed sections
./setup.sh --skip packages fonts   # skip listed sections, run the rest
./setup.sh --copilot               # standard run + copilot
./setup.sh --chatgpt               # standard run + OpenAI Codex CLI
./setup.sh --shellgpt              # standard run + ShellGPT
./setup.sh --all                   # everything, including copilot + chatgpt + shellgpt
./setup.sh --help
```

## Shell & Terminal

- **Shell:** Zsh + [Oh My Zsh](https://ohmyz.sh), `agnoster` theme
- **Prompt:** user@host context suppressed (`prompt_context(){}`) — clean prompt
- **Plugin:** `zsh-syntax-highlighting`
- **Terminal (macOS/Linux):** [Alacritty](https://github.com/alacritty/alacritty)
- **Terminal (WSL2/Windows):** Windows Terminal with matching color scheme
- **Font:** MesloLGM Nerd Font, 13.5pt bold
- **Color scheme:** Black background, white foreground — defined in `terminal_configs/`

### `.zshrc` Customisations

Setup appends a guarded block (`# >>> dotfiles customizations <<<`) rather than
replacing the whole file, so Oh My Zsh updates on new machines don't break anything.

Included: PATH setup (Homebrew + `~/.gnubin` on macOS), editor selection (nvim
locally / vim over SSH), pyenv, fzf, kubectl/helm completions (loaded only when
the tool is present), GPG TTY, tmux auto-attach.

## tmux

Uses [gpakosz/.tmux](https://github.com/gpakosz/.tmux) as the base.

Customisations applied to `~/.tmux.conf.local` by setup:

| Setting | Value |
|---------|-------|
| Prefix | `C-a` (sole prefix; `C-b` unbound so it passes through to remote/nested tmux) |
| Mouse | Enabled |
| OS clipboard copy | Enabled |
| Separators | Powerline Nerd Font (`\uE0B0`–`\uE0B3`) |
| `a` | Last window |
| `n` | Next window |

On WSL2, copy-mode bindings are additionally wired to `win32yank.exe` and true-color
passthrough is configured.

To deploy the tmux config to a remote VM:

```bash
./scripts/setup_tmux_remote_vm.sh <host> <user>
```

## Vim

Uses [alistardust/.vim](https://github.com/alistardust/.vim) (`Divine` branch) with
[Pathogen](https://github.com/tpope/vim-pathogen) for plugin management.

Key plugins: `airline`, `fzf` + `fzf.vim`, `fugitive`, `nerdtree`, `nerdtree-git`,
`tagbar`, `undotree`, `signify`, `vimux`, `vim-tmux-focus-events`, `easymotion`,
`supertab`, `surround`, `unimpaired`, and more.

On WSL2, `~/.vimrc.local` is patched with `termguicolors` and a `win32yank`
clipboard provider.

## Package Lists

| File | Platform |
|------|----------|
| `brew_packages.txt` | macOS (Homebrew) |
| `apt-packages.txt` | Ubuntu / Debian |
| `dnf-packages.txt` | RHEL / Fedora / CentOS |
| `pacman-packages.txt` | Arch Linux |

## Terminal Configs

```
terminal_configs/
├── alacritty.toml              # Alacritty config (symlinked to ~/.config/alacritty/)
├── colors_and_fonts.txt        # Reference ANSI color values and font name
├── iterm2.itermcolors          # iTerm2 color preset (matching scheme)
├── my_presets.itermcolors      # Additional iTerm2 presets
└── windows-terminal-settings.json  # Windows Terminal color scheme + font (WSL2)
```

### WSL2 / Windows Terminal

Import `terminal_configs/windows-terminal-settings.json` into Windows Terminal:
**Settings → Open JSON file**, then merge the `"schemes"` entry and `"profiles.defaults"` block.

Copy `wslconfig.template` to `%USERPROFILE%\.wslconfig` on Windows and adjust
`memory`/`processors` for your machine, then run `wsl --shutdown` from PowerShell.

## Editor Configs

- **VSCode:** `vscode/vscode_settings.json`
- **JetBrains:** `idea_ides/` — exported settings for IntelliJ and PyCharm, plus the
  Superdark color scheme (`Superdark.icls`)

## Utility Scripts

| Script | Description |
|--------|-------------|
| `scripts/setup_tmux_remote_vm.sh <host> <user>` | SCP `~/.tmux.conf.local` to a remote host and clone the gpakosz/.tmux framework there |
| `scripts/clean_python_cache` | Recursively remove `.pyc` files and `__pycache__` directories from the current directory |

## KDE Plasma (Linux)

### Mac-like Key Shortcuts

The `keyd` section installs a system-level key remapping daemon. `configs/keyd.conf`
maps Meta (Super/Windows key, the Linux equivalent of ⌘) as follows:

| Shortcut | Action |
|----------|--------|
| `Meta+C` | Copy |
| `Meta+V` | Paste |
| `Meta+X` | Cut |
| `Meta+Z` | Undo |
| `Meta+A` | Select all |

All other Meta+key combinations (Meta alone, Meta+Space, Meta+Tab, Meta+D, etc.)
pass through unchanged so KDE system shortcuts continue to work.

> **After first install:** log out and back in for the `keyd` group membership
> to take effect.

### Virtual Desktops / Spaces

[MACsimize6](https://github.com/Ubiquitine/MACsimize6) is installed as a KWin
script. Fullscreening or maximising a window automatically moves it to its own
dedicated virtual desktop (macOS Spaces behaviour); restoring the window returns
it to the main desktop and removes the temporary one.

Desktop switching shortcuts:

| Shortcut | Action |
|----------|--------|
| `Meta+Ctrl+←` / `→` | Switch to adjacent desktop |
| `Ctrl+F1` / `F2` / `F3` / `F4` | Jump directly to desktop 1–4 |
| `Meta+W` | Overview — all windows and desktops (like Mission Control) |
| `Meta+F9` / `Ctrl+F9` | Exposé — windows on current desktop |
| `Meta+F10` / `Ctrl+F10` | Exposé — windows across all desktops |

### Per-Monitor Desktops (Plasma 6.7+)

KDE Plasma 6.7 (scheduled June 2026) adds **independent virtual desktops per
monitor** — each display can show a different desktop simultaneously, matching
macOS Spaces behaviour across multiple screens. The feature is Wayland-only
(no X11 support). Once Plasma 6.7 is available via pacman on CachyOS, enable it
in **System Settings → Display & Monitor → Virtual Desktops → Per-screen virtual desktops**.

## GitHub Copilot CLI

Run setup with `--copilot` to install the CLI and bootstrap
`~/.copilot/copilot-instructions.md` with assistant/user preferences plus
coding and quality rules.

```bash
./setup.sh --only copilot
# then:
copilot /login
```

## OpenAI Codex CLI

Run setup with `--chatgpt` to install the official OpenAI Codex CLI, which signs
in with a ChatGPT account or API key on first launch.

```bash
./setup.sh --only chatgpt
# then:
codex
```

## ShellGPT

Run setup with `--shellgpt` to install the open-source ShellGPT CLI for more
general chat-style terminal use.

```bash
./setup.sh --only shellgpt
export OPENAI_API_KEY="your-api-key"
sgpt "summarize this article"
```

## Supported Platforms

| Platform | Package manager | Notes |
|----------|----------------|-------|
| macOS (Apple Silicon + Intel) | Homebrew | Full support, GNU tools symlinked |
| Ubuntu / Debian | apt | Full support |
| RHEL / Fedora / CentOS | dnf / yum | Full support |
| Arch Linux | pacman | Full support |
| WSL2 (Ubuntu 24.04 recommended) | apt | Full support + WSL section |
| WSL1 | — | Not supported — use WSL2 |
