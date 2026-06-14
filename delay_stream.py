#!/usr/bin/env python3
#
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "flask>=3.1.3",
#     "flask-sock>=0.7.0",
#     "numpy>=2.4.6",
#     "sounddevice>=0.5.5",
# ]
# ///

"""
Captures audio from a named input device (e.g. iPhone via USB) and
streams it to a browser via WebSocket with a configurable delay.

Usage:
    pip install sounddevice flask flask-sock numpy
    python delay_stream.py --delay 40 --device iPhone --port 5000
    Then open http://localhost:5000
"""
import argparse
import collections
import os
import queue
import socket
import subprocess
import threading

import numpy as np
import sounddevice as sd
from flask import Flask, render_template_string, request
from flask_sock import Sock

# ---------------------------------------------------------------------------
# Runtime config (overwritten in main)
# ---------------------------------------------------------------------------
SAMPLE_RATE = 44100
CHANNELS = 2
DTYPE = "float32"   # CoreAudio native; avoids zeros when requesting int16 from iOS devices
CHUNK_FRAMES = 4096
DEFAULT_DELAY = 30.0

def _local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _ensure_certs(cert="cert.pem", key="key.pem"):
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key
    ip = _local_ip()
    print(f"Generating self-signed certificate (localhost + {ip}, expires 9999-12-31)...")
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", key, "-out", cert, "-nodes",
        "-not_after", "99991231235959Z",
        "-subj", "/CN=localhost",
        "-addext", f"subjectAltName=DNS:localhost,IP:127.0.0.1,IP:{ip}",
    ], check=True, capture_output=True)
    print(f"  cert.pem / key.pem created (valid until 9999-12-31).")
    return cert, key


app = Flask(__name__)
sock = Sock(app)

# Global rolling buffer — holds last MAX_BUFFER_SECONDS of audio.
# All WebSocket connections read directly from it using an absolute chunk index.
MAX_BUFFER_SECONDS = 300
_buffer: collections.deque = collections.deque()
_buffer_cond = threading.Condition()
_total_chunks = 0        # total chunks written since start
_max_buffer_chunks = 1   # set in main once SAMPLE_RATE/CHUNK_FRAMES are known

_chunk_count = 0
_last_silent: bool | None = None
_monitor_q: queue.Queue | None = None


def _audio_callback(indata, frames, time_info, status):
    global _chunk_count, _total_chunks, _last_silent
    pcm = (indata * 32767).clip(-32768, 32767).astype(np.int16)
    chunk = bytes(pcm)
    _chunk_count += 1
    if _chunk_count % 50 == 0:
        silent = float(np.sqrt(np.mean(indata ** 2))) == 0.0
        if silent != _last_silent:
            if silent:
                print("[warning] silence detected — check input device or permissions", flush=True)
            else:
                print("[info] signal detected", flush=True)
            _last_silent = silent
    with _buffer_cond:
        _buffer.append(chunk)
        if len(_buffer) > _max_buffer_chunks:
            _buffer.popleft()
        _total_chunks += 1
        _buffer_cond.notify_all()
    if _monitor_q is not None:
        try:
            _monitor_q.put_nowait(indata.copy())
        except queue.Full:
            pass


def _monitor_callback(outdata, frames, time_info, status):
    try:
        outdata[:] = _monitor_q.get_nowait()
    except queue.Empty:
        outdata[:] = 0


@app.route("/")
def index():
    return render_template_string(
        _HTML,
        default_delay=DEFAULT_DELAY,
        sample_rate=SAMPLE_RATE,
        channels=CHANNELS,
    )


@sock.route("/ws")
def ws_stream(ws):
    try:
        delay_secs = max(0.0, float(request.args.get("delay", DEFAULT_DELAY)))
    except ValueError:
        delay_secs = DEFAULT_DELAY

    delay_chunks = max(0, int(delay_secs * SAMPLE_RATE / CHUNK_FRAMES))

    # Wait until the buffer has enough history for the requested delay.
    # If delay is 0 or the server has been running long enough, skip immediately.
    while delay_chunks > 0:
        with _buffer_cond:
            _buffer_cond.wait(timeout=1.0)
            current = _total_chunks
        if current >= delay_chunks:
            break
        current_secs = round(current * CHUNK_FRAMES / SAMPLE_RATE)
        needed_secs = round(delay_secs)
        try:
            ws.send(f"buffering:{current_secs}:{needed_secs}")
        except Exception:
            return

    with _buffer_cond:
        read_idx = _total_chunks - delay_chunks

    try:
        while True:
            with _buffer_cond:
                # Wait for the next chunk to be written
                while _total_chunks <= read_idx:
                    _buffer_cond.wait(timeout=2.0)

                oldest = _total_chunks - len(_buffer)
                if read_idx < oldest:
                    # Client fell too far behind; jump to oldest available
                    read_idx = oldest

                chunk = _buffer[read_idx - oldest]
                read_idx += 1

            ws.send(chunk)
    except Exception:
        pass


_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Audio Delay</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, sans-serif;
      background: #0f0f0f;
      color: #e8e8e8;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      gap: 2rem;
      padding: 2rem 1rem;
    }
    h1 { font-size: 1.4rem; font-weight: 500; letter-spacing: 0.04em; margin: 0; text-align: center; }
    .row { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; justify-content: center; }
    label { font-size: 1rem; color: #999; }
    input[type=number] {
      width: 100px;
      padding: 0.75rem 0.6rem;
      font-size: 1.1rem;
      background: #1c1c1c;
      border: 1px solid #333;
      border-radius: 10px;
      color: #e8e8e8;
      text-align: center;
      -webkit-appearance: none;
    }
    input[type=number]:focus { outline: none; border-color: #555; }
    button {
      padding: 0.75rem 2rem;
      font-size: 1.1rem;
      font-weight: 600;
      border: none;
      border-radius: 10px;
      cursor: pointer;
      background: #2563eb;
      color: #fff;
      transition: background 0.15s;
      min-width: 110px;
      min-height: 48px;
      touch-action: manipulation;
    }
    button:active { opacity: 0.85; }
    button.pausing { background: #dc2626; }
    #status { font-size: 0.9rem; color: #666; min-height: 1em; text-align: center; max-width: 300px; }
    #level { font-size: 1rem; color: #888; font-variant-numeric: tabular-nums; }
    @media (max-width: 400px) {
      h1 { font-size: 1.2rem; }
      input[type=number] { width: 90px; }
    }
  </style>
</head>
<body>
  <h1>Audio Delay Stream</h1>
  <div class="row">
    <label for="delayInput">Delay (s)</label>
    <input id="delayInput" type="number" min="0" max="600" step="0.1"
           value="{{ default_delay }}">
    <button id="btn" onclick="toggle()">Play</button>
  </div>
  <div id="level" aria-live="off">–</div>
  <div id="status">Press Play to connect.</div>

  <script>
    const SAMPLE_RATE = {{ sample_rate }};
    const CHANNELS    = {{ channels }};
    const DEFAULT_DELAY = {{ default_delay }};

    let ws = null, audioCtx = null, nextTime = 0, active = false;
    let lastLevelUpdate = 0;

    function toggle() { active ? stop() : start(); }

    function start() {
      const raw = parseFloat(document.getElementById('delayInput').value);
      const delay = Math.max(0, isNaN(raw) ? DEFAULT_DELAY : raw);
      audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
      audioCtx.resume();
      nextTime = 0;

      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      ws = new WebSocket(proto + '://' + location.host + '/ws?delay=' + delay);
      ws.binaryType = 'arraybuffer';

      ws.onopen  = () => setStatus('Connecting…');
      ws.onclose = () => { if (active) setStatus('Disconnected.'); };
      ws.onerror = () => setStatus('Connection error.');

      ws.onmessage = (evt) => {
        if (typeof evt.data === 'string') {
          const [, current, needed] = evt.data.split(':');
          const pct = Math.round(100 * current / needed);
          setStatus('Buffering ' + current + ' / ' + needed + 's (' + pct + '%)…');
          return;
        }

        const int16  = new Int16Array(evt.data);
        const frames = int16.length / CHANNELS;
        const buf    = audioCtx.createBuffer(CHANNELS, frames, SAMPLE_RATE);

        let peak = 0, sumSq = 0, totalSamples = 0;
        for (let ch = 0; ch < CHANNELS; ch++) {
          const out = buf.getChannelData(ch);
          for (let i = 0; i < frames; i++) {
            const s = int16[i * CHANNELS + ch] / 32768.0;
            out[i] = s;
            if (Math.abs(s) > peak) peak = Math.abs(s);
            sumSq += s * s;
            totalSamples++;
          }
        }

        const nowMs = Date.now();
        if (nowMs - lastLevelUpdate >= 1000) {
          const peakDb = peak > 0 ? 20 * Math.log10(peak) : -Infinity;
          const rms = Math.sqrt(sumSq / totalSamples);
          const rmsDb = rms > 0 ? 20 * Math.log10(rms) : -Infinity;
          const fmt = db => isFinite(db) ? db.toFixed(1) + ' dB' : '-∞ dB';
          document.getElementById('level').textContent = 'Peak: ' + fmt(peakDb) + '  RMS: ' + fmt(rmsDb);
          lastLevelUpdate = nowMs;
        }

        const src = audioCtx.createBufferSource();
        src.buffer = buf;
        src.connect(audioCtx.destination);

        const now = audioCtx.currentTime;
        if (nextTime < now + 0.05) nextTime = now + 0.05;
        src.start(nextTime);
        nextTime += buf.duration;

        setStatus('Playing — ' + delay + 's delay.');
      };

      active = true;
      setBtn('Pause', true);
    }

    function stop() {
      if (ws)       { ws.close(); ws = null; }
      if (audioCtx) { audioCtx.close(); audioCtx = null; }
      active = false; nextTime = 0;
      setBtn('Play', false);
      setStatus('Stopped.');
      document.getElementById('level').textContent = '–';
    }

    function setBtn(label, isPausing) {
      const btn = document.getElementById('btn');
      btn.textContent = label;
      btn.classList.toggle('pausing', isPausing);
    }

    function setStatus(msg) {
      document.getElementById('status').textContent = msg;
    }
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream audio from a device to a browser with a delay.")
    parser.add_argument("-d", "--delay",        type=float, default=30.0,  help="Default delay in seconds (default: 30)")
    parser.add_argument("-p", "--port",         type=int,   default=8080,  help="Web server port (default: 8080)")
    parser.add_argument("-r", "--rate",         type=int,   default=None,  help="Override sample rate (default: auto-detect)")
    parser.add_argument("-c", "--channels",     type=int,   default=2,     help="Number of channels (default: 2)")
    parser.add_argument(      "--chunk",        type=int,   default=4096,  help="Chunk size in frames (default: 4096)")
    parser.add_argument("-l", "--list-devices", action="store_true",       help="List input/output devices with numbers and exit")
    parser.add_argument("-i", "--input",        type=int,   default=None,  help="Input device number (from --list-devices; default: iPhone)")
    parser.add_argument("-o", "--output",       type=int,   default=None,  help="Output device number for monitor playback (no monitor if omitted)")
    parser.add_argument("-m", "--monitor",      action="store_true",       help="Prompt to select output device for monitor playback")
    args = parser.parse_args()

    DEFAULT_DELAY = args.delay
    CHANNELS      = args.channels
    CHUNK_FRAMES  = args.chunk

    devices = sd.query_devices()
    input_devices  = [(i, d) for i, d in enumerate(devices) if d["max_input_channels"] > 0]
    output_devices = [(i, d) for i, d in enumerate(devices) if d["max_output_channels"] > 0]

    if args.list_devices:
        print("\nInput devices:")
        for n, (i, d) in enumerate(input_devices, 1):
            print(f"  {n:2d}. {d['name']:<40s}  {d['max_input_channels']}ch  {int(d['default_samplerate'])}Hz")
        print("\nOutput devices:")
        for n, (i, d) in enumerate(output_devices, 1):
            print(f"  {n:2d}. {d['name']:<40s}  {d['max_output_channels']}ch  {int(d['default_samplerate'])}Hz")
        raise SystemExit(0)

    if args.input is not None:
        if not 1 <= args.input <= len(input_devices):
            print(f"Invalid input device number {args.input}. Run --list-devices to see options.")
            raise SystemExit(1)
        device_idx, _ = input_devices[args.input - 1]
    else:
        device_idx = next(
            (i for i, d in input_devices if "iphone" in d["name"].lower()), None
        )
        if device_idx is None:
            print("iPhone not found. Run --list-devices and use -i N to select a device.")
            raise SystemExit(1)

    dev_info    = sd.query_devices(device_idx)
    SAMPLE_RATE = args.rate or int(dev_info["default_samplerate"])
    CHANNELS    = min(args.channels, dev_info["max_input_channels"])
    _max_buffer_chunks = max(1, int(MAX_BUFFER_SECONDS * SAMPLE_RATE / CHUNK_FRAMES))

    cert, key = _ensure_certs()
    ssl_ctx = (cert, key)
    scheme = "https"
    ip = _local_ip()
    print(f"\n  NOTE: Browser will warn about the self-signed certificate.")
    print(f"  Desktop : visit {scheme}://localhost:{args.port} and click 'Advanced' → proceed.")
    print(f"  iPhone  : visit {scheme}://{ip}:{args.port} in Safari,")
    print(f"            tap 'Show Details' → 'visit this website', then reload.\n")

    print(f"Device  : {dev_info['name']}")
    print(f"Rate    : {SAMPLE_RATE} Hz  |  Channels: {CHANNELS}  |  Chunk: {CHUNK_FRAMES} frames")
    print(f"Buffer  : {MAX_BUFFER_SECONDS}s rolling ({_max_buffer_chunks} chunks)")
    print(f"Delay   : {DEFAULT_DELAY}s default (adjustable in browser)")
    print(f"URL     : {scheme}://localhost:{args.port}")
    print(f"Network : {scheme}://{_local_ip()}:{args.port}\n")

    if args.monitor and args.output is None:
        print("\nAvailable output devices:")
        for n, (i, d) in enumerate(output_devices, 1):
            print(f"  {n:2d}. {d['name']:<40s}  {d['max_output_channels']}ch  {int(d['default_samplerate'])}Hz")
        while True:
            try:
                choice = int(input("\nSelect output device number: "))
                if 1 <= choice <= len(output_devices):
                    args.output = choice
                    break
                print(f"Please enter a number between 1 and {len(output_devices)}.")
            except ValueError:
                print("Please enter a valid number.")

    if args.output is not None:
        if not 1 <= args.output <= len(output_devices):
            print(f"Invalid output device number {args.output}. Run --list-devices to see options.")
            raise SystemExit(1)
        monitor_device_idx, monitor_device_info = output_devices[args.output - 1]
        _monitor_q = queue.Queue(maxsize=50)
        monitor_stream = sd.OutputStream(
            device=monitor_device_idx,
            channels=CHANNELS,
            samplerate=SAMPLE_RATE,
            dtype=DTYPE,
            blocksize=CHUNK_FRAMES,
            callback=_monitor_callback,
        )
        monitor_stream.start()
        print(f"Monitor : {monitor_device_info['name']}")

    audio_stream = sd.InputStream(
        device=device_idx,
        channels=CHANNELS,
        samplerate=SAMPLE_RATE,
        dtype=DTYPE,
        blocksize=CHUNK_FRAMES,
        callback=_audio_callback,
    )
    audio_stream.start()

    app.run(host="0.0.0.0", port=args.port, threaded=True, ssl_context=ssl_ctx)
