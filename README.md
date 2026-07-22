# grok-sucks

Keep **Cursor Grok\*** models disabled in local Cursor settings.

Cursor sometimes re-enables Grok after you turn it off in **Settings → Models**, and nudges new chats toward it. That can push you onto a more expensive path via Auto / recommended model. See the [forum report](https://forum.cursor.com/t/grok-re-enables-itself-after-being-disabled-in-settings/165894) (acknowledged by Cursor staff).

This is a tiny **stdlib-only** Python script that writes the same preference flags the Models UI uses — directly in Cursor’s local state DB — and optionally polls so Grok stays off.

> Unofficial. Not affiliated with Cursor or xAI. Storage format can change on Cursor updates. Use at your own risk.

## Requirements

- Python 3.10+
- Cursor installed (reads/writes its local `state.vscdb`)

No pip packages.

## Install

```bash
git clone https://github.com/svasenkov/grok-sucks.git
cd grok-sucks
```

## Usage

```bash
# What Cursor currently has for Grok / composer
python grok_sucks.py status

# One-shot: disable Grok* toggles; move active surfaces off Grok
python grok_sucks.py once

# Preview without writing
python grok_sucks.py once --dry-run

# Also strip Grok from feature fallback lists / explore subagent default
python grok_sucks.py once --hard

# Poll every 5s and re-disable if Cursor turns Grok back on
python grok_sucks.py watch --interval 5 --fallback composer-2.5 --hard
```

Default command is `watch`.

| Flag | Default | Meaning |
|------|---------|---------|
| `--interval` | `5` | Seconds between polls in `watch` |
| `--fallback` | `composer-2.5` | Model to select when a surface was on Grok |
| `--hard` | off | Also scrub `featureModelConfigs` fallbacks / subagent defaults |
| `--dry-run` | off | Print actions, do not write |
| `--db` | auto | Override path to `state.vscdb` |

### Match rule

Any model id whose name starts with `grok` (case-insensitive), e.g. `grok-4.5`, `grok-code-fast-1`.

## How it works

Cursor stores Models toggles in SQLite:

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` |
| Linux | `~/.config/Cursor/User/globalStorage/state.vscdb` |
| Windows | `%APPDATA%\Cursor\User\globalStorage\state.vscdb` |

Key: `…persistentStorage.applicationUser` → `aiSettings`:

- `modelOverrideEnabled` / `modelOverrideDisabled` — Settings → Models toggles
- `modelConfig.*.modelName` / `selectedModels` — active picker per surface

On each pass the script:

1. Moves every `grok*` id from **enabled** to **disabled**
2. If composer / cmd-k / etc. is on Grok, switches it to `--fallback`
3. With `--hard`, removes Grok from `featureModelConfigs` fallback lists and explore subagent default

Writes only when something actually changed. Uses `BEGIN IMMEDIATE` and retries on `database is locked`.

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

- **Unofficial** local storage — Cursor updates may rename keys or fields.
- While Cursor is running it may rewrite preferences from memory; that is why `watch` exists.
- Does not claim to control **server-side** Auto routing if Grok is chosen off-machine.
- If you intentionally enable Grok in the UI, the watcher will turn it off again (by design).

## License

MIT
