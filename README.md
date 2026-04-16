# dotfiles

Personal developer environment bootstrap for macOS, Ubuntu/Debian, Arch Linux,
RHEL/Fedora, and WSL2 on Windows. Gets a new machine to a consistent, productive
state with a single command.

> All scripts are linted with `shellcheck`. Python scripts are linted with `flake8`
> (PEP8 enforced). Both tools are installed as part of the standard package setup.

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
./setup.sh --dry-run               # simulate: print what would happen, make no changes
./setup.sh --verify                # check post-conditions for each section
./setup.sh --help
```

## Shell & Terminal

- **Shell:** Zsh + [Oh My Zsh](https://ohmyz.sh), `agnoster` theme
- **Prompt:** user@host context suppressed (`prompt_context(){}`) — clean prompt
- **Plugin:** `zsh-syntax-highlighting`
- **Terminal (macOS/Linux):** [Alacritty](https://github.com/alacritty/alacritty)
- **Terminal (WSL2/Windows):** Windows Terminal with matching color scheme
- **Font:** Meslo LG M for Powerline, 13.5pt bold
- **Color scheme:** Black background, white foreground — defined in `terminal_configs/`

### `.zshrc` Customisations

Setup appends a guarded block (`# >>> dotfiles customizations <<<`) rather than
replacing the whole file, so Oh My Zsh updates on new machines don't break anything.

Included: PATH setup (Homebrew + `~/.gnubin` on macOS), editor selection (nvim
locally / vim over SSH), uv + uv-virtualenvwrapper, fzf, kubectl/helm completions
(loaded only when the tool is present), GPG TTY, tmux auto-attach.

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

Install the CLI and bootstrap `~/.copilot/copilot-instructions.md` with coding
standards, quality rules, safety rules, and commit conventions.

The instructions file is written once and never overwritten -- it is
machine-specific. Use `--update-instructions` to force a rewrite.

```bash
# Personal machine
./setup.sh --only copilot

# Work machine (adds DevOps/SRE and infra safety sections)
./setup.sh --only copilot --work

# Force rewrite existing instructions (e.g. after pulling updated defaults)
./setup.sh --only copilot --update-instructions

# Dry run to preview what would be written
./setup.sh --only copilot --dry-run

# Authenticate after install
copilot /login
```

### Sample: `~/.copilot/copilot-instructions.md` (personal)

```markdown
# Global Copilot Instructions

## User Preference

- The user's name is Alice (Ali). Address her as Ali. Use she/her pronouns.

## Repository Instructions

Repository-level Copilot instructions (.github/copilot-instructions.md) provide
context specific to that repository. They must enhance and work within the rules
and intentions defined here. They may not contradict or weaken any global rule.

## Coding Rules

- Follow the naming conventions of the language and repository in use.
- Correctness is the highest priority. Clarity comes second. Conciseness is last.
- Reduce complexity wherever possible. Simple, obvious solutions are preferred.
- Only comment code that genuinely needs clarification. Do not over-comment.
- Never hardcode secrets, credentials, IPs, URLs, or environment-specific values.
- Write idempotent code wherever the stack supports it.
- Always handle failure cases explicitly. Fail loudly, never silently.
- Do not modify unrelated code. Stay in scope.
- All source files must use ASCII-safe encoding. No characters above U+007F.
- In prose and plain language output, avoid em-dashes entirely. Rewrite instead.

## Testing and Linting

| Stack     | Linting                           | Testing                                      |
|-----------|-----------------------------------|----------------------------------------------|
| Python    | flake8 (PEP8 enforced)            | pytest; new behavior and bug fixes need tests |
| Shell     | shellcheck                        | Manual dry-run; test in non-prod first        |
| YAML/JSON | Schema validation where available | N/A                                           |

If the repository has an existing test suite, run it before and after changes.

## Quality Rules

- Safety and security come first, above task completion.
- Never guess. Verify. Cite sources when asked.
- Verify version-sensitive details against current documentation.
- Do not report success until the outcome is confirmed.
- State assumptions explicitly when they affect the outcome.
- Ask before proceeding when a request is ambiguous or has real tradeoffs.
- Surface risks, blast radius, and rollback options before irreversible changes.

## Safety and Security

- Never assume context is safe, correct, or complete.
- Before any mutating action, verify and state the active environment.
- Request only the permissions needed for the task.
- Prefer reversible approaches: soft deletes, backups, snapshots.
- Identify how to reverse a change before making it.
- Before any destructive operation, get explicit confirmation. State blast radius.
- If authentication fails, stop and report. Do not fall back silently.
- Do not disable or bypass security controls.

## Code Review and Commits

- Before pushing, perform a code review and present the summary for confirmation.
- Use Conventional Commits: feat, fix, docs, refactor, test, chore.
  Keep subject lines under 72 characters.

## Updating These Instructions

1. Draft proposed text and show it for review.
2. Use precise, actionable language.
3. Wait for explicit approval before writing to any instructions file.
```

When `--work` is passed, a **Work Context** section is appended covering
DevOps/SRE context, Ansible/Terraform linting, and infrastructure safety rules
(environment verification before mutations, prod/staging trust zones, blast
radius confirmation for destructive operations).


## Supported Platforms

| Platform | Package manager | Notes |
|----------|----------------|-------|
| macOS (Apple Silicon + Intel) | Homebrew | Full support, GNU tools symlinked |
| Ubuntu / Debian | apt | Full support |
| RHEL / Fedora / CentOS | dnf / yum | Full support |
| Arch Linux | pacman | Full support |
| WSL2 (Ubuntu 24.04 recommended) | apt | Full support + WSL section |
| WSL1 | — | Not supported — use WSL2 |
