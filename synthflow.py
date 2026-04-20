"""
SynthFlow - AI Voice Dictation for Windows
Hold Ctrl+Shift to record, release to transcribe and paste.
"""

import threading
import time
import tempfile
import os
import sys
import queue
import json
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import sounddevice as sd
import soundfile as sf
import numpy as np
from openai import OpenAI
import pyperclip
import keyboard
import pystray
from PIL import Image, ImageDraw
import configparser
import datetime

# winreg is Windows-only; guard so the file is importable on other platforms
try:
    import winreg
    _WINREG_AVAILABLE = True
except ImportError:
    _WINREG_AVAILABLE = False

APP_NAME = "SynthFlow"
STARTUP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".synthflow.ini")

DEFAULT_CONFIG = {
    "provider": "openai",           # "openai" or "google"
    "api_key": "",
    "hotkey": "ctrl+shift",
    "model": "whisper-1",
    "cleanup": "true",
    "cleanup_model": "gpt-4o-mini",
    "sample_rate": "16000",
    "language": "en",
    "audio_device": "",             # empty = system default
    "run_at_startup": "false",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.0-flash",
}

def load_config():
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE)
    if "synthflow" not in cfg:
        cfg["synthflow"] = DEFAULT_CONFIG
    return cfg["synthflow"]

def save_config(section_dict):
    cfg = configparser.ConfigParser()
    cfg["synthflow"] = section_dict
    with open(CONFIG_FILE, "w") as f:
        cfg.write(f)

# ── Windows Startup Registry ──────────────────────────────────────────────────

def set_run_at_startup(enable: bool):
    """Add or remove SynthFlow from Windows startup via the registry."""
    if not _WINREG_AVAILABLE:
        return
    exe_path = sys.executable if not getattr(sys, "frozen", False) else sys.executable
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY,
            0, winreg.KEY_SET_VALUE
        )
        if enable:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{exe_path}"')
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print(f"[startup registry] {e}")

def is_run_at_startup() -> bool:
    """Check whether the startup registry key is currently set."""
    if not _WINREG_AVAILABLE:
        return False
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY,
            0, winreg.KEY_READ
        )
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False

# ── Audio Device Helpers ──────────────────────────────────────────────────────

def get_input_devices() -> list[tuple[int, str]]:
    """Return list of (device_index, display_name) for all input devices."""
    devices = []
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            devices.append((i, f"{dev['name']} (#{i})"))
    return devices

def device_index_from_name(name: str):
    """Resolve a saved device display-name back to its index, or None for default."""
    if not name:
        return None
    for idx, display in get_input_devices():
        if display == name:
            return idx
    return None

# ── Audio Recording ───────────────────────────────────────────────────────────

class AudioRecorder:
    def __init__(self, sample_rate=16000, device=None):
        self.sample_rate = sample_rate
        self.device = device      # None = system default
        self.recording = False
        self.frames = []

    def start(self):
        self.frames = []
        self.recording = True
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time_info, status):
        if self.recording:
            self.frames.append(indata.copy())

    def stop(self):
        self.recording = False
        if hasattr(self, "_stream"):
            self._stream.stop()
            self._stream.close()
        if not self.frames:
            return None
        audio = np.concatenate(self.frames, axis=0)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        sf.write(tmp.name, audio, self.sample_rate)
        return tmp.name


# ── Transcription + Cleanup ───────────────────────────────────────────────────

CLEANUP_SYSTEM = """You are a voice transcription cleanup assistant.
Your job is to clean up raw speech-to-text output:
- Remove filler words (um, uh, like, you know, etc.)
- Fix run-on sentences with proper punctuation
- Capitalize correctly
- Keep the meaning and tone exactly the same
- Do NOT add new content, summarize, or change the intent
- Return ONLY the cleaned text, nothing else."""

def transcribe(audio_path: str, client: OpenAI, config: dict) -> str:
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model=config.get("model", "whisper-1"),
            file=f,
            language=config.get("language", "en"),
        )
    return result.text

def cleanup_text(text: str, client: OpenAI, config: dict) -> str:
    if not text.strip():
        return text
    resp = client.chat.completions.create(
        model=config.get("cleanup_model", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": CLEANUP_SYSTEM},
            {"role": "user", "content": text},
        ],
        max_tokens=1000,
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


# ── Gemini Transcription + Cleanup ───────────────────────────────────────────

def transcribe_gemini(audio_path: str, api_key: str, config: dict) -> str:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(config.get("gemini_model", "gemini-2.0-flash"))
    audio_file = genai.upload_file(path=audio_path, mime_type="audio/wav")
    try:
        response = model.generate_content([
            audio_file,
            "Transcribe this audio exactly as spoken. Return only the transcription text, nothing else.",
        ])
        return response.text.strip()
    finally:
        genai.delete_file(audio_file.name)

def cleanup_text_gemini(text: str, api_key: str, config: dict) -> str:
    if not text.strip():
        return text
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        config.get("gemini_model", "gemini-2.0-flash"),
        system_instruction=CLEANUP_SYSTEM,
    )
    response = model.generate_content(text)
    return response.text.strip()


# ── System Tray Icon ──────────────────────────────────────────────────────────

def make_tray_icon(color="#4A90D9"):
    """Draw a simple microphone icon."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Mic body
    d.rounded_rectangle([22, 4, 42, 36], radius=10, fill=color)
    # Mic stand arc
    d.arc([12, 20, 52, 52], start=0, end=180, fill=color, width=4)
    # Stand pole
    d.rectangle([30, 48, 34, 58], fill=color)
    # Stand base
    d.rectangle([22, 56, 42, 60], fill=color)
    return img

def make_recording_icon():
    return make_tray_icon("#E74C3C")  # red when recording


# ── Main App ──────────────────────────────────────────────────────────────────

class SynthFlowApp:
    def __init__(self):
        self.config = dict(load_config())
        device_idx = device_index_from_name(self.config.get("audio_device", ""))
        self.recorder = AudioRecorder(
            int(self.config.get("sample_rate", 16000)),
            device=device_idx,
        )
        self.client = None
        self.is_recording = False
        self.status_queue = queue.Queue()
        self.tray_icon = None
        self._hotkey_held = False
        self._hotkey_registered = False
        self.root = None
        self._overlay = None

        # Circular log buffer: list of strings (newest last)
        self._log: list[str] = []
        self._log_widget = None   # set once settings window is built

    # ── API client ──────────────────────────────────────────────────────────

    def get_client(self):
        api_key = self.config.get("api_key", "").strip()
        if not api_key:
            return None
        if self.client is None:
            self.client = OpenAI(api_key=api_key)
        return self.client

    def reset_client(self):
        self.client = None

    # ── Logging ─────────────────────────────────────────────────────────────

    def _log_entry(self, message: str):
        """Append a timestamped entry to the in-memory log and update the widget."""
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {message}"
        self._log.append(entry)
        if len(self._log) > 200:          # keep last 200 entries
            self._log = self._log[-200:]
        if self._log_widget:
            try:
                self._log_widget.configure(state="normal")
                self._log_widget.insert("end", entry + "\n")
                self._log_widget.see("end")
                self._log_widget.configure(state="disabled")
            except Exception:
                pass

    # ── Overlay ─────────────────────────────────────────────────────────────

    def show_overlay(self, text, color="#E74C3C"):
        """Show a small always-on-top status pill."""
        if self._overlay:
            try:
                self._overlay.destroy()
            except Exception:
                pass
        self._overlay = tk.Toplevel()
        self._overlay.overrideredirect(True)
        self._overlay.attributes("-topmost", True)
        self._overlay.attributes("-alpha", 0.88)
        self._overlay.configure(bg=color)

        screen_w = self._overlay.winfo_screenwidth()
        screen_h = self._overlay.winfo_screenheight()
        w, h = 220, 44
        x = (screen_w - w) // 2
        y = screen_h - 120
        self._overlay.geometry(f"{w}x{h}+{x}+{y}")

        lbl = tk.Label(
            self._overlay,
            text=text,
            fg="white",
            bg=color,
            font=("Segoe UI", 13, "bold"),
        )
        lbl.pack(expand=True, fill="both", padx=8, pady=6)

    def hide_overlay(self):
        if self._overlay:
            try:
                self._overlay.destroy()
            except Exception:
                pass
            self._overlay = None

    # ── Recording flow ───────────────────────────────────────────────────────

    def start_recording(self):
        if self.is_recording:
            return
        provider = self.config.get("provider", "openai")
        if provider == "google":
            ready = bool(self.config.get("gemini_api_key", "").strip())
        else:
            ready = bool(self.get_client())
        if not ready:
            self.show_overlay("⚠ Set your API key first", "#E67E22")
            self.root.after(2000, self.hide_overlay)
            return

        self.is_recording = True
        self.recorder.start()
        self.show_overlay("🎙  Recording…", "#E74C3C")
        if self.tray_icon:
            try:
                self.tray_icon.icon = make_recording_icon()
            except Exception:
                pass

    def stop_recording(self):
        if not self.is_recording:
            return
        self.is_recording = False
        audio_path = self.recorder.stop()

        if self.tray_icon:
            try:
                self.tray_icon.icon = make_tray_icon()
            except Exception:
                pass

        if not audio_path:
            self.hide_overlay()
            return

        self.show_overlay("⚙  Transcribing…", "#3498DB")
        threading.Thread(target=self._process_audio, args=(audio_path,), daemon=True).start()

    def _process_audio(self, audio_path):
        try:
            provider = self.config.get("provider", "openai")

            # ── Transcribe ───────────────────────────────────────────────
            if provider == "google":
                gemini_key = self.config.get("gemini_api_key", "").strip()
                text = transcribe_gemini(audio_path, gemini_key, self.config)
            else:
                text = transcribe(audio_path, self.get_client(), self.config)
            self._log_entry(f"Transcribed: {text[:120]}{'…' if len(text) > 120 else ''}")

            if not text.strip():
                self.root.after(0, lambda: self.show_overlay("(nothing heard)", "#7F8C8D"))
                self.root.after(1500, self.hide_overlay)
                self._log_entry("No speech detected.")
                return

            # ── AI Cleanup ───────────────────────────────────────────────
            do_cleanup = self.config.get("cleanup", "true").lower() == "true"
            if do_cleanup:
                self.root.after(0, lambda: self.show_overlay("✨ Polishing…", "#9B59B6"))
                if provider == "google":
                    text = cleanup_text_gemini(text, gemini_key, self.config)
                else:
                    text = cleanup_text(text, self.get_client(), self.config)
                self._log_entry(f"Cleaned:     {text[:120]}{'…' if len(text) > 120 else ''}")

            # ── Paste ────────────────────────────────────────────────────
            pyperclip.copy(text)

            # Wait for the user to have fully released the hotkey so our
            # Ctrl+V isn't swallowed or mis-interpreted by the OS.
            deadline = time.time() + 1.0
            hotkey = self.config.get("hotkey", "ctrl+shift")
            while time.time() < deadline:
                if not any(keyboard.is_pressed(k) for k in hotkey.split("+")):
                    break
                time.sleep(0.05)
            time.sleep(0.2)   # small grace period after keys are up

            keyboard.send("ctrl+v")

            self.root.after(0, lambda: self.show_overlay("✅ Pasted!", "#27AE60"))
            self.root.after(1500, self.hide_overlay)
            self._log_entry("Pasted OK.")

        except Exception as e:
            err_msg = str(e)
            self._log_entry(f"ERROR: {err_msg}")
            short = err_msg[:60]
            self.root.after(0, lambda: self.show_overlay(f"❌ {short}", "#C0392B"))
            self.root.after(3000, self.hide_overlay)

        finally:
            # Always clean up the temp WAV, even if transcription failed.
            try:
                if os.path.exists(audio_path):
                    os.unlink(audio_path)
            except OSError:
                pass

    # ── Hotkey registration ──────────────────────────────────────────────────

    def register_hotkey(self):
        if self._hotkey_registered:
            keyboard.unhook_all()
            self._hotkey_registered = False

        hotkey = self.config.get("hotkey", "ctrl+shift")

        def on_press():
            if not self._hotkey_held:
                self._hotkey_held = True
                self.root.after(0, self.start_recording)

        def on_release():
            if self._hotkey_held:
                self._hotkey_held = False
                self.root.after(0, self.stop_recording)

        try:
            keyboard.add_hotkey(hotkey, on_press, trigger_on_release=False)
            keyboard.add_hotkey(hotkey, on_release, trigger_on_release=True)
            self._hotkey_registered = True
        except Exception as e:
            messagebox.showerror("Hotkey Error", str(e))

    # ── Settings window ──────────────────────────────────────────────────────

    def open_settings(self):
        if self.root:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()

    def build_settings_window(self):
        self.root = tk.Tk()
        self.root.title("SynthFlow Settings")
        self.root.geometry("520x680")
        self.root.resizable(False, False)
        self.root.configure(bg="#1E1E2E")
        self.root.protocol("WM_DELETE_WINDOW", self.root.withdraw)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TLabel",      background="#1E1E2E", foreground="#CDD6F4", font=("Segoe UI", 10))
        style.configure("TEntry",      fieldbackground="#313244", foreground="#CDD6F4", insertcolor="#CDD6F4")
        style.configure("TButton",     background="#89B4FA", foreground="#1E1E2E", font=("Segoe UI", 10, "bold"))
        style.configure("TCheckbutton",background="#1E1E2E", foreground="#CDD6F4")
        style.configure("TCombobox",   fieldbackground="#313244", foreground="#CDD6F4", selectbackground="#45475A")
        style.configure("TFrame",      background="#1E1E2E")

        pad = {"padx": 20, "pady": 5}

        # ── Header ──────────────────────────────────────────────────────────
        header = tk.Frame(self.root, bg="#181825", pady=14)
        header.pack(fill="x")
        tk.Label(header, text="🎙  SynthFlow", bg="#181825", fg="#89B4FA",
                 font=("Segoe UI", 18, "bold")).pack()
        tk.Label(header, text="AI Voice Dictation for Windows", bg="#181825",
                 fg="#6C7086", font=("Segoe UI", 10)).pack()

        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill="x")
        frame.columnconfigure(1, weight=1)

        row = 0

        # ── Provider ────────────────────────────────────────────────────────
        ttk.Label(frame, text="AI Provider").grid(row=row, column=0, sticky="w", **pad)
        self._provider_var = tk.StringVar(value=self.config.get("provider", "openai"))
        provider_cb = ttk.Combobox(frame, textvariable=self._provider_var,
                                   values=["openai", "google"], width=14, state="readonly")
        provider_cb.grid(row=row, column=1, sticky="w", **pad); row += 1

        # ── OpenAI API Key ──────────────────────────────────────────────────
        ttk.Label(frame, text="OpenAI API Key").grid(row=row, column=0, sticky="w", **pad)
        self._api_var = tk.StringVar(value=self.config.get("api_key", ""))
        ttk.Entry(frame, textvariable=self._api_var, width=32, show="•").grid(
            row=row, column=1, sticky="ew", **pad); row += 1

        # ── Gemini API Key ──────────────────────────────────────────────────
        ttk.Label(frame, text="Gemini API Key").grid(row=row, column=0, sticky="w", **pad)
        self._gemini_key_var = tk.StringVar(value=self.config.get("gemini_api_key", ""))
        ttk.Entry(frame, textvariable=self._gemini_key_var, width=32, show="•").grid(
            row=row, column=1, sticky="ew", **pad); row += 1

        # ── Hotkey ──────────────────────────────────────────────────────────
        ttk.Label(frame, text="Hold hotkey to record").grid(row=row, column=0, sticky="w", **pad)
        self._hotkey_var = tk.StringVar(value=self.config.get("hotkey", "ctrl+shift"))
        ttk.Entry(frame, textvariable=self._hotkey_var, width=20).grid(
            row=row, column=1, sticky="w", **pad); row += 1

        # ── Audio device ────────────────────────────────────────────────────
        ttk.Label(frame, text="Microphone").grid(row=row, column=0, sticky="w", **pad)
        input_devices = get_input_devices()
        device_names  = ["System Default"] + [name for _, name in input_devices]
        saved_device  = self.config.get("audio_device", "")
        self._device_var = tk.StringVar(
            value=saved_device if saved_device in device_names else "System Default"
        )
        device_cb = ttk.Combobox(frame, textvariable=self._device_var,
                                  values=device_names, width=30, state="readonly")
        device_cb.grid(row=row, column=1, sticky="w", **pad); row += 1

        # ── Language ────────────────────────────────────────────────────────
        ttk.Label(frame, text="Language (ISO code)").grid(row=row, column=0, sticky="w", **pad)
        self._lang_var = tk.StringVar(value=self.config.get("language", "en"))
        ttk.Entry(frame, textvariable=self._lang_var, width=10).grid(
            row=row, column=1, sticky="w", **pad); row += 1

        # ── AI Cleanup ──────────────────────────────────────────────────────
        ttk.Label(frame, text="AI cleanup (remove filler words)").grid(row=row, column=0, sticky="w", **pad)
        self._cleanup_var = tk.BooleanVar(value=self.config.get("cleanup", "true") == "true")
        ttk.Checkbutton(frame, variable=self._cleanup_var).grid(
            row=row, column=1, sticky="w", **pad); row += 1

        # ── Cleanup model ───────────────────────────────────────────────────
        ttk.Label(frame, text="Cleanup model").grid(row=row, column=0, sticky="w", **pad)
        self._cmodel_var = tk.StringVar(value=self.config.get("cleanup_model", "gpt-4o-mini"))
        ttk.Combobox(frame, textvariable=self._cmodel_var, width=18,
                     values=["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"]).grid(
            row=row, column=1, sticky="w", **pad); row += 1

        # ── Whisper model ───────────────────────────────────────────────────
        ttk.Label(frame, text="Whisper model").grid(row=row, column=0, sticky="w", **pad)
        self._wmodel_var = tk.StringVar(value=self.config.get("model", "whisper-1"))
        ttk.Entry(frame, textvariable=self._wmodel_var, width=16).grid(
            row=row, column=1, sticky="w", **pad); row += 1

        # ── Gemini model ────────────────────────────────────────────────────
        ttk.Label(frame, text="Gemini model").grid(row=row, column=0, sticky="w", **pad)
        self._gmodel_var = tk.StringVar(value=self.config.get("gemini_model", "gemini-2.0-flash"))
        ttk.Combobox(frame, textvariable=self._gmodel_var, width=22,
                     values=["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]).grid(
            row=row, column=1, sticky="w", **pad); row += 1

        # ── Run at startup ──────────────────────────────────────────────────
        ttk.Label(frame, text="Run at Windows startup").grid(row=row, column=0, sticky="w", **pad)
        startup_actual = is_run_at_startup()
        self._startup_var = tk.BooleanVar(value=startup_actual)
        startup_cb = ttk.Checkbutton(frame, variable=self._startup_var)
        startup_cb.grid(row=row, column=1, sticky="w", **pad)
        if not _WINREG_AVAILABLE:
            startup_cb.configure(state="disabled")
            ttk.Label(frame, text="(Windows only)", foreground="#6C7086").grid(
                row=row, column=1, sticky="e", **pad)
        row += 1

        # ── Status label ────────────────────────────────────────────────────
        self._status_lbl = tk.Label(frame, text="", bg="#1E1E2E", fg="#A6E3A1",
                                     font=("Segoe UI", 10))
        self._status_lbl.grid(row=row, column=0, columnspan=2, pady=6); row += 1

        # ── Save button ─────────────────────────────────────────────────────
        def save():
            # Persist all settings
            self.config["provider"]      = self._provider_var.get().strip()
            self.config["api_key"]       = self._api_var.get().strip()
            self.config["gemini_api_key"]= self._gemini_key_var.get().strip()
            self.config["hotkey"]        = self._hotkey_var.get().strip()
            self.config["language"]      = self._lang_var.get().strip()
            self.config["cleanup"]       = "true" if self._cleanup_var.get() else "false"
            self.config["cleanup_model"] = self._cmodel_var.get().strip()
            self.config["model"]         = self._wmodel_var.get().strip()
            self.config["gemini_model"]  = self._gmodel_var.get().strip()
            self.config["run_at_startup"]= "true" if self._startup_var.get() else "false"

            chosen_device = self._device_var.get()
            self.config["audio_device"] = "" if chosen_device == "System Default" else chosen_device

            save_config(self.config)
            self.reset_client()

            # Update recorder device live
            device_idx = device_index_from_name(self.config["audio_device"])
            self.recorder.device = device_idx

            # Update startup registry
            set_run_at_startup(self._startup_var.get())

            self.register_hotkey()
            self._log_entry("Settings saved.")
            self._status_lbl.config(text="✅ Saved!")
            self.root.after(2500, lambda: self._status_lbl.config(text=""))

        btn_frame = tk.Frame(self.root, bg="#1E1E2E")
        btn_frame.pack(pady=4)
        tk.Button(btn_frame, text="  Save Settings  ", command=save,
                  bg="#89B4FA", fg="#1E1E2E", font=("Segoe UI", 11, "bold"),
                  relief="flat", padx=10, pady=6, cursor="hand2").pack()

        # ── Log panel ───────────────────────────────────────────────────────
        log_frame = tk.Frame(self.root, bg="#1E1E2E")
        log_frame.pack(fill="both", expand=True, padx=16, pady=(6, 4))

        tk.Label(log_frame, text="Recent activity", bg="#1E1E2E", fg="#6C7086",
                 font=("Segoe UI", 9)).pack(anchor="w")

        self._log_widget = scrolledtext.ScrolledText(
            log_frame,
            height=7,
            bg="#11111B",
            fg="#A6ADC8",
            font=("Consolas", 9),
            relief="flat",
            state="disabled",
            wrap="word",
            insertbackground="#CDD6F4",
        )
        self._log_widget.pack(fill="both", expand=True)

        # Replay any log entries that arrived before the window was built
        if self._log:
            self._log_widget.configure(state="normal")
            for entry in self._log:
                self._log_widget.insert("end", entry + "\n")
            self._log_widget.see("end")
            self._log_widget.configure(state="disabled")

        def clear_log():
            self._log.clear()
            self._log_widget.configure(state="normal")
            self._log_widget.delete("1.0", "end")
            self._log_widget.configure(state="disabled")

        tk.Button(log_frame, text="Clear log", command=clear_log,
                  bg="#313244", fg="#CDD6F4", font=("Segoe UI", 8),
                  relief="flat", cursor="hand2").pack(anchor="e", pady=2)

        # ── Footer tip ──────────────────────────────────────────────────────
        tip = tk.Label(self.root,
            text=f"Hold  [{self.config.get('hotkey','ctrl+shift')}]  to record · Release to transcribe & paste",
            bg="#181825", fg="#6C7086", font=("Segoe UI", 9))
        tip.pack(fill="x", side="bottom", pady=6)

        return self.root

    # ── System tray ──────────────────────────────────────────────────────────

    def run_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Settings", lambda: self.root.after(0, self.open_settings)),
            pystray.MenuItem("Quit", self.quit_app),
        )
        self.tray_icon = pystray.Icon(
            "SynthFlow",
            make_tray_icon(),
            "SynthFlow",
            menu,
        )
        self.tray_icon.run()

    def quit_app(self):
        keyboard.unhook_all()
        if self.tray_icon:
            self.tray_icon.stop()
        if self.root:
            self.root.quit()

    # ── Run ──────────────────────────────────────────────────────────────────

    def run(self):
        root = self.build_settings_window()

        # Show settings on first launch (no API key yet)
        if not self.config.get("api_key", "").strip():
            root.deiconify()
        else:
            root.withdraw()

        self.register_hotkey()

        # Tray runs in background thread
        tray_thread = threading.Thread(target=self.run_tray, daemon=True)
        tray_thread.start()

        root.mainloop()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = SynthFlowApp()
    app.run()
