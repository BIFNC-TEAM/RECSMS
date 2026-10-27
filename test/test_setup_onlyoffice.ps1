# Run with Windows PowerShell 5.1: powershell.exe -NoProfile -ExecutionPolicy Bypass -File test/test_setup_onlyoffice.ps1
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'setup_onlyoffice.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Installer script does not parse.' }
$probe = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-SetupNativeCommand'}, $true)
if (-not $probe) { throw 'Probe helper missing.' }
. ([scriptblock]::Create($probe.Extent.Text))
$powershellPath = (Get-Command powershell.exe -ErrorAction Stop).Source
if (Test-SetupNativeCommand -FilePath $powershellPath -Arguments @('-NoProfile', '-Command', "[Console]::Error.WriteLine('expected native stderr'); exit 1")) { throw 'Failed command incorrectly reported success.' }
if (-not (Test-SetupNativeCommand -FilePath $powershellPath -Arguments @('-NoProfile', '-Command', 'exit 0'))) { throw 'Successful command incorrectly reported failure.' }
if ($ErrorActionPreference -ne 'Stop') { throw 'Probe changed caller error policy.' }
Write-Host 'PASS: native stderr and nonzero exit are safe; success and caller policy are preserved.'
