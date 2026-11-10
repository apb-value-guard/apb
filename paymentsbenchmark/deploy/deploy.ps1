# Deploys dist/ to paymentsbenchmark.com on the Oracle VM.
# Usage (from anywhere):  powershell -File C:\dev\apb\paymentsbenchmark\deploy\deploy.ps1
# Rebuild first if the content changed:  python build.py
#
# Safe to rerun. Each run is a new release under /var/www/paymentsbenchmark/releases
# (newest 5 kept); the Nginx site file is only written on the first run.

$ErrorActionPreference = "Stop"

$VmIp   = "132.145.20.183"
$VmUser = "ubuntu"
$SshKey = if ($env:APB_SSH_KEY) { $env:APB_SSH_KEY } else { "$env:USERPROFILE\OneDrive\OpenClaw\OpenClaw.ppk" }
$Plink  = "C:\Program Files\PuTTY\plink.exe"
$Pscp   = "C:\Program Files\PuTTY\pscp.exe"

$Root   = Split-Path -Parent $PSScriptRoot
$Dist   = Join-Path $Root "dist"
$Tar    = Join-Path $env:TEMP "paymentsbenchmark-site.tar.gz"

if (-not (Test-Path $SshKey)) { throw "SSH key not found: $SshKey (set APB_SSH_KEY)" }
if (-not (Test-Path (Join-Path $Dist "index.html"))) { throw "dist/index.html missing. Run: python build.py" }

Write-Host "== Packing dist/"
if (Test-Path $Tar) { Remove-Item $Tar }
& tar.exe -czf $Tar --exclude=vercel.json -C $Dist .
if ($LASTEXITCODE -ne 0) { throw "tar failed" }

Write-Host "== Uploading"
& $Pscp -batch -q -i $SshKey $Tar "$PSScriptRoot\nginx-paymentsbenchmark.conf" "$PSScriptRoot\remote-install.sh" "${VmUser}@${VmIp}:/home/ubuntu/"
if ($LASTEXITCODE -ne 0) { throw "upload failed" }

Write-Host "== Installing on the VM"
& $Plink -batch -no-antispoof -i $SshKey "${VmUser}@${VmIp}" `
  "sed -i 's/\r$//' ~/remote-install.sh && bash ~/remote-install.sh ~/paymentsbenchmark-site.tar.gz ~/nginx-paymentsbenchmark.conf && rm -f ~/remote-install.sh"
if ($LASTEXITCODE -ne 0) { throw "remote install failed (rolled back)" }

Remove-Item $Tar
Write-Host "== Deployed. https://paymentsbenchmark.com"
