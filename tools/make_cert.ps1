<#  Create a SELF-SIGNED code-signing certificate for ZEAZDEV COMPANY LIMITED (ZCoder) (3 years).
    It signs the exe, but Windows/SmartScreen only trusts it on machines where you import the .cer
    (Trusted Root + Trusted Publishers). Public distribution needs a real certificate (OV/EV or Azure Trusted Signing).
    usage: powershell -ExecutionPolicy Bypass -File tools\make_cert.ps1 [-Subject "CN=..."] #>
param([string]$Subject = "CN=ZEAZDEV COMPANY LIMITED, O=ZEAZDEV COMPANY LIMITED, C=TH", [string]$OutDir = ".")
$ErrorActionPreference = "Stop"
$c = New-SelfSignedCertificate -Type CodeSigningCert -Subject $Subject -KeyAlgorithm RSA -KeyLength 3072 `
     -HashAlgorithm SHA256 -KeyExportPolicy Exportable -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears(3)
$cer = Join-Path $OutDir "zcoder-codesign.cer"
Export-Certificate -Cert $c -FilePath $cer | Out-Null
"Created $($c.Subject)"
"Thumbprint: $($c.Thumbprint)"
"Public cert: $cer  (import to trust: Import-Certificate -FilePath $cer -CertStoreLocation Cert:\CurrentUser\Root ; ...\TrustedPublisher)"
"Sign with:   set ZCODER_CERT_THUMBPRINT=$($c.Thumbprint) then build_exe.bat"
