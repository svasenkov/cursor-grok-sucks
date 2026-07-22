# grok-sucks

Remove **Cursor Grok\*** from Settings → Models and keep it from coming back.

Cursor sometimes re-enables Grok after you turn it off, and nudges new chats toward it ([forum report](https://forum.cursor.com/t/grok-re-enables-itself-after-being-disabled-in-settings/165894), acknowledged by Cursor staff). That can push billing via Auto / recommended model.

This is a tiny **stdlib-only** Python script that:

1. **Patches** Cursor’s workbench JS so `grok*` is filtered out of the Models list / picker (this is what actually hides it in the UI).
2. **Scrubs** local `state.vscdb` preferences so Grok is not selected / cached.
3. Optionally **polls** so both stay clean after Cursor updates or re-nudges.

> Unofficial. Not affiliated with Cursor or xAI. Patches and storage format can break on Cursor updates. Use at your own risk.

## Why a workbench patch?

Writing only to `state.vscdb` is **not enough** while Cursor is running: the UI reads an in-memory copy and periodically overwrites the DB, so Grok reappears in Settings even after you delete it from disk.

The patch changes the client-side filter that builds the model list, so Grok never renders.

## Requirements

- Python 3.10+
- Cursor installed
- Write access to Cursor’s app files (for the UI patch)

No pip packages.

## Install

```bash
git clone https://github.com/svasenkov/grok-sucks.git
cd grok-sucks
```

## Usage

```bash
# Status: state DB + whether workbench is patched
python grok_sucks.py status

# Patch workbench + scrub state (then RESTART Cursor)
python grok_sucks.py once --hard

# Workbench patch only
python grok_sucks.py patch

# Remove workbench patch
python grok_sucks.py unpatch

# Poll every 5s (re-scrubs state; re-applies patch after Cursor updates)
python grok_sucks.py watch --interval 5 --fallback composer-2.5 --hard
```

**After `patch` / first `once`: fully quit and reopen Cursor** (or Command Palette → “Developer: Reload Window”). The Models list is built at load time.

| Flag | Default | Meaning |
|------|---------|---------|
| `--interval` | `5` | Seconds between polls in `watch` |
| `--fallback` | `composer-2.5` | Model to select when a surface was on Grok |
| `--hard` | off | Also scrub `featureModelConfigs` fallbacks / subagent defaults |
| `--no-patch` | off | State DB only (UI list will keep showing Grok) |
| `--dry-run` | off | Print actions, do not write |
| `--db` | auto | Override path to `state.vscdb` |

### Match rule

Any model id whose name starts with `grok` (case-insensitive), e.g. `grok-4.5`, `grok-code-fast-1`.

## How it works

### Workbench patch (UI)

Edits (with marker `/*grok-sucks*/`):

- `…/out/vs/workbench/workbench.desktop.main.js`
- `…/out/vs/workbench/workbench.glass.main.js`

| OS | App resources |
|----|----------------|
| macOS | `/Applications/Cursor.app/Contents/Resources/app` |
| Linux | `/usr/share/cursor/resources/app` (and similar) |
| Windows | `%LOCALAPPDATA%\Programs\cursor\resources\app` |

Filters `grok*` out of the shared model-visibility helper and `getAvailableDefaultModels()`.

After a **Cursor update**, files are replaced — run `patch` / `watch` again, then restart.

### State DB (preferences)

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` |
| Linux | `~/.config/Cursor/User/globalStorage/state.vscdb` |
| Windows | `%APPDATA%\Cursor\User\globalStorage/state.vscdb` |

Scrubs `availableDefaultModels2`, `modelOverride*`, active `modelConfig`, and (with `--hard`) `featureModelConfigs`.

## Autostart (optional)

### macOS LaunchAgent

Save as `~/Library/LaunchAgents/com.grok-sucks.plist` (edit paths):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.grok-sucks</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/PATH/TO/grok-sucks/grok_sucks.py</string>
    <string>watch</string>
    <string>--interval</string>
    <string>5</string>
    <string>--hard</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/grok-sucks.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/grok-sucks.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.grok-sucks.plist
```

Still run `once` / `patch` once after install (and after Cursor updates), then restart Cursor so the UI patch loads.

### Linux (systemd user)

`~/.config/systemd/user/grok-sucks.service`:

```ini
[Unit]
Description=Keep Cursor Grok disabled

[Service]
ExecStart=/usr/bin/python3 /PATH/TO/grok-sucks/grok_sucks.py watch --interval 5 --hard
Restart=always

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now grok-sucks.service
```

## Limits

- **Unofficial** — Cursor updates may change minified symbols; `patch` will report `pattern not found`.
- Patching the app may affect code signature / Gatekeeper on some systems.
- Does not claim to control **server-side** Auto routing if Grok is chosen off-machine.
- If you intentionally want Grok back: `python grok_sucks.py unpatch` and restart.

## License

MIT
