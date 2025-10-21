$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Resolve-Path (Join-Path $ScriptDir '..')
Set-Location $RootDir

$VenvPython = Join-Path $RootDir '.venv\Scripts\python.exe'
if (Test-Path $VenvPython) {
  Write-Host 'Using venv: .venv' -ForegroundColor Green
  $env:Path = (Join-Path $RootDir '.venv\Scripts') + ';' + $env:Path
}

Write-Host 'Prism CLI shell ready. Type commands or "exit" to leave.' -ForegroundColor Cyan
Write-Host 'Available commands: login, logout, install, install-local, update, update-local, list, reload, info, uninstall' -ForegroundColor Gray

if (Test-Path $VenvPython) {
  $py = $VenvPython
} else {
  $py = 'python'
}

while ($true) {
  $input = Read-Host -Prompt 'prism>'
  if ([string]::IsNullOrWhiteSpace($input)) { continue }
  if ($input -in @('exit','quit','q')) { break }
  
  # Parse the input as command + args
  $parts = $input.Trim() -split '\s+', 2
  $command = $parts[0]
  $args_str = if ($parts.Length -gt 1) { $parts[1] } else { '' }
  
  # Build the full command
  $full_cmd = "scripts/prism_cli.py $command"
  if ($args_str) {
    $full_cmd += " $args_str"
  }
  
  # Execute
  Invoke-Expression "& `$py $full_cmd"
}
