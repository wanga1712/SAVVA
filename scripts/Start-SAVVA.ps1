$ErrorActionPreference = "Stop"

$remoteHost = "s13"
$localPort = 8765
$healthUrl = "http://127.0.0.1:$localPort/api/health"
$runtimeDir = Join-Path $env:LOCALAPPDATA "SAVVA"
$pidPath = Join-Path $runtimeDir "tunnel.pid"
$stdoutPath = Join-Path $runtimeDir "tunnel.out.log"
$stderrPath = Join-Path $runtimeDir "tunnel.err.log"
$sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
$chromeCandidates = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

function Get-SavvaHealth {
    try {
        return Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3 -ErrorAction Stop
    }
    catch {
        return $null
    }
}

function Show-LauncherError {
    param([string]$Message)
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.Forms.MessageBox]::Show(
        $Message,
        "SAVVA",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
}

function Test-LocalPortOpen {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync("127.0.0.1", $localPort)
        return $task.Wait(700) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

try {
    $health = Get-SavvaHealth
    if ($null -eq $health) {
        & $sshPath -o BatchMode=yes -o ConnectTimeout=10 $remoteHost "systemctl --user start savva.service"
        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось запустить SAVVA на $remoteHost через Tailscale."
        }

        if (Test-LocalPortOpen) {
            throw "Локальный порт $localPort уже занят другим приложением."
        }

        $tunnel = Start-Process `
            -FilePath $sshPath `
            -ArgumentList @(
                "-N",
                "-o", "BatchMode=yes",
                "-o", "ExitOnForwardFailure=yes",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=3",
                "-L", "127.0.0.1:${localPort}:127.0.0.1:${localPort}",
                $remoteHost
            ) `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -PassThru

        $health = $null
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            Start-Sleep -Milliseconds 500
            $health = Get-SavvaHealth
            if ($null -ne $health) {
                break
            }
            if ($tunnel.HasExited) {
                break
            }
        }

        if ($null -eq $health) {
            if (-not $tunnel.HasExited) {
                Stop-Process -Id $tunnel.Id -Force
            }
            $detail = ""
            if (Test-Path -LiteralPath $stderrPath) {
                $detail = (Get-Content -LiteralPath $stderrPath -Tail 8) -join "`n"
            }
            throw "SSH-туннель к $remoteHost не поднялся.`n$detail"
        }
        Set-Content -LiteralPath $pidPath -Value $tunnel.Id -Encoding ascii
    }

    if (-not $health.api_key_configured -and $health.provider -ne "ollama") {
        throw "Backend SAVVA на $remoteHost запущен, но OPENROUTER_API_KEY не настроен."
    }

    $chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $chrome) {
        $chromeCommand = Get-Command chrome.exe -ErrorAction SilentlyContinue
        if ($chromeCommand) {
            $chrome = $chromeCommand.Source
        }
    }
    if (-not $chrome) {
        throw "Google Chrome не найден."
    }

    $appUrl = $healthUrl -replace "/api/health$", ""
    Start-Process -FilePath $chrome -ArgumentList @("--app=$appUrl")
}
catch {
    Show-LauncherError $_.Exception.Message
    exit 1
}
