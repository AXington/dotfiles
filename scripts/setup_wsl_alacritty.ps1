﻿<#
.SYNOPSIS
    Bootstrap WSL2 with the latest Ubuntu LTS, Alacritty, and dotfiles on Windows 11.

.DESCRIPTION
    Run from an elevated PowerShell session (right-click PowerShell -> Run as Administrator).
    Automatically detects the latest Ubuntu LTS available in the WSL store.

    After this script completes:
      1. Run 'wsl -d <distro>' to finish first-time Ubuntu setup (username + password)
      2. Inside Ubuntu, run: ~/dotfiles/setup.sh

    Idempotent -- safe to re-run after a reboot or partial install.

.NOTES
    Requires: Windows 11, PowerShell 5.1+, administrator privileges
    If execution policy blocks the script, run first:
        Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.EXAMPLE
    # From an elevated PowerShell prompt:
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
    .\scripts\setup_wsl_alacritty.ps1
#>

#Requires -RunAsAdministrator

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -- Environment validation ---------------------------------------------------
foreach ($var in @('APPDATA', 'USERPROFILE', 'TEMP')) {
    if ([string]::IsNullOrEmpty([System.Environment]::GetEnvironmentVariable($var))) {
        Write-Host "  [xx] Required environment variable `$$var is not set -- cannot continue." -ForegroundColor Red
        exit 1
    }
}

# -- Constants -----------------------------------------------------------------
$DOTFILES_REPO      = 'https://github.com/AXington/dotfiles.git'
$DOTFILES_RAW       = 'https://raw.githubusercontent.com/AXington/dotfiles/master'
$ALACRITTY_CFG_DIR  = "$env:APPDATA\alacritty"
$ALACRITTY_TOML     = "$ALACRITTY_CFG_DIR\alacritty.toml"
$WSLCONFIG_PATH     = "$env:USERPROFILE\.wslconfig"
$SYSTEM_FONTS_DIR   = 'C:\Windows\Fonts'
$FONTS_REG_KEY      = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts'
$POWERLINE_BASE_URL = 'https://raw.githubusercontent.com/powerline/fonts/master/Meslo%20Slashed'

$MESLO_M_FONTS = [ordered]@{
    'Meslo LG M Regular for Powerline.ttf'     = "$POWERLINE_BASE_URL/Meslo%20LG%20M%20Regular%20for%20Powerline.ttf"
    'Meslo LG M Bold for Powerline.ttf'        = "$POWERLINE_BASE_URL/Meslo%20LG%20M%20Bold%20for%20Powerline.ttf"
    'Meslo LG M Italic for Powerline.ttf'      = "$POWERLINE_BASE_URL/Meslo%20LG%20M%20Italic%20for%20Powerline.ttf"
    'Meslo LG M Bold Italic for Powerline.ttf' = "$POWERLINE_BASE_URL/Meslo%20LG%20M%20Bold%20Italic%20for%20Powerline.ttf"
}

# -- Helpers -------------------------------------------------------------------
function Write-Step { param([string]$Msg) Write-Host "`n==> $Msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg) Write-Host "  [ok] $Msg" -ForegroundColor Green }
function Write-Warn { param([string]$Msg) Write-Host "  [!!] $Msg" -ForegroundColor Yellow }

function Test-CommandExists {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

# WSL outputs UTF-16LE which PowerShell captures with embedded NUL bytes.
# This helper strips NUL characters so string matching works correctly.
function Get-WslOutput {
    param([scriptblock]$Cmd)
    try {
        $raw = & $Cmd 2>&1
        return ($raw | ForEach-Object { "$_" -replace "`0", '' } | Where-Object { $_ -ne '' })
    } catch {
        return @()
    }
}

function Get-LatestUbuntuLTS {
    # Query the WSL store for available distros and return the highest-versioned
    # Ubuntu LTS (even year, .04 release). Falls back to Ubuntu-24.04 on failure.
    try {
        $lines    = Get-WslOutput { wsl --list --online }
        $versions = $lines |
            Where-Object  { $_ -match 'Ubuntu-(\d+)\.(\d+)' } |
            ForEach-Object {
                if ($_ -match 'Ubuntu-(\d+)\.(\d+)') {
                    $year  = [int]$Matches[1]
                    $month = [int]$Matches[2]
                    if ($year % 2 -eq 0 -and $month -eq 4) {
                        [PSCustomObject]@{
                            Name  = "Ubuntu-$($Matches[1]).$($Matches[2])"
                            Year  = $year
                        }
                    }
                }
            } |
            Where-Object { $null -ne $_ } |
            Sort-Object Year -Descending

        if ($versions) {
            Write-Ok "Latest Ubuntu LTS available: $($versions[0].Name)"
            return $versions[0].Name
        }
    } catch {
        Write-Warn "Could not query WSL store: $_"
    }

    Write-Warn 'Falling back to Ubuntu-24.04'
    return 'Ubuntu-24.04'
}

function Invoke-WebRequestSafe {
    # Wrapper around Invoke-WebRequest with consistent error handling.
    param(
        [string]$Uri,
        [string]$OutFile = $null
    )
    try {
        if ($OutFile) {
            Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing
        } else {
            return (Invoke-WebRequest -Uri $Uri -UseBasicParsing)
        }
    } catch {
        throw "Failed to download ${Uri}: $_"
    }
}

# -- 1. WSL2 -------------------------------------------------------------------
Write-Step 'Setting up WSL2...'

$wslInstalled = $false
try {
    $wslOut = (wsl --version 2>&1) -join ' '
    $wslInstalled = ($LASTEXITCODE -eq 0 -and $wslOut -match 'WSL version')
} catch {
    $wslInstalled = $false
}

if (-not $wslInstalled) {
    Write-Warn 'WSL not detected -- installing...'
    $installOut = (wsl --install --no-launch 2>&1) -join ' '
    if ($LASTEXITCODE -ne 0 -or $installOut -match 'restart|reboot') {
        Write-Warn 'WSL install requires a reboot to complete.'
        Write-Host ''
        Write-Host '  Please reboot and re-run this script to continue.' -ForegroundColor Yellow
        exit 0
    }
    Write-Ok 'WSL2 installed'
} else {
    Write-Ok "WSL2 already installed ($( ($wslOut -split ' ')[0..2] -join ' ' ))"
}

try {
    wsl --set-default-version 2 | Out-Null
} catch {
    Write-Warn "Could not set WSL default version: $_ (may need reboot)"
}

# -- 2. Latest Ubuntu LTS ------------------------------------------------------
Write-Step 'Installing latest Ubuntu LTS...'

$UbuntuDistro     = Get-LatestUbuntuLTS
$installedDistros = (Get-WslOutput { wsl --list --quiet }) -join ' '

if ($installedDistros -notmatch [regex]::Escape($UbuntuDistro)) {
    Write-Warn "Installing $UbuntuDistro -- this may take a few minutes..."
    wsl --install -d $UbuntuDistro --no-launch | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "$UbuntuDistro installed"
    } else {
        Write-Warn "$UbuntuDistro install returned exit code $LASTEXITCODE -- try the Microsoft Store if this persists"
    }
} else {
    Write-Ok "$UbuntuDistro already installed"
}

# -- 3. Alacritty --------------------------------------------------------------
Write-Step 'Installing Alacritty...'

if (-not (Test-CommandExists 'alacritty')) {
    if (Test-CommandExists 'winget') {
        winget install --id Alacritty.Alacritty `
            --accept-package-agreements --accept-source-agreements --silent
        if ($LASTEXITCODE -eq 0) {
            Write-Ok 'Alacritty installed via winget'
            Write-Warn 'You may need to open a new terminal session before Alacritty is in PATH.'
        } else {
            Write-Warn "winget install returned exit code $LASTEXITCODE -- check winget logs or install manually from https://alacritty.org"
        }
    } else {
        Write-Warn 'winget not available -- download Alacritty manually from https://alacritty.org'
    }
} else {
    Write-Ok 'Alacritty already installed'
}

# -- 4. Meslo LG M for Powerline fonts ----------------------------------------
Write-Step 'Installing Meslo LG M for Powerline fonts...'

foreach ($entry in $MESLO_M_FONTS.GetEnumerator()) {
    $destPath = Join-Path $SYSTEM_FONTS_DIR $entry.Key
    $regName  = [System.IO.Path]::GetFileNameWithoutExtension($entry.Key) + ' (TrueType)'

    # Check registry first -- font may be registered even if file is present
    $regExists = $null -ne (Get-ItemProperty -Path $FONTS_REG_KEY -Name $regName -ErrorAction SilentlyContinue)

    if ((Test-Path $destPath) -and $regExists) {
        Write-Ok "Already installed: $($entry.Key)"
        continue
    }

    # Strip random extension from GetRandomFileName before appending .ttf
    $tmpBase = [System.IO.Path]::GetRandomFileName() -replace '\.[^.]*$', ''
    $tmp     = Join-Path $env:TEMP ($tmpBase + '.ttf')

    try {
        Invoke-WebRequestSafe -Uri $entry.Value -OutFile $tmp
        Copy-Item -Path $tmp -Destination $destPath -Force
        Set-ItemProperty -Path $FONTS_REG_KEY -Name $regName -Value $entry.Key
        Write-Ok "Installed: $($entry.Key)"
    } catch {
        Write-Warn "Failed to install $($entry.Key): $_"
    } finally {
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
}

# -- 5. Alacritty config -------------------------------------------------------
Write-Step 'Writing Alacritty config...'

New-Item -ItemType Directory -Force -Path $ALACRITTY_CFG_DIR | Out-Null

try {
    $tomlContent = (Invoke-WebRequestSafe -Uri "$DOTFILES_RAW/terminal_configs/alacritty.toml").Content
    $tomlContent = $tomlContent.TrimEnd() + @"


[shell]
program = "wsl.exe"
args = ["--distribution", "$UbuntuDistro"]
"@
    # Write UTF-8 without BOM -- PS 5.1's Set-Content -Encoding UTF8 adds a BOM
    # which Alacritty's TOML parser does not expect.
    [System.IO.File]::WriteAllText($ALACRITTY_TOML, $tomlContent, [System.Text.UTF8Encoding]::new($false))
    Write-Ok "Config written: $ALACRITTY_TOML"
} catch {
    Write-Warn "Failed to write Alacritty config: $_"
}

# -- 6. .wslconfig -------------------------------------------------------------
Write-Step 'Writing .wslconfig...'

if (-not (Test-Path $WSLCONFIG_PATH)) {
    try {
        Invoke-WebRequestSafe -Uri "$DOTFILES_RAW/wslconfig.template" -OutFile $WSLCONFIG_PATH
        Write-Ok "Written: $WSLCONFIG_PATH"
        Write-Warn "Edit $WSLCONFIG_PATH to tune memory/CPU for your machine, then run: wsl --shutdown"
    } catch {
        Write-Warn "Failed to write .wslconfig: $_"
    }
} else {
    Write-Ok '.wslconfig already exists -- not overwriting'
}

# -- 7. Clone dotfiles into WSL ------------------------------------------------
Write-Step 'Cloning dotfiles into WSL...'

$wslReady = (Get-WslOutput { wsl -d $UbuntuDistro -- bash -c 'echo ready' }) -join ''
$wslReady = $wslReady.Trim() -replace "`0", ''

if ($wslReady -eq 'ready') {
    # Ensure git is available in WSL -- not guaranteed on a fresh Ubuntu install
    $gitCheck = (Get-WslOutput { wsl -d $UbuntuDistro -- bash -c 'command -v git' }) -join ''
    if ([string]::IsNullOrWhiteSpace($gitCheck)) {
        Write-Warn 'git not found in WSL -- installing...'
        wsl -d $UbuntuDistro -- bash -c 'sudo apt-get update -qq && sudo apt-get install -y -qq git' | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warn 'Failed to install git in WSL -- dotfiles clone skipped. Install git manually and re-run.'
        }
    }

    $present = (Get-WslOutput { wsl -d $UbuntuDistro -- bash -c 'test -d ~/dotfiles && echo yes || echo no' }) -join ''
    $present = $present.Trim() -replace "`0", ''
    if ($present -ne 'yes') {
        wsl -d $UbuntuDistro -- bash -c "git clone $DOTFILES_REPO ~/dotfiles" | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok 'Dotfiles cloned to ~/dotfiles in WSL'
        } else {
            Write-Warn 'git clone failed -- clone manually inside WSL: git clone https://github.com/AXington/dotfiles.git ~/dotfiles'
        }
    } else {
        Write-Ok 'Dotfiles already present in WSL'
    }
} else {
    Write-Warn 'WSL not yet initialized -- dotfiles will be cloned after first-time Ubuntu setup.'
}

# -- Done ----------------------------------------------------------------------
$sep = '=' * 52
Write-Host "`n$sep" -ForegroundColor Cyan
Write-Host '  Bootstrap complete!' -ForegroundColor Cyan
Write-Host $sep -ForegroundColor Cyan
Write-Host ''
Write-Host 'Next steps:' -ForegroundColor White
Write-Host ''
Write-Host "  1. Complete first-time Ubuntu setup:" -ForegroundColor White
Write-Host "       wsl -d $UbuntuDistro" -ForegroundColor Gray
Write-Host "     Set your Unix username and password when prompted." -ForegroundColor Gray
Write-Host ''
Write-Host '  2. Inside WSL, run the dotfiles bootstrap:' -ForegroundColor White
Write-Host '       ~/dotfiles/setup.sh' -ForegroundColor Gray
Write-Host "     (or clone first if needed: git clone $DOTFILES_REPO ~/dotfiles)" -ForegroundColor Gray
Write-Host ''
Write-Host "  3. Open Alacritty -- it will launch directly into $UbuntuDistro." -ForegroundColor White
Write-Host ''
Write-Host '  4. Tune .wslconfig memory/CPU for your machine, then apply:' -ForegroundColor White
Write-Host "       notepad $WSLCONFIG_PATH" -ForegroundColor Gray
Write-Host '       wsl --shutdown' -ForegroundColor Gray
Write-Host ''
