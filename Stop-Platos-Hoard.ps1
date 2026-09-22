#Requires -Version 5.1
param([switch]$Quiet,[switch]$BackgroundOnly)
$ErrorActionPreference='Continue'

$root=$PSScriptRoot
$runtime=Join-Path $root 'data\desktop-runtime.json'
$currentPid=$PID
$targets=New-Object System.Collections.Generic.List[int]

function Add-Target($ProcessId) {
    if ($ProcessId -and [int]$ProcessId -ne $currentPid -and -not $targets.Contains([int]$ProcessId)) {
        $targets.Add([int]$ProcessId)
    }
}

function Get-Info([int]$ProcessId) {
    Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
}

function Test-PlatoBackend($Process) {
    if (-not $Process -or -not $Process.CommandLine) { return $false }
    $command=$Process.CommandLine.ToLowerInvariant()
    return $command.Contains($root.ToLowerInvariant()) -and $command.Contains('app.py')
}

if (Test-Path -LiteralPath $runtime) {
    try {
        $saved=Get-Content -LiteralPath $runtime -Raw | ConvertFrom-Json
        if (-not $BackgroundOnly) { Add-Target $saved.desktop_pid }
        Add-Target $saved.backend_pid
        if (-not $BackgroundOnly) { Add-Target $saved.supervisor_pid }
    } catch { }
}

# Recover servers started from Faustus/Claude or left from an older launcher.
foreach ($listener in @(Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue)) {
    $process=Get-Info ([int]$listener.OwningProcess)
    if (-not (Test-PlatoBackend $process)) { continue }
    $rootProcess=$process
    $parent=Get-Info ([int]$process.ParentProcessId)
    if (Test-PlatoBackend $parent) { $rootProcess=$parent }
    Add-Target $rootProcess.ProcessId
}

if (-not $BackgroundOnly) {
    # The standalone window does not listen on a port, so identify it by its
    # unique slug. This never matches Faustus itself or another app shell.
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains('--slug=platos-hoard-standalone') } |
        ForEach-Object { Add-Target $_.ProcessId }
}

$stopped=New-Object System.Collections.Generic.List[int]
foreach ($target in $targets) {
    if (-not (Get-Process -Id $target -ErrorAction SilentlyContinue)) { continue }
    & taskkill.exe /PID $target /T /F 2>$null | Out-Null
    if (-not (Get-Process -Id $target -ErrorAction SilentlyContinue)) { $stopped.Add($target) }
}

if (-not $BackgroundOnly) { Remove-Item -LiteralPath $runtime -Force -ErrorAction SilentlyContinue }
if (-not $Quiet) {
    if ($stopped.Count) { Write-Host ("Plato's Hoard detenido. PIDs: "+($stopped -join ', ')) }
    else { Write-Host "Plato's Hoard no estaba abierto." }
}
