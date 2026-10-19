# Fetch the pinned upstream GUI-test fixtures into an ignored test-only cache.
# This does not build, execute, or inject the fetched applications.
$ErrorActionPreference = 'Stop'
$pin = @{}
Get-Content (Join-Path $PSScriptRoot 'pywinauto_testapps.pin') | ForEach-Object {
    $key, $value = $_ -split '=', 2
    $pin[$key] = $value
}

if (-not $pin.repo -or $pin.commit -notmatch '^[0-9a-f]{40}$') {
    throw 'Invalid PywinautoTestapps fixture pin.'
}

$destination = Join-Path $PSScriptRoot '_cache\PywinautoTestapps'
if (-not (Test-Path $destination)) {
    git clone --no-checkout $pin.repo $destination
    if ($LASTEXITCODE -ne 0) { throw 'Could not clone pinned test-app source.' }
}
git -C $destination fetch --quiet origin $pin.commit
if ($LASTEXITCODE -ne 0) { throw 'Could not fetch pinned test-app commit.' }
git -C $destination checkout --quiet --detach $pin.commit
if ($LASTEXITCODE -ne 0) { throw 'Could not check out pinned test-app commit.' }

$head = (git -C $destination rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $head -ne $pin.commit) {
    throw "Fixture pin mismatch: expected $($pin.commit), got $head"
}
Write-Output "Pinned PywinautoTestapps at $head"
