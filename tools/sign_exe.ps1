<#  Sign all *.exe files under dist\ (Authenticode, SHA256 + timestamp) for ZCoder / ZEAZDEV COMPANY LIMITED.
    Pick ONE certificate source:
      -Pfx C:\path\cert.pfx [-Password ...]   (or env ZCODER_PFX / AUTODJ_PFX)
      -Thumbprint <hex>                        (or env ZCODER_CERT_THUMBPRINT / AUTODJ_CERT_THUMBPRINT; cert in CurrentUser\My / LocalMachine\My)
    Optional: -SearchPath <path>, -TimestampServer <url> #>
param(
  [string]$SearchPath = "dist",
  [string]$Pfx = $(if ($env:ZCODER_PFX) { $env:ZCODER_PFX } else { $env:AUTODJ_PFX }),
  [string]$Password = $(if ($env:ZCODER_PFX_PASSWORD) { $env:ZCODER_PFX_PASSWORD } else { $env:AUTODJ_PFX_PASSWORD }),
  [string]$Thumbprint = $(if ($env:ZCODER_CERT_THUMBPRINT) { $env:ZCODER_CERT_THUMBPRINT } else { $env:AUTODJ_CERT_THUMBPRINT }),
  [string]$TimestampServer = "http://timestamp.digicert.com"
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $SearchPath)) { throw "not found: $SearchPath (build first)" }

if ($Pfx) {
  $sec = if ($Password) { ConvertTo-SecureString $Password -AsPlainText -Force } else { $null }
  $cert = Get-PfxCertificate -FilePath $Pfx -Password $sec
} elseif ($Thumbprint) {
  $cert = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My | Where-Object Thumbprint -eq ($Thumbprint -replace "\s", "") | Select-Object -First 1
  if (-not $cert) { throw "certificate $Thumbprint not found in the CurrentUser/LocalMachine My store" }
} else { throw "no certificate: pass -Pfx or -Thumbprint (see tools\make_cert.ps1 for a self-signed one)" }

$exes = Get-ChildItem -Path $SearchPath -Filter "*.exe" -Recurse
if (-not $exes) { throw "no .exe files found under $SearchPath" }

foreach ($exe in $exes) {
  $File = $exe.FullName
  $r = Set-AuthenticodeSignature -FilePath $File -Certificate $cert -HashAlgorithm SHA256 -TimestampServer $TimestampServer
  "{0}: {1} ({2})" -f $File, $r.Status, $r.StatusMessage
  if ($r.Status -ne "Valid" -or $null -eq $r.SignerCertificate) {
    throw ("Authenticode signing failed for {0}: {1} ({2})" -f $File, $r.Status, $r.StatusMessage)
  }
}
"Successfully processed signing for all .exe files under $SearchPath."
