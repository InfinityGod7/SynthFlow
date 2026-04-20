# 🎙 SynthFlow — AI Voice Dictation for Windows

Your own local clone of Wispr Flow. Hold a hotkey to record, release to
transcribe and paste — using OpenAI Whisper + GPT cleanup.

---

## Features

- **Global hotkey** — Hold `Ctrl+Shift` anywhere to record
- **OpenAI Whisper** — Accurate speech-to-text in 100+ languages
- **AI cleanup** — GPT-4o-mini removes filler words, fixes punctuation
- **Auto-paste** — Transcribed text is pasted directly into any app
- **System tray** — Runs quietly in the background
- **Settings UI** — Configure API key, hotkey, language, models

---

## Quick Start (run from source)

```
pip install openai sounddevice soundfile numpy pyperclip keyboard pystray pillow pynput
python synthflow.py
```

---

## Build a standalone .exe

Requires Windows + Python 3.10+:

```
build.bat
```

The `.exe` will appear in `dist\SynthFlow.exe`. No Python needed to run it.

---

## Setup

1. Launch SynthFlow — settings window opens automatically on first run
2. Paste your **OpenAI API key** (get one at platform.openai.com)
3. Click **Save Settings**
4. The app minimizes to system tray — right-click the mic icon to access settings

---

## Usage

| Action | How |
|--------|-----|
| Start recording | Hold `Ctrl+Shift` (or your custom hotkey) |
| Stop + transcribe | Release the hotkey |
| Open settings | Right-click tray icon → Settings |
| Quit | Right-click tray icon → Quit |

### Status indicators (bottom of screen)

| Color | Meaning |
|-------|---------|
| 🔴 Red | Recording |
| 🔵 Blue | Transcribing |
| 🟣 Purple | AI cleanup running |
| 🟢 Green | Pasted! |
| 🟠 Orange | No API key set |

---

## Configuration (`~/.synthflow.ini`)

| Setting | Default | Description |
|---------|---------|-------------|
| `api_key` | *(empty)* | Your OpenAI API key |
| `hotkey` | `ctrl+shift` | Hold to record |
| `language` | `en` | ISO language code |
| `cleanup` | `true` | Enable AI text polish |
| `cleanup_model` | `gpt-4o-mini` | Model for cleanup |
| `model` | `whisper-1` | Whisper model |

---

## Cost estimate

A typical 10-second voice note costs roughly **$0.001–$0.002**
(Whisper: $0.006/min · GPT-4o-mini: very cheap for short cleanup).

---

## Customization ideas

- Add a `--language` flag for multilingual switching
- Add voice shortcuts / snippet expansion
- Build a transcript history log
- Add tone modes (formal, casual, bullet points)
- Swap OpenAI for a local Whisper model (whisper.cpp) for offline use
