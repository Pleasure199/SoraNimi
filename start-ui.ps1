param(
    [int]$Port = 8765,
    [string]$Workspace = $PSScriptRoot,
    [switch]$Demo,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AgentArguments
)

$AgentPython = 'F:\miniconda3\envs\Lang\python.exe'
if (-not (Test-Path -LiteralPath $AgentPython)) {
    throw "Conda Lang Python not found: $AgentPython"
}
$LaunchArguments = @('-m', 'soranimi', '--ui', '--workspace', $Workspace, '--port', $Port)
if ($Demo) { $LaunchArguments += '--demo' }
& $AgentPython @LaunchArguments @AgentArguments
exit $LASTEXITCODE
