#!/usr/bin/env python3
"""Плавающее окошко Piper: прелоад синтеза → управление воспроизведением."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

SOCKET_PATH = os.environ.get("SPEAK_PIPER_SOCK", "/tmp/speak_piper_mpv.sock")
CANCEL_FILE = os.environ.get("SPEAK_PIPER_CANCEL", "/tmp/speak_piper.cancel")
RUNNING_FILE = os.environ.get("SPEAK_PIPER_RUNNING", "/tmp/speak_piper.running")
APP_DIR = os.environ.get(
    "SPEAK_PIPER_APP_DIR",
    os.path.dirname(os.path.abspath(__file__)),
)
VOICE_CONF = os.path.join(APP_DIR, "voice.conf")
SPEED_CONF = os.path.join(APP_DIR, "speed.conf")
VOICES_DIR = os.path.join(APP_DIR, "voices")
POLL_MS = 250
LOAD_POLL_MS = 200

VOICE_RE = re.compile(r"^ru_RU-(.+)-medium\.onnx$")
SPEEDS = (1.0, 1.25, 1.5, 1.75, 2.0)


def list_voices() -> list[str]:
    if not os.path.isdir(VOICES_DIR):
        return []
    names: list[str] = []
    for fname in sorted(os.listdir(VOICES_DIR)):
        m = VOICE_RE.match(fname)
        if m and os.path.isfile(os.path.join(VOICES_DIR, fname)):
            names.append(m.group(1))
    return names


def read_voice(default: str = "irina") -> str:
    try:
        with open(VOICE_CONF, encoding="utf-8") as f:
            name = f.read().strip()
        if name:
            return name
    except OSError:
        pass
    return os.environ.get("SPEAK_PIPER_VOICE", default)


def write_voice(name: str) -> None:
    with open(VOICE_CONF, "w", encoding="utf-8") as f:
        f.write(name.strip() + "\n")


def read_speed(default: float = 1.0) -> float:
    try:
        with open(SPEED_CONF, encoding="utf-8") as f:
            value = float(f.read().strip().replace(",", "."))
        if value in SPEEDS:
            return value
    except (OSError, ValueError):
        pass
    env = os.environ.get("SPEAK_PIPER_SPEED")
    if env:
        try:
            value = float(env)
            if value in SPEEDS:
                return value
        except ValueError:
            pass
    return default


def write_speed(speed: float) -> None:
    with open(SPEED_CONF, "w", encoding="utf-8") as f:
        f.write(f"{speed}\n")


def format_speed(speed: float) -> str:
    if speed == int(speed):
        return f"x{int(speed)}"
    text = f"{speed:.2f}".rstrip("0").rstrip(".")
    return f"x{text}"


def format_chars(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(n)


def mpv_cmd(*args):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            sock.connect(SOCKET_PATH)
            payload = json.dumps({"command": list(args)}).encode("utf-8") + b"\n"
            sock.sendall(payload)
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
            if not data:
                return None
            return json.loads(data.decode("utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def mpv_alive() -> bool:
    return mpv_cmd("get_property", "pause") is not None


def worker_alive() -> bool:
    if not os.path.isfile(RUNNING_FILE):
        return False
    try:
        with open(RUNNING_FILE, encoding="utf-8") as f:
            pids = [line.strip() for line in f if line.strip()]
    except OSError:
        return False
    for pid in pids:
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError):
            continue
    return False


class SpeakControls(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Piper")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.configure(bg="#1e1e1e")

        try:
            self.attributes("-type", "utility")
        except tk.TclError:
            pass

        self.btn_font = tkfont.Font(family="Sans", size=12, weight="bold")
        self.small_font = tkfont.Font(family="Sans", size=10)
        self.tiny_font = tkfont.Font(family="Sans", size=9, weight="bold")
        self.hint_font = tkfont.Font(family="Sans", size=8)

        self.chars = int(os.environ.get("SPEAK_PIPER_CHARS", "0") or "0")
        self.voice_name = os.environ.get("SPEAK_PIPER_VOICE", read_voice())
        self.speed = read_speed()
        self.started_at = time.time()
        self.mode = "loading"  # loading | playing
        self._closing = False

        self.root_frame = tk.Frame(self, bg="#1e1e1e", padx=10, pady=10)
        self.root_frame.pack()

        self.loading_frame = tk.Frame(self.root_frame, bg="#1e1e1e")
        self.controls_frame = tk.Frame(self.root_frame, bg="#1e1e1e")

        self._build_loading()
        self._build_controls()

        self.loading_frame.pack()
        self.controls_frame.pack_forget()

        self.bind("<Escape>", lambda _e: self.close_all())
        self.protocol("WM_DELETE_WINDOW", self.close_all)

        self.update_idletasks()
        self._place_bottom_right()
        self.after(LOAD_POLL_MS, self._watch_loading)

    def _build_loading(self) -> None:
        frame = self.loading_frame

        title = tk.Label(
            frame,
            text="Синтез речи…",
            bg="#1e1e1e",
            fg="#f0f0f0",
            font=self.btn_font,
        )
        title.pack(anchor="w")

        detail = f"{self.voice_name}"
        if self.chars > 0:
            detail += f" · {format_chars(self.chars)} симв."
        self.load_detail = tk.Label(
            frame,
            text=detail,
            bg="#1e1e1e",
            fg="#aaaaaa",
            font=self.small_font,
        )
        self.load_detail.pack(anchor="w", pady=(2, 8))

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Piper.Horizontal.TProgressbar",
            troughcolor="#2a2a2a",
            background="#457b9d",
            bordercolor="#2a2a2a",
            lightcolor="#457b9d",
            darkcolor="#1d3557",
            thickness=8,
        )
        self.progress = ttk.Progressbar(
            frame,
            mode="indeterminate",
            length=220,
            style="Piper.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x")
        self.progress.start(12)

        bottom = tk.Frame(frame, bg="#1e1e1e")
        bottom.pack(fill="x", pady=(8, 0))

        self.load_timer = tk.Label(
            bottom,
            text="0 с",
            bg="#1e1e1e",
            fg="#888888",
            font=self.hint_font,
        )
        self.load_timer.pack(side="left")

        tk.Button(
            bottom,
            text="✕",
            bg="#9b2226",
            fg="#f0f0f0",
            activebackground="#c1121f",
            activeforeground="#ffffff",
            font=self.btn_font,
            width=3,
            bd=0,
            relief="flat",
            padx=8,
            pady=4,
            cursor="hand2",
            command=self.close_all,
        ).pack(side="right")

    def _build_controls(self) -> None:
        frame = self.controls_frame
        style = {
            "font": self.btn_font,
            "width": 3,
            "bd": 0,
            "relief": "flat",
            "padx": 8,
            "pady": 6,
            "cursor": "hand2",
        }

        controls = tk.Frame(frame, bg="#1e1e1e")
        controls.pack()

        tk.Button(
            controls,
            text="⏸",
            bg="#3a3a3a",
            fg="#f0f0f0",
            activebackground="#505050",
            activeforeground="#ffffff",
            command=self.pause,
            **style,
        ).grid(row=0, column=0, padx=3)

        tk.Button(
            controls,
            text="▶",
            bg="#2d6a4f",
            fg="#f0f0f0",
            activebackground="#40916c",
            activeforeground="#ffffff",
            command=self.play,
            **style,
        ).grid(row=0, column=1, padx=3)

        tk.Button(
            controls,
            text="✕",
            bg="#9b2226",
            fg="#f0f0f0",
            activebackground="#c1121f",
            activeforeground="#ffffff",
            command=self.close_all,
            **style,
        ).grid(row=0, column=2, padx=3)

        self.speed_buttons: dict[float, tk.Button] = {}
        speed_row = tk.Frame(frame, bg="#1e1e1e")
        speed_row.pack(pady=(8, 0), fill="x")

        tk.Label(
            speed_row,
            text="Скор.:",
            bg="#1e1e1e",
            fg="#aaaaaa",
            font=self.small_font,
        ).pack(side="left")

        for speed in SPEEDS:
            btn = tk.Button(
                speed_row,
                text=format_speed(speed),
                font=self.tiny_font,
                bd=0,
                relief="flat",
                padx=6,
                pady=3,
                cursor="hand2",
                command=lambda s=speed: self.set_speed(s),
            )
            btn.pack(side="left", padx=2)
            self.speed_buttons[speed] = btn

        self.voices = list_voices() or [read_voice()]
        current = read_voice(self.voices[0])
        if current not in self.voices:
            current = self.voices[0]

        voice_row = tk.Frame(frame, bg="#1e1e1e")
        voice_row.pack(pady=(8, 0), fill="x")

        tk.Label(
            voice_row,
            text="Голос:",
            bg="#1e1e1e",
            fg="#aaaaaa",
            font=self.small_font,
        ).pack(side="left")

        self.voice_var = tk.StringVar(value=current)
        menu = tk.OptionMenu(voice_row, self.voice_var, *self.voices, command=self.on_voice_change)
        menu.config(
            bg="#3a3a3a",
            fg="#f0f0f0",
            activebackground="#505050",
            activeforeground="#ffffff",
            highlightthickness=0,
            bd=0,
            font=self.small_font,
            indicatoron=True,
            cursor="hand2",
        )
        menu["menu"].config(bg="#2a2a2a", fg="#f0f0f0", font=self.small_font)
        menu.pack(side="left", padx=(6, 0))

        self.hint = tk.Label(
            frame,
            text="голос — со следующего запуска",
            bg="#1e1e1e",
            fg="#666666",
            font=self.hint_font,
        )
        self.hint.pack(pady=(4, 0))

    def _show_playing(self) -> None:
        if self.mode == "playing":
            return
        self.mode = "playing"
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        self.loading_frame.pack_forget()
        self.controls_frame.pack()
        self._refresh_speed_buttons()
        mpv_cmd("set_property", "speed", self.speed)
        self.update_idletasks()
        self._place_bottom_right()
        self.after(POLL_MS, self._watch_mpv)

    def _refresh_speed_buttons(self) -> None:
        for speed, btn in self.speed_buttons.items():
            if speed == self.speed:
                btn.config(bg="#1d3557", fg="#ffffff", activebackground="#457b9d")
            else:
                btn.config(bg="#3a3a3a", fg="#f0f0f0", activebackground="#505050")

    def set_speed(self, speed: float) -> None:
        self.speed = speed
        write_speed(speed)
        mpv_cmd("set_property", "speed", speed)
        self._refresh_speed_buttons()
        self.hint.config(text=f"скорость {format_speed(speed)}", fg="#8f8")

    def on_voice_change(self, name: str) -> None:
        write_voice(name)
        self.hint.config(text=f"выбран «{name}» → следующий запуск", fg="#8f8")

    def _place_bottom_right(self) -> None:
        self.update_idletasks()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(12, sw - w - 24)
        y = max(12, sh - h - 72)
        self.geometry(f"+{x}+{y}")

    def pause(self) -> None:
        mpv_cmd("set_property", "pause", True)

    def play(self) -> None:
        mpv_cmd("set_property", "pause", False)

    def _request_cancel(self) -> None:
        try:
            with open(CANCEL_FILE, "w", encoding="utf-8") as f:
                f.write("1\n")
        except OSError:
            pass
        subprocess.run(["pkill", "-x", "piper-tts"], check=False, capture_output=True)
        mpv_cmd("quit")

    def close_all(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._request_cancel()
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        self.destroy()

    def _watch_loading(self) -> None:
        if self._closing:
            return

        elapsed = int(time.time() - self.started_at)
        self.load_timer.config(text=f"{elapsed} с")

        if mpv_alive():
            self._show_playing()
            return

        # Воркер умер во время синтеза (ошибка/отмена) — закрываемся
        if elapsed >= 1 and not worker_alive():
            self.destroy()
            return

        self.after(LOAD_POLL_MS, self._watch_loading)

    def _watch_mpv(self) -> None:
        if self._closing:
            return
        if not os.path.exists(SOCKET_PATH) or not mpv_alive():
            self.destroy()
            return
        eof = mpv_cmd("get_property", "eof-reached")
        if eof and eof.get("data") is True:
            mpv_cmd("quit")
            self.destroy()
            return
        self.after(POLL_MS, self._watch_mpv)


def main() -> int:
    try:
        os.remove(CANCEL_FILE)
    except OSError:
        pass
    SpeakControls().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
