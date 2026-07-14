<#
.SYNOPSIS
    Автоматическая установка/обновление Windows CAD Server (windows_cad_server.py) 
    и запуск его как Windows-службы (NSSM) с автозапуском.
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
    Write-ErrorExit "Запуск скрипта требует прав администратора. Откройте PowerShell от имени Администратора."
}

Write-Info "Создаём рабочий каталог $WorkDir"
if (-not (Test-Path $WorkDir)) { New-Item -ItemType Directory -Path $WorkDir | Out-Null }

$ScriptPath = Join-Path $WorkDir 'windows_cad_server.py'
Write-Info "Скачиваем скрипт сервера с $CadScriptUrl..."
try {
    Invoke-WebRequest -Uri $CadScriptUrl -OutFile $ScriptPath -UseBasicParsing -ErrorAction Stop
} catch {
    Write-ErrorExit "Не удалось скачать скрипт. Убедитесь, что адрес портала $PortalIp доступен. Ошибка: $_"
}

if (-not (Test-Path $PythonPath)) {
    $pyInPath = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($pyInPath) { 
        $PythonPath = $pyInPath
        Write-Info "Python найден в PATH: $PythonPath" 
    } else {
        Write-Info "Python не найден – скачиваем и устанавливаем Python 3.12..."
        $installer = "$env:TEMP\python-3.12.0-amd64.exe"
        Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.0/python-3.12.0-amd64.exe' -OutFile $installer -UseBasicParsing
        Write-Info "Запускаем тихую установку (это может занять пару минут)..."
        $process = Start-Process -FilePath $installer -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 TargetDir=C:\Python312" -Wait -PassThru
        if ($process.ExitCode -ne 0) { Write-Warn "Установщик Python вернул код $($process.ExitCode)" }
        $PythonPath = 'C:\Python312\python.exe'
        if (-not (Test-Path $PythonPath)) { Write-ErrorExit "Не удалось установить Python." }
        Write-Info "Python установлен."
    }
} else { Write-Info "Python найден: $PythonPath" }

if (-not (Test-Path $NssmDir)) {
    Write-Info "Скачиваем NSSM (менеджер служб)..."
    $zip = "$env:TEMP\nssm.zip"
    Invoke-WebRequest -Uri $NssmUrl -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $NssmDir -Force
    $nssmExe = Get-ChildItem $NssmDir -Recurse -Filter nssm.exe | Where-Object {$_.FullName -match 'win64'} | Select-Object -First 1
    if (-not $nssmExe) { Write-ErrorExit "Не найден nssm.exe" }
    $NssmPath = $nssmExe.FullName
    Write-Info "NSSM готов: $NssmPath"
} else {
    $NssmPath = Get-ChildItem $NssmDir -Recurse -Filter nssm.exe | Select-Object -First 1 | %{$_.FullName}
    Write-Info "NSSM уже установлен: $NssmPath"
}

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Write-Info "Служба $ServiceName уже существует. Обновляем её настройки..."
    Stop-Service -Name $ServiceName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
} else {
    Write-Info "Создаём новую службу $ServiceName..."
    & $NssmPath install $ServiceName $PythonPath $ScriptPath | Out-Null
}

& $NssmPath set $ServiceName AppDirectory $WorkDir | Out-Null
& $NssmPath set $ServiceName Start SERVICE_AUTO_START | Out-Null
& $NssmPath set $ServiceName AppRestartDelay 5000 | Out-Null

Write-Info "Запускаем службу..."
Start-Service -Name $ServiceName

$svc = Get-Service -Name $ServiceName
if ($svc.Status -eq 'Running') {
    Write-Host "`n✅ ГОТОВО! Сервер запущен и работает в фоне." -ForegroundColor Green
    Write-Host "Он будет автоматически запускаться при старте Windows."
} else {
    Write-Warn "`n⚠️ Служба зарегистрирована, но не запустилась. Проверьте логи Windows."
}
