# Zyvega - Zhiyun Video Light Controller

Control Zhiyun Vega series video lights (PL103 and similar) from Linux via
Bluetooth Mesh, reverse-engineered from the Android app.

## Project setup

- Python 3.12+, managed with `uv`
- No external dependencies - we use the system `bluetooth-mesh` D-Bus service
- Run: `uv run main.py`
- Platform: Arch Linux with `bluetooth-mesh.service` running

## Architecture overview

The Android app (Zhiyun Vega, `com.zhiyun.vega`) uses the **Nordic nRF Mesh
SDK** on top of standard Bluetooth Mesh. Light control commands are sent as
**vendor model messages** with custom opcodes. The actual command encoding is
handled by a native library (`libzylink.so`), so the exact wire format is not
fully visible in the Java/smali decompilation.

On Linux, we use the **BlueZ bluetooth-mesh daemon** (`bluetooth-meshd`) via
its D-Bus API (`org.bluez.mesh`) to replicate this. Our Python code talks to
D-Bus directly using the standard library — no external packages.

```
┌─────────────┐    D-Bus     ┌──────────────────┐    BLE    ┌──────────┐
│  zyvega.py   │────────────▶│  bluetooth-meshd  │─────────▶│  PL103   │
│  (Python)    │  org.bluez  │  (BlueZ mesh)     │  Proxy   │  Light   │
└─────────────┘    .mesh     └──────────────────┘  0x1828   └──────────┘
```

## Bluetooth Mesh protocol details (from app decompile)

### Transport layer

The light communicates over standard **BT Mesh Proxy** protocol:

| Component              | UUID / Value                             |
|------------------------|------------------------------------------|
| Mesh Proxy Service     | `0x1828`                                 |
| Proxy Data In (write)  | `0x2ADD`                                 |
| Proxy Data Out (notify)| `0x2ADE`                                 |
| Provisioning Service   | `0x1827`                                 |
| Prov Data In           | `0x2ADB`                                 |
| Prov Data Out          | `0x2ADC`                                 |

There is also a **custom Zhiyun BLE service** for direct (non-mesh) control:

| Component              | UUID                                     |
|------------------------|------------------------------------------|
| Custom Service         | `0xFEE9`                                 |
| Custom Write Char      | `D44BC439-ABFD-45A2-B575-925416129600`   |
| Custom Read Char       | `D44BC439-ABFD-45A2-B575-925416129601`   |

### Mesh network parameters (from ZYMeshNetworkGenerator.smali)

The app generates a mesh network JSON conforming to the BT Mesh CDB schema:

- **Mesh name**: "ZY Mesh Network"
- **Provisioner name**: "ZY Mesh Provisioner"
- **Provisioner UUID**: `9EE44BEF-29FC-41E8-9E53-EE567A2118DF`
- **Default device key**: `CABF7E4AC8B9E254372BBD6146D318BB`
- **Unicast range**: `0x0001` – `0x199A`
- **Group range**: `0xC000` – `0xCC9A`
- **Scene range**: `0x0001` – `0x3333`
- **Default TTL**: 5
- **Network transmit**: count=2, interval=1
- **Security**: "insecure" (NoOOB provisioning)
- **NetKey**: randomly generated 128-bit
- **AppKey**: randomly generated 128-bit, bound to NetKey index 0
- **Default group address**: `0xC000`
- **Provisioner model**: `0x0001` on element `0x0001`
- **Node features**: friend=2, lowPower=2, proxy=2, relay=2

### Vendor command opcodes (from ZYLightClient.smali)

These are the CID (Command ID) constants used by the native library:

| Opcode | Hex    | Name            | Parameters (Java side)                      |
|--------|--------|-----------------|---------------------------------------------|
| Light  | 0x1001 | kCmdIdLight     | `(int deviceId, float brightness)`          |
|        |        |                 | `(int deviceId, float brightness, int mode)`|
| CCT    | 0x1002 | kCmdIdColorTemp | `(int deviceId, int colorTemp)`             |
| RGB    | 0x1003 | kCmdIdRGB       | `(int deviceId, int r, int g, int b)`       |
|        |        |                 | `(int deviceId, float bright, int r,g,b)`   |
| Hue    | 0x1004 | kCmdIdHue       | `(int deviceId, float hue)`                 |
| Sat    | 0x1005 | kCmdIdSat       | `(int deviceId, float saturation)`          |
| CMY    | 0x1006 | kCmdIdCMY       | `(int deviceId, int cmyValue)`              |
| Chroma | 0x1007 | kCmdIdChmcoor   | `(int deviceId, int gamut, float x, float y)` |
| Volt   | 0x2001 | kCmdVoltage     | query only                                  |
| MTU    | 0x2002 | kCmdMtu         | query only                                  |
| Info   | 0x2003 | kCmdDeviceInfo  | query only — returns model, gen, serialNo   |
| Online | 0xFFFF | kCmdOnlineStat  | status event                                |

### Response format (from ZYLightClient.onMessageReceived)

Responses are routed by Message.what (opcode):

- **0x1001**: Bundle key "light" (float) — brightness value
- **0x1002**: Bundle key "colorTemp" (short) — color temperature
- **0x2001**: Bundle keys "voltage" (short), "deviceId" (int)
- **0x2002**: Bundle keys "mtu" (short), "error" (short)
- **0x2003**: Bundle keys "generation", "model", "serialNo", "specification" (strings)
- **0xFFFF**: Bundle keys "deviceId" (int), "is_online" (short)

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

### PL103 capabilities (from light_static_configuration.json)

| Parameter | Range           |
|-----------|-----------------|
| CCT       | 2700K – 6500K   |
| Intensity | 0% – 100%       |
| Enable    | true / false     |
| Flicker   | 0               |

PLM103 (RGB model) additionally supports: GM ±10, RGB color space, HSI color
space, CCT range 2500K-10000K.

## What is known vs unknown

### Known (from decompile)
- Standard BT Mesh Proxy transport (0x1828)
- Network topology: provisioner → node with vendor model
- Vendor opcode IDs: 0x1001-0x1007, 0x2001-0x2003
- Java-side parameter types for each command
- Network configuration JSON schema
- Provisioning is NoOOB ("insecure")
- Device capability definitions per model/firmware

### Unknown (hidden in libzylink.so native code)
- **Company identifier** for vendor model messages (Bluetooth SIG assigned)
- **Vendor model ID** registered on the light's elements
- **Exact byte-level payload format** for each command
- **Opcode encoding** — how 0x1001 maps to BT Mesh 3-byte vendor opcode
- Whether the native lib uses standard `VendorModelMessageUnacked` or constructs
  raw PDUs

### How to determine unknowns
1. **Packet capture**: Use `btmon` or nRF Sniffer to capture traffic between
   the Android app and the light — vendor opcodes and payloads will be visible
   in the access layer
2. **Reverse-engineer libzylink.so**: The ARM64 binary at
   `vega-decompiled/lib/arm64-v8a/libzylink.so` contains the encoding logic
3. **Composition data**: Once provisioned, the light's Composition Data (Config
   Composition Data Get, opcode 0x8008) reveals its vendor model ID and company
   identifier
4. **Trial and error**: Try common company IDs and simple payload encodings
   with the known opcodes

## Linux bluetooth-mesh D-Bus API (org.bluez.mesh)

### Key interfaces

**Network1** (`/org/bluez/mesh`):
- `CreateNetwork(app_root, uuid)` — create new mesh network as provisioner
- `Attach(app_root, token)` — reconnect to existing network
- `Join(app_root, uuid)` — join as unprovisioned device
- `Import(app_root, uuid, dev_key, net_key, ...)` — import existing config
- `Leave(token)` — remove node

**Node1** (`/org/bluez/mesh/node<uuid>`):
- `Send(element_path, destination, key_index, options, data)` — send mesh msg
- `DevKeySend(...)` — send with device key
- `AddAppKey(...)` — distribute app keys to nodes
- `Publish(element_path, model, options, data)` — publish to model's pub addr

**Management1** (`/org/bluez/mesh/node<uuid>`):
- `UnprovisionedScan(options)` — scan for unprovisioned devices
- `AddNode(uuid, options)` — provision a device
- `CreateAppKey(net_index, app_index)` — create application key
- `ImportRemoteNode(primary, count, device_key)` — import remote node

**Application1** (implemented by us):
- Properties: `CompanyID`, `ProductID`, `VersionID`, `CRPL`
- Callbacks: `JoinComplete(token)`, `JoinFailed(reason)`

**Element1** (implemented by us):
- `MessageReceived(source, key_index, destination, data)` — incoming messages
- `DevKeyMessageReceived(source, remote, net_index, data)` — config messages
- Properties: `Index`, `Models`, `VendorModels`, `Location`

**Provisioner1** (implemented by us):
- `ScanResult(rssi, data, options)` — unprovisioned device found
- `RequestProvData(count)` → returns `(net_index, unicast)` for new device
- `AddNodeComplete(uuid, unicast, count)` — provisioning succeeded
- `AddNodeFailed(uuid, reason)` — provisioning failed

**ProvisionAgent1** (implemented by us):
- Handles OOB auth (not needed — the lights use NoOOB)

### Message format for Node1.Send()

The `data` parameter is the **access layer payload**: opcode bytes followed by
parameter bytes. For vendor messages this is:

```
[opcode_byte | 0xC0] [company_id_lo] [company_id_hi] [params...]
```

The 3-byte vendor opcode is: `(0xC0 | opcode_6bits)` + `company_id` (2 bytes LE).

## Implementation plan

### Phase 1: Network setup and provisioning
1. Register D-Bus application with element exposing vendor models
2. Create mesh network (`CreateNetwork`)
3. Scan for unprovisioned lights (`UnprovisionedScan`)
4. Provision the light (`AddNode` with NoOOB)
5. Read composition data to learn the light's vendor model ID and company ID
6. Add app key and bind to vendor model

### Phase 2: Basic control
7. Send vendor model messages for brightness (0x1001) and CCT (0x1002)
8. Parse status responses
9. Build CLI interface

### Phase 3: Extended features
10. RGB, HSI, CMY control for color models
11. Effects playback
12. Multi-device and group control

## Decompiled app reference

The decompiled APK is at `./vega-decompiled/`. Key locations:

| What                      | Path                                                  |
|---------------------------|-------------------------------------------------------|
| Mesh network generator    | `smali/com/zhishen/zylink/network/mesh/ZYMeshNetworkGenerator.smali` |
| Mesh network model        | `smali/com/zhishen/zylink/network/ZYMeshNetwork.smali` |
| Main light client         | `smali/com/zhishen/zylink/zylight/ZYLightClient.smali` |
| Mesh+BLE client           | `smali/com/zhishen/zylink/zylight/ZYLightMeshBleClient.smali` |
| Mesh repository           | `smali/com/zhishen/zylink/zylight/NrfMeshRepository.smali` |
| BLE mesh manager          | `smali/com/zhishen/zylink/network/BleMeshManager.smali` |
| Device info types         | `smali/com/zhishen/zylink/zylight/DeviceInfo.smali` |
| JNI callback interface    | `smali/com/zhishen/zylink/zylight/callbacks/JniBridgeCmdCallback.smali` |
| Nordic vendor msg         | `smali_classes3/no/nordicsemi/android/mesh/transport/VendorModelMessageUnacked.smali` |
| Nordic vendor msg (acked) | `smali_classes3/no/nordicsemi/android/mesh/transport/VendorModelMessageAcked.smali` |
| Native library            | `lib/arm64-v8a/libzylink.so` |
| PL103 device config       | `assets/pl103/1.6.4.config` |
| Static capabilities       | `assets/light_static_configuration.json` |
| Demo scene                | `assets/light_scene_demo.json` |
| Effect configs            | `assets/autoFx/effect/*.json` |
| Combo effects             | `assets/fx_combination_configuration.json` |

## Key code patterns

### Sending a vendor message via D-Bus (pseudocode)

```python
# After provisioning and app key binding:
node.Send(
    element_path,           # our element object path
    destination=0x0002,     # light's unicast address
    key_index=0,            # app key index
    options={},
    data=bytes([
        opcode_byte | 0xC0, # vendor opcode marker
        company_lo,         # company ID low byte
        company_hi,         # company ID high byte
        *payload            # command-specific payload
    ])
)
```

### D-Bus application object tree (what we must expose)

```
/com/zyvega
├── org.bluez.mesh.Application1  (CompanyID, ProductID, VersionID)
├── org.bluez.mesh.Provisioner1  (ScanResult, RequestProvData, etc.)
├── org.freedesktop.DBus.ObjectManager
└── /com/zyvega/ele00
    ├── org.bluez.mesh.Element1  (Index=0, Models, VendorModels)
    └── MessageReceived(), DevKeyMessageReceived()
```
