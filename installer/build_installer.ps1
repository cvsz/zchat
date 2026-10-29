<#
.SYNOPSIS
    Build ZChat NSIS Installer for Windows 11

.DESCRIPTION
    Builds a production-ready NSIS installer for ZChat/AutoDJ with all components,
    dependencies, code signing, and verification.

.PARAMETER Version
    Version string for the installer (default: 2.20.0)

.PARAMETER SignCertificate
    Certificate thumbprint for code signing (optional)

.PARAMETER PfxPath
    Path to PFX file for code signing (alternative to thumbprint)

.PARAMETER PfxPassword
    Password for PFX file

.PARAMETER OutputDir
    Output directory for installer (default: ..\dist\installer)

.PARAMETER SkipSign
    Skip code signing step

.PARAMETER SkipBuild
    Skip NSIS build, only sign existing installer

.EXAMPLE
    .\build_installer.ps1 -Version "2.20.0" -SignCertificate "A1B2C3D4E5F6..."

.EXAMPLE
    .\build_installer.ps1 -PfxPath "C:\certs\zcoder.pfx" -PfxPassword "secret"

.EXAMPLE
    .\build_installer.ps1 -SkipSign -OutputDir "C:\Builds\ZChat"
#>

[CmdletBinding()]
param(
    [string]$Version = "2.20.0",
    [string]$SignCertificate = $env:ZCODER_CERT_THUMBPRINT,
    [string]$PfxPath,
    [string]$PfxPassword = $env:ZCODER_PFX_PASSWORD,
    [string]$OutputDir,
    [switch]$SkipSign,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$SrcRoot = Resolve-Path "$ScriptDir\.."
$InstallerDir = $ScriptDir
$NsiFile = "$InstallerDir\ZChatInstaller.nsi"
$LicenseFile = "$InstallerDir\LICENSE.txt"
$StagingDir = "$InstallerDir\staging"
$OutDir = if ($OutputDir) { Resolve-Path $OutputDir } else { "$SrcRoot\dist\installer" }
$InstallerExe = "$OutDir\ZChat-$Version-Setup.exe"
$NsiPath = "${env:ProgramFiles}\NSIS\makensis.exe"
if (-not (Test-Path $NsiPath)) { $NsiPath = "${env:ProgramFiles(x86)}\NSIS\makensis.exe" }

# ------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------
function Write-Header($msg) {
    Write-Host ("=" * 60)
    Write-Host $msg
    Write-Host ("=" * 60)
}

function Write-Step($msg) {
    Write-Host "`n=== $msg ==="
}

function Invoke-Cmd($cmd, $args) {
    Write-Host "  > $cmd $args" -ForegroundColor Gray
    $result = & $cmd $args
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE: $cmd $args"
    }
    return $result
}

# ------------------------------------------------------------
# Prerequisites Check
# ------------------------------------------------------------
Write-Header "ZChat Installer Build v$Version"

if (-not (Test-Path $NsiPath)) {
    throw "NSIS not found at $NsiPath. Install from https://nsis.sourceforge.io/"
}
Write-Host "NSIS found: $NsiPath"

if (-not (Test-Path $NsiFile)) {
    throw "NSI file not found: $NsiFile"
}
Write-Host "NSI file found: $NsiFile"

if (-not $SkipBuild) {
    if (-not (Test-Path "$SrcRoot\dist\auto-dj\AutoDJ.exe")) {
        Write-Warning "AutoDJ.exe not found at $SrcRoot\dist\auto-dj\AutoDJ.exe"
        Write-Warning "Run full-build.bat first for complete installer"
    }
}

# ------------------------------------------------------------
# Prepare Output Directory
# ------------------------------------------------------------
Write-Step "Preparing Output Directory"
if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
}
Write-Host "Output: $OutDir"

# ------------------------------------------------------------
# Prepare Staging Directory
# ------------------------------------------------------------
if (-not $SkipBuild) {
    Write-Step "Preparing Staging Directory"
    if (Test-Path $StagingDir) { Remove-Item -Recurse -Force $StagingDir }
    New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null

    # Copy built applications
    $components = @(
        "auto-dj",
        "room-control",
        "chat-im-private",
        "status-random",
        "status-marquee",
        "status-changer",
        "im-autoreply",
        "web-status",
        "cloudbot-manager",
        "name-color-changer",
        "icon-changer",
        "name-changer",
        "line",
        "zchat-client"
    )

    foreach ($comp in $components) {
        $src = "$SrcRoot\dist\$comp"
        $dst = "$StagingDir\dist\$comp"
        if (Test-Path $src) {
            Write-Host "  Copying $comp..."
            Copy-Item $src $dst -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    # Support files
    $files = @(
        "$SrcRoot\app.ico",
        "$SrcRoot\config.example.toml",
        "$SrcRoot\README.md"
    )
    foreach ($f in $files) {
        if (Test-Path $f) {
            Copy-Item $f "$StagingDir\" -Force
        }
    }

    $dirs = @("tools", "assets")
    foreach ($d in $dirs) {
        $src = "$SrcRoot\$d"
        $dst = "$StagingDir\$d"
        if (Test-Path $src) {
            Copy-Item $src $dst -Recurse -Force
        }
    }

    # License
    Copy-Item $LicenseFile "$StagingDir\LICENSE.txt" -Force
}

# ------------------------------------------------------------
# Build NSIS Installer
# ------------------------------------------------------------
if (-not $SkipBuild) {
    Write-Step "Building NSIS Installer"
    Set-Location $InstallerDir

    $nsiArgs = @(
        "/V2"
        "/NOCD"
        "/X", 'SetCompressor /SOLID lzma'
        "/DVERSION=$Version"
        "/DOUTPUT=`"$InstallerExe`""
        "`"$NsiFile`""
    )

    Write-Host "  Building installer..."
    & $NsiPath $nsiArgs
    if ($LASTEXITCODE -ne 0) {
        throw "NSIS build failed with exit code $LASTEXITCODE"
    }
    Write-Host "  Build successful"
}

# ------------------------------------------------------------
# Verify Installer Exists
# ------------------------------------------------------------
if (-not (Test-Path $InstallerExe)) {
    throw "Installer not created at $InstallerExe"
}
$size = (Get-Item $InstallerExe).Length
Write-Host "`nInstaller created: $InstallerExe"
Write-Host "Size: $([math]::Round($size/1MB, 2)) MB"

# ------------------------------------------------------------
# Code Signing
# ------------------------------------------------------------
if (-not $SkipSign -and (Test-Path $InstallerExe)) {
    Write-Step "Code Signing"

    if ($SignCertificate) {
        Write-Host "Signing with certificate thumbprint: $SignCertificate"
        $signArgs = @(
            "sign",
            "/tr", "http://timestamp.digicert.com",
            "/td", "sha256",
            "/fd", "sha256",
            "/sha1", $SignCertificate,
            "`"$InstallerExe`""
        )
        Write-Host "  > signtool $signArgs"
        & signtool $signArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Signing failed with thumbprint"
        } else {
            Write-Host "  Signing successful"
        }
    }
    elseif ($PfxPath -and (Test-Path $PfxPath)) {
        Write-Host "Signing with PFX: $PfxPath"
        $signArgs = @(
            "sign",
            "/f", "`"$PfxPath`"",
            "/p", $PfxPassword,
            "/tr", "http://timestamp.digicert.com",
            "/td", "sha256",
            "/fd", "sha256",
            "`"$InstallerExe`""
        )
        Write-Host "  > signtool $signArgs"
        & signtool $signArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Signing failed with PFX"
        } else {
            Write-Host "  Signing successful"
        }
    }
    elseif ($env:ZCODER_CERT_THUMBPRINT) {
        Write-Host "Signing with env ZCODER_CERT_THUMBPRINT"
        $signArgs = @(
            "sign",
            "/tr", "http://timestamp.digicert.com",
            "/td", "sha256",
            "/fd", "sha256",
            "/sha1", $env:ZCODER_CERT_THUMBPRINT,
            "`"$InstallerExe`""
        )
        & signtool $signArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Signing failed"
        } else {
            Write-Host "  Signing successful"
        }
    }
    else {
        Write-Warning "No signing certificate configured. Set ZCODER_CERT_THUMBPRINT or use -SignCertificate/-PfxPath"
    }

    # Verify signature
    Write-Host "  Verifying signature..."
    & signtool verify /pa /v "`"$InstallerExe`""
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Signature: VALID" -ForegroundColor Green
    } else {
        Write-Warning "Signature: NOT SIGNED or INVALID"
    }
}

# ------------------------------------------------------------
# Cleanup Staging
# ------------------------------------------------------------
if (Test-Path $StagingDir) {
    Remove-Item -Recurse -Force $StagingDir
}

# ------------------------------------------------------------
# Summary
# ------------------------------------------------------------
Write-Header "BUILD SUMMARY"
Write-Host "Installer: $InstallerExe"
Write-Host "Version: $Version"
Write-Host "Size: $([math]::Round((Get-Item $InstallerExe).Length / 1MB, 2)) MB"

if (Test-Path $InstallerExe) {
    $sig = & signtool verify /pa "`"$InstallerExe`"" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Signature: VALID" -ForegroundColor Green
    } else {
        Write-Host "Signature: NOT SIGNED" -ForegroundColor Yellow
    }
}

Write-Host "`nUsage:"
Write-Host "  Silent install:        `$InstallerExe /S /D=C:\Path\To\ZChat"
Write-Host "  Select components:     `$InstallerExe /S /COMPONENTS=`"AutoDJ,RoomControl`""
Write-Host "  Silent uninstall:      `"`"C:\Path\To\ZChat\uninstall.exe`" /S`""
Write-Host ""
Write-Host "Build completed successfully!" -ForegroundColor Green