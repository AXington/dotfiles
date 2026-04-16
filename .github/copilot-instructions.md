# dotfiles -- Copilot Instructions

## Purpose
Personal developer environment bootstrap repo. Manages shell config, terminal
emulator settings, tmux, Vim, editor configs, and package lists across macOS,
Ubuntu/Debian, Arch Linux, RHEL/Fedora, and WSL2 (Windows 11).

These instructions add context specific to this repository. They work within
and enhance the rules defined in the global Copilot instructions
(~/.copilot/copilot-instructions.md). They do not override any global rule.

## Languages & Tools
- Bash (primary): `setup.sh`, `scripts/*.sh`
- PowerShell: `scripts/setup_wsl_alacritty.ps1` (Windows WSL2 bootstrap; Windows only)
- Python: `scripts/strip_proofpoint_url`, `scripts/clean_python_cache`
- Config formats: TOML (Alacritty), JSON (VSCode), XML (JetBrains color scheme)

## Directory Structure
```
dotfiles/
|-- setup.sh                          # Main OS-aware bootstrap entry point
|-- scripts/
|   |-- setup_wsl_alacritty.ps1      # Windows 11 WSL2 + Alacritty bootstrap
|   |-- setup_tmux_remote_vm.sh      # Deploy tmux config to remote VM
|   |-- update_eks_kube_config        # Auto-configure all EKS cluster kubeconfigs
|   |-- strip_proofpoint_url          # Decode Proofpoint URL defense links
|   |-- clean_python_cache            # Remove __pycache__ directories
|   `-- add_disk.sh                   # Format disk and update /etc/fstab
|-- terminal_configs/                 # alacritty.toml, iTerm2 colors, Windows Terminal
|-- idea_ides/                        # PyCharm/IntelliJ exported settings + Superdark theme
|-- vscode/                           # vscode_settings.json
|-- brew_packages.txt                 # macOS packages
|-- apt-packages.txt                  # Ubuntu/Debian packages
|-- dnf-packages.txt                  # RHEL/Fedora packages
|-- pacman-packages.txt               # Arch Linux packages
`-- wslconfig.template                # WSL2 memory/CPU tuning template
```

## Key Commands
```bash
./setup.sh                                        # Bootstrap current OS
./setup.sh --only copilot                         # Install and configure Copilot CLI only
./setup.sh --only copilot --work                  # Include work-context Copilot instructions
./scripts/setup_tmux_remote_vm.sh <HOST> <USER>  # Deploy tmux config to remote VM
./scripts/update_eks_kube_config                  # Auto-configure all EKS cluster kubeconfigs
./scripts/strip_proofpoint_url                    # Decode Proofpoint URL defense links
./scripts/add_disk.sh [DEVICE]                    # Format disk and update /etc/fstab
```

## What `setup.sh` Manages
- Homebrew + packages (macOS), apt/dpkg (Ubuntu/Debian), dnf/yum (RHEL/Fedora),
  or pacman (Arch)
- `~/.gnubin` PATH prepend for GNU tools on macOS
- gpakosz/.tmux framework + `.tmux.conf.local` customizations:
  - Prefix: `C-a`, mouse enabled, OS clipboard copy on
  - Separators disabled; bindings: `a`=last-window, `n`=next-window
- AXington/.vim cloned on `heavenly` branch with submodule init
- `.zshrc` customization block (appended between markers, never overwrites existing content)
- GitHub Copilot CLI install + global instructions bootstrap (skips if file already present)

## Conventions
- Script naming: `snake_case` (no extension for executables, `.sh` for utility scripts)
- Shebang: `#!/usr/bin/env bash`
- OS detection: `$OSTYPE` (`darwin*`, `linux-gnu`, `linux-musl`) with WSL detection
  via `/proc/version`
- All scripts are idempotent. Operations are safe to re-run without side effects.
- All scripts handle errors explicitly and fail loudly.
- All scripts in `scripts/` run standalone. None are sourced.
- Font: Meslo LG M for Powerline across all terminal emulators.
- PowerShell scripts must be saved with a UTF-8 BOM (EF BB BF). All other files
  use UTF-8 without BOM.

## Platform Notes
- macOS: Homebrew, iTerm2 colors, `~/.gnubin` symlinks. No Windows or Linux-only tooling.
- Linux (all distros): apt, dnf, or pacman depending on distro. No Homebrew or Windows tooling.
- WSL2: Linux setup applies. `wslconfig.template` governs VM resource limits.
- Windows: only `scripts/setup_wsl_alacritty.ps1` is Windows-native. Do not apply
  Windows-specific configs or tooling to macOS or Linux contexts.

## No CI/CD or Tests
No automated testing. Validation is through OS detection and package manager checks.
Run `./setup.sh --verify` to check machine state.
