# UCI Capture

Standalone viewer and recorder for the Ultimate 64 VIC and audio streams.
The app fetches `/Temp/current.vpl`
over FTP once per second by default and applies validated RGB values to the preview and newly
recorded frames. Preview and recording use the standard C64 palette until the
first valid VPL is loaded. The app retains the last valid palette during file
transfer failures.

## Run on Windows

Extract the Windows x64 ZIP and open `UCI-Capture.exe` from the extracted
folder. The ZIP includes Python and its dependencies. For a source checkout,
open `Launch.bat` to install dependencies into `.venv` and launch the app.

Close any other app using UDP ports 11000 and 11001. Enter the Ultimate 64
address or hostname in the connection dialog. If streams are already running,
enter `auto` to listen for them. Select NTSC if your device uses NTSC timing.
If Windows asks, allow the app to receive traffic on your local network.

Use **Output** or **O** to choose a folder, optional filename, and format before recording. The
default is MP4 in your Videos folder, or your home folder if Videos is absent.
Press **Record** or **R** to start immediately, then
**Stop recording** to finish the file. Output selections apply to the session.
Leave the filename blank for an automatic name. If your chosen filename already
exists, the app adds a suffix such as `-2`.
The toolbar also starts streams, mutes preview audio, and switches to fullscreen.
The corresponding shortcuts are **R**, **S**, **M**, and **F11**.
Press **Esc** or close the window to finish recording and exit.
Muting preview audio leaves recorded audio enabled.

Click **Snapshot** or press **X** to copy the current frame to the clipboard
at its original resolution with the active palette. On Linux, install
`wl-clipboard` for Wayland or `xclip` for X11.

Set the palette polling rate in the connection dialog or cycle it with the
**Palette** button or **P**. The available rates are **Off**, **250 ms**,
**500 ms**, **1 s**, and **2 s**. Off keeps the active colors. When launched with
polling off, the app uses the standard C64 palette.
See the [runtime palette guide](U64_Stream_Capture_Palette.md) for the VPL format
and an example of publishing updates from C64 software.
To clear a stale file, click **Delete VPL** or press **D** and confirm the device
and path. Successful deletion restores standard C64 colors in the app. With
polling enabled, the app can pick up a newly published file.

Click **GitHub docs** or press **F1** to open the [project repository](https://github.com/omnicron-c64/U64_Streamer).

Click **Preview scanlines** or press **C** to cycle through **Off**, **Light**,
and **Strong**. With scanlines on, the preview uses the largest whole-number
scale that fits, starting at 2×. Click **Record scanlines** or press **B** to
set the preset for the next recording. The recording preset defaults to
**Off**. Light and Strong record at 3× source resolution to give each source
row room for a scanline. The preset is fixed when recording starts.

Recordings default to MP4 with H.264 video and stereo AAC audio. To preserve exact
rendered RGB values and uncompressed stereo audio, select MKV in Output for
lossless FFV1 and PCM. The status line displays palette failures and recording
errors. Palette polling may miss changes occurring between checks. Publish each
palette by closing a temporary file and renaming it to `current.vpl`.

## Run from source

Use Python 3.10–3.13 with Tk support. In this folder, run:

```sh
python -m pip install -r requirements.txt
python uci_capture.py
```

For direct startup, pass `--host YOUR_DEVICE_IP --start-streams`.
Use `--ntsc` for NTSC audio timing, `--password` for a password prompt,
`--palette /Temp/current.vpl` to change the file, `--palette-interval` to set the
polling interval in seconds, and `--video-port` or
`--audio-port` to change UDP ports. Without `--start-streams`, start streams from
the Elite menu or press S. `--host auto` learns the source from incoming packets
when streams are already running. Streams continue after the viewer exits.
Use `--output-dir PATH` and `--format mp4|mkv` to set recording output on launch.

PAL and NTSC raw 4-bit video packets are supported. Incomplete frames are skipped.
Small audio packet gaps are filled with silence. PAL audio uses 47983 Hz and NTSC
uses 47940 Hz according to the Ultimate data-stream specification.

## Build release archives

Build on the target operating system with Python 3.10–3.13.

On Windows x64, run from PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1
```

On Linux, install Bash, Python with venv and Tkinter support, and binutils.
Package examples for common distributions:

| Distribution | Packages |
| --- | --- |
| Ubuntu / Debian | `python3 python3-venv python3-tk binutils` |
| Fedora | `python3 python3-tkinter binutils` |
| Arch Linux | `python tk binutils` |

```sh
bash build_linux.sh
```

If the system Python is outside the supported range, set `PYTHON` to the path
of a supported interpreter with Tkinter, for example `PYTHON=python3.12`.
The Linux archive targets the build machine's architecture and glibc baseline.
For wider compatibility, build on the oldest supported glibc distribution.
The GitHub workflow uses Ubuntu 22.04 for Linux x64.

Both scripts create an isolated build environment, verify that the packaged app
can encode and decode video and audio, and write archives and SHA-256 hashes
under `dist/` or to the terminal. GitHub Actions builds both archives on pushes
to `main`, version tags, or manual runs. For a release, run **Build release
archives** from Actions with a tag such as `v1.0.0`. The workflow publishes the
release with both verified archives and their SHA-256 checksums.
See [third-party software](THIRD_PARTY.md) before distributing binaries.

The optional Discord compression script takes a video path and writes an MP4
beside it. Install `imageio-ffmpeg` in the same environment as the app dependencies
before running `python compress_for_discord.py PATH_TO_VIDEO`.

## Protocol references

- https://1541u-documentation.readthedocs.io/en/master/data_streams.html
- The device's `/api.html` and `/openapi.yaml` describe REST stream controls.

Live verification used a Windows PC and Ultimate 64 Elite firmware 3.15a-UCE.2.
Other operating systems require testing.

## License

UCI Capture is licensed under the GNU General Public License, version 3 or any
later version. See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY.md).
