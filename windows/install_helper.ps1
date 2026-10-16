<#
.SYNOPSIS
    Installs the Botsy helper on this Windows PC (or removes it with -Uninstall).

.DESCRIPTION
    The Botsy helper watches your Downloads folder and sends EverBee and eRank export
    files to your Botsy server. It never sends any other file. This script:
      1. checks that Python 3.11 or newer and the Windows OpenSSH Client are installed;
      2. asks for your server address;
      3. makes a key for this PC (only if it has none yet) and shows the one line to
         paste on the server so the server accepts it;
      4. copies the helper to %LOCALAPPDATA%\Botsy and writes %APPDATA%\Botsy\helper.json;
      5. starts the helper now and at every logon (Scheduled Task "Botsy helper").
    You can run it again at any time. It keeps your key and your settings.

.PARAMETER Uninstall
    Stops the helper and removes the Scheduled Task. Settings, key and log are kept.

.PARAMETER Server
    The server address, so the script does not have to ask for it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install_helper.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install_helper.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [string]$Server = ""
)

# Works in Windows PowerShell 5.1 (built into Windows 10 and 11) and PowerShell 7.
# Keep this file plain ASCII: PowerShell 5.1 misreads other characters.
$ErrorActionPreference = "Stop"

$TaskName     = "Botsy helper"
$UploadUser   = "botsy-upload"
# The server's setup command: deploy/lightsail/setup.sh installs it in /usr/local/sbin,
# where it runs root's own copy of the setup files.
$ServerSetup  = "botsy-setup"
# Where the server keeps upload keys (deploy/lightsail/sshd_botsy_upload.conf, AuthorizedKeysFile).
$ServerKeys   = "/etc/ssh/botsy-upload/authorized_keys"
$AppDir       = Join-Path $env:LOCALAPPDATA "Botsy"
$HelperPath   = Join-Path $AppDir "botsy_helper.py"
$ConfigDir    = Join-Path $env:APPDATA "Botsy"
$ConfigPath   = Join-Path $ConfigDir "helper.json"
$LogPath      = Join-Path $ConfigDir "helper.log"
$SshDir       = Join-Path $env:USERPROFILE ".ssh"
$KeyPath      = Join-Path $SshDir "botsy_upload"
$StartupLink  = Join-Path ([Environment]::GetFolderPath("Startup")) "Botsy helper.lnk"


function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) {
    Write-Host "    $Text" -ForegroundColor Green
}

function Write-Note([string]$Text) {
    Write-Host "    $Text" -ForegroundColor Yellow
}

function Wait-ForEnter {
    # "Run with PowerShell" closes the window when the script ends; this keeps it open.
    Write-Host ""
    [void](Read-Host "Press Enter to close this window")
}

function Stop-WithMessage([string]$Text) {
    Write-Host ""
    Write-Host $Text -ForegroundColor Red
    Wait-ForEnter
    exit 1
}

function Invoke-Program([string]$Exe, [string[]]$Arguments) {
    # Runs a program and returns its exit code and output lines, or $null if it cannot
    # start. PowerShell 5.1 turns a program's error output into script errors when
    # ErrorActionPreference is Stop, so it is relaxed while the program runs.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = @(& $Exe @Arguments 2>&1 | ForEach-Object { "$_" })
        return [pscustomobject]@{ Code = $LASTEXITCODE; Lines = $lines }
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Get-OpenSshDir {
    # A 32-bit PowerShell on 64-bit Windows sees System32 redirected; Sysnative is the real one.
    $system = "System32"
    if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
        $system = "Sysnative"
    }
    return (Join-Path (Join-Path $env:SystemRoot $system) "OpenSSH")
}

function Find-Python {
    # Tries the Python launcher first ("py -3"), then "python". Returns the newest
    # usable one as @{Version; Exe; Pythonw}, or a result with Exe = $null.
    $code = "import sys;print(sys.version_info[0]);print(sys.version_info[1]);print(sys.executable)"
    $tries = @(
        @{ Exe = "py";     Pre = @("-3") },
        @{ Exe = "python"; Pre = @() }
    )
    $best = [pscustomobject]@{ Version = $null; Exe = $null; Pythonw = $null }
    foreach ($option in $tries) {
        if (-not (Get-Command $option.Exe -ErrorAction SilentlyContinue)) { continue }
        $result = Invoke-Program $option.Exe ($option.Pre + @("-c", $code))
        if ($null -eq $result -or $result.Code -ne 0 -or $result.Lines.Count -lt 3) { continue }
        $major = 0
        $minor = 0
        if (-not [int]::TryParse($result.Lines[0].Trim(), [ref]$major)) { continue }
        if (-not [int]::TryParse($result.Lines[1].Trim(), [ref]$minor)) { continue }
        $version = New-Object System.Version $major, $minor
        if ($null -ne $best.Version -and $version -le $best.Version) { continue }
        $exe = $result.Lines[2].Trim()
        $pythonw = Join-Path (Split-Path $exe -Parent) "pythonw.exe"
        $best = [pscustomobject]@{ Version = $version; Exe = $exe; Pythonw = $pythonw }
    }
    return $best
}

function Get-Setting($Object, [string]$Name) {
    # Reads one value from the old helper.json, or $null when it is not there.
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Test-ServerAddress([string]$Text) {
    return ($Text -match '^[A-Za-z0-9][A-Za-z0-9.-]*$')
}

function Read-ServerAddress([string]$Current) {
    while ($true) {
        if ($Current) {
            $answer = Read-Host "Server address (press Enter to keep $Current)"
        } else {
            $answer = Read-Host "Server address (the static IP of your Lightsail server, like 3.120.45.67)"
        }
        $answer = "$answer".Trim()
        if (-not $answer -and $Current) { return $Current }
        # People often paste "user@address" or "ssh://address/"; keep only the address.
        $answer = $answer -replace '^ssh://', '' -replace '^[^@]*@', '' -replace '/+$', ''
        if (Test-ServerAddress $answer) { return $answer }
        Write-Note "That does not look like a server address. Type only the address, for"
        Write-Note "example 3.120.45.67 (no spaces, no user name, no :22 at the end)."
    }
}

function Get-FolderSetting([string]$Actual, [string]$Usual, [string]$Placeholder) {
    # Writes "%USERPROFILE%\..." when the folder is in the usual place, so helper.json
    # matches docs/SERVER.md; otherwise the real path (for example a OneDrive folder).
    if ($Actual -and ($Actual.TrimEnd('\') -ne $Usual.TrimEnd('\'))) { return $Actual }
    return $Placeholder
}

function Get-DownloadsFolder {
    try {
        $path = (New-Object -ComObject Shell.Application).NameSpace("shell:Downloads").Self.Path
        if ($path -and (Test-Path -LiteralPath $path)) { return $path }
    } catch {
        # No shell folder information (very old or stripped-down Windows): use the usual place.
    }
    return (Join-Path $env:USERPROFILE "Downloads")
}

function Stop-Helper {
    # Stops a running helper so the new copy (or nothing, when uninstalling) takes over.
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    $running = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*botsy_helper.py*" -and $_.CommandLine -notlike "*--check*" }
    foreach ($process in @($running)) {
        if ($null -ne $process) {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Register-Helper([string]$Pythonw) {
    # Returns "task" when the Scheduled Task was made, or "startup" when Windows refused
    # and a shortcut in the Startup folder was made instead (same effect: runs at logon).
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $argument = '"' + $HelperPath + '"'
    try {
        $action = New-ScheduledTaskAction -Execute $Pythonw -Argument $argument -WorkingDirectory $AppDir
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Principal $principal -Settings $settings -Force `
            -Description "Sends EverBee and eRank exports from Downloads to the Botsy server." | Out-Null
        if (Test-Path -LiteralPath $StartupLink) { Remove-Item -LiteralPath $StartupLink -Force }
        return "task"
    } catch {
        Write-Note "Windows did not allow a Scheduled Task ($($_.Exception.Message))."
        Write-Note "Using a shortcut in your Startup folder instead; it does the same job."
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut($StartupLink)
        $link.TargetPath = $Pythonw
        $link.Arguments = $argument
        $link.WorkingDirectory = $AppDir
        $link.WindowStyle = 7
        $link.Description = $TaskName
        $link.Save()
        return "startup"
    }
}

function Start-Helper([string]$How, [string]$Pythonw) {
    if ($How -eq "task") {
        Start-ScheduledTask -TaskName $TaskName
    } else {
        Start-Process -FilePath $Pythonw -ArgumentList ('"' + $HelperPath + '"') `
            -WorkingDirectory $AppDir -WindowStyle Hidden
    }
}


function Uninstall-Helper {
    Write-Step "Removing the Botsy helper"
    Stop-Helper
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Ok "Removed the Scheduled Task '$TaskName'."
    } else {
        Write-Ok "There was no Scheduled Task '$TaskName'."
    }
    if (Test-Path -LiteralPath $StartupLink) {
        Remove-Item -LiteralPath $StartupLink -Force
        Write-Ok "Removed the Startup shortcut."
    }
    Write-Host ""
    Write-Host "The helper is stopped and will not start at logon any more."
    Write-Host "These were kept (delete them yourself if you want):"
    Write-Host "  settings and log:  $ConfigDir"
    Write-Host "  program copy:      $AppDir"
    Write-Host "  key:               $KeyPath"
    Write-Host "  search list and status: the Botsy folder in your Documents"
}


function Install-Helper {
    Write-Host "Botsy helper setup" -ForegroundColor Cyan
    Write-Host "This sends EverBee and eRank export files from your Downloads folder to your"
    Write-Host "Botsy server. It never sends any other file and never changes your files."

    # 1. Python ---------------------------------------------------------------------
    Write-Step "Checking Python"
    $python = Find-Python
    if ($null -eq $python.Exe) {
        Stop-WithMessage ("Python was not found. Install it from https://www.python.org/downloads/ " +
            "(tick 'Add python.exe to PATH' on the first screen), then run this script again.")
    }
    if ($python.Version -lt (New-Object System.Version 3, 11)) {
        Stop-WithMessage ("Python $($python.Version) is too old; Botsy needs 3.11 or newer. Install the " +
            "latest from https://www.python.org/downloads/ and run this script again.")
    }
    Write-Ok "Python $($python.Version) at $($python.Exe)"
    if ($python.Exe -like "*WindowsApps*") {
        Write-Note "This Python comes from the Microsoft Store. It may keep the log in a hidden"
        Write-Note "place. The python.org installer is recommended."
    }
    $pythonw = $python.Pythonw
    if (-not (Test-Path -LiteralPath $pythonw)) {
        Write-Note "pythonw.exe was not found next to python.exe; the helper will use python.exe"
        Write-Note "and a small black window may appear at logon."
        $pythonw = $python.Exe
    }

    # 2. OpenSSH Client ---------------------------------------------------------------
    Write-Step "Checking the Windows OpenSSH Client"
    $openSsh = Get-OpenSshDir
    $sftp = Join-Path $openSsh "sftp.exe"
    $keygen = Join-Path $openSsh "ssh-keygen.exe"
    if (-not (Test-Path -LiteralPath $sftp) -or -not (Test-Path -LiteralPath $keygen)) {
        Stop-WithMessage ("The Windows OpenSSH Client is not turned on. Open Settings > System > " +
            "Optional features (Windows 10: Settings > Apps > Optional features), choose 'Add a " +
            "feature' or 'View features', search for 'OpenSSH Client', install it, then run " +
            "this script again.")
    }
    Write-Ok "Found $sftp"

    # 3. Server address ---------------------------------------------------------------
    Write-Step "Your Botsy server"
    $old = $null
    if (Test-Path -LiteralPath $ConfigPath) {
        try {
            $old = [System.IO.File]::ReadAllText($ConfigPath) | ConvertFrom-Json
        } catch {
            Write-Note "The old settings file could not be read; making a new one."
        }
    }
    $address = "$Server".Trim()
    if ($address -and -not (Test-ServerAddress $address)) {
        Write-Note "'$address' does not look like a server address."
        $address = ""
    }
    if (-not $address) { $address = Read-ServerAddress ([string](Get-Setting $old "host")) }
    Write-Ok "Server: $address"

    # 4. Key ------------------------------------------------------------------------
    Write-Step "This PC's key for the server"
    # Only letters, digits and "-" in the key's label, so the server line below can be
    # pasted into the server's shell without anything in it being read as a command.
    $keyComment = "botsy-upload@" + ($env:COMPUTERNAME -replace '[^A-Za-z0-9-]', '')
    if (-not (Test-Path -LiteralPath $SshDir)) { New-Item -ItemType Directory -Path $SshDir | Out-Null }
    if (Test-Path -LiteralPath $KeyPath) {
        Write-Ok "Using the key you already have: $KeyPath"
    } else {
        # Start-Process passes this line unchanged, so -N "" really is an empty passphrase
        # in every PowerShell version.
        $keyArgs = '-q -t ed25519 -N "" -C "' + $keyComment + '" -f "' + $KeyPath + '"'
        $made = Start-Process -FilePath $keygen -ArgumentList $keyArgs -NoNewWindow -Wait -PassThru
        if ($made.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $KeyPath)) {
            Stop-WithMessage "ssh-keygen could not make the key $KeyPath."
        }
        Write-Ok "Made a new key: $KeyPath"
    }
    $pubPath = "$KeyPath.pub"
    if (-not (Test-Path -LiteralPath $pubPath)) {
        $derived = Invoke-Program $keygen @("-y", "-f", $KeyPath)
        if ($null -eq $derived -or $derived.Code -ne 0) { Stop-WithMessage "Could not read the key $KeyPath." }
        [System.IO.File]::WriteAllText($pubPath, ($derived.Lines -join "`n") + "`n")
    }
    $keyParts = @(([System.IO.File]::ReadAllText($pubPath)).Trim() -split '\s+')
    if ($keyParts.Count -lt 2 -or $keyParts[0] -notmatch '^ssh-ed25519$|^ssh-rsa$|^ecdsa-' -or
            $keyParts[1] -notmatch '^[A-Za-z0-9+/=]+$') {
        Stop-WithMessage "The key file $pubPath does not look right. Delete $KeyPath and $pubPath, then run this script again."
    }
    $publicKey = $keyParts[0] + " " + $keyParts[1] + " " + $keyComment

    # 5. Program and settings ---------------------------------------------------------
    Write-Step "Copying the helper and writing its settings"
    $source = Join-Path $PSScriptRoot "botsy_helper.py"
    if (-not (Test-Path -LiteralPath $source)) {
        Stop-WithMessage "botsy_helper.py must be in the same folder as this script ($PSScriptRoot)."
    }
    Stop-Helper
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    Copy-Item -LiteralPath $source -Destination $HelperPath -Force
    Unblock-File -LiteralPath $HelperPath -ErrorAction SilentlyContinue

    $downloads = Get-FolderSetting (Get-DownloadsFolder) (Join-Path $env:USERPROFILE "Downloads") '%USERPROFILE%\Downloads'
    $documents = [Environment]::GetFolderPath("MyDocuments")
    $outbox = Get-FolderSetting (Join-Path $documents "Botsy") (Join-Path $env:USERPROFILE "Documents\Botsy") '%USERPROFILE%\Documents\Botsy'
    $config = [ordered]@{
        host         = $address
        user         = $UploadUser
        key          = '%USERPROFILE%\.ssh\botsy_upload'
        watch_dir    = $downloads
        poll_seconds = 30
        max_age_days = 14
        outbox_dir   = $outbox
    }
    if ($null -ne $old) {
        # Keep anything you changed by hand, except the server address you just typed.
        foreach ($property in $old.PSObject.Properties) {
            if ($property.Name -ne "host") { $config[$property.Name] = $property.Value }
        }
    }
    $json = $config | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText($ConfigPath, $json, (New-Object System.Text.UTF8Encoding $false))
    Write-Ok "Program:  $HelperPath"
    Write-Ok "Settings: $ConfigPath"

    # 6. Start at logon -----------------------------------------------------------------
    Write-Step "Making the helper start when you log in"
    $how = Register-Helper $pythonw
    if ($how -eq "task") { Write-Ok "Scheduled Task '$TaskName' is ready." }
    else { Write-Ok "Startup shortcut is ready: $StartupLink" }

    # 7. Server side --------------------------------------------------------------------
    $serverLine = "sudo $ServerSetup --upload-pubkey `"$publicKey`""
    $commandFile = Join-Path $ConfigDir "server-command.txt"
    # Only for when the setup script stops before it adds the key; "restrict" matches it.
    $fallback = "echo `"restrict $publicKey`" | sudo tee -a $ServerKeys"
    [System.IO.File]::WriteAllText($commandFile, $serverLine + "`r`n`r`n" +
        "Only if the server never says 'Upload key added' or 'That upload key was already allowed':`r`n" +
        $fallback + "`r`n")
    Write-Step "One step on the server (only needed once per PC)"
    Write-Host "    1. Open the Lightsail console, click your server, then 'Connect using SSH'."
    Write-Host "    2. Paste this one line into the black window and press Enter:"
    Write-Host ""
    Write-Host $serverLine -ForegroundColor White
    Write-Host ""
    try {
        Set-Clipboard -Value $serverLine
        Write-Ok "(The line is already copied: just paste it.)"
    } catch {
        # No clipboard (for example over Remote Desktop without one); the line is printed anyway.
    }
    Write-Host "    3. The server needs a few seconds. Wait until it has finished and check"
    Write-Host "       that it said 'Upload key added' (or 'That upload key was already allowed')."
    Write-Host "    The line is also saved in $commandFile"
    Write-Host "    Only if the server never says either of those, paste this line instead:"
    Write-Host "    $fallback"
    Write-Host ""
    [void](Read-Host "Press Enter here when you have done that on the server")

    # 8. Test ---------------------------------------------------------------------------
    $answer = Read-Host "Test the connection to the server now? (Y/n)"
    if ("$answer".Trim() -notmatch '^[nN]') {
        Write-Step "Testing the connection"
        $check = Invoke-Program $python.Exe @($HelperPath, "--check")
        if ($null -eq $check) {
            Write-Note "The test could not start."
        } else {
            foreach ($line in $check.Lines) { Write-Host "    $line" }
            if ($check.Code -ne 0) {
                Write-Note "Fix the problem above, then test again with:"
                Write-Note "  `"$($python.Exe)`" `"$HelperPath`" --check"
            }
        }
    }

    # 9. Start now ----------------------------------------------------------------------
    Start-Helper $how $pythonw
    Write-Step "Done"
    Write-Host "    The helper is running and will start by itself each time you log in."
    Write-Host "    It checks your Downloads folder every 30 seconds for EverBee and eRank exports."
    Write-Host "    What it did:     $LogPath"
    $results = [Environment]::ExpandEnvironmentVariables([string]$config["outbox_dir"])
    Write-Host "    Server results:  $(Join-Path $results 'status.txt') (updated hourly)"
    Write-Host "    Search list:     $(Join-Path $results 'search-list.txt') (opens when it changes)"
    Write-Host "    To remove it:    powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Uninstall"
}


try {
    if ($Uninstall) { Uninstall-Helper } else { Install-Helper }
} catch {
    Write-Host ""
    Write-Host "Something went wrong: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Nothing on your PC was deleted. You can run this script again." -ForegroundColor Red
    Wait-ForEnter
    exit 1
}
Wait-ForEnter
exit 0
