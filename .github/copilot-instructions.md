# Copilot Instructions

## Commands

```bash
./setup.sh
./setup.sh --skip alacritty
./setup.sh --copilot
./setup.sh --chatgpt
./setup.sh --shellgpt
./setup.sh --all
./setup.sh --only zsh vim
./setup.sh --only chatgpt
./setup.sh --only shellgpt
./setup.sh --skip packages fonts
./setup.sh --dry-run --only tmux
./setup.sh --verify
./setup.sh --verify --only zsh
powershell -ExecutionPolicy Bypass -File .\scripts\setup_wsl_alacritty.ps1
```

`setup.sh` is the primary entrypoint. The supported section names are `packages`, `gnubin`, `fonts`, `tmux`, `zsh`, `vim`, `alacritty`, `wsl`, `python`, `copilot`, `chatgpt`, and `shellgpt`. Use `./setup.sh --verify --only <section>` as the closest equivalent to a single test for one area.

## High-level architecture

- `setup.sh` contains almost all repository behavior. It detects the current OS/distro, enables or disables sections by default, then runs either `section_*` functions or `verify_*` functions for the selected sections.
- The package lists (`brew_packages.txt`, `apt-packages.txt`, `dnf-packages.txt`, `pacman-packages.txt`) are data inputs for the `packages` section; the script chooses the matching file based on the detected platform.
- Most tracked files are source artifacts consumed by `setup.sh`: `terminal_configs/` for terminal appearance, `vscode/` and `idea_ides/` for editor exports, and `wslconfig.template` plus `terminal_configs/windows-terminal-settings.json` for Windows-side WSL setup.
- `scripts/` contains standalone utilities for adjacent machine-setup tasks (`setup_wsl_alacritty.ps1` and small helper scripts). They are part of the repo's tooling surface, but the main bootstrap flow is still `setup.sh`.

## Key conventions

- Treat the repo as a section-based, idempotent bootstrap system. If a change belongs to an existing concern, extend the matching `section_*` and `verify_*` functions instead of adding a parallel setup path.
- Preserve the platform defaults defined near the top of `setup.sh`: `copilot`, `chatgpt`, and `shellgpt` are opt-in; `gnubin` is macOS-only by default; `wsl` is enabled only when running inside WSL.
- Do not overwrite whole user config files when changing bootstrap behavior. The script intentionally appends guarded blocks to `~/.zshrc`, `~/.tmux.conf.local`, and `~/.vimrc.local`, and the verify mode checks for those markers.
- Keep the "shared vs machine-local" split intact. Shared repo artifacts such as `terminal_configs/alacritty.toml` are symlinked into place, while machine-local files such as `~/.vimrc.local` are copied and then patched.
- Keep terminal appearance changes synchronized across `terminal_configs/alacritty.toml` and `terminal_configs/windows-terminal-settings.json`; the repo treats those as matching Linux/macOS and Windows variants of the same theme/font setup.
- When changing clone/install behavior for external dependencies, the repo prefers GitHub SSH remotes (`git@github.com:...`) for cloned dotfile dependencies like `.vim`, `.tmux`, Powerline fonts, and zsh plugins.
