# dotfiles

Personal developer environment bootstrap for macOS, Ubuntu/Debian, Arch Linux,
RHEL/Fedora, and WSL2 on Windows. Gets a new machine to a consistent, productive
state with a single command.

## Quick Start

```bash
git clone git@github.com:AXington/dotfiles.git ~/dotfiles
cd ~/dotfiles
./setup.sh
```

For WSL2 on Windows, include the WSL-specific setup:

```bash
./setup.sh --skip alacritty   # Windows Terminal is your GUI emulator on WSL2; no need to install Alacritty inside WSL
```

## What `setup.sh` Does

Setup is divided into independent sections. By default all sections run on the
current platform (with `gnubin` on macOS only, `wsl` on WSL2 only, and `copilot`
opt-in).

| Section | What it sets up |
|---------|----------------|
| `packages` | Homebrew (macOS), apt, dnf/yum, or pacman packages from the relevant package list |
| `gnubin` | *(macOS only)* Symlinks all GNU tool binaries into `~/.gnubin` so they shadow BSD tools |
| `fonts` | Powerline fonts cloned and installed |
| `tmux` | [gpakosz/.tmux](https://github.com/gpakosz/.tmux) framework, symlinked config, customisations applied to `~/.tmux.conf.local` |
| `zsh` | Oh My Zsh (non-interactive install), zsh-syntax-highlighting plugin, agnoster theme, dotfiles customisation block appended to `~/.zshrc` |
| `vim` | [AXington/.vim](https://github.com/AXington/.vim) on the `Divine` branch, submodules initialised, `.vimrc` symlinked; `.vimrc.local` copied (not symlinked — it's machine-specific and patched by the WSL section) |
| `alacritty` | Alacritty terminal installed, config symlinked from `terminal_configs/alacritty.toml`, man page, terminfo, and zsh completions set up |
| `wsl` | *(WSL2 only)* `wslu`, `win32yank.exe`, `/etc/wsl.conf`, clipboard + true-color patches to `~/.tmux.conf.local` and `~/.vimrc.local` |
| `copilot` | *(opt-in)* GitHub Copilot CLI installed, global instructions bootstrapped to `~/.copilot/copilot-instructions.md` |

### Selective Runs

```bash
./setup.sh --only zsh vim          # run only the listed sections
./setup.sh --skip packages fonts   # skip listed sections, run the rest
./setup.sh --copilot               # standard run + copilot
./setup.sh --all                   # everything, including copilot
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

Uses [AXington/.vim](https://github.com/AXington/.vim) (`Divine` branch) with
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
| `scripts/add_disk.sh [device]` | Partition, format (ext4), mount at `/data`, and add to `/etc/fstab`. Idempotent. Requires root. |
| `scripts/setup_tmux_remote_vm.sh <host> <user>` | SCP `~/.tmux.conf.local` to a remote host and clone the gpakosz/.tmux framework there |
| `scripts/update_eks_kube_config` | Auto-update kubeconfig for all EKS clusters in the current AWS account/region |
| `scripts/strip_proofpoint_url` | Decode Proofpoint URL Defense links (v1/v2/v3) back to the original URL |
| `scripts/clean_python_cache` | Recursively remove `.pyc` files and `__pycache__` directories from the current directory |

## GitHub Copilot CLI

Run setup with `--copilot` to install the CLI and bootstrap
`~/.copilot/copilot-instructions.md` with assistant/user preferences plus
coding and quality rules.

```bash
./setup.sh --only copilot
# then:
copilot /login
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
