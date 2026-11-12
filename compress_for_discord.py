"""Re-encode an MP4 to fit a decimal megabyte upload limit."""

import argparse
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

import av
import imageio_ffmpeg


def compress(source, limit):
    with av.open(source) as video:
        if not video.duration or not video.streams.video:
            raise ValueError("The input needs a video stream with a known duration.")
        seconds = video.duration / av.time_base
        has_audio = bool(video.streams.audio)

    audio_rate = 80000 if has_audio else 0
    video_rate = int(limit * 0.96 * 8 / seconds) - audio_rate
    if video_rate <= 0:
        raise ValueError("The size limit is too small for this video's duration.")
    output = source.with_name(f"{source.stem}-Discord-{uuid4().hex[:8]}.mp4")
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
               "-nostdin", "-i", str(source), "-map", "0:v:0", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-preset", "fast", "-fps_mode", "vfr"]
    audio = ["-map", "0:a:0", "-c:a", "aac", "-b:a", str(audio_rate)] if has_audio else ["-an"]

    with tempfile.TemporaryDirectory(prefix="discord-encode-", dir=source.parent) as folder:
        temporary = Path(folder) / "encoded.mp4"
        stats = str(Path(folder) / "pass")
        for attempt in range(1, 4):
            options = ["-b:v", str(video_rate), "-passlogfile", stats]
            print(f"Attempt {attempt}: analyzing video at {video_rate // 1000} kb/s", flush=True)
            subprocess.run(command + options + ["-pass", "1", "-an", "-f", "null", "-"],
                           check=True)
            print("Encoding MP4", flush=True)
            subprocess.run(command + options + ["-pass", "2"] + audio +
                           ["-movflags", "+faststart", "-y", str(temporary)], check=True)
            size = temporary.stat().st_size
            if size <= limit:
                temporary.rename(output)
                return output, size
            video_rate = int((video_rate + audio_rate) * limit / size * 0.96) - audio_rate
            if video_rate <= 0:
                break
    raise RuntimeError("The encoded file did not fit the requested limit.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--max-mb", type=float, default=50)
    args = parser.parse_args()
    if args.max_mb <= 0:
        parser.error("--max-mb must be positive")
    output, size = compress(args.source.resolve(strict=True), int(args.max_mb * 1_000_000))
    print(f"Saved {output} ({size / 1_000_000:.2f} MB)")


if __name__ == "__main__":
    main()
