# delay-audio-stream

Stream audio from any input device (e.g. iPhone) to a browser with a configurable delay.

Useful for syncing audio broadcasts which arrive earlier.

## Requirements

- macOS
- [Homebrew](https://brew.sh)

## Install

```bash
brew install uv
git clone https://github.com/chigkim/delay-audio-stream.git
cd delay-audio-stream
```

## Usage

```bash
uv run delay_stream.py
```
`

This starts the server using iPhone as the default input with a 40 second delay on port 8080. Open `https://localhost:8080` in your browser.

On first run, macOS may block audio capture. If you hear silence:

1. Open **System Settings → Privacy & Security → Microphone**
2. Enable access for **Terminal** (or whichever terminal app you use)
3. Restart the script

### List available devices

```bash
uv run delay_stream.py --list-devices
```

### Common options

| Flag | Long form | Description | Default |
|------|-----------|-------------|---------|
| `-d` | `--delay` | Default delay in seconds | `40` |
| `-p` | `--port` | Web server port | `8080` |
| `-i` | `--input` | Input device number (from `--list-devices`) | iPhone |
| `-o` | `--output` | Output device number for live monitor playback | none |
| `-m` | `--monitor` | Prompt to select monitor output device | — |
| `-l` | `--list-devices` | List all input/output devices and exit | — |

### Examples

```bash
# iPhone input, 40 second delay, port 8080
uv run delay_stream.py -d 40 -p 8080

# Select input and output devices by number
uv run delay_stream.py --list-devices
uv run delay_stream.py -i 1 -o 2

# Monitor output with interactive device selection
uv run delay_stream.py -m
```

## Browser

The server runs over HTTPS with a self-signed certificate (generated automatically on first run, valid forever). Your browser will show a security warning — this is expected.

- **Desktop**: click **Advanced** → **Proceed**
- **iPhone**: tap **Show Details** → **visit this website**, then reload the page

Once connected, set the delay in seconds and press **Play**. The audio starts immediately from the current live position minus the delay. Press **Pause** to stop and **Play** again to jump back to live.

## Run without cloning

```bash
uv run https://raw.githubusercontent.com/chigkim/delay-audio-stream/main/delay_stream.py
```

