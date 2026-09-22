#Requires -Version 5.1
$ErrorActionPreference='Stop'

$root=$PSScriptRoot
$python=Join-Path $root '.venv\Scripts\python.exe'
$app=Join-Path $root 'app.py'
$runtime=Join-Path $root 'data\desktop-runtime.json'
$logs=Join-Path $root 'data\desktop-logs'
$electron='D:\LocalAI\Faustus\desktop\node_modules\electron\dist\electron.exe'
$shell='D:\LocalAI\Faustus\desktop\app-shell.cjs'
$icon='C:\Users\luism\Desktop\Proyectos independientes\Icons\Platos Hoard.png'
$url='http://127.0.0.1:5000/editor'
$title="Plato's Hoard"
$ownsRuntime=$false

function Test-Alive([int]$ProcessId) {
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Stop-Tree([int]$ProcessId) {
    if ($ProcessId -le 0 -or -not (Test-Alive $ProcessId)) { return }
    & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
}

function Show-LaunchError([string]$Message) {
    try {
        Add-Type -AssemblyName PresentationFramework -ErrorAction Stop
        [System.Windows.MessageBox]::Show($Message,$title,'OK','Error') | Out-Null
    } catch { }
}

New-Item -ItemType Directory -Force -Path $logs | Out-Null
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$launcherLog=Join-Path $logs "launcher-$stamp.log"
$serverOut=Join-Path $logs "server-$stamp.log"
$serverErr=Join-Path $logs "server-$stamp.err.log"

try {
    if (-not (Test-Path -LiteralPath $python)) { throw 'Falta .venv. Ejecuta install.bat una vez.' }
    if (-not (Test-Path -LiteralPath $app)) { throw 'No se encuentra app.py.' }
    if (-not (Test-Path -LiteralPath $electron)) { throw 'No se encuentra el runtime de escritorio de Faustus.' }
    if (-not (Test-Path -LiteralPath $shell)) { throw 'No se encuentra la ventana de escritorio de Faustus.' }

    # A second double-click raises the existing window instead of starting a
    # second server/window pair on the same port.
    if (Test-Path -LiteralPath $runtime) {
        try {
            $old=Get-Content -LiteralPath $runtime -Raw | ConvertFrom-Json
            if ($old.desktop_pid -and $old.backend_pid -and
                (Test-Alive ([int]$old.desktop_pid)) -and (Test-Alive ([int]$old.backend_pid))) {
                try { (New-Object -ComObject WScript.Shell).AppActivate($title) | Out-Null } catch { }
                exit 0
            }
        } catch { }
        & (Join-Path $root 'Stop-Platos-Hoard.ps1') -Quiet
    }

    # A stale backend left by Faustus is adopted only long enough to stop it.
    # The stop script verifies the command belongs to this exact checkout.
    & (Join-Path $root 'Stop-Platos-Hoard.ps1') -Quiet -BackgroundOnly
    $occupied=Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
    if ($occupied) { throw 'El puerto 5000 lo usa otro programa y no es seguro cerrarlo.' }

    $backend=Start-Process -FilePath $python -ArgumentList @('app.py') `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr

    $ready=$false
    for ($i=0;$i -lt 80;$i++) {
        Start-Sleep -Milliseconds 500
        if ($backend.HasExited) { throw "El servidor de Plato termino al arrancar. Mira $serverErr" }
        try {
            $response=Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -lt 500) { $ready=$true;break }
        } catch { }
    }
    if (-not $ready) { throw "Plato no respondio en 40 segundos. Mira $serverErr" }

    $electronArgs=@(
        ('"'+$shell+'"'),
        ('--url='+$url),
        ('--title="'+$title+'"'),
        ('--icon="'+$icon+'"'),
        '--slug=platos-hoard-standalone'
    )
    $desktop=Start-Process -FilePath $electron -ArgumentList $electronArgs `
        -WorkingDirectory $root -WindowStyle Normal -PassThru

    @{
        supervisor_pid=$PID
        backend_pid=$backend.Id
        desktop_pid=$desktop.Id
        started_at=[DateTimeOffset]::Now.ToUnixTimeSeconds()
        root=$root
    } | ConvertTo-Json | Set-Content -LiteralPath $runtime -Encoding UTF8
    $ownsRuntime=$true

    "[$(Get-Date -Format o)] backend=$($backend.Id) desktop=$($desktop.Id)" | Set-Content -LiteralPath $launcherLog
    Wait-Process -Id $desktop.Id -ErrorAction SilentlyContinue
} catch {
    $message=$_.Exception.Message
    "[$(Get-Date -Format o)] ERROR $message" | Add-Content -LiteralPath $launcherLog
    Show-LaunchError $message
} finally {
    if ($backend) { Stop-Tree $backend.Id }
    if ($ownsRuntime) { Remove-Item -LiteralPath $runtime -Force -ErrorAction SilentlyContinue }
}
