[CmdletBinding()]
param()

$wrapperPath = Join-Path $HOME ".local\bin\flocks.cmd"
if (-not (Test-Path -LiteralPath $wrapperPath)) {
    throw "Flocks launcher not found: $wrapperPath"
}

$cmdPath = $env:ComSpec
if ([string]::IsNullOrWhiteSpace($cmdPath)) {
    $cmdPath = "cmd.exe"
}

# Route installer-created shortcuts through UAC, but keep the real app entrypoint
# on the shared flocks.cmd wrapper so shortcut launches match terminal launches.
Start-Process -FilePath $cmdPath -ArgumentList @("/c", "`"$wrapperPath`" start") -WorkingDirectory $HOME -WindowStyle Hidden -Verb RunAs
