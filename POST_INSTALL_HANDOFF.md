# Post-Install Handoff: Windows → CachyOS Migration
**For the next Copilot session after CachyOS is installed**

Sam (they/them) is the preferred assistant name. Alice (she/her) is the user.

---

## What Just Happened

Alice completed a full Windows-to-CachyOS Linux migration preparation on a physical gaming/media PC.
CachyOS has been installed (or is about to be). Everything needed to restore the system is on the
external G: drive (WD Elements 8TB). All scripts are in `~/dotfiles/scripts/` and on D:\.

**Distro chosen:** CachyOS + KDE Plasma (performance-optimized Arch-based)
**Reason:** Best gaming performance (custom kernel), AUR access, good Plex/audio support, KDE for multi-monitor

---

## Hardware

| Component | Details |
|-----------|---------|
| CPU/GPU | AMD (Ryzen CPU + AMD GPU) |
| RAM | 32 GB |
| Monitor | Samsung LC32G5xT 32" curved 2560x1440 |
| OS SSD | Samsung SSD 970 EVO 500GB (was C:, now CachyOS root) |
| Games NVMe | Samsung SSD 990 EVO Plus 2TB (was D:, → /games) |
| Archive HDD | WDC WD80EMAZ-00WJTA0 8TB (was E:, → /mnt/archive) |
| Backup Drive | WD Elements 8TB (G:, keep connected through entire restore) |
| Audio In | Focusrite Sapphire USB interface (class-compliant, no drivers needed) |
| Audio Out | iFi Zen 2 USB DAC (class-compliant, no drivers needed) |
| Keyboard | ZSA (use Keymapp Linux AppImage from zsa.io) |
| Peripherals | Razer (use openrazer-driver-dkms + polychromatic) |
| Motherboard | ASUS (has AURA RGB → OpenRGB) |

---

## What's on the External Drive (G: / mounted as /run/media/alice/...)

```
G:\
  Migration\
    Personal\{Documents, Downloads, Pictures, Desktop, Music, Videos}
    GameSaves\{BaldursGate3, BaldursGate3, StardewValley, StardewValley,
               Borderlands3, Borderlands 3, FF7Remake, FINAL FANTASY VII REMAKE,
               StarOcean, STAR OCEAN THE SECOND STORY R,
               TinyTinasWonderlands, Tiny Tina's Wonderlands,
               ItTakesTwo}
    Games_Backup\
      Steam\steamapps\          <- 468 GB of Steam games including Rocksmith 2014
      Steam\userdata\17960811\  <- Steam profile data (all games)
    ICC_Profiles\               <- Samsung LC32G5xT calibration profile + X-Rite profiles
  Music\                        <- 67 GB stereo music library (45 artists, organized)
  Music_Multichannel\           <- 138 GB true multichannel 5.1 (24 artists, organized)
  Movies\                       <- 3.28 TB, 277 movie dirs (Plex-named)
  Television\                   <- 660 GB, 24 series (Plex-named)
  Legacy\                       <- Old E:\ drive backup (leave alone unless needed)
  Archives\                     <- Red Special Library, misc archives
  Migration_Dotfiles\           <- Plain rsync copy of ~/dotfiles repo
  Migration_Dotfiles.bundle     <- Git bundle of ~/dotfiles repo (verified)
```

---

## Post-Install Steps (do in this order)

### Step 1 — First boot housekeeping
```bash
# Update system first
sudo pacman -Syu

# Confirm you can see the backup drive
lsblk -o NAME,SIZE,MODEL,MOUNTPOINT

# Mount G: if not auto-mounted by KDE
sudo mkdir -p /mnt/g
sudo mount -t ntfs3 /dev/sdX /mnt/g   # replace sdX with actual device
```

### Step 2 — Format and mount secondary drives
```bash
# From the D:\ backup (copy to home first):
cp /mnt/g/Migration_Dotfiles/scripts/setup_drives_post_install.sh ~/
sudo bash ~/setup_drives_post_install.sh
```

**What it does:** Formats the Games NVMe as ext4 → `/games` and the Archive HDD as ext4 →
`/mnt/archive`, writes UUID-based fstab entries with `noatime`. It asks you to confirm
device names interactively before formatting anything.

**Expected drive mapping after boot (confirm with `lsblk -o NAME,SIZE,MODEL`):**
- Samsung 990 EVO Plus 2TB → probably `/dev/nvme1n1` → `/games`
- WDC WD80EMAZ 8TB → probably `/dev/sda` → `/mnt/archive`

> **WARNING:** The script is destructive. Confirm which device is which before typing "yes".
> The backup is already on G: so nothing is at risk, but double-check anyway.

### Step 3 — Restore dotfiles
```bash
# Clone from git bundle OR rsync copy on G:
cd ~
git clone /mnt/g/Migration_Dotfiles.bundle dotfiles
# OR: rsync -a /mnt/g/Migration_Dotfiles/ ~/dotfiles/

cd ~/dotfiles
bash setup.sh   # installs zsh, tmux, vim, packages
```

### Step 4 — Install applications
```bash
# Run the app installer (do NOT run as root)
bash ~/dotfiles/scripts/install_apps_cachyos.sh
```

This installs everything via pacman + yay (AUR). It is idempotent and has a `--dry-run` mode.
See the **App List** section below for what gets installed and why.

### Step 5 — Restore personal data
```bash
# Auto-detects the backup drive
bash ~/dotfiles/scripts/restore_data.sh

# Or explicit mount point:
bash ~/dotfiles/scripts/restore_data.sh /run/media/alice/YourDriveName

# Dry-run first if you want to review:
bash ~/dotfiles/scripts/restore_data.sh --dry-run
```

**What it restores:**
- `~/Documents`, `~/Downloads`, `~/Pictures`, `~/Desktop`, `~/Music`, `~/Videos`
- Game saves (see paths below)
- Steam steamapps → `/games/Steam/steamapps/`
- Steam userdata → `~/.local/share/Steam/userdata/`
- ICC profiles → `~/.local/share/icc/` (auto-applies via colord)

### Step 6 — Set up Rocksmith 2014 audio (CRITICAL — see full section below)

### Step 7 — Set up Plex
```bash
# Install (should already be done by install_apps_cachyos.sh)
sudo systemctl enable --now plexmediaserver

# Plex data dir
sudo mkdir -p /var/lib/plexmediaserver/Library
sudo chown -R plex:plex /var/lib/plexmediaserver

# Point libraries to (already organized on G: root, or copy to /mnt/archive first):
#   Movies:            /run/media/alice/.../Movies  (or /mnt/archive/Movies)
#   Television:        /run/media/alice/.../Television
#   Music:             /run/media/alice/.../Music
#   Music Multichannel: /run/media/alice/.../Music_Multichannel
```

Plex web UI: http://localhost:32400/web

### Step 8 — ICC color calibration
```bash
bash ~/dotfiles/scripts/restore_icc_profiles.sh /run/media/alice/YourDriveName
```
Applies the Samsung LC32G5xT calibration profile (`LC32G5xT_20220101.icm`) via colord.
Also copies X-Rite i1Studio profiles. If colord isn't running: `sudo systemctl start colord`.

---

## Rocksmith 2014 Audio Setup (CRITICAL)

This is the trickiest part. Follow exactly.

### Audio chain
```
Guitar → Focusrite USB → snd-usb-audio → PipeWire → WineASIO → RS_ASIO → Rocksmith (Proton)
Playback: RS_ASIO → WineASIO → PipeWire → iFi USB DAC → speakers/headphones
```

### RS_ASIO config
The Windows config used:
- Input:  Focusrite USB ASIO Ch1
- Output: iFi USB Audio Device
- Buffer: 48 samples custom (~1ms at 48kHz)
- Version: RS_ASIO v0.7.4 (already in game dir)

On Linux, `Driver=ASIO4ALL v2` becomes `Driver=Wine ASIO`. RS_ASIO.ini goes in the game dir.

A template is at: `G:\Migration\Games_Backup\Steam\steamapps\common\Rocksmith2014\RS_ASIO.ini.linux-template`
Copy it over the existing `RS_ASIO.ini`:
```bash
RS_DIR="$HOME/.local/share/Steam/steamapps/common/Rocksmith2014"
cp "$RS_DIR/RS_ASIO.ini.linux-template" "$RS_DIR/RS_ASIO.ini"
```

### PipeWire quantum (low latency)
```bash
mkdir -p ~/.config/pipewire/pipewire.conf.d/
cat > ~/.config/pipewire/pipewire.conf.d/99-rocksmith.conf << 'EOF'
context.properties = {
    default.clock.rate = 48000
    default.clock.quantum = 48
    default.clock.min-quantum = 48
    default.clock.max-quantum = 48
}
EOF
systemctl --user restart pipewire pipewire-pulse
```

### WineASIO (must be 32-bit, in Proton prefix)
Rocksmith is a 32-bit app. WineASIO must be registered in its Proton prefix.
```bash
# Install WineASIO
yay -S wineasio

# Register in Proton prefix (AppID 221680 = Rocksmith 2014)
PROTON_PREFIX="$HOME/.local/share/Steam/steamapps/compatdata/221680/pfx"
WINEPREFIX="$PROTON_PREFIX" wine regsvr32 /usr/lib32/wine/i386-windows/wineasio.dll
```

### Steam launch option for Rocksmith
In Steam → Right-click Rocksmith 2014 → Properties → Launch Options:
```
PIPEWIRE_LATENCY=48/48000 %command%
```

### Audio routing with qpwgraph
After launching Rocksmith once (it will fail), open qpwgraph and connect:
- WineASIO input → Focusrite USB (capture)
- WineASIO output → iFi Zen 2 (playback)

### Proton version
Use **GE-Proton 8.26** or later. Install via ProtonUp-Qt (AUR: `protonup-qt`).
Right-click Rocksmith in Steam → Properties → Compatibility → Force specific Proton version.

### Rocksmith DLC
All 1,313 DLC files are backed up. They'll be restored to the correct location by `restore_data.sh`.
The DLC folder is: `~/.local/share/Steam/steamapps/common/Rocksmith2014/dlc/`

---

## Game Save Restore Paths

| Game | Backup Source (on G:) | Linux Destination |
|------|-----------------------|-------------------|
| Baldur's Gate 3 | `Migration/GameSaves/BaldursGate3` | `~/.local/share/Larian Studios/Baldur's Gate 3/` |
| Stardew Valley | `Migration/GameSaves/StardewValley` | `~/.config/StardewValley/` |
| Borderlands 3 | `Migration/GameSaves/Borderlands3` | `~/.local/share/Steam/steamapps/compatdata/397540/pfx/drive_c/Users/steamuser/My Documents/My Games/Borderlands 3/` |
| FF7 Remake | `Migration/GameSaves/FF7Remake` | via Proton prefix |
| It Takes Two | `Migration/GameSaves/ItTakesTwo` | `~/.local/share/Steam/steamapps/compatdata/1426210/pfx/drive_c/users/steamuser/AppData/Local/ItTakesTwo/` |
| Star Ocean | `Migration/GameSaves/StarOcean` | via Proton prefix |
| Tiny Tina's | `Migration/GameSaves/TinyTinasWonderlands` | via Proton prefix |

`restore_data.sh` handles all of these automatically.

---

## App List (install_apps_cachyos.sh covers all of these)

| Category | App | Source |
|----------|-----|--------|
| Browser | Firefox, Chrome | pacman / AUR |
| Communication | Discord | AUR (discord) |
| Music | Tidal (tidal-hifi) | AUR |
| Music | Strawberry (foobar2000 replacement) | pacman |
| Music | Picard (MusicBrainz tagger) | pacman |
| Audio tools | Audacity, TuxGuitar, MuseScore | pacman |
| CD ripping | whipper (EAC replacement) | AUR |
| Video | VLC, HandBrake, MKVToolNix, MakeMKV, mpv | pacman / AUR |
| Media server | Plex Media Server | AUR (plex-media-server) |
| Gaming | Steam | pacman |
| Gaming | ProtonUp-Qt | AUR |
| Gaming | MangoHud + GOverlay | AUR |
| Productivity | LibreOffice | pacman |
| PDF | Okular | pacman |
| Audio plugin | yabridge + Reaper | AUR / manual |
| Guitar plugin | Darkglass Suite (via yabridge) | manual/Wine |
| Tabs | RocksmithToTabGUI | Bottles/Wine |
| Audio routing | qpwgraph | pacman |
| Hardware | OpenRGB (ASUS AURA, Lian-Li) | AUR |
| Hardware | openrazer-driver-dkms + polychromatic | AUR |
| Hardware | CoreCtrl (Ryzen Master replacement) | AUR |
| Monitoring | btop, amdgpu_top, psensor, MangoHud | pacman / AUR |
| Fan control | lm-sensors + fancontrol | pacman |
| Display cal | DisplayCAL + ArgyllCMS | AUR |
| Camera | gphoto2 + darktable | pacman |
| Keyboard | Keymapp AppImage (from zsa.io) | manual |
| DVD audio | ffmpeg (DVD Audio Extractor replacement) | pacman |

---

## Music Library (already organized on G:\)

The music library is fully organized and tagged (0 errors as of pre-install):

| Directory | Contents |
|-----------|----------|
| `G:\Music\` | 45 stereo artists, 67 GB, FLAC + MP3 |
| `G:\Music_Multichannel\` | 24 artists, 37 albums, 138 GB — TRUE 5.1 surround |

**Multichannel inventory:** `G:\Multichannel_Music_Inventory.txt` — full list of 37 albums.

Notable multichannel artists: Pink Floyd, Rush, Yes, King Crimson, Transatlantic, Steven Wilson,
Tears For Fears, David Bowie (Ziggy Stardust DTS only), Miles Davis, Depeche Mode, Toto,
Talking Heads, Soundgarden, Temple of the Dog, and more.

**Plex note:** Multichannel doesn't work on the NVIDIA Shield in Plex as of last check.
Keep Music and Music_Multichannel as separate libraries.

---

## Media Library (already on G:\)

| Library | Count | Size |
|---------|-------|------|
| Movies | 277 dirs | 3.28 TB |
| Television | 24 series | 660 GB |

All files are Plex-named (`Movie Title (Year)` format). `G:\Legacy\` contains the old E:\
drive state — it's safe to leave as-is until everything is verified working, then it can be
cleaned up.

---

## Scripts Reference

| Script | Location | When to run |
|--------|----------|-------------|
| `setup_drives_post_install.sh` | Copy from G:\Migration_Dotfiles\scripts\ | First boot, as root |
| `setup.sh` | `~/dotfiles/setup.sh` | After dotfiles cloned |
| `install_apps_cachyos.sh` | `~/dotfiles/scripts/` | After setup.sh, as regular user |
| `restore_data.sh` | `~/dotfiles/scripts/` | After /games and /mnt/archive mounted |
| `restore_icc_profiles.sh` | `~/dotfiles/scripts/` | After restore_data.sh |
| `update_eks_kube_config` | `~/dotfiles/scripts/` | For work k8s clusters |

---

## Known Gotchas

1. **Proton prefix must be initialized before WineASIO registration** — launch Rocksmith once
   (even if it crashes), then register WineASIO.

2. **PipeWire quantum change** affects ALL audio system-wide. If it causes issues with other
   apps, revert after gaming by removing `99-rocksmith.conf` or changing quantum back to 1024.

3. **fstrim.timer** should be enabled for SSDs — `sudo systemctl enable --now fstrim.timer`.
   Do NOT use the `discard` mount option (causes I/O storms).

4. **No swap partition** — 32 GB RAM + zram (CachyOS default). This is correct, don't add swap.

5. **Stardew Valley saves** go to `~/.config/StardewValley/`, NOT `~/.local/share/StardewValley/`.

6. **G: drive volume label** may appear differently under Linux. The auto-detect in `restore_data.sh`
   looks for a directory containing both `Migration/` and `Legacy/` — it doesn't depend on the label.

7. **openrazer** requires the DKMS module to be built against the running kernel after install.
   If Razer devices aren't detected, run: `sudo modprobe razerkbd` (or relevant module).

8. **DisplayCAL + ArgyllCMS** — the Samsung ICC profile is at `G:\Migration\ICC_Profiles\LC32G5xT_20220101.icm`.
   `restore_icc_profiles.sh` handles importing it. If colord isn't running, start it first.

9. **G:\Legacy** still has 24,653 files (the old E:\ drive state). Don't delete it until the
   new install is fully verified. Some of it overlaps with organized G:\ root dirs (expected).

10. **`G:\Migration\Games_Backup\Steam\userdata\17960811\`** contains the Steam profile for user 17960811
    (all games' cloud save data). `restore_data.sh` handles this.

---

## Quick Reference — Key Paths on New CachyOS Install

```
/                          <- OS SSD (Samsung 970 EVO 500GB)
/games                     <- Games NVMe (Samsung 990 EVO Plus 2TB)
/games/Steam/steamapps/    <- Steam games
/mnt/archive               <- Archive HDD (WDC 8TB)
~/.local/share/Steam/      <- Steam client data
~/.local/share/Steam/steamapps/compatdata/221680/   <- Rocksmith Proton prefix
~/dotfiles/                <- This repo
```

---

## What Alice Uses This Machine For

- Gaming: Rocksmith 2014 (primary concern), BG3, Stardew Valley, Borderlands 3, others
- Audio: Guitar recording into Focusrite Sapphire → iFi Zen 2 DAC → studio monitors
- DAW: Reaper (native Linux binary) with yabridge for VST plugins (Darkglass Suite)
- Plex server: serving Movies, TV, Music to NVIDIA Shield and other clients
- Music: Tidal (tidal-hifi AUR), Strawberry for local files
- General: Firefox, Discord, LibreOffice
- Work: EKS/k8s config managed via `update_eks_kube_config` script

---

*Generated: 2026-04-10 | Session 7fb6b244 | Pre-install state: all backups verified, 0 tag errors*
