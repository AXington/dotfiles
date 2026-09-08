# shellcheck shell=bash
# Section: copilot
# shellcheck disable=SC2088,SC2015

# -- 13. GitHub Copilot CLI ----------------------------------------------------

section_copilot() {
    log "Setting up GitHub Copilot CLI..."

    if ! command_exists copilot; then
        case "$OS" in
            macos)
                run brew install copilot-cli ;;
            linux|wsl)
                if [[ "$DRY_RUN" == "true" ]]; then
                    printf '\e[2;37m  [dry] install Copilot CLI via fetch_and_run\e[0m\n'
                else
                    fetch_and_run "https://gh.io/copilot-install"
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
    local instructions_src="${SCRIPT_DIR}/configs/copilot-instructions.md"
    install_instructions "$instructions_dir" "$instructions_file" "$instructions_src" "Copilot"

    local settings_file="${instructions_dir}/settings.json"
    if [[ -f "$settings_file" ]]; then
        # Deliberately a no-op. This file holds deliberate per-machine choices
        # (model, effort level, allowed URLs), and rewriting it on every setup
        # run silently overrode them. Creation-time defaults only.
        ok "Copilot settings already present (left unchanged)."
    else
        log "Writing Copilot settings..."
        if [[ "$DRY_RUN" == "true" ]]; then
            printf '\e[2;37m  [dry] write Copilot settings to %s\e[0m\n' "$settings_file"
        else
            printf '{"model":"claude-opus-5","memory":true}\n' > "$settings_file"
        fi
        ok "Copilot settings written to ${settings_file}."
    fi

    # superpowers: community fork adds Copilot CLI support for obra/superpowers
    # The installer creates a single nested symlink (.copilot/skills/superpowers ->
    # marketplace-cache/.../skills) but the CLI requires each skill to be a direct
    # child of ~/.copilot/skills/. After installing we flatten: remove the nested
    # symlink and create one symlink per skill directly in ~/.copilot/skills/.
    local superpowers_cache="$HOME/.copilot/marketplace-cache/dwaintr-superpowers-copilot/plugins/superpowers/skills"
    local skills_dir="$HOME/.copilot/skills"
    # Probe test-driven-development, not brainstorming: this repo ships its own
    # skills/brainstorming/, and _install_local_skills replaces the superpowers
    # symlink with the repo copy, so a brainstorming probe never matched and
    # Superpowers was reinstalled on every run.
    if [[ -L "${skills_dir}/test-driven-development" ]]; then
        ok "Superpowers for Copilot already installed."
    else
        log "Installing Superpowers for GitHub Copilot CLI..."
        if [[ "$DRY_RUN" == "true" ]]; then
            printf '\e[2;37m  [dry] install Superpowers via DwainTR/superpowers-copilot\e[0m\n'
            printf '\e[2;37m  [dry] flatten per-skill symlinks in %s\e[0m\n' "$skills_dir"
        else
            fetch_and_run "https://raw.githubusercontent.com/DwainTR/superpowers-copilot/main/install.sh"
            # Remove the nested dir/symlink the installer creates and replace with flat symlinks
            rm -rf "${skills_dir}/superpowers"
            for skill_path in "$superpowers_cache"/*/; do
                ln -sf "$skill_path" "${skills_dir}/$(basename "$skill_path")"
            done
        fi
        ok "Superpowers installed for Copilot."
    fi

    # gstack -- Garry Tan's engineering team skills for Copilot CLI
    # Uses ridermw/gstack (PR garrytan/gstack#393) which adds --host copilot.
    # The setup script clones to a cache dir, builds the browse binary with bun,
    # generates .agents/skills/ docs, then writes per-skill dirs under
    # ~/.copilot/skills/gstack-*/ and a runtime root at ~/.copilot/skills/gstack/.
    local gstack_cache="$HOME/.copilot/marketplace-cache/gstack"
    local gstack_runtime="$HOME/.copilot/skills/gstack"
    if [[ -d "$gstack_runtime" ]]; then
        ok "gstack for Copilot already installed."
    else
        log "Installing gstack for GitHub Copilot CLI..."
        install_gstack \
            "$gstack_cache" \
            "--host copilot" \
            "git@github.com:ridermw/gstack.git" \
            "--branch add-copilot-cli-support"
    fi

    # Install local skills from this repo. Mirrors section_claude; without this,
    # repo skills only reach Copilot via the opt-in copilot_skills section.
    _install_local_skills "$skills_dir"
    ok "All local skills installed to ~/.copilot/skills/"

    log "To authenticate, run: copilot /login"

    # Extensions -- user-level Copilot CLI extensions. This is the only
    # mechanism that loads in every repo: JSON command hooks are read from
    # <git root>/.github/hooks, which would mean committing personal tooling
    # into every repository.
    local ext_src="${SCRIPT_DIR}/copilot-extensions"
    local ext_dest="$HOME/.copilot/extensions"
    if [[ -d "$ext_src" ]]; then
        run mkdir -p "$ext_dest"
        for ext_path in "$ext_src"/*/; do
            [[ -d "$ext_path" ]] || continue
            local ext_name
            ext_name="$(basename "$ext_path")"
            if [[ -L "${ext_dest}/${ext_name}" || -d "${ext_dest}/${ext_name}" ]]; then
                ok "Extension '${ext_name}' already installed."
            else
                run ln -sf "$ext_path" "${ext_dest}/${ext_name}"
                ok "Extension '${ext_name}' installed."
            fi
        done
    fi
}

# -- Verification (--verify mode) ---------------------------------------------
# shellcheck disable=SC2088  # Tilde in quoted strings is intentional display text
# shellcheck disable=SC2015  # A && B || C pattern is safe here (pass/fail always succeed)

verify_copilot() {
    command_exists copilot              && pass "Copilot CLI installed"                        || fail "Copilot CLI not installed"
    [[ -f "$HOME/.copilot/copilot-instructions.md" ]] \
                                        && pass "Copilot instructions written"                 || fail "Copilot instructions missing"
    [[ -f "$HOME/.copilot/settings.json" ]] \
                                        && pass "Copilot settings written"                     || fail "Copilot settings missing"
    [[ -L "$HOME/.copilot/extensions/ledger-companion" \
       || -d "$HOME/.copilot/extensions/ledger-companion" ]] \
                                        && pass "Ledger companion extension installed"         || fail "Ledger companion extension missing"
    [[ ! -e "$HOME/.copilot/hooks/session-start-orient.json" ]] \
                                        && pass "Dead JSON command hooks removed"              || fail "Dead JSON command hook still present"
    # Probe a superpowers-only skill. brainstorming is unusable here: this repo
    # ships its own skills/brainstorming/, and _install_local_skills replaces
    # the superpowers symlink with the repo copy, so the check always failed.
    [[ -L "$HOME/.copilot/skills/test-driven-development" ]] \
                                        && pass "Superpowers for Copilot installed (flat symlinks)" || fail "Superpowers for Copilot not installed"
    { command_exists bun || [[ -x "$HOME/.bun/bin/bun" ]]; } \
                                        && pass "bun installed (gstack dependency)"             || fail "bun not installed (required by gstack)"
    [[ -d "$HOME/.copilot/skills/gstack" ]] \
                                        && pass "gstack installed for Copilot"                  || fail "gstack not installed for Copilot"
    [[ -e "$HOME/.copilot/extensions/prompt-injection-guard/extension.mjs" ]] \
                                        && pass "prompt-injection-guard extension installed"    || fail "prompt-injection-guard extension missing"

    # Check local skills installed. Mirrors verify_claude: assert every skill
    # this repo ships, rather than probing one name as a proxy.
    local skill src skills_dir="$HOME/.copilot/skills"
    [[ -d "$skills_dir" ]] \
        && pass "Copilot skills directory exists" \
        || fail "Copilot skills directory missing (~/.copilot/skills/)"
    for src in "${SCRIPT_DIR}/skills"/*/; do
        [[ -d "$src" ]] || continue
        skill="$(basename "$src")"
        [[ -f "${skills_dir}/${skill}/SKILL.md" ]] \
            && pass "Copilot skill installed: ${skill}" \
            || fail "Copilot skill missing: ${skill}"
    done
}
