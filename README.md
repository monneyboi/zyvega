# Zyvega

Control Zhiyun Vega video lights (PL103, PLM103, etc.) from Linux via Bluetooth.

The wire protocol was reverse-engineered from the Android app (`com.zhiyun.vega`)
by decompiling the APK and disassembling the native `libzylink.so` library.

## How it works

The Zhiyun app uses two separate BLE protocols:

- **Bluetooth Mesh (0x1828)** -- provisioning and network setup only
- **Custom BLE service (0xFEE9)** -- all runtime light control via a proprietary
  framed protocol ("ZYBL")

For day-to-day use you only need the custom BLE service. The mesh subsystem is
available if you want to provision lights into a mesh network, but it's not
required for controlling them.

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- BlueZ (the `bluetoothd` service must be running)
- System packages: `dbus-python`, `python-gobject` (PyGObject)
- For mesh commands only: `bluetooth-mesh.service` (BlueZ meshd)

On Arch Linux:

```
sudo pacman -S python-dbus python-gobject bluez
```

## Usage

```
uv run main.py
```

This drops you into an interactive shell. Mesh commands require root
(`sudo uv run main.py`).

### Controlling a light

Scan for nearby Zhiyun lights, connect, and start controlling:

```
> ble scan
[ble] Scanning for 10 seconds...
  [0] PL103  AA:BB:CC:DD:EE:FF  RSSI=-45  mfid=0x0905

> ble connect 0
[ble] Connecting to PL103 (AA:BB:CC:DD:EE:FF)...
[ble] Connected, resolving services...
[ble] Ready.

> brightness 75
[ble] Setting brightness to 75%

> cct 4500
[ble] Setting CCT to 4500K

> get brightness
[ble]    [brightness] device=0 brightness=75%

> get info
[ble]    [device_info] serial=XXXX model=PL103
```

### All commands

**Light control** (requires an active BLE connection):

| Command | Description |
|---|---|
| `brightness <0-100>` | Set brightness (percent) |
| `cct <2700-6500>` | Set color temperature (Kelvin) |
| `get brightness` | Query current brightness |
| `get cct` | Query current color temperature |
| `get info` | Query device info (serial number, model) |
| `get devid` | Query device ID |

**BLE connection management:**

| Command | Description |
|---|---|
| `ble scan [seconds]` | Scan for Zhiyun lights (default 10s) |
| `ble connect <index\|mac>` | Connect by scan index or MAC address |
| `ble disconnect` | Disconnect |
| `ble status` | Show connection state |
| `ble raw <hex>` | Send raw bytes (no framing) |
| `ble send <cid> [hex]` | Send a framed ZYBL command by CID |

**Mesh** (requires `bluetooth-mesh.service` and root):

| Command | Description |
|---|---|
| `mesh start` | Connect to the mesh daemon |
| `mesh scan [seconds]` | Scan for unprovisioned devices |
| `mesh provision <index>` | Provision a device by scan index |
| `mesh configure <addr>` | Get composition data, add app key, bind model |
| `mesh nodes` | List provisioned nodes |
| `mesh reset` | Delete the network and start fresh |

### Low-level debugging

You can send arbitrary ZYBL frames with `ble send`. The tool handles framing
(header, length, CRC) automatically -- you just provide the CID and optional
payload bytes:

```
> ble send 2003
```

This sends a device info query (CID 0x2003, no payload). Responses are parsed
and printed automatically.

For completely raw writes (no framing at all), use `ble raw`:

```
> ble raw 243c0a0001000100032000001234
```

## Protocol overview

Commands are written to the custom GATT characteristic
`D44BC439-ABFD-45A2-B575-925416129600` on the `0xFEE9` service.

Each command is a ZYBL frame:

```
24 3C              header ("$<")
<len>              data section length
00                 padding
<data section>     field1(2) + seq(2) + cid(2) + payload(N)
<crc>              CRC-16/XMODEM over data section (2 bytes, LE)
```

Control payloads (brightness, CCT, etc.) follow the pattern:

```
device_id (u16 LE) + write_flag (u8: 0x00=read, 0x01=write) + value bytes
```

See `CLAUDE.md` for the full protocol documentation.

## Project structure

| File | Purpose |
|---|---|
| `main.py` | CLI entry point, GLib main loop, command dispatch |
| `gatt.py` | BLE GATT controller, ZYBL wire protocol implementation |
| `zyvega.py` | Bluetooth Mesh controller (provisioning, configuration) |

## License

This is a personal reverse-engineering project. The Zhiyun Vega app and
`libzylink.so` are proprietary software owned by Zhiyun.
