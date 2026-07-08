# One-shot installer for the AI Editor VS Code extension, straight from GitHub
# Releases -- no Marketplace/Open VSX publish required.
#
#   iwr https://raw.githubusercontent.com/pradeepA2125/shadow-forge/main/install.ps1 -useb | iex
#
# Downloads the .vsix attached to the latest GitHub Release and installs it
# via `code --install-extension`. Set $env:CRUCIBLE_INSTALL_REPO or
# $env:CRUCIBLE_INSTALL_CODE_BIN to target a fork or a non-default editor
# binary (code-insiders, cursor, ...).
$ErrorActionPreference = "Stop"

$Repo = if ($env:CRUCIBLE_INSTALL_REPO) { $env:CRUCIBLE_INSTALL_REPO } else { "pradeepA2125/shadow-forge" }
$ApiUrl = "https://api.github.com/repos/$Repo/releases/latest"

function Find-CodeBin {
    if ($env:CRUCIBLE_INSTALL_CODE_BIN) {
        $cmd = Get-Command $env:CRUCIBLE_INSTALL_CODE_BIN -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
        throw "CRUCIBLE_INSTALL_CODE_BIN='$($env:CRUCIBLE_INSTALL_CODE_BIN)' not found on PATH"
    }
    foreach ($bin in @("code", "code-insiders", "cursor")) {
        $cmd = Get-Command $bin -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw "no VS Code CLI found on PATH (looked for: code, code-insiders, cursor). Install the 'code' shell command first (VS Code: Ctrl+Shift+P -> 'Shell Command: Install code command in PATH'), then re-run this script."
}

$CodeBin = Find-CodeBin
Write-Host "==> using editor CLI: $CodeBin"

Write-Host "==> looking up latest release of $Repo"
$Release = Invoke-RestMethod -Uri $ApiUrl -Headers @{ "User-Agent" = "ai-editor-installer" }

$Asset = $Release.assets | Where-Object { $_.name -like "*.vsix" } | Select-Object -First 1
if (-not $Asset) { throw "latest release has no .vsix asset attached" }

Write-Host "==> found $($Release.tag_name): $($Asset.name)"

$WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $WorkDir | Out-Null
$VsixPath = Join-Path $WorkDir $Asset.name

try {
    Write-Host "==> downloading vsix"
    Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $VsixPath

    Write-Host "==> installing into $CodeBin"
    & $CodeBin --install-extension $VsixPath --force

    Write-Host "==> done. Open (or reload) VS Code, open a folder, and the AI Editor setup wizard will guide you through the rest."
} finally {
    Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
}
