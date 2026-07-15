<#
.SYNOPSIS
    Automatic installation/update of Windows CAD Server (windows_cad_server.py)
    and starting it via the Startup folder so it runs as the current user.
#>

$PortalIp = $args[0]
if (-not $PortalIp) {
    Write-Host "Please provide the portal IP and port (e.g., 192.168.88.10:8080) as an argument."
    exit 1
}

$CadScriptUrl  = "http://$PortalIp/api/cad-server-script"
$ServiceName   = 'CadServer'
$WorkDir       = 'C:\CadServer'
$PythonPath    = 'C:\Python312\python.exe'
$NssmDir       = "$env:ProgramFiles\NSSM"

function Write-Info ($msg) { Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Warn ($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-ErrorExit ($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red; exit 1 }

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-ErrorExit "Script requires administrator privileges. Open PowerShell as Administrator."
}

Write-Info "Creating working directory $WorkDir"
if (-not (Test-Path $WorkDir)) { New-Item -ItemType Directory -Path $WorkDir | Out-Null }

$ScriptPath = Join-Path $WorkDir 'windows_cad_server.py'
Write-Info "Downloading server script from $CadScriptUrl..."
try {
    Invoke-WebRequest -Uri $CadScriptUrl -OutFile $ScriptPath -UseBasicParsing -ErrorAction Stop
} catch {
    Write-ErrorExit "Failed to download script. Make sure $PortalIp is accessible. Error: $_"
}

if (-not (Test-Path $PythonPath)) {
    $pyInPath = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($pyInPath) { 
        $PythonPath = $pyInPath
        Write-Info "Python found in PATH: $PythonPath" 
    } else {
        Write-Info "Python not found - downloading and installing Python 3.12..."
        $installer = "$env:TEMP\python-3.12.0-amd64.exe"
        Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.0/python-3.12.0-amd64.exe' -OutFile $installer -UseBasicParsing
        Write-Info "Running silent install (this may take a few minutes)..."
        $process = Start-Process -FilePath $installer -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 TargetDir=C:\Python312" -Wait -PassThru
        if ($process.ExitCode -ne 0) { Write-Warn "Python installer returned code $($process.ExitCode)" }
        $PythonPath = 'C:\Python312\python.exe'
        if (-not (Test-Path $PythonPath)) { Write-ErrorExit "Failed to install Python." }
        Write-Info "Python installed."
    }
} else { Write-Info "Python found: $PythonPath" }

Write-Info "Installing Python dependencies (fastapi, uvicorn, python-multipart, pywin32)..."
$pipProcess = Start-Process -FilePath $PythonPath -ArgumentList "-m pip install fastapi uvicorn python-multipart pywin32" -Wait -NoNewWindow -PassThru
if ($pipProcess.ExitCode -ne 0) { Write-Warn "Failed to install some dependencies. Server might not start." }


Write-Info "Checking for old NSSM service..."
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service) {
    Write-Info "Stopping and removing old NSSM service..."
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    if (Test-Path "$NssmDir\win64\nssm.exe") {
        & "$NssmDir\win64\nssm.exe" remove $ServiceName confirm
    }
}

Write-Info "Creating startup script..."
$BatPath = "$WorkDir\start_cad_server.bat"
Set-Content -Path $BatPath -Value "@echo off`r`nstart `"CadServer`" /min `"$PythonPath`" `"$WorkDir\windows_cad_server.py`""

Write-Info "Creating shortcut in Startup folder..."
$WshShell = New-Object -ComObject WScript.Shell
$StartupFolder = [Environment]::GetFolderPath('Startup')
$Shortcut = $WshShell.CreateShortcut("$StartupFolder\CadServer.lnk")
$Shortcut.TargetPath = $BatPath
$Shortcut.WorkingDirectory = $WorkDir
$Shortcut.WindowStyle = 7 # Minimized
$Shortcut.Save()

Write-Info "Starting CAD Server as current user..."
Stop-Process -Name python -Force -ErrorAction SilentlyContinue
Stop-Process -Name accoreconsole -Force -ErrorAction SilentlyContinue
Start-Process -FilePath $BatPath -WindowStyle Hidden

Write-Host "`nDONE! Server is running in the background as the current user." -ForegroundColor Green
Write-Host "It will automatically start when you log in to Windows."
