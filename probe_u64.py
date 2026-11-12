"""Identify a streaming Ultimate 64 and inspect its palette-related REST data."""

import argparse
import getpass
import http.client
import json
import socket
import struct
import time
from urllib.parse import quote


def find_sender(port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("", port))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            listener.settimeout(deadline - time.monotonic())
            packet, (sender, _) = listener.recvfrom(65535)
            if len(packet) == 780 and struct.unpack_from("<HBBH", packet, 6) == (384, 4, 4, 0):
                return sender
    raise TimeoutError("No VIC video packets received")


def get_json(host, path, password):
    connection = http.client.HTTPConnection(host, timeout=3)
    try:
        headers = {"X-Password": password} if password else {}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"{path} returned HTTP {response.status} {response.reason}")
        return json.loads(response.read())
    finally:
        connection.close()


def palette_fields(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            if "palette" in key.casefold():
                yield " / ".join((*path, key)), child
            elif isinstance(child, dict):
                yield from palette_fields(child, (*path, key))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="U64 IP or hostname. Omit to identify it from VIC UDP.")
    parser.add_argument("--video-port", type=int, default=11000)
    parser.add_argument("--password", action="store_true", help="Prompt for the U64 network password.")
    args = parser.parse_args()
    try:
        if not args.host:
            print(f"Waiting up to 15 seconds for VIC video on UDP {args.video_port}...", flush=True)
        host = args.host or find_sender(args.video_port)
        print(f"U64 address: {host}", flush=True)
        password = getpass.getpass("U64 network password: ") if args.password else ""
        info = get_json(host, "/v1/info", password)
        print(f"Device: {info.get('product', '?')}, firmware {info.get('firmware_version', '?')}")
        path = "/v1/configs/" + quote("U64 Specific Settings")
        previous = None
        print("Watching palette-related configuration once per second. Press Ctrl+C to quit.")
        while True:
            started = time.monotonic()
            fields = dict(palette_fields(get_json(host, path, password)))
            if fields != previous:
                print(json.dumps(fields, indent=2), flush=True)
                previous = fields
            time.sleep(max(0, 1 - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError, http.client.HTTPException) as error:
        parser.exit(1, f"Probe failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
