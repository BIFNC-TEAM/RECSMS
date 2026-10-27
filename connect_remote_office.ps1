# The SSH client requests the password interactively; no login password is saved here.
[CmdletBinding()]
param([string]$HostAddress = '81.70.105.92', [string]$UserName = 'ubuntu')
& ssh.exe -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:8080:127.0.0.1:18080 -R 127.0.0.1:18765:127.0.0.1:8765 "$UserName@$HostAddress"
exit $LASTEXITCODE
