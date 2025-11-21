# Zyvega - PL103 Video Light Controller

A Python-based command-line tool for controlling PL103 video lights via Bluetooth LE. This implementation is based on reverse engineering the Zhiyun Vega app protocol.

## Features

- Control brightness (0-100%)
- Adjust color temperature (2700-6500K)
- Set RGB colors
- Interactive mode for multiple commands
- Automatic device discovery
- Cross-platform support (Linux, macOS, Windows)

## Installation

```bash
# Install dependencies using uv (recommended)
uv pip install -e .

# Or using pip
pip install -e .
```

## Usage

### Basic Commands

```bash
# Scan for available PL103 devices
python main.py scan

# Set brightness to 75%
python main.py brightness 75

# Set color temperature to 5600K
python main.py temp 5600

# Set RGB color: 50% brightness, full red
python main.py rgb 50 255 0 0

# Specify device address (if you have multiple devices)
python main.py --address AA:BB:CC:DD:EE:FF brightness 100
```

### Interactive Mode

For sending multiple commands without reconnecting:

```bash
python main.py interactive
```

Once connected, you can use:
- `b <0-100>` - Set brightness
- `t <2700-6500>` - Set color temperature
- `rgb <0-100> R G B` - Set RGB color
- `q` or `quit` - Exit

Example session:
```
> b 75
Set brightness to 75%
> t 5600
Set color temperature to 5600K
> rgb 80 255 128 64
Set RGB to (255, 128, 64) at 80%
> q
```

## Architecture

### Python Library (`videolight_control.py`)

The library provides:

- **CrcCheck** - CRC16 checksum calculator for protocol messages
- **VideoLightCommand** - Command builder that constructs properly formatted BLE messages
- **VideoLightController** - Bluetooth LE controller using the Bleak library

### CLI (`main.py`)

A Click-based command-line interface that provides:

- Simple one-shot commands for quick control
- Interactive mode for extended control sessions
- Device scanning and auto-discovery
- Input validation and error handling

## Protocol Details

### Command IDs

From the reverse-engineered Zhiyun Vega app protocol:

```
CMD_LIGHT:     0x1001  - Brightness/Power control
CMD_COLOR_TEMP: 0x1002  - Color temperature control
CMD_RGB:       0x1003  - RGB control
CMD_HUE:       0x1004  - Hue control
CMD_SAT:       0x1005  - Saturation control
CMD_CMY:       0x1006  - CMY control
CMD_CHMCOOR:   0x1007  - Chromatic coordinate control
```

### Message Structure

```
Header:       0x24 0x3C
Length:       N (data section length)
Unknown:      0x00
Data Section:
  Field1:     0x01 0x00 (big-endian)
  Sequence:   2 bytes (big-endian)
  Msg Type:   2 bytes (big-endian)
  Payload:    variable
CRC:          2 bytes (little-endian)
```

### Bluetooth LE Details

- **Service UUID:** `0000fee9-0000-1000-8000-00805f9b34fb`
- **Write Characteristic:** `d44bc439-abfd-45a2-b575-925416129600`
- **Notify Characteristic:** `d44bc439-abfd-45a2-b575-925416129601`

### Payload Formats

**Brightness (0x1001):**
```
[deviceId_high, deviceId_low, value_high, value_low]
- Device ID: 0x0380 (896 decimal, default)
- Value: 0-10000 (brightness × 100)
```

**Color Temperature (0x1002):**
```
[deviceId_high, deviceId_low, kelvin_high, kelvin_low]
- Device ID: 0x0380 (896 decimal, default)
- Kelvin: 2700-6500
```

**RGB (0x1003):**
```
[deviceId_high, deviceId_low, brightness_high, brightness_low, r, g, b]
- Device ID: 0x0380 (896 decimal, default)
- Brightness: 0-10000 (brightness × 100)
- R, G, B: 0-255 each
```

## Device ID Notes

The default device ID `0x0380` (896 decimal) is used for most commands. This can be overridden using the `--device-id` option:

```bash
# Broadcast to all devices
python main.py --device-id 0xFFFF brightness 75

# Address device 0
python main.py brightness 75 --device-id 0x0000
```

## Legacy Web Implementation

The original JavaScript implementation is still available:

- `videolight_control.js` - JavaScript library using Web Bluetooth API
- `videolight_test.html` - Browser-based testing interface

To use the web interface, open `videolight_test.html` in a browser that supports Web Bluetooth (Chrome, Edge).

## Troubleshooting

**Device not found:**
- Ensure the light is powered on and in pairing mode
- Check Bluetooth is enabled on your system
- Try running with sudo on Linux if permission issues occur

**Commands not working:**
- Try different device IDs (0x0000, 0xFFFF, 0x0380)
- Verify the light is properly connected
- Check the device responses in the console output

**Connection issues on Linux:**
- Ensure you have the necessary Bluetooth permissions
- Install bluez: `sudo apt install bluez`
- Add your user to the bluetooth group: `sudo usermod -a -G bluetooth $USER`

## Development

The project uses:
- **Python 3.12+** - Modern Python features
- **Click** - CLI framework
- **Bleak** - Cross-platform Bluetooth LE library

## License

This project is based on reverse engineering of the Zhiyun Vega app protocol and is provided for educational and personal use.
