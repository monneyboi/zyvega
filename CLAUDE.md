# Zyvega - Zhiyun Video Light Controller

Control Zhiyun Vega series video lights (PL103 and similar) from Linux via
Bluetooth, reverse-engineered from the Android app.

## Project setup

- Python 3.12+, managed with `uv`
- No external dependencies - we use system D-Bus bindings (`dbus-python`, `PyGObject`)
- Run: `uv run main.py` (mesh commands require `sudo`)
- Platform: Arch Linux with `bluetooth-mesh.service` running

## Architecture overview

The Android app (Zhiyun Vega, `com.zhiyun.vega`) uses **two separate BLE
protocols** for different purposes:

1. **Bluetooth Mesh** (via Nordic nRF Mesh SDK) — used **only for provisioning
   and network setup**. The mesh proxy service (0x1828) carries provisioning
   PDUs and config messages (composition data, app key distribution, model
   binding). After provisioning completes, mesh is not used for light control.

2. **Custom Zhiyun BLE service** (0xFEE9) — used for **all runtime light
   control**. Commands are encoded by the native library (`libzylink.so`) and
   written directly to a custom GATT characteristic. This is a proprietary
   protocol we call "ZYBL".

The command flow in the app is:
```
ZYLightClient.setLight(deviceId, brightness)
  → libzylink.so (native JNI — encodes command bytes)
  → onCmdCallback(opcode, Message)
  → onLinkDataSend(byte[])
  → BleMeshManager.sendCmdData(byte[])
  → GATT write to D44BC439-...-9600 (0xFEE9 custom characteristic)
```

Our code mirrors this with two subsystems:

```
┌──────────┐  D-Bus   ┌──────────────────┐   BLE    ┌──────────┐
│ zyvega.py │────────▶│  bluetooth-meshd  │────────▶│          │
│ (mesh)    │ org.bluez│  (BlueZ mesh)     │ 0x1828  │          │
└──────────┘  .mesh   └──────────────────┘ (prov)   │  PL103   │
                                                     │  Light   │
┌──────────┐  D-Bus   ┌──────────────────┐   BLE    │          │
│  gatt.py  │────────▶│    bluetoothd     │────────▶│          │
│ (control) │ org.bluez│  (BlueZ)          │ 0xFEE9  │          │
└──────────┘          └──────────────────┘ (ctrl)   └──────────┘
```

## BLE service details (from app decompile)

### Mesh services (provisioning only)

| Component              | UUID / Value                             |
|------------------------|------------------------------------------|
| Mesh Proxy Service     | `0x1828`                                 |
| Proxy Data In (write)  | `0x2ADD`                                 |
| Proxy Data Out (notify)| `0x2ADE`                                 |
| Provisioning Service   | `0x1827`                                 |
| Prov Data In           | `0x2ADB`                                 |
| Prov Data Out          | `0x2ADC`                                 |

### Custom Zhiyun service (runtime control)

| Component              | UUID                                     |
|------------------------|------------------------------------------|
| Custom Service         | `0xFEE9`                                 |
| Custom Write Char      | `D44BC439-ABFD-45A2-B575-925416129600`   |
| Custom Read Char       | `D44BC439-ABFD-45A2-B575-925416129601`   |

## ZYBL wire protocol (custom BLE service)

Commands sent over the 0xFEE9 service use a framed protocol:

```
Header (2B)  Len (1B)  Pad (1B)  Data Section (N B)          CRC (2B LE)
  24 3C       len       00        field1 + seq + cid + payload   crc16
```

- **Header**: always `$<` (`0x24 0x3C`)
- **Len**: length of data section in bytes
- **Pad**: always `0x00`
- **Data section**: `field1(u16 LE) + seq(u16 LE) + cid(u16 LE) + payload`
  - `field1`: always `0x0001` (purpose unknown, possibly protocol version)
  - `seq`: sequence number (incrementing)
  - `cid`: command ID (e.g. `0x2003` for device info)
- **CRC**: CRC-16/XMODEM over the data section (poly=0x1021, init=0)

Responses use the same framing. Implemented in `gatt.py` as `zybl_frame()` and
`zybl_parse()`.

### Command IDs (from ZYLightClient.smali)

| CID    | Name            | Parameters (Java side)                      |
|--------|-----------------|---------------------------------------------|
| 0x1001 | kCmdIdLight     | `(int deviceId, float brightness)`          |
|        |                 | `(int deviceId, float brightness, int mode)`|
| 0x1002 | kCmdIdColorTemp | `(int deviceId, int colorTemp)`             |
| 0x1003 | kCmdIdRGB       | `(int deviceId, int r, int g, int b)`       |
|        |                 | `(int deviceId, float bright, int r,g,b)`   |
| 0x1004 | kCmdIdHue       | `(int deviceId, float hue)`                 |
| 0x1005 | kCmdIdSat       | `(int deviceId, float saturation)`          |
| 0x1006 | kCmdIdCMY       | `(int deviceId, int cmyValue)`              |
| 0x1007 | kCmdIdChmcoor   | `(int deviceId, int gamut, float x, float y)` |
| 0x2001 | kCmdVoltage     | query only                                  |
| 0x2002 | kCmdMtu         | query only                                  |
| 0x2003 | kCmdDeviceInfo  | query only — returns model, gen, serialNo   |
| 0xFFFF | kCmdOnlineStat  | status event                                |

### Response format (from ZYLightClient.onMessageReceived)

Responses are routed by Message.what (opcode):

- **0x1001**: Bundle key "light" (float) — brightness value
- **0x1002**: Bundle key "colorTemp" (short) — color temperature
- **0x2001**: Bundle keys "voltage" (short), "deviceId" (int)
- **0x2002**: Bundle keys "mtu" (short), "error" (short)
- **0x2003**: Bundle keys "generation", "model", "serialNo", "specification" (strings)
- **0xFFFF**: Bundle keys "deviceId" (int), "is_online" (short)

## Device details (discovered via composition data)

- **Company ID**: `0x0059` (Nordic Semiconductor — light uses nRF SDK)
- **Vendor Model ID**: `0x0002` on element 0
- **SIG models**: 0x0000 (Config Server), 0x0002 (Health Server), 0x1000 (Generic OnOff Server)
- **Features**: Relay, Proxy, Friend enabled; Low Power disabled
- **CRPL**: 40

### Mesh network parameters (from ZYMeshNetworkGenerator.smali)

- **Mesh name**: "ZY Mesh Network"
- **Provisioner name**: "ZY Mesh Provisioner"
- **Provisioner UUID**: `9EE44BEF-29FC-41E8-9E53-EE567A2118DF`
- **Default device key**: `CABF7E4AC8B9E254372BBD6146D318BB`
- **Unicast range**: `0x0001` – `0x199A`
- **Group range**: `0xC000` – `0xCC9A`
- **Default TTL**: 5
- **Security**: "insecure" (NoOOB provisioning)

### PL103 capabilities (from light_static_configuration.json)

| Parameter | Range           |
|-----------|-----------------|
| CCT       | 2700K – 6500K   |
| Intensity | 0% – 100%       |
| Enable    | true / false     |
| Flicker   | 0               |

PLM103 (RGB model) additionally supports: GM +/-10, RGB color space, HSI color
space, CCT range 2500K-10000K.

### Device config protocol (from .config files)

Each device model has versioned `.config` files in `assets/pl<model>/` defining
supported CIDs. Example for PL103 (`1.6.4.config`):

**Required CIDs** (must be implemented):
- Basic: 0x0001, 0x0003, 0x0004, 0x0005, 0x0006 (200ms timeout)
- Status: 0x2001, 0x2003, 0x2004 (200ms timeout)
- Effects: 0x7001, 0x7003, 0x7004, 0x7011, 0x7013, 0x7014 (2000ms timeout)
- Config: 0x8001, 0x8002, 0x8003, 0x8004 (2000ms timeout)
- Feature: 0x1008 (200ms timeout)

**Optional CIDs** (capability-dependent):
- 0x1001 (brightness), 0x1002 (CCT), 0x1008, 0x1101, 0x1201, 0x1202
- All with `controlMode: "0x33"` and 200ms timeout
- 0x1002 includes color temp range: std 2700-6500K

**Device features**: MTU 150 bytes

## What is known vs unknown

### Known
- Mesh is for provisioning only; runtime control is via 0xFEE9 custom BLE
- Company ID: `0x0059`, Vendor Model: `0x0002`
- ZYBL frame format (header, CRC, CID field)
- Command IDs (CIDs): 0x1001-0x1007, 0x2001-0x2003
- Java-side parameter types for each command
- Network configuration and provisioning flow
- Device capabilities per model/firmware

### Unknown (hidden in libzylink.so)
- **Payload byte format** for each CID — how Java parameters (float brightness,
  int colorTemp, etc.) are encoded into the ZYBL payload bytes after the CID
- Whether the payload encoding is simple (e.g. raw float32/int16) or has
  additional structure (sub-fields, flags, device addressing within payload)
- The exact role of `field1` (`0x0001`) in the ZYBL frame

### How to determine unknowns
1. **Reverse-engineer libzylink.so**: ARM64 binary at
   `vega-decompiled/lib/arm64-v8a/libzylink.so` — contains all encoding logic
2. **Packet capture**: Use `btmon` to capture traffic between the Android app
   and the light — ZYBL frames will be visible in ATT write operations
3. **Trial and error**: Send ZYBL frames with guessed payload formats using
   `ble send` and observe the light's response

## Implementation plan

### Phase 1: Provisioning (complete)
1. Register D-Bus application with element exposing vendor models
2. Create mesh network (`CreateNetwork`)
3. Scan for unprovisioned lights (`UnprovisionedScan`)
4. Provision the light (`AddNode` with NoOOB)
5. Read composition data — discovered company=0x0059, vendor model=0x0002
6. Add app key and bind to vendor model

### Phase 2: Direct BLE control (current)
7. Connect to light via standard BLE (0xFEE9 service) — **done** (`gatt.py`)
8. Implement ZYBL wire protocol (framing, CRC) — **done** (`gatt.py`)
9. Reverse-engineer payload format from `libzylink.so`
10. Implement brightness (0x1001) and CCT (0x1002) commands
11. Parse responses

### Phase 3: Extended features
12. RGB, HSI, CMY control for color models
13. Effects playback
14. Multi-device and group control

## Code structure

| File       | Purpose                                                    |
|------------|------------------------------------------------------------|
| `main.py`  | CLI entry point, GLib main loop, command dispatch          |
| `gatt.py`  | Direct BLE GATT controller (0xFEE9), ZYBL protocol        |
| `zyvega.py`| Mesh controller (provisioning, config via bluetooth-meshd) |

## BlueZ mesh D-Bus API reference (org.bluez.mesh)

### Key interfaces

**Network1** (`/org/bluez/mesh`):
- `CreateNetwork(app_root, uuid)` — create new mesh network as provisioner
- `Attach(app_root, token)` — reconnect to existing network
- `Leave(token)` — remove node

**Management1** (`/org/bluez/mesh/node<uuid>`):
- `UnprovisionedScan(options)` — scan for unprovisioned devices
- `AddNode(uuid, options)` — provision a device
- `CreateAppKey(net_index, app_index)` — create application key

**Application1** (implemented by us):
- Properties: `CompanyID`, `ProductID`, `VersionID`, `CRPL`
- Callbacks: `JoinComplete(token)`, `JoinFailed(reason)`

**Provisioner1** (implemented by us):
- `ScanResult(rssi, data, options)` — unprovisioned device found
- `RequestProvData(count)` -> returns `(net_index, unicast)` for new device
- `AddNodeComplete(uuid, unicast, count)` — provisioning succeeded
- `AddNodeFailed(uuid, reason)` — provisioning failed

### D-Bus application object tree

```
/com/zyvega
├── org.bluez.mesh.Application1  (CompanyID, ProductID, VersionID)
├── org.bluez.mesh.Provisioner1  (ScanResult, RequestProvData, etc.)
├── org.freedesktop.DBus.ObjectManager
└── /com/zyvega/ele00
    ├── org.bluez.mesh.Element1  (Index=0, Models, VendorModels)
    └── MessageReceived(), DevKeyMessageReceived()
```

## BlueZ D-Bus lessons learned

- All calls to the mesh daemon (CreateNetwork, Attach, AddNode) MUST be async
  (reply_handler/error_handler) — blocking calls deadlock because the daemon
  calls back to our GetManagedObjects during the same call
- GetManagedObjects must advertise Provisioner1 interface (even with empty props)
  or the daemon denies scan/provision
- ProvisionAgent1 object must implement Properties.GetAll — daemon reads
  Capabilities via D-Bus properties, not GetManagedObjects
- Must run as root (sudo uv run main.py) for mesh D-Bus policy permissions

## Decompiled app reference

The decompiled APK is at `./vega-decompiled/`. Key locations:

| What                      | Path                                                  |
|---------------------------|-------------------------------------------------------|
| Mesh network generator    | `smali/com/zhishen/zylink/network/mesh/ZYMeshNetworkGenerator.smali` |
| Main light client         | `smali/com/zhishen/zylink/zylight/ZYLightClient.smali` |
| BLE client (control path) | `smali/com/zhishen/zylink/zylight/ZYLightBleClient.smali` |
| BLE mesh manager          | `smali/com/zhishen/zylink/network/BleMeshManager.smali` |
| Mesh repository           | `smali/com/zhishen/zylink/zylight/NrfMeshRepository.smali` |
| JNI callback interface    | `smali/com/zhishen/zylink/zylight/callbacks/JniBridgeCmdCallback.smali` |
| Native library            | `lib/arm64-v8a/libzylink.so` |
| PL103 device config       | `assets/pl103/1.6.4.config` |
| Static capabilities       | `assets/light_static_configuration.json` |
