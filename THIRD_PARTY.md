# Third-party software

UCI Capture uses [Python](https://www.python.org/), [Tcl/Tk](https://www.tcl-lang.org/),
[NumPy](https://numpy.org/), [Pygame](https://www.pygame.org/),
[SDL](https://www.libsdl.org/), [PyAV](https://pyav.org/), and
[FFmpeg](https://ffmpeg.org/) with [x264](https://code.videolan.org/videolan/x264).
Release bundles are assembled with [PyInstaller](https://pyinstaller.org/).
The NumPy, Pygame, and PyAV wheel metadata and bundled license files are included
in each release archive. Wheel versions are pinned in `requirements.txt`.

PyAV 17.1.0 wheels ship [FFmpeg 8.1.1](https://ffmpeg.org/releases/ffmpeg-8.1.1.tar.xz).
The [PyAV 17.1.0 release](https://github.com/PyAV-Org/PyAV/releases/tag/v17.1.0)
and [PyAV's FFmpeg build repository](https://github.com/PyAV-Org/pyav-ffmpeg)
document the upstream builds. FFmpeg's build with libx264 uses the GPL.
See [FFmpeg's licensing information](https://www.ffmpeg.org/legal.html).
Review the redistribution terms and corresponding-source requirements for the
bundled native libraries before uploading a binary release.
