# Runtime palette updates in UCI Capture

The Ultimate 64 VIC stream carries C64 color indices. UCI Capture converts those indices to RGB for preview, snapshots, and recordings. To capture runtime palette changes, have the software that changes the palette publish its current RGB values as a VPL file on the Ultimate 64. UCI Capture fetches that file over FTP. [Ultimate 64 streaming specification](https://1541u-documentation.readthedocs.io/en/master/data_streams.html)

## File location and format

The default FTP path is `/Temp/current.vpl` on the **Ultimate 64 filesystem**. To use another path, enter it in **VPL file on device** in the connection dialog or pass `--palette PATH`.

Use 16 ASCII RGB entries in C64 color-number order from 0 to 15. A simple format is `RR GG BB` in hexadecimal followed by LF (`$0A`). For example, `00 00 00` represents black. With exactly this layout, including the final LF, the file is **144 bytes**. UCI Capture also accepts lowercase hex, CRLF, blank lines, comments starting with `#`, and an optional fourth VPL field. [Ultimate palette and VPL reference](https://1541u-documentation.readthedocs.io/en/latest/uci/control_target.html#runtime-and-vpl-file-behavior)

## Publishing a palette from C64 software

With **Command Interface** enabled on firmware that supports UCI palette commands, apply the runtime palette using `04 52` followed by 16 binary RGB triples. Use those same 48 bytes to build the ASCII VPL. To publish a palette that is already active, read its RGB values with `04 51`. Both commands leave file publication to the C64 program. [Ultimate palette command reference](https://1541u-documentation.readthedocs.io/en/latest/uci/control_target.html#ctrl-cmd-get-palette-0x51)

This example uses Ultimate DOS target `$01` and the 144-byte format above. Use a DOS target you control, since opening and closing files affects that target’s current handle.

| Step | UCI bytes | Action |
| --- | --- | --- |
| 1 | `01 03` | Close any file handle left open by the publisher on DOS target `$01`. |
| 2 | `01 02 0A` + `/Temp/current.tmp` | Open a fresh temporary file. `$0A` is write plus create-always, so a leftover temp file can be truncated and reused. |
| 3 | `01 05 00 00` + 144 ASCII bytes | Write the complete VPL payload. The two zero bytes are the command’s padding. |
| 4 | `01 03` | Close the write handle. |
| 5 | `01 02 01` + `/Temp/current.tmp`, then `01 07` | Reopen for reading and use `FILE_INFO` to verify that the first four response bytes give a little-endian file size of 144 (`90 00 00 00`). |
| 6 | `01 03` | Close the read handle. |
| 7 | `01 09` + `/Temp/current.vpl` + `00` | Remove the old published file. A first publication can proceed if the file is already absent. |
| 8 | `01 0A` + `/Temp/current.tmp` + `00` + `/Temp/current.vpl` + `00` | Rename the closed, size-checked temporary file into place. |

The table shows hex bytes and literal ASCII paths. Rename requires a NUL separator between paths. The example also sends a trailing NUL on delete and rename. Check command status before proceeding. If writing, closing, or verifying the temporary file fails, leave the published file in place. [Ultimate DOS command reference](https://1541u-documentation.readthedocs.io/en/master/uci/ultimate_dos_target.html)

When UCI is idle, queue command bytes through `$DF1D` and push the command by writing `$01` to `$DF1C`. Wait for a reply, read available response bytes from `$DF1E` and status bytes from `$DF1F`, then acknowledge the reply by writing `$02` to `$DF1C`. A frame-driven program can advance the publishing steps over multiple frames to avoid waiting for file operations inside its rendering loop. [UCI register reference](https://1541u-documentation.readthedocs.io/en/latest/uci/core_uci_architecture.html)

## How UCI Capture applies updates

- The app starts with the built-in C64 palette and replaces all 16 colors together after reading a valid VPL.
- Polling defaults to **1 s**. Choose **250 ms**, **500 ms**, **1 s**, **2 s**, or **Off** in the connection dialog, or cycle the options with the **Palette** button or **P**. Off keeps the current colors.
- The app reads the configured published path. If the file is briefly absent between delete and rename, or a fetch fails, it keeps the last valid palette and retries while polling is enabled.
- To remove a stale file deliberately, use **Delete VPL** and confirm the path and device. After a successful FTP deletion, the app restores the built-in C64 palette until a fresh VPL is loaded.
- Palette changes take effect on subsequently decoded frames. The file carries no frame timestamp, so updates can lag the hardware change and short-lived palettes can be missed between polls.
