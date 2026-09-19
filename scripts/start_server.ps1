<#
Quick start / stop for the Pom-Pom Agent server.

Usage (run from the repo root, e.g. D:\Pom-Pom):
  powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1              # foreground, Ctrl+C to stop
  powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -Debug       # DEBUG mode (diagnostics + /api/debug/*)
  powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -Background  # hidden window, logs to test-results/
  powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -Public      # 本机服务 + 公网隧道（打印公网地址）
  powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -Stop        # stop the running server
  powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -BindHost 0.0.0.0   # reachable on the LAN
  powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -Port 8080   # custom port

Notes:
  - Any previous instance on the same port (this project only) is stopped first;
  - PYTHONIOENCODING=utf-8 is set so Chinese logs never garble;
  - Background mode writes test-results/server-debug.out.log and .err.log.
  - This file is intentionally ASCII-only: Windows PowerShell 5.1 reads BOM-less
    .ps1 files as GBK, which corrupts non-ASCII string literals.
#>
param(
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1",
    [switch]$Debug,
    [switch]$Background,
    [switch]$Public,
    [switch]$Stop,
    [switch]$NoStopExisting,
    [string]$Token = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$ResultDir = Join-Path $Root "test-results"
$UvicornArgs = @("-m", "uvicorn", "app.main:app", "--host", $BindHost, "--port", "$Port")
$ProbeHost = if ($BindHost -eq "0.0.0.0" -or $BindHost -eq "::") { "127.0.0.1" } else { $BindHost }
$TunnelExe = Join-Path $Root "tools\cloudflared.exe"
$TunnelOut = Join-Path $ResultDir "cloudflared.out.log"
$TunnelErr = Join-Path $ResultDir "cloudflared.err.log"
$ServerPidFile = Join-Path $ResultDir "pom-server.pid"
$TunnelPidFile = Join-Path $ResultDir "pom-tunnel.pid"

function Test-PomCommandLine([string]$CommandLine, [string]$Kind) {
    # Strict on purpose: a command line that merely mentions this directory (any
    # python started from here, ours or not) must never be treated as the server.
    if (-not $CommandLine) { return $false }
    if ($CommandLine.IndexOf($Root, [StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
    if ($Kind -eq "tunnel") {
        return ($CommandLine -like "*cloudflared*") -and
        ($CommandLine -like "*--url http://127.0.0.1:$Port*")
    }
    if ($CommandLine -notlike "*uvicorn*") { return $false }
    if ($CommandLine -notlike "*app.main:app*") { return $false }
    # Exact port: "--port 80" must not answer for 8000.
    return [bool]($CommandLine -match ('--port\s+{0}(\s|$)' -f $Port))
}

function Test-PomOwned([int]$TargetId, [string]$Kind) {
    # A venv python re-execs the base interpreter, so the real listener may not
    # have the project path in its own command line -- check its launcher too.
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $TargetId" -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    if (Test-PomCommandLine $proc.CommandLine $Kind) { return $true }
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($proc.ParentProcessId)" -ErrorAction SilentlyContinue
    if (-not $parent -or $parent.Name -ne "python.exe") { return $false }
    return (Test-PomCommandLine $parent.CommandLine $Kind)
}

function Get-RecordedPids([string]$File) {
    if (-not (Test-Path $File)) { return @() }
    $raw = Get-Content $File -ErrorAction SilentlyContinue | Select-Object -First 1
    $value = 0
    if ([int]::TryParse($raw, [ref]$value) -and $value -gt 0) { return @($value) }
    return @()
}

function Test-PortBusy([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect("127.0.0.1", $Port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Stop-PomServer {
    $stopped = 0
    foreach ($entry in @(
            @{ File = $ServerPidFile; Label = "server"; Kind = "server" },
            @{ File = $TunnelPidFile; Label = "tunnel"; Kind = "tunnel" })) {
        foreach ($targetId in Get-RecordedPids $entry.File) {
            if (Test-PomOwned $targetId $entry.Kind) {
                Write-Host "Stopping $($entry.Label) process $targetId ..."
                Stop-Process -Id $targetId -Force -ErrorAction SilentlyContinue
                $stopped++
            } else {
                Write-Host "Pid $targetId in $($entry.Label) pid file is not ours any more; leaving it alone."
            }
        }
        Remove-Item -LiteralPath $entry.File -ErrorAction SilentlyContinue
    }
    # A launcher may have left the real listener behind; take it back only if its
    # command line still points at this project.
    Start-Sleep -Seconds 2
    if (Test-PortBusy $Port) {
        $owner = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty OwningProcess
        if ($owner -and (Test-PomOwned $owner "server")) {
            Write-Host "Stopping lingering listener $owner ..."
            Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
            $stopped++
            Start-Sleep -Seconds 1
        }
    }
    if ($stopped -eq 0) {
        Write-Host "No recorded Pom-Pom server to stop."
    }
}

function Start-PomTunnel([int]$Seconds = 45) {
    if (-not (Test-Path $TunnelExe)) {
        Write-Host "cloudflared not found at $TunnelExe (download it to tools\\cloudflared.exe)"
        return ""
    }
    New-Item -ItemType Directory -Force -Path $ResultDir | Out-Null
    $tunnelProc = Start-Process -FilePath $TunnelExe `
        -ArgumentList @("tunnel", "--url", "http://127.0.0.1:$Port", "--no-autoupdate") `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardOutput $TunnelOut `
        -RedirectStandardError $TunnelErr
    if ($tunnelProc) { $tunnelProc.Id | Out-File -FilePath $TunnelPidFile -Encoding ascii }
    for ($i = 0; $i -lt $Seconds; $i++) {
        Start-Sleep -Seconds 1
        foreach ($file in @($TunnelErr, $TunnelOut)) {
            if (-not (Test-Path $file)) { continue }
            $match = Select-String -Path $file -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($match) { return $match.Matches[0].Value }
        }
    }
    return ""
}

function Wait-Health([int]$Seconds = 60) {
    $url = "http://${ProbeHost}:$Port/api/health"
    for ($i = 0; $i -lt $Seconds; $i++) {
        try {
            $data = Invoke-RestMethod -Uri $url -TimeoutSec 5
            $key = if ($data.model_configured) { "configured" } else { "MISSING" }
            Write-Host "Health OK: db_chunks=$($data.db_chunks) model_key=$key"
            Write-Host "Open http://127.0.0.1:$Port/ (deep thinking toggle is next to the input box)."
            if ($BindHost -eq "0.0.0.0" -or $BindHost -eq "::") {
                $lan = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
                    Select-Object -First 1 -ExpandProperty IPAddress
                if ($lan) {
                    Write-Host "LAN devices (same Wi-Fi): http://${lan}:$Port/"
                }
            }
            return $true
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    Write-Host "Health check timed out after $Seconds s. See $ResultDir\server-debug.err.log"
    return $false
}

if ($Stop) {
    Stop-PomServer
    exit 0
}

if (-not (Test-Path $Python)) {
    Write-Host "Python not found at $Python"
    Write-Host "Create it first: python -m venv .venv; .venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

if (-not $NoStopExisting) {
    Stop-PomServer
}

if (Test-PortBusy $Port) {
    # An instance started by an older version has no pid file; accept it only if
    # its command line points at this project, otherwise leave it alone.
    $owner = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess
    if ($owner -and (Test-PomOwned $owner "server")) {
        Write-Host "Port $Port is held by this project (pid $owner); stopping it."
        Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

if (Test-PortBusy $Port) {
    Write-Host "Port $Port is already in use by a process that is not this project."
    Write-Host "Stop it yourself, or pick another port with -Port."
    exit 1
}

if ($Public) {
    # 隧道要在后台跑，所以 -Public 强制后台模式
    $Background = $true
}

$env:PYTHONIOENCODING = "utf-8"
if ($Token) {
    # Access token: without it anyone who has the public link can spend your quota.
    $env:POM_ACCESS_TOKEN = $Token
    Write-Host "Access token enabled. Share the link with #token=<your token> appended."
} else {
    $env:POM_ACCESS_TOKEN = ""
}
if ($Debug) {
    $env:DEBUG = "true"
} else {
    Remove-Item Env:\DEBUG -ErrorAction SilentlyContinue
}

$modeLabel = if ($Debug) { "DEBUG" } else { "normal" }
Write-Host "Starting Pom-Pom server ($modeLabel mode): http://${BindHost}:$Port"

Push-Location $Root
try {
    if ($Background) {
        New-Item -ItemType Directory -Force -Path $ResultDir | Out-Null
        $serverProc = Start-Process -FilePath $Python -ArgumentList $UvicornArgs -WindowStyle Hidden `
            -PassThru `
            -RedirectStandardOutput (Join-Path $ResultDir "server-debug.out.log") `
            -RedirectStandardError (Join-Path $ResultDir "server-debug.err.log")
        if ($serverProc) { $serverProc.Id | Out-File -FilePath $ServerPidFile -Encoding ascii }
        Wait-Health -Seconds 90 | Out-Null
        if ($Public) {
            Write-Host "Starting Cloudflare quick tunnel ..."
            $publicUrl = Start-PomTunnel -Seconds 60
            if ($publicUrl) {
                Write-Host "Public URL: $publicUrl" -ForegroundColor Cyan
                $healthy = $false
                for ($attempt = 1; $attempt -le 3 -and -not $healthy; $attempt++) {
                    try {
                        Invoke-RestMethod -Uri "$publicUrl/api/health" -TimeoutSec 20 | Out-Null
                        $healthy = $true
                    } catch {
                        if ($attempt -lt 3) { Start-Sleep -Seconds 5 }
                    }
                }
                if ($healthy) {
                    Write-Host "Public health check OK (share this link; it changes after every restart)." -ForegroundColor Green
                } else {
                    Write-Host "Tunnel is up (see $TunnelErr). If the link does not open from this machine,"
                    Write-Host "it is usually the local DNS lagging - outside visitors can still reach it."
                }
            } else {
                Write-Host "Tunnel started but no URL was found in the log; see $TunnelErr"
            }
        }
        Write-Host "Stop it with: powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -Stop"
    } else {
        & $Python @UvicornArgs
    }
} finally {
    Pop-Location
}
