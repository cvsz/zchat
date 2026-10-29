# ZChat Installer

Single source of truth for Windows 11 automated installer.

## Overview

This directory contains the NSIS-based installer for ZChat/AutoDJ that packages all built components into a single, code-signed Windows installer.

## Structure

```
installer/
├── ZChatInstaller.nsi       # Main NSIS script (single source of truth)
├── build_installer.bat      # Windows batch build script
├── build_installer.ps1      # PowerShell build script (recommended)
├── LICENSE.txt              # License agreement
├── check_mpv.bat            # mpv dependency check
├── check_ytdlp.bat          # yt-dlp dependency check
├── README.md                # This file
└── staging/                 # Temporary staging (created during build)
```

## Requirements

- **NSIS 3.0+** - [Download](https://nsis.sourceforge.io/Download)
- **Windows 10/11** (build host)
- **Python 3.12+** with build dependencies (for building EXEs first)
- **Code signing certificate** (optional, for production)

## Quick Start

### 1. Build EXEs First
```cmd
full-build-exe.bat
```

### 2. Build Installer

**PowerShell (recommended):**
```powershell
cd installer
.\build_installer.ps1 -Version "2.20.0" -SignCertificate "A1B2C3D4E5F6..."
```

**Batch:**
```cmd
cd installer
build_installer.bat
```

### 3. With PFX File
```powershell
.\build_installer.ps1 -PfxPath "C:\certs\zcoder.pfx" -PfxPassword "secret"
```

### 4. Skip Signing (Development)
```powershell
.\build_installer.ps1 -SkipSign
```

## Output

```
dist/installer/
└── ZChat-2.20.0-Setup.exe    # Signed installer (~50-100 MB)
```

## Installer Features

| Feature | Description |
|---------|-------------|
| **Component Selection** | Choose which apps to install |
| **Dependency Checks** | Validates mpv & yt-dlp |
| **Auto-launch Option** | Start AutoDJ on Windows login |
| **Shortcuts** | Start Menu + optional Desktop |
| **Silent Install** | `/S /D=C:\Path` |
| **Silent Uninstall** | `uninstall.exe /S` |
| **Code Signing** | Authenticode SHA256 + RFC3161 timestamp |
| **Windows 11 Compatible** | Modern UI, high DPI, per-user/per-machine |

## Component Selection

| Component | Description | Required |
|-----------|-------------|----------|
| Core | Config, tools, assets | ✅ Yes |
| AutoDJ | Music bot with YouTube | |
| Room Control | Room management | |
| Chat IM Private | Private chat monitor | |
| Status Random | Random status rotation | |
| Status Marquee | Scrolling status | |
| Status Changer | Status rotation | |
| IM Autoreply | Private message auto-reply | |
| Web Status | Web-based status UI | |
| CloudBot Manager | Bot dashboard | |
| Name Color Changer | Nickname color toggle | |
| Icon Changer | Room icon toggle | |
| Name Changer | Nickname changer | |
| Line Status | LINE status integration | |
| ZChat Client | Unified client | |

## Silent Installation

```cmd
# Basic silent install
ZChat-2.20.0-Setup.exe /S /D=C:\Program Files\ZChat

# With specific components
ZChat-2.20.0-Setup.exe /S /COMPONENTS="AutoDJ,RoomControl,CloudBotManager" /D=C:\ZChat

# With auto-launch and desktop shortcut
ZChat-2.20.0-Setup.exe /S /COMPONENTS="AutoDJ" /AUTOLAUNCH=1 /DESKTOP=1
```

## Silent Uninstall

```cmd
"C:\Program Files\ZChat\uninstall.exe" /S
```

## Code Signing

### Environment Variables
```cmd
set ZCODER_CERT_THUMBPRINT=A1B2C3D4E5F6...
set ZCODER_PFX=C:\certs\zcoder.pfx
set ZCODER_PFX_PASSWORD=your_password
```

### PowerShell Parameters
```powershell
# Thumbprint (cert in Windows Store)
.\build_installer.ps1 -SignCertificate "A1B2C3D4E5F6..."

# PFX file
.\build_installer.ps1 -PfxPath "C:\certs\zcoder.pfx" -PfxPassword "secret"

# Skip signing (dev)
.\build_installer.ps1 -SkipSign
```

## Dependency Handling

The installer checks for:
1. **mpv** - Required for audio playback
2. **yt-dlp** - Required for YouTube playback

If missing, installer shows download links:
- mpv: https://mpv.io/installation/
- yt-dlp: https://github.com/yt-dlp/yt-dlp/releases/latest

## Configuration

After install, user edits:
```
%PROGRAMFILES%\ZChat\config.toml
```

Example:
```toml
bot_nick = "MyBot"
admins = ["MyNick"]
prefix = "!"
music_dirs = ["C:\Music"]
autoplay_sources = ["https://youtube.com/playlist?list=..."]
```

## Build Integration

Add to `build.bat` or `full-build-exe.bat`:
```bat
call installer\build_installer.bat
```

Or in CI/CD:
```yaml
- name: Build Installer
  run: |
    cd installer
    .\build_installer.ps1 -Version "${{ github.ref_name }}" -SignCertificate "${{ secrets.CERT_THUMBPRINT }}"
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| NSIS not found | Install NSIS 3.0+ from nsis.sourceforge.io |
| Signing fails | Verify cert in Windows Store (CurrentUser\My or LocalMachine\My) |
| mpv not found | Install via `winget install mpv` or add to PATH |
| yt-dlp not found | Place `yt-dlp.exe` next to `mpv.exe` or install via pip |
| Installer won't run | Run as Administrator |
| Components missing | Run `full-build-exe.bat` first |

## License

See [LICENSE.txt](LICENSE.txt)