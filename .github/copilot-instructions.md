# dotfiles — Copilot Instructions

## Purpose
Personal developer environment bootstrap repo. Manages shell config, terminal emulator settings, tmux, Vim, editor configs, and package lists across macOS, Ubuntu/Debian, and Arch Linux.

## Languages & Tools
- Bash (primary): `setup.sh`, `setup_alacritty.sh`, `scripts/*.sh`
- Python: `scripts/strip_proofpoint_url`, `scripts/clean_python_cache`
- Config formats: TOML/YAML (Alacritty), JSON (VSCode), XML (JetBrains color scheme)

## Directory Structure
```
dotfiles/
├── setup.sh                   # Main OS-aware bootstrap entry point
├── setup_alacritty.sh         # Alacritty terminal setup + symlinks
├── scripts/                   # Utility and remote provisioning scripts
├── terminal_configs/          # alacritty.yml, alacritty.toml, iTerm2 colors
├── idea_ides/                 # PyCharm/IntelliJ exported settings + Superdark theme
├── vscode/                    # vscode_settings.json
├── brew_packages.txt          # macOS packages (29)
├── apt-packages.txt           # Ubuntu/Debian packages (20)
└── pacman-packages.txt        # Arch Linux packages (17)
```

## Key Commands
```bash
./setup.sh                                        # Bootstrap current OS (detects macOS/Ubuntu/Arch)
./setup_alacritty.sh                              # Set up Alacritty + symlink config
./scripts/setup_tmux_remote_vm.sh <HOST> <USER>  # Deploy tmux config to remote VM
./scripts/update_eks_kube_config                  # Auto-configure all EKS cluster kubeconfigs
./scripts/strip_proofpoint_url                    # Decode Proofpoint URL defense links
./scripts/add_disk.sh [DEVICE]                    # Format disk and update /etc/fstab
```

## What `setup.sh` Manages
- Homebrew + packages (macOS) or apt/pacman packages (Linux)
- `~/.gnubin` PATH prepend for GNU tools on macOS
- gpakosz/.tmux framework + `.tmux.conf.local` customizations:
  - Prefix: `C-a`, mouse enabled, OS clipboard copy on
  - Separators disabled; bindings: `a`=last-window, `n`=next-window
- AXington/.vim cloned on `heavenly` branch with submodule init

## Conventions
- Script naming: `snake_case` (no extensions for executables, `.sh` for sourced/utility scripts)
- Shebang: `#!/usr/bin/env bash`
- OS detection: `$OSTYPE` (`darwin*`, `linux-gnu`, `linux-musl`)
- Idempotent design: scripts use `-f` force flag on symlinks, `hash` to check tool availability
- All scripts in `scripts/` are utility/maintenance, not sourced — they run standalone
- Font: Meslo LG M for Powerline across all terminal tools

## No CI/CD or Tests
No automated testing. Validation is implicit through OS detection and package manager checks.
