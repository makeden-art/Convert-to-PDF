<#
.SYNOPSIS
    Uninstall script for Windows CAD Server.
    Stops the server, removes shortcuts, and disables Auto-Logon.
#>

function Write-Info ($msg) { Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Warn ($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-ErrorExit ($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red; exit 1 }

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warn "Script requires administrator privileges. Prompting for elevation..."
    $procInfo = New-Object System.Diagnostics.ProcessStartInfo
    $procInfo.FileName = "powershell.exe"
    $procInfo.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $procInfo.Verb = "runas"
    try {
        [System.Diagnostics.Process]::Start($procInfo) | Out-Null
    } catch {
        Write-ErrorExit "Elevation failed or cancelled by user."
    }
    exit
}

Write-Info "Stopping CAD Server processes..."
Stop-Process -Name python -Force -ErrorAction SilentlyContinue
Stop-Process -Name accoreconsole -Force -ErrorAction SilentlyContinue

Write-Info "Removing Startup shortcuts..."
$StartupFolder = [Environment]::GetFolderPath('Startup')
$ShortcutPath = "$StartupFolder\CadServer.lnk"
if (Test-Path $ShortcutPath) {
    Remove-Item -Path $ShortcutPath -Force
    Write-Info "Deleted $ShortcutPath"
}

Write-Info "Cleaning up CAD Server directory..."
$WorkDir = 'C:\CadServer'
if (Test-Path $WorkDir) {
    Remove-Item -Path $WorkDir -Recurse -Force
    Write-Info "Deleted $WorkDir"
}

Write-Info "Disabling Windows Auto-Logon..."
$RegPath = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
try {
    Set-ItemProperty -Path $RegPath -Name "AutoAdminLogon" -Value "0" -ErrorAction Stop
    Remove-ItemProperty -Path $RegPath -Name "DefaultPassword" -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path $RegPath -Name "DefaultUserName" -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path $RegPath -Name "DefaultDomainName" -ErrorAction SilentlyContinue
    Write-Info "Auto-Logon successfully disabled."
} catch {
    Write-Warn "Failed to update registry. Error: $_"
}

Write-Host "`nDONE! The CAD Server has been fully uninstalled and Auto-Logon disabled." -ForegroundColor Green
Write-Host "Press any key to exit..."
$Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") | Out-Null
