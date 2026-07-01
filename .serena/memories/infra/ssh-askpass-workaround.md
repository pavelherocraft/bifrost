# SSH password auth on Windows OpenSSH (SSH_ASKPASS trick)

## Problem
Windows OpenSSH (ssh.exe) cannot accept a password via CLI argument, stdin
pipe, or env var the way `sshpass` does on Linux. PowerShell pipelines are
ignored because ssh reads the password from the TTY, which the opencode
bash tool does not allocate.

## Workaround (OpenSSH 8.4+)
Use SSH_ASKPASS mechanism with a tiny helper script. Verified working on
ssh.exe 9.5.5.1 (Windows 11).

### Step 1 — helper script (plain text password)
Path: `C:\Users\Admin\AppData\Local\Temp\opencode\askpass.cmd`
Content (single line):
    @echo <PASSWORD>

Creation:
```powershell
Set-Content -LiteralPath "C:\Users\Admin\AppData\Local\Temp\opencode\askpass.cmd" -Value '@echo <PASSWORD>' -Encoding ASCII
```

### Step 2 — env vars in PowerShell session
```powershell
$env:SSH_ASKPASS         = "C:\Users\Admin\AppData\Local\Temp\opencode\askpass.cmd"
$env:SSH_ASKPASS_REQUIRE = "force"   # required when no TTY
$env:DISPLAY             = "none"    # dummy, ignored on Windows but harmless
```

### Step 3 — invoke ssh with password-auth only
```powershell
ssh -o PreferredAuthentications=password `
    -o PubkeyAuthentication=no `
    -o StrictHostKeyChecking=accept-new `
    -p <PORT> <user>@<host> 'bash -s' < script.sh
```

For `scp` the same env vars work; just swap `ssh` -> `scp`.

## Mechanism
- ssh detects no TTY + `SSH_ASKPASS_REQUIRE=force`.
- On first password prompt, ssh spawns the path from `SSH_ASKPASS`, reads
  its stdout, uses that as the password.
- `PreferredAuthentications=password` ensures only password auth is attempted.
- `PubkeyAuthentication=no` avoids fallback to key auth.

## PowerShell quoting gotchas (important)
- PowerShell here-strings (`@' ... '@`) emit a UTF-8 BOM that breaks the
  first bash line (shows as `bash: line 1: <BOM>cmd: command not found`).
  Fix: prepend a `true` no-op as the first line of the script.
- PowerShell's native-command argument passing breaks on nested `""` in
  ssh args (e.g. `--format "{{...}}"`). Avoid: either use single quotes
  inside, or pipe the whole script through stdin (`bash -s`).
- `&&` is not supported in PS 5.1 — use `;` or `if ($?) { ... }`.

## Why not the alternatives
| Tool | Reason it failed |
|---|---|
| `sshpass` | Not available on Windows |
| `plink` (PuTTY) | Not installed |
| `Posh-SSH` PowerShell module | Not installed |
| `echo pass \| ssh ...` | ssh ignores stdin pipe; reads TTY |
| Inline `-o PubkeyAuthentication` | Doesn't help, still needs password source |

## Security notes
- Helper script contains PLAIN-TEXT password in temp dir.
- Anyone with file access to `C:\Users\Admin\AppData\Local\Temp\opencode\`
  can read it.
- For long-term use: replace with SSH key auth:
  1. `ssh-keygen` locally
  2. Upload pub key to `~/.ssh/authorized_keys` on host (one-time via the askpass trick)
  3. Disable `PasswordAuthentication` in `sshd_config`
  4. Delete `askpass.cmd`

## Applied to VM hc-srv21-hcbifrost (current session)
- VM credentials recorded in memory: `infra/hcbifrost-vm`
- Helper script: `C:\Users\Admin\AppData\Local\Temp\opencode\askpass.cmd`
- Used throughout this session for: ssh exec, scp file download,
  remote `go build`, git remote setup on VM.
