<#
.SYNOPSIS
    Automatic installation/update of Windows CAD Server (windows_cad_server.py)
    and starting it as a Windows service (NSSM) with auto-start.
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
$NssmUrl       = 'https://nssm.cc/release/nssm-2.24.zip'
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

if (-not (Test-Path $NssmDir)) {
    Write-Info "Downloading NSSM (service manager)..."
    $zip = "$env:TEMP\nssm.zip"
    Invoke-WebRequest -Uri $NssmUrl -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $NssmDir -Force
    $nssmExe = Get-ChildItem $NssmDir -Recurse -Filter nssm.exe | Where-Object {$_.FullName -match 'win64'} | Select-Object -First 1
    if (-not $nssmExe) { Write-ErrorExit "nssm.exe not found" }
    $NssmPath = $nssmExe.FullName
    Write-Info "NSSM ready: $NssmPath"
} else {
    $NssmPath = Get-ChildItem $NssmDir -Recurse -Filter nssm.exe | Select-Object -First 1 | %{$_.FullName}
    Write-Info "NSSM already installed: $NssmPath"
}

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Write-Info "Service $ServiceName already exists. Updating settings..."
    Stop-Service -Name $ServiceName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
} else {
    Write-Info "Creating new service $ServiceName..."
    & $NssmPath install $ServiceName $PythonPath $ScriptPath | Out-Null
}

& $NssmPath set $ServiceName AppDirectory $WorkDir | Out-Null
& $NssmPath set $ServiceName Start SERVICE_AUTO_START | Out-Null
& $NssmPath set $ServiceName AppRestartDelay 5000 | Out-Null

Write-Info "Starting service..."
Start-Service -Name $ServiceName

$svc = Get-Service -Name $ServiceName
if ($svc.Status -eq 'Running') {
    Write-Host "`nDONE! Server is running in the background." -ForegroundColor Green
    Write-Host "It will automatically start when Windows boots."
} else {
    Write-Warn "`nService registered but failed to start. Check Windows event logs."
}
