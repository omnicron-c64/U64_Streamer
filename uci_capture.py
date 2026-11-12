"""Ultimate 64 UDP viewer and recorder with runtime VPL palette tracking."""

import argparse
from functools import lru_cache
from fractions import Fraction
from ftplib import FTP
import http.client
import json
from pathlib import Path, PureWindowsPath
import queue
import socket
import struct
import tempfile
import threading
import time
from urllib.parse import urlencode
from uuid import uuid4
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import av
import numpy as np
import pygame

from snapshot_clipboard import copy_snapshot


PALETTE_INTERVALS = (0, 0.25, 0.5, 1, 2)
PALETTE_LABELS = ("Off", "250 ms", "500 ms", "1 s", "2 s")
PROJECT_URL = "https://github.com/omnicron-c64/U64_Streamer"


# Ultimate 64 firmware defaults, in C64 color-index order.
DEFAULT_PALETTE = np.frombuffer(bytes.fromhex(
    "000000 F7F7F7 8D2F34 6AD4CD 9835A4 4CB442 2C29B1 EFEF5D "
    "984E20 5B3800 D1676D 4A4A4A 7B7B7B 9FEF93 6D6AEF B2B2B2"
), dtype=np.uint8).reshape(16, 3)


def parse_vpl(data):
    colors = []
    for line in data.decode("ascii").splitlines():
        fields = line.partition("#")[0].split()
        if not fields:
            continue
        if len(fields) not in (3, 4):
            raise ValueError("Each VPL entry needs three RGB values")
        color = [int(value, 16) for value in fields[:3]]
        if any(value < 0 or value > 255 for value in color):
            raise ValueError("RGB values must be between 00 and FF")
        colors.append(color)
    if len(colors) != 16:
        raise ValueError("Palette needs exactly 16 colors")
    return np.array(colors, dtype=np.uint8)


def start_streams(args, host=None):
    host = host or args.host
    if not host:
        raise ValueError("A device address is needed to start streams")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect((host, 80))
        local_ip = route.getsockname()[0]
    for stream, port in (("video", args.video_port), ("audio", args.audio_port)):
        connection = http.client.HTTPConnection(host, timeout=3)
        try:
            headers = {"X-Password": args.password} if args.password else {}
            connection.request("PUT", f"/v1/streams/{stream}:start?" + urlencode({"ip": f"{local_ip}:{port}"}), headers=headers)
            response = connection.getresponse()
            body = response.read()
            if response.status != 200:
                raise RuntimeError(f"{stream} stream returned HTTP {response.status}")
            errors = json.loads(body).get("errors", [])
            if errors:
                raise RuntimeError(str(errors))
        finally:
            connection.close()


class Recorder:
    def __init__(self, path, height, fps, sample_rate, scanline_opacity=0):
        suffix = Path(path).suffix.lower()
        if suffix not in (".mp4", ".mkv"):
            raise ValueError("Choose an .mp4 or .mkv filename")
        mp4 = suffix == ".mp4"
        self.events = queue.Queue(maxsize=512)
        self.error = None
        self.origin = time.monotonic()
        self.scale = 3 if scanline_opacity else 1
        self.scanline_levels = (
            np.arange(256, dtype=np.uint16) * (256 - scanline_opacity) >> 8
        ).astype(np.uint8)
        self.container = av.open(str(path), "w")
        self.video = self.container.add_stream("libx264" if mp4 else "ffv1", rate=fps)
        self.video.width = 384 * self.scale
        self.video.height = height * self.scale
        self.video.pix_fmt = "yuv420p" if mp4 else "bgr0"
        if mp4:
            self.video.options = {"crf": "14", "preset": "veryfast"}
        # Preserve distinct arrival timestamps when UDP frames arrive in bursts.
        self.video.codec_context.time_base = Fraction(1, 1000000)
        self.video.time_base = Fraction(1, 1000000)
        self.audio = self.container.add_stream("aac" if mp4 else "pcm_s16le",
                                               rate=48000 if mp4 else sample_rate)
        self.audio.layout = "stereo"
        self.sample_rate = sample_rate
        self.height = height
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def submit(self, kind, timestamp, data):
        if self.error:
            return
        try:
            self.events.put_nowait((kind, timestamp, data))
        except queue.Full:
            self.error = "Recording could not keep up with incoming data"

    def run(self):
        audio_cursor = 0
        last_video_pts = -1
        try:
            while True:
                item = self.events.get()
                if item is None:
                    break
                kind, timestamp, data = item
                if kind == "video":
                    if data.shape[0] != self.height:
                        raise ValueError("Video mode changed. Start a new recording.")
                    if self.scale > 1:
                        data = data.repeat(self.scale, axis=0).repeat(self.scale, axis=1)
                        scanlines = data[self.scale - 1::self.scale]
                        scanlines[:] = self.scanline_levels[scanlines]
                    frame = av.VideoFrame.from_ndarray(data, format="rgb24")
                    frame.pts = max(last_video_pts + 1, round((timestamp - self.origin) * 1000000))
                    frame.time_base = Fraction(1, 1000000)
                    last_video_pts = frame.pts
                    stream = self.video
                else:
                    if not audio_cursor:
                        audio_cursor = max(0, round((timestamp - self.origin) * self.sample_rate))
                    samples = np.frombuffer(data, dtype="<i2").reshape(1, -1)
                    frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="stereo")
                    frame.sample_rate = self.sample_rate
                    frame.pts = audio_cursor
                    frame.time_base = Fraction(1, self.sample_rate)
                    audio_cursor += len(data) // 4
                    stream = self.audio
                for packet in stream.encode(frame):
                    self.container.mux(packet)
            for stream in (self.video, self.audio):
                for packet in stream.encode():
                    self.container.mux(packet)
        except Exception as error:
            self.error = str(error)
        finally:
            self.container.close()

    def close(self):
        while self.worker.is_alive():
            try:
                self.events.put(None, timeout=0.1)
                break
            except queue.Full:
                continue
        self.worker.join()


class Receiver:
    def __init__(self, args):
        self.args = args
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.host = args.host
        self.palette = DEFAULT_PALETTE
        self.palette_status = "Standard C64 palette"
        self.palette_changes = 0
        self.palette_from_file = False
        self.palette_generation = 0
        self.palette_interval = args.palette_interval
        self.palette_wakeup = threading.Event()
        self.frame = None
        self.recorder = None
        self.frames = 0
        self.dropped_frames = 0
        self.audio_packets = 0
        self.audio_missing = 0
        self.audio_queue = queue.Queue(maxsize=32)
        self.network_error = ""
        self.threads = []
        self.sockets = []
        for port in (args.video_port, args.audio_port):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
            sock.bind(("", port))
            sock.settimeout(0.3)
            self.sockets.append(sock)
        for function, arguments in ((self.video_loop, (self.sockets[0],)),
                                    (self.audio_loop, (self.sockets[1],)),
                                    (self.palette_loop, ())):
            worker = threading.Thread(target=function, args=arguments, daemon=True)
            worker.start()
            self.threads.append(worker)

    def packets(self, sock):
        while not self.stop.is_set():
            try:
                packet, address = sock.recvfrom(65535)
                if not self.host:
                    self.host = address[0]
                if address[0] == self.host:
                    yield packet
            except socket.timeout:
                continue
            except OSError as error:
                if not self.stop.is_set():
                    self.network_error = str(error)
                return

    def palette_loop(self):
        while not self.stop.is_set():
            self.palette_wakeup.clear()
            with self.lock:
                interval = self.palette_interval
                generation = self.palette_generation
            if self.stop.is_set():
                return
            if not interval:
                self.palette_wakeup.wait()
                continue
            started = time.monotonic()
            if self.host:
                try:
                    with FTP(self.host, timeout=2) as ftp:
                        ftp.login(self.args.ftp_user, self.args.password)
                        data = bytearray()
                        ftp.retrbinary("RETR " + self.args.palette, data.extend)
                    palette = parse_vpl(data)
                    with self.lock:
                        if self.palette_interval and self.palette_generation == generation:
                            if not np.array_equal(self.palette, palette):
                                self.palette = palette
                                self.palette_changes += 1
                            self.palette_status = "Palette checked " + time.strftime("%H:%M:%S")
                            self.palette_from_file = True
                except Exception as error:
                    with self.lock:
                        if self.palette_interval and self.palette_generation == generation:
                            active = "Last VPL palette" if self.palette_from_file else "Standard C64 palette"
                            self.palette_status = f"{active} (VPL read failed: {error})"
            self.palette_wakeup.wait(max(0, interval - (time.monotonic() - started)))

    def cycle_palette_interval(self):
        with self.lock:
            index = PALETTE_INTERVALS.index(self.palette_interval)
            self.palette_interval = PALETTE_INTERVALS[(index + 1) % len(PALETTE_INTERVALS)]
            self.palette_status = ("Checking for a VPL palette" if self.palette_interval
                                   else "Palette updates off · keeping current colors")
        self.palette_wakeup.set()

    def delete_palette_file(self):
        host = self.host
        path = self.args.palette
        if not host:
            raise ValueError("Connect to an Ultimate 64 first")
        if not path.lower().endswith(".vpl"):
            raise ValueError("The configured path must end in .vpl")
        with FTP(host, timeout=2) as ftp:
            ftp.login(self.args.ftp_user, self.args.password)
            ftp.delete(path)
        with self.lock:
            self.palette_generation += 1
            self.palette_from_file = False
            if not np.array_equal(self.palette, DEFAULT_PALETTE):
                self.palette = DEFAULT_PALETTE
                self.palette_changes += 1
            self.palette_status = "Standard C64 palette"
        self.palette_wakeup.set()

    def video_loop(self, sock):
        frame_id = None
        startup_fragment = False
        pixels = np.zeros((312, 384), dtype=np.uint8)
        lines = set()
        for packet in self.packets(sock):
            if len(packet) < 12:
                continue
            _, number, line_flags, width, count, bits, encoding = struct.unpack_from("<HHHHBBH", packet)
            line = line_flags & 0x7fff
            if width != 384 or bits != 4 or encoding != 0 or not 1 <= count <= 4:
                continue
            if line + count > 312 or len(packet) != 12 + width * count // 2:
                continue
            if frame_id != number:
                if frame_id is not None and ((number - frame_id) & 65535) > 32768:
                    continue
                startup_fragment = frame_id is None and line != 0
                frame_id = number
                lines.clear()
            packed = np.frombuffer(packet, dtype=np.uint8, offset=12)
            decoded = np.empty(len(packed) * 2, dtype=np.uint8)
            decoded[0::2] = packed & 15
            decoded[1::2] = packed >> 4
            pixels[line:line + count] = decoded.reshape(count, width)
            lines.update(range(line, line + count))
            if line_flags & 0x8000:
                height = line + count
                if height not in (240, 272) or len(lines) != height:
                    if not startup_fragment:
                        self.dropped_frames += 1
                    continue
                timestamp = time.monotonic()
                with self.lock:
                    rgb = self.palette[pixels[:height]]
                    self.frame = rgb
                    self.frames += 1
                    if self.recorder:
                        self.recorder.submit("video", timestamp, rgb)

    def audio_loop(self, sock):
        previous = None
        pending = bytearray()
        for packet in self.packets(sock):
            if len(packet) != 770:
                continue
            sequence = struct.unpack_from("<H", packet)[0]
            missing = 0 if previous is None else (sequence - previous - 1) & 65535
            if missing > 32768:
                continue
            # A large jump indicates the stream restarted. Avoid allocating a long silence.
            if missing > 250:
                missing = 0
                pending.clear()
            previous = sequence
            self.audio_missing += missing
            self.audio_packets += 1
            data = bytes(768 * missing) + packet[2:]
            with self.lock:
                if self.recorder:
                    self.recorder.submit("audio", time.monotonic(), data)
            pending.extend(data)
            while len(pending) >= 768 * 10:
                chunk = bytes(pending[:768 * 10])
                del pending[:768 * 10]
                try:
                    self.audio_queue.put_nowait(chunk)
                except queue.Full:
                    self.audio_queue.get_nowait()
                    self.audio_queue.put_nowait(chunk)

    def close(self):
        self.stop.set()
        self.palette_wakeup.set()
        for worker in self.threads:
            worker.join(timeout=3)
        for sock in self.sockets:
            sock.close()
        if self.recorder:
            self.recorder.close()


SCANLINE_PRESETS = (("Off", 0), ("Light", 64), ("Strong", 128))


def connection_dialog(root, args):
    dialog = tk.Toplevel(root)
    dialog.title("UCI Capture · Connect")
    dialog.resizable(False, False)
    form = ttk.Frame(dialog, padding=24)
    form.grid()
    ttk.Label(form, text="Connect to an Ultimate 64", font=("TkDefaultFont", 14, "bold")).grid(
        row=0, columnspan=2, sticky="w", pady=(0, 16))
    host = tk.StringVar(value="")
    palette = tk.StringVar(value=args.palette)
    palette_rate = tk.StringVar(value=PALETTE_LABELS[PALETTE_INTERVALS.index(args.palette_interval)])
    password = tk.StringVar()
    ntsc = tk.BooleanVar(value=args.ntsc)
    start = tk.BooleanVar(value=True)
    entries = []
    for row, (label, variable) in enumerate(
            (("IP address or hostname", host), ("VPL file on device", palette),
             ("Network password", password)), start=1):
        ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=5)
        entry = ttk.Entry(form, textvariable=variable, width=32, show="*" if variable is password else "")
        entry.grid(row=row, column=1, sticky="ew")
        entries.append(entry)
    ttk.Label(form, text="Enter auto to listen for a stream already running.").grid(
        row=4, columnspan=2, sticky="w", pady=(4, 12))
    ttk.Label(form, text="Palette polling").grid(row=5, column=0, sticky="w", pady=5)
    ttk.Combobox(form, state="readonly", textvariable=palette_rate, values=PALETTE_LABELS).grid(
        row=5, column=1, sticky="ew")
    ttk.Checkbutton(form, text="NTSC video timing", variable=ntsc).grid(row=6, columnspan=2, sticky="w")
    ttk.Checkbutton(form, text="Start video and audio on connect", variable=start).grid(
        row=7, columnspan=2, sticky="w")

    def connect():
        if not host.get().strip():
            messagebox.showwarning("Device address", "Enter your Ultimate 64 address or auto.", parent=dialog)
            return
        args.host = host.get().strip()
        args.palette = palette.get().strip() or args.palette
        args.palette_interval = PALETTE_INTERVALS[PALETTE_LABELS.index(palette_rate.get())]
        args.password = password.get()
        args.ntsc = ntsc.get()
        args.start_streams = start.get() and args.host != "auto"
        dialog.destroy()

    ttk.Button(form, text="Connect", command=connect).grid(row=8, column=1, sticky="e", pady=(16, 0))
    dialog.bind("<Return>", lambda _: connect())
    dialog.bind("<Escape>", lambda _: dialog.destroy())
    entries[0].focus_set()
    root.wait_window(dialog)
    return args.host is not None


def recording_name(value, extension):
    name = value.strip()
    suffix = Path(name).suffix.lower()
    if suffix in (".mp4", ".mkv"):
        if suffix != f".{extension}":
            raise ValueError("The filename extension must match the selected format.")
        name = name[:-len(suffix)]
    if value.strip() and (
        not name or name.endswith((".", " ")) or PureWindowsPath(name).is_reserved()
        or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in name)
    ):
        raise ValueError("Enter a filename without a folder or reserved characters.")
    return name


def reserve_recording_path(directory, name, extension):
    if not name:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = f"U64-{stamp}-{uuid4().hex[:8]}"
    take = 1
    while True:
        stem = name if take == 1 else f"{name}-{take}"
        path = directory / f"{stem}.{extension}"
        try:
            path.touch(exist_ok=False)
        except FileExistsError:
            take += 1
        else:
            return path


def output_dialog(root, directory, extension, filename):
    dialog = tk.Toplevel(root)
    dialog.title("Recording output")
    dialog.resizable(False, False)
    form = ttk.Frame(dialog, padding=20)
    form.grid()
    folder = tk.StringVar(value=str(directory))
    file_format = tk.StringVar(value=extension)
    file_name = tk.StringVar(value=filename)
    result = None

    def browse():
        selected = filedialog.askdirectory(parent=dialog, initialdir=folder.get(), mustexist=True)
        if selected:
            folder.set(selected)

    def save():
        nonlocal result
        target = Path(folder.get().strip()).expanduser()
        if not folder.get().strip() or not target.is_dir():
            messagebox.showerror("Recording folder", "Select an existing folder.", parent=dialog)
            return
        try:
            name = recording_name(file_name.get(), file_format.get())
        except ValueError as error:
            messagebox.showerror("Recording filename", str(error), parent=dialog)
            return
        result = target.resolve(), file_format.get(), name
        dialog.destroy()

    ttk.Label(form, text="Save recordings to").grid(row=0, columnspan=3, sticky="w")
    ttk.Entry(form, textvariable=folder, width=46).grid(row=1, column=0, columnspan=2, pady=(4, 12))
    ttk.Button(form, text="Browse…", command=browse).grid(row=1, column=2, padx=(8, 0), pady=(4, 12))
    ttk.Label(form, text="Filename (optional)").grid(row=2, column=0, columnspan=3, sticky="w")
    ttk.Entry(form, textvariable=file_name, width=46).grid(row=3, column=0, columnspan=2, pady=(4, 12))
    ttk.Label(form, text="Format").grid(row=4, column=0, sticky="w")
    ttk.Combobox(form, state="readonly", textvariable=file_format, values=("mp4", "mkv"), width=8).grid(
        row=4, column=1, sticky="w")
    ttk.Button(form, text="Use output", command=save).grid(row=4, column=2, sticky="e")
    dialog.bind("<Return>", lambda _: save())
    dialog.bind("<Escape>", lambda _: dialog.destroy())
    root.wait_window(dialog)
    return result


def layout_controls(width, font, controls):
    buttons = []
    left = 16
    top = 0
    for key, label, primary in controls:
        text = font.render(label, True, (247, 249, 253))
        rect = pygame.Rect(left, top, text.get_width() + 24, 34)
        if rect.right > width - 12 and left > 16:
            left, top = 16, top + 42
            rect.topleft = left, top
        buttons.append((key, text, rect, primary))
        left = rect.right + 8
    return buttons


def draw_controls(window, buttons, top):
    hitboxes = {}
    mouse = pygame.mouse.get_pos()
    for key, text, relative_rect, primary in buttons:
        rect = relative_rect.move(0, top)
        color = (152, 43, 63) if primary else (44, 54, 74)
        if rect.collidepoint(mouse):
            color = tuple(min(255, value + 24) for value in color)
        pygame.draw.rect(window, color, rect, border_radius=7)
        window.blit(text, text.get_rect(center=rect.center))
        hitboxes[key] = rect
    return hitboxes


@lru_cache(maxsize=4)
def scanline_overlay(size, source_height, opacity):
    width, height = size
    overlay = pygame.Surface(size, pygame.SRCALPHA)
    row_height = height // source_height
    gap = max(1, row_height // 3)
    for bottom in range(row_height, height + 1, row_height):
        overlay.fill((0, 0, 0, opacity), (0, bottom - gap, width, gap))
    return overlay


def self_test():
    tk.Tcl().eval("info patchlevel")
    scanline_overlay((768, 544), 272, 64)
    with tempfile.TemporaryDirectory() as folder:
        for extension in ("mp4", "mkv"):
            path = Path(folder) / f"capture.{extension}"
            recorder = Recorder(path, 272, 50, 47983, scanline_opacity=64)
            frame = np.full((272, 384, 3), 200, dtype=np.uint8)
            for index in range(4):
                timestamp = recorder.origin + index / 50
                recorder.submit("video", timestamp, frame)
                recorder.submit("audio", timestamp, bytes(960 * 4))
            recorder.close()
            if recorder.error:
                raise RuntimeError(recorder.error)
            with av.open(path) as capture:
                frames = list(capture.decode(video=0))
                if len(frames) != 4 or frames[0].width != 1152:
                    raise RuntimeError(f"Invalid video in {extension} smoke test")
            with av.open(path) as capture:
                if not any(frame.samples for frame in capture.decode(audio=0)):
                    raise RuntimeError(f"Missing audio in {extension} smoke test")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="Ultimate 64 IPv4 address, hostname, or auto")
    parser.add_argument("--palette", default="/Temp/current.vpl")
    parser.add_argument("--palette-interval", type=float, choices=PALETTE_INTERVALS, default=1,
                        help="Seconds between palette checks (0 disables polling)")
    parser.add_argument("--ftp-user", default="anonymous")
    parser.add_argument("--password", action="store_true", help="Prompt for network password")
    parser.add_argument("--video-port", type=int, default=11000)
    parser.add_argument("--audio-port", type=int, default=11001)
    parser.add_argument("--ntsc", action="store_true", help="Use NTSC audio clock")
    parser.add_argument("--start-streams", action="store_true", help="Start video and audio via REST on launch")
    parser.add_argument("--output-dir", type=Path, help="Directory for recordings")
    parser.add_argument("--format", choices=("mp4", "mkv"), default="mp4", help="Recording file format")
    parser.add_argument("--self-test", action="store_true", help="Check bundled libraries and recording codecs")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    output_dir = args.output_dir
    if output_dir is None:
        videos_dir = Path.home() / "Videos"
        output_dir = videos_dir if videos_dir.is_dir() else Path.home()
    output_dir = output_dir.expanduser().resolve()
    output_format = args.format
    output_name = ""
    root = tk.Tk()
    root.withdraw()
    args.password = (simpledialog.askstring("Network password", "Ultimate 64 password", show="*", parent=root) or ""
                     if args.password and args.host else "")
    if args.host is None and not connection_dialog(root, args):
        root.destroy()
        return
    if args.host == "auto":
        args.host = None
    elif args.host:
        try:
            args.host = socket.gethostbyname(args.host)
        except OSError as error:
            messagebox.showerror("Connection failed", str(error), parent=root)
            root.destroy()
            return
    sample_rate = 47940 if args.ntsc else 47983
    pygame.mixer.pre_init(sample_rate, -16, 2, 1024)
    pygame.init()
    windowed_size = (960, 810)
    window = pygame.display.set_mode(windowed_size, pygame.RESIZABLE)
    pygame.display.set_caption("UCI Capture — Ultimate 64")
    font = pygame.font.SysFont(("segoeui", "dejavusans"), 16)
    title_font = pygame.font.SysFont(("segoeui", "dejavusans"), 24, bold=True)
    channel = pygame.mixer.Channel(0) if pygame.mixer.get_init() else None
    receiver = None
    buttons = {}
    message = ""
    scanline_preset = 0
    recording_scanline_preset = 0
    muted = False
    fullscreen = False
    clock = pygame.time.Clock()
    try:
        try:
            receiver = Receiver(args)
        except OSError as error:
            messagebox.showerror("Could not open UDP ports", str(error), parent=root)
            return
        if args.start_streams:
            try:
                start_streams(args)
            except Exception as error:
                message = "Stream start failed: " + str(error)
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                if event.type == pygame.KEYDOWN:
                    key = event.key
                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    key = next((key for key, rect in buttons.items() if rect.collidepoint(event.pos)), None)
                else:
                    continue
                if key == pygame.K_ESCAPE:
                    running = False
                if key == pygame.K_F11:
                    fullscreen = not fullscreen
                    window = pygame.display.set_mode((0, 0) if fullscreen else windowed_size,
                                                     pygame.FULLSCREEN if fullscreen else pygame.RESIZABLE)
                if key == pygame.K_m:
                    muted = not muted
                    if channel:
                        channel.set_volume(0 if muted else 1)
                if key == pygame.K_c:
                    scanline_preset = (scanline_preset + 1) % len(SCANLINE_PRESETS)
                if key == pygame.K_b:
                    recording_scanline_preset = (recording_scanline_preset + 1) % len(SCANLINE_PRESETS)
                if key == pygame.K_p:
                    receiver.cycle_palette_interval()
                if key == pygame.K_o:
                    selected = output_dialog(root, output_dir, output_format, output_name)
                    if selected:
                        output_dir, output_format, output_name = selected
                if key == pygame.K_d:
                    if not receiver.host:
                        message = "Connect to an Ultimate 64 first"
                    elif messagebox.askyesno("Delete VPL file",
                                             f"Delete {args.palette} from {receiver.host} over FTP?",
                                             parent=root):
                        try:
                            receiver.delete_palette_file()
                            message = f"Deleted {args.palette} · restored standard C64 colors"
                        except Exception as error:
                            message = "VPL delete failed: " + str(error)
                if key == pygame.K_F1:
                    try:
                        opened = webbrowser.open(PROJECT_URL)
                    except (OSError, webbrowser.Error):
                        opened = False
                    if not opened:
                        message = "Open " + PROJECT_URL
                if key == pygame.K_x:
                    with receiver.lock:
                        snapshot = receiver.frame
                    if snapshot is None:
                        message = "Wait for video before taking a snapshot"
                    else:
                        try:
                            copy_snapshot(snapshot)
                            message = "Snapshot copied to clipboard"
                        except Exception as error:
                            message = "Snapshot failed: " + str(error)
                if key == pygame.K_s:
                    try:
                        start_streams(args, receiver.host)
                        message = "Video and audio streams started"
                    except Exception as error:
                        message = "Stream start failed: " + str(error)
                if key == pygame.K_r:
                    with receiver.lock:
                        recorder = receiver.recorder
                        receiver.recorder = None
                        frame = receiver.frame
                    if recorder:
                        recorder.close()
                        message = "Recording failed: " + recorder.error if recorder.error else "Recording saved"
                    elif frame is not None:
                        filename = None
                        try:
                            filename = reserve_recording_path(output_dir, output_name, output_format)
                            preset, opacity = SCANLINE_PRESETS[recording_scanline_preset]
                            recorder = Recorder(filename, frame.shape[0], 60 if args.ntsc else 50,
                                                sample_rate, scanline_opacity=opacity)
                            with receiver.lock:
                                receiver.recorder = recorder
                            message = f"Recording to {filename.name} | Scanlines {preset}"
                        except Exception as error:
                            if filename is not None:
                                try:
                                    filename.unlink()
                                except OSError:
                                    pass
                            message = "Recording failed: " + str(error)
                    else:
                        message = "Wait for video before recording"
            if channel and channel.get_queue() is None:
                try:
                    sound = pygame.mixer.Sound(buffer=receiver.audio_queue.get_nowait())
                    if channel.get_busy():
                        channel.queue(sound)
                    else:
                        channel.play(sound)
                except queue.Empty:
                    pass
            window.fill((20, 23, 30))
            with receiver.lock:
                frame = receiver.frame
                recorder = receiver.recorder
            w, h = window.get_size()
            scanline_name, scanline_opacity = SCANLINE_PRESETS[scanline_preset]
            next_preset = SCANLINE_PRESETS[recording_scanline_preset][0]
            palette_label = PALETTE_LABELS[PALETTE_INTERVALS.index(receiver.palette_interval)]
            controls = [
                (pygame.K_r, "Stop recording" if recorder else "Record", True),
                (pygame.K_o, f"Output: {output_format.upper()}", False),
                (pygame.K_x, "Snapshot", False),
                (pygame.K_s, "Start streams", False),
                (pygame.K_m, "Unmute" if muted else "Mute", False),
                (pygame.K_p, f"Palette: {palette_label}", False),
                (pygame.K_d, "Delete VPL", False),
                (pygame.K_c, f"Preview scanlines: {scanline_name}", False),
                (pygame.K_b, f"Record scanlines: {next_preset}", False),
                (pygame.K_F11, "Windowed" if fullscreen else "Fullscreen", False),
                (pygame.K_F1, "GitHub docs", False),
            ]
            toolbar = layout_controls(w, font, controls)
            footer_height = toolbar[-1][2].bottom + 111
            video_top = 55
            preview_height = max(1, h - video_top - footer_height)
            pygame.draw.rect(window, (9, 13, 21), (0, video_top, w, preview_height))
            window.blit(title_font.render("UCI Capture", True, (247, 249, 253)), (16, 12))
            host_text = font.render(receiver.host or "Listening for device", True, (168, 184, 206))
            window.blit(host_text, (max(165, w - host_text.get_width() - 16), 18))
            scanline_hint = ""
            if frame is not None:
                surface = pygame.surfarray.make_surface(frame.transpose(1, 0, 2))
                scale = min(w / 384, preview_height / frame.shape[0])
                show_scanlines = scanline_opacity > 0 and scale >= 2
                if show_scanlines:
                    scale = int(scale)
                size = (max(1, int(384 * scale)), max(1, int(frame.shape[0] * scale)))
                position = ((w - size[0]) // 2, video_top + (preview_height - size[1]) // 2)
                window.blit(pygame.transform.scale(surface, size), position)
                if show_scanlines:
                    window.blit(scanline_overlay(size, frame.shape[0], scanline_opacity), position)
                elif scanline_opacity:
                    scanline_hint = "   ·   Enlarge preview to see scanlines"
            else:
                waiting = title_font.render("Waiting for video", True, (168, 184, 206))
                window.blit(waiting, waiting.get_rect(center=(w // 2, video_top + preview_height // 2)))
            footer_top = video_top + preview_height
            buttons = draw_controls(window, toolbar, footer_top + 10)
            status_top = max(rect.bottom for rect in buttons.values()) + 10
            recording_error = recorder.error if recorder else None
            activity = (("Recording error: " + recording_error) if recording_error else
                        receiver.network_error or message or "Ready")
            statuses = [
                activity,
                f"Output   {output_dir}   ·   {output_name + '.' + output_format if output_name else 'Automatic filename'}",
                receiver.palette_status + scanline_hint,
                f"Frames {receiver.frames:,}   ·   Incomplete {receiver.dropped_frames:,}   ·   "
                f"Audio packets {receiver.audio_packets:,}   ·   Missing {receiver.audio_missing:,}",
            ]
            for index, status in enumerate(statuses):
                color = (255, 164, 169) if index == 0 and recording_error else (185, 198, 217)
                window.blit(font.render(status, True, color), (16, status_top + index * 21))
            pygame.display.flip()
            clock.tick(120)
    finally:
        if receiver:
            receiver.close()
        root.destroy()
        pygame.quit()


if __name__ == "__main__":
    main()
