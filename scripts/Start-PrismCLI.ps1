$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Resolve-Path (Join-Path $ScriptDir "..")
$Shell = Join-Path $ScriptDir "prism_cli_shell.ps1"
Start-Process powershell -ArgumentList @('-NoExit','-NoLogo','-ExecutionPolicy','Bypass','-File', $Shell) -WorkingDirectory $RootDir
