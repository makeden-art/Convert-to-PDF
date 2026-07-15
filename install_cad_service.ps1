<#
.SYNOPSIS
    Installs Windows CAD Server (windows_cad_server.py) as an NSSM system service.
    Requires user credentials so AutoCAD/Office can access the user profile in Session 0.
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
$NssmUrl       = "https://nssm.cc/release/nssm-2.24.zip"

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


Write-Info "Creating Desktop folders for MS Office (Session 0 hack)..."
$desktop1 = "C:\Windows\System32\config\systemprofile\Desktop"
$desktop2 = "C:\Windows\SysWOW64\config\systemprofile\Desktop"
if (-not (Test-Path $desktop1)) { New-Item -ItemType Directory -Path $desktop1 -Force | Out-Null }
if (-not (Test-Path $desktop2)) { New-Item -ItemType Directory -Path $desktop2 -Force | Out-Null }

Write-Info "Checking for NSSM..."
if (-not (Test-Path "$NssmDir\win64\nssm.exe")) {
    Write-Info "Downloading NSSM from $NssmUrl..."
    $zipPath = "$env:TEMP\nssm.zip"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $NssmUrl -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath $env:TEMP -Force
    $extractedDir = Get-ChildItem -Path $env:TEMP -Filter "nssm-*" -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Path $NssmDir -Force | Out-Null
    Copy-Item -Path "$($extractedDir.FullName)\*" -Destination $NssmDir -Recurse -Force
}

Write-Info "Stopping and removing any old NSSM service..."
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service) {
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    & "$NssmDir\win64\nssm.exe" remove $ServiceName confirm
    Start-Sleep -Seconds 2
}

Write-Info "Stopping any running Python instances..."
Stop-Process -Name python -Force -ErrorAction SilentlyContinue

Write-Info "Removing old Startup shortcut (if any)..."
$StartupFolder = [Environment]::GetFolderPath('Startup')
$OldShortcut = "$StartupFolder\CadServer.lnk"
if (Test-Path $OldShortcut) { Remove-Item $OldShortcut -Force }

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Для работы AutoCAD и Office в фоне нужны ваши учетные данные!" -ForegroundColor Yellow
Write-Host "Служба должна работать от имени вашего пользователя," -ForegroundColor Yellow
Write-Host "чтобы она видела лицензию AutoCAD и настройки Windows." -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

$creds = Get-Credential -Message "Введите логин (Например: .\User) и пароль вашего аккаунта Windows"

if (-not $creds) { Write-ErrorExit "Credentials are required to run AutoCAD/Office as a service!" }

$username = $creds.UserName
if (-not $username.Contains("\") -and -not $username.Contains("@")) {
    $username = ".\$username"
}
$password = $creds.GetNetworkCredential().Password

Write-Info "Installing $ServiceName via NSSM..."
$nssmPath = "$NssmDir\win64\nssm.exe"

& $nssmPath install $ServiceName $PythonPath """$ScriptPath"""
& $nssmPath set $ServiceName AppDirectory $WorkDir
& $nssmPath set $ServiceName ObjectName $username $password
& $nssmPath set $ServiceName AppStdout "$WorkDir\stdout.log"
& $nssmPath set $ServiceName AppStderr "$WorkDir\stderr.log"

Write-Info "Starting service $ServiceName..."
Start-Service -Name $ServiceName

$status = (Get-Service -Name $ServiceName).Status
if ($status -eq 'Running') {
    Write-Info "SUCCESS! $ServiceName is now running in the background as $username."
} else {
    Write-Warn "Service is installed but failed to start. Check $WorkDir\stderr.log"
}
