$ErrorActionPreference = 'Stop'
try {
    $archiveRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
    $archivePython = Join-Path $archiveRoot 'runtime\pythonw.exe'
    $archiveLauncher = Join-Path $archiveRoot 'starten.pyw'
    $archiveIcon = Join-Path $archiveRoot 'Recherche\web\archive.ico'
    foreach ($archiveFile in @($archivePython, $archiveLauncher, $archiveIcon)) {
        if (-not (Test-Path -LiteralPath $archiveFile -PathType Leaf)) {
            throw 'Die Einrichtung ist noch nicht vollstaendig. Bitte das Setup erneut ausfuehren.'
        }
    }
    $archiveShell = New-Object -ComObject WScript.Shell
    $archiveDesktop = $archiveShell.SpecialFolders.Item('Desktop')
    $archiveName = 'Mein Recherchearchiv (Setup)'
    $archiveLinkPath = Join-Path $archiveDesktop ($archiveName + '.lnk')
    $archiveIndex = 1
    while (Test-Path -LiteralPath $archiveLinkPath) {
        $archiveExisting = $archiveShell.CreateShortcut($archiveLinkPath)
        if ($archiveExisting.TargetPath -eq $archivePython -and $archiveExisting.Arguments -eq ('"' + $archiveLauncher + '"')) {
            break
        }
        $archiveIndex += 1
        $archiveLinkPath = Join-Path $archiveDesktop ($archiveName + ' (' + $archiveIndex + ').lnk')
    }
    $archiveShortcut = $archiveShell.CreateShortcut($archiveLinkPath)
    $archiveShortcut.TargetPath = $archivePython
    $archiveShortcut.Arguments = '"' + $archiveLauncher + '"'
    $archiveShortcut.WorkingDirectory = $archiveRoot
    $archiveShortcut.IconLocation = $archiveIcon + ',0'
    $archiveShortcut.Description = 'Mein Recherchearchiv - BIP, ICC/ICJ und UN'
    $archiveShortcut.Save()
    Write-Host ('Desktop-Verknuepfung erstellt: ' + [System.IO.Path]::GetFileName($archiveLinkPath))
    Write-Host 'Der Archivordner muss an seinem jetzigen Ort bleiben. Nach einem Umzug diese Datei erneut ausfuehren.'
    exit 0
} catch {
    Write-Host ('Die Verknuepfung konnte nicht erstellt werden: ' + $_.Exception.Message)
    exit 1
}
