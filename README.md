# Zyvega - PL103 Video Light Controller

A Python-based command-line tool for controlling PL103 video lights via **Bluetooth Mesh**. This implementation is based on reverse engineering the Zhiyun Vega app protocol and uses the BlueZ mesh stack.

## Features

- **Bluetooth Mesh networking** - Control lights through a mesh network
- Control brightness (0-100%)
- Adjust color temperature (2700-6500K)
- Set RGB colors
- Power on/off control
- Automatic device discovery and provisioning
- Multi-device support through mesh addressing

## Installation

```bash
# Install dependencies using uv (recommended)
uv pip install -e .

# Or using pip
pip install -e .
```

## Usage

### Mesh Provisioning Commands

The PL103 lamp supports Bluetooth Mesh networking. You need to provision the device before you can control it.

```bash
# Scan for unprovisioned mesh devices
python main.py mesh scan

# Setup (provision and configure) a device
python main.py mesh setup
# Or specify device address
python main.py mesh setup --address AA:BB:CC:DD:EE:FF

# List all provisioned nodes
python main.py mesh list

# Remove a node from the mesh
python main.py mesh remove AA:BB:CC:DD:EE:FF

# Teardown the entire mesh network
python main.py mesh teardown --confirm
```

### Control Commands (After Provisioning)

Once your lamp is provisioned, you can control it:

```bash
# Turn light on/off
uv run python main.py power on
uv run python main.py power off

# Set brightness to 75%
uv run python main.py brightness 75

# Set color temperature to 5600K
uv run python main.py temp 5600

# Set RGB color: 50% brightness, full red
uv run python main.py rgb 50 255 0 0

# Control specific device (if you have multiple)
uv run python main.py --address D6:2C:5F:C2:E4:DD brightness 100

# Verbose logging
uv run python main.py -v brightness 75
```

## Architecture

### Mesh Provisioning (`mesh_provisioner.py`)

Handles device provisioning:

- **MeshProvisioner** - Provisions devices into the mesh network
- Network creation and management
- Device discovery via Mesh Provisioning Service (0x1827)
- Configuration storage in `~/.config/zyvega/mesh/`
- Integration with BlueZ mesh stack via `mesh-cfgclient`

### Mesh Control (`mesh_control.py`)

Controls provisioned devices:

- **MeshLightController** - Sends commands through mesh network
- Generic OnOff model for power control
- Generic Level model for brightness
- Vendor model for color temperature and RGB
- Multi-node support with address targeting

### CLI (`main.py`)

Click-based interface:

- Mesh provisioning commands
- Control commands via mesh
- Device management

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

## Troubleshooting

### Debug Logging

Use the `-v` or `--verbose` flag to enable detailed debug logging:

```bash
# Scan with debug logging
python main.py -v scan

# Connect and control with debug logging
python main.py -v brightness 75
```

This will show:
- All BLE devices found during scan (name, address, RSSI)
- Connection attempts and retries
- Service and characteristic discovery
- UUIDs of all available services
- Detailed error messages with stack traces
- Command bytes being sent/received

**Device not found:**
- Ensure the light is powered on and in pairing mode
- Check Bluetooth is enabled on your system
- Run with `-v` flag to see all BLE devices found
- Try running with sudo on Linux if permission issues occur

**Connection fails:**
- Run with `-v` flag to see detailed connection logs
- Check if the correct service UUID is found (0000fee9-0000-1000-8000-00805f9b34fb)
- Verify write and notify characteristics are available
- Try moving closer to the device (check RSSI in verbose output)
- Ensure device isn't already connected to another device

**Commands not working:**
- Try different device IDs (0x0000, 0xFFFF, 0x0380)
- Use `-v` flag to see command bytes and responses
- Verify the light is properly connected
- Check the device responses in the console output

**Connection issues on Linux:**
- Ensure you have the necessary Bluetooth permissions
- Install bluez: `sudo apt install bluez`
- Add your user to the bluetooth group: `sudo usermod -a -G bluetooth $USER`

## Complete Workflow

### 1. Initial Setup (First Time)

```bash
# Ensure bluetooth-mesh service is running
sudo systemctl status bluetooth-meshd

# Scan for your PL103 lamp
uv run python main.py scan

# Provision the lamp into mesh network
uv run python main.py mesh setup
```

This will:
- Create mesh network configuration in `~/.config/zyvega/mesh/`
- Scan for unprovisioned devices (advertising service 0x1827)
- Provision with no OOB authentication
- Assign unicast address (0x0001, 0x0002, etc.)
- Configure with application keys

### 2. Control Your Lamp

```bash
# Turn on
uv run python main.py power on

# Set brightness
uv run python main.py brightness 80

# Set warm white
uv run python main.py temp 3200
```

### 3. Manage Multiple Lamps

```bash
# List all provisioned lamps
uv run python main.py mesh list

# Control specific lamp
uv run python main.py -a D6:2C:5F:C2:E4:DD brightness 50

# Add another lamp
uv run python main.py mesh setup -a AA:BB:CC:DD:EE:FF
```

### 4. Teardown (Optional)

```bash
# Remove specific lamp
uv run python main.py mesh remove D6:2C:5F:C2:E4:DD

# Destroy entire network
uv run python main.py mesh teardown --confirm
```

## Development

The project uses:
- **Python 3.12+** - Modern Python features
- **Click** - CLI framework
- **Bleak** - Cross-platform Bluetooth LE library
- **dbus-python** - D-Bus bindings for mesh communication
- **PyGObject** - GLib integration for mesh stack

## License

This project is based on reverse engineering of the Zhiyun Vega app protocol and is provided for educational and personal use.
