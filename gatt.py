"""Direct BLE GATT control for Zhiyun Vega lights.

Uses the custom Zhiyun BLE service (0xFEE9) via BlueZ D-Bus API (org.bluez).
Implements the ZYBL wire protocol for framing commands.

Separate from the mesh controller — this talks to org.bluez (bluetoothd),
not org.bluez.mesh (bluetooth-meshd).
"""

import struct

import dbus
from gi.repository import GLib

# --- ZYBL command IDs ---

CID_BRIGHTNESS = 0x1001
CID_CCT = 0x1002
CID_SATURATION = 0x1005
CID_CHROMA = 0x1007
CID_HSI = 0x100A
CID_BRIGHTNESS_MODE = 0x100B
CID_VOLTAGE = 0x2001
CID_DEVICE_INFO = 0x2003
CID_DEVICE_ID = 0x2005
CID_ONLINE = 0xFFFF

# BlueZ D-Bus constants
BLUEZ_SERVICE = "org.bluez"
ADAPTER_PATH = "/org/bluez/hci0"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHAR_IFACE = "org.bluez.GattCharacteristic1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROPS_IFACE = "org.freedesktop.DBus.Properties"

# Zhiyun custom service / characteristic UUIDs
ZY_SERVICE_UUID = "0000fee9-0000-1000-8000-00805f9b34fb"
ZY_WRITE_UUID = "d44bc439-abfd-45a2-b575-925416129600"
ZY_READ_UUID = "d44bc439-abfd-45a2-b575-925416129601"

# Connection states
STATE_DISCONNECTED = "DISCONNECTED"
STATE_CONNECTING = "CONNECTING"
STATE_RESOLVING = "RESOLVING"
STATE_READY = "READY"

# --- ZYBL wire protocol ---

ZYBL_HEADER = bytes([0x24, 0x3C])


def _crc16_xmodem(data):
    """CRC-16/XMODEM: poly=0x1021, init=0, no reflect, no xor out."""
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return crc


def zybl_frame(cid, payload=b"", seq=1):
    """Build a complete ZYBL message frame.

    Data section: field1(u16 LE) + seq(u16 LE) + cid(u16 LE) + payload
    Frame: header(2) + len(1) + 0x00(1) + data_section(N) + crc(2 LE)
    """
    data_section = struct.pack("<HHH", 0x0100, seq, cid) + payload
    crc = _crc16_xmodem(data_section)
    length = len(data_section)
    return ZYBL_HEADER + bytes([length, 0x00]) + data_section + struct.pack("<H", crc)


def zybl_parse(raw):
    """Parse a ZYBL frame. Returns (seq, cid, payload) or None on error."""
    if len(raw) < 10:  # minimum: 2 header + 1 len + 1 pad + 6 data_section_min + 2 crc
        return None
    if raw[0:2] != ZYBL_HEADER:
        return None

    length = raw[2]
    # pad = raw[3]  # always 0x00
    if len(raw) < length + 6:
        return None

    data_section = raw[4:4 + length]
    crc_received = struct.unpack_from("<H", raw, 4 + length)[0]
    crc_computed = _crc16_xmodem(data_section)

    if crc_received != crc_computed:
        print(f"[zybl] CRC mismatch: got {crc_received:#06x}, expected {crc_computed:#06x}")
        return None

    if length < 6:
        return None

    field1, seq, cid = struct.unpack_from("<HHH", data_section, 0)
    payload = data_section[6:]
    return (seq, cid, payload)


# --- ZYBL payload builders ---


def _control_payload(device_id, value_bytes):
    """Build a control (write) payload: device_id(u16 LE) + 0x01 + value."""
    return struct.pack("<HB", device_id, 0x01) + value_bytes


def _query_payload(device_id, value_len):
    """Build a query (read) payload: device_id(u16 LE) + 0x00 + zeroes."""
    return struct.pack("<HB", device_id, 0x00) + b"\x00" * value_len


# --- ZYBL response parsing ---


CID_NAMES = {
    CID_BRIGHTNESS: "brightness",
    CID_CCT: "cct",
    CID_SATURATION: "saturation",
    CID_CHROMA: "chroma",
    CID_HSI: "hsi",
    CID_BRIGHTNESS_MODE: "brightness+mode",
    CID_VOLTAGE: "voltage",
    CID_DEVICE_INFO: "device_info",
    CID_DEVICE_ID: "device_id",
    CID_ONLINE: "online_status",
}


def parse_response(cid, payload):
    """Parse a ZYBL response payload by CID. Returns a human-readable string."""
    name = CID_NAMES.get(cid, f"0x{cid:04x}")

    if cid == CID_DEVICE_INFO:
        # payload is null-terminated strings: serial + model + ...
        parts = payload.split(b"\x00")
        strings = [p.decode("ascii", errors="replace") for p in parts if p]
        if len(strings) >= 2:
            return f"[{name}] serial={strings[0]} model={strings[1]}"
        return f"[{name}] {strings}"

    # Control responses have: device_id(u16) + write_flag(u8) + value
    if len(payload) < 3:
        return f"[{name}] payload={payload.hex()}"

    device_id = struct.unpack_from("<H", payload, 0)[0]
    flag = payload[2]
    value = payload[3:]

    if cid == CID_BRIGHTNESS and len(value) >= 4:
        bright = struct.unpack_from("<f", value, 0)[0]
        return f"[{name}] device={device_id} brightness={bright:.0f}%"

    if cid == CID_CCT and len(value) >= 2:
        kelvin = struct.unpack_from("<H", value, 0)[0]
        return f"[{name}] device={device_id} cct={kelvin}K"

    if cid == CID_SATURATION and len(value) >= 4:
        sat = struct.unpack_from("<f", value, 0)[0]
        return f"[{name}] device={device_id} saturation={sat:.0f}%"

    if cid == CID_HSI and len(value) >= 10:
        hue, sat, intensity = struct.unpack_from("<ffH", value, 0)
        return f"[{name}] device={device_id} hue={hue:.1f} sat={sat:.0f}% intensity={intensity}"

    if cid == CID_BRIGHTNESS_MODE and len(value) >= 5:
        bright = struct.unpack_from("<f", value, 0)[0]
        mode = struct.unpack_from("<b", value, 4)[0]
        return f"[{name}] device={device_id} brightness={bright:.0f}% mode={mode}"

    if cid == CID_VOLTAGE and len(value) >= 2:
        voltage = struct.unpack_from("<H", value, 0)[0]
        return f"[{name}] device={device_id} voltage={voltage}"

    if cid == CID_ONLINE and len(value) >= 2:
        online = struct.unpack_from("<H", value, 0)[0]
        return f"[{name}] device={device_id} online={bool(online)}"

    return f"[{name}] device={device_id} flag={flag:#x} value={value.hex()}"


class GattController:
    """Direct BLE GATT controller for Zhiyun lights via BlueZ D-Bus."""

    def __init__(self, bus):
        self._bus = bus
        self._state = STATE_DISCONNECTED
        self._seq = 0
        self._device_id = None  # mfid from BLE advertisement (u16)

        # Discovered devices: list of (path, name, mac, rssi, mfid)
        self._discovered = []

        # Connected device state
        self._device_path = None
        self._write_char_path = None
        self._read_char_path = None
        self._props_signal = None
        self._notify_signal = None

    @property
    def state(self):
        return self._state

    # --- Scanning ---

    def scan(self, seconds=10):
        """Scan for Zhiyun lights advertising the 0xFEE9 service."""
        self._discovered.clear()

        try:
            adapter = dbus.Interface(
                self._bus.get_object(BLUEZ_SERVICE, ADAPTER_PATH),
                ADAPTER_IFACE,
            )
        except dbus.exceptions.DBusException as e:
            print(f"[ble] Cannot access adapter: {e.get_dbus_message()}")
            return

        # Set discovery filter to BLE only
        try:
            adapter.SetDiscoveryFilter({"Transport": dbus.String("le")})
        except dbus.exceptions.DBusException:
            pass  # filter not critical

        # Listen for new devices
        sig = self._bus.add_signal_receiver(
            self._on_interfaces_added,
            dbus_interface=DBUS_OM_IFACE,
            signal_name="InterfacesAdded",
        )

        # Also check already-known devices
        self._check_existing_devices()

        try:
            adapter.StartDiscovery()
            print(f"[ble] Scanning for {seconds} seconds...")
        except dbus.exceptions.DBusException as e:
            print(f"[ble] StartDiscovery failed: {e.get_dbus_message()}")
            sig.remove()
            return

        def _stop_scan():
            try:
                adapter.StopDiscovery()
            except dbus.exceptions.DBusException:
                pass
            sig.remove()
            if not self._discovered:
                print("[ble] No Zhiyun lights found.")
            else:
                print(f"[ble] Scan complete. Found {len(self._discovered)} device(s).")
            return False

        GLib.timeout_add_seconds(seconds, _stop_scan)

    def _check_existing_devices(self):
        """Check BlueZ ObjectManager for already-known devices with 0xFEE9."""
        try:
            om = dbus.Interface(
                self._bus.get_object(BLUEZ_SERVICE, "/"),
                DBUS_OM_IFACE,
            )
            objects = om.GetManagedObjects()
        except dbus.exceptions.DBusException:
            return

        for path, ifaces in objects.items():
            if DEVICE_IFACE in ifaces:
                self._maybe_add_device(path, ifaces[DEVICE_IFACE])

    def _on_interfaces_added(self, path, ifaces):
        """Signal handler for new BlueZ objects (discovered devices)."""
        if DEVICE_IFACE in ifaces:
            self._maybe_add_device(path, ifaces[DEVICE_IFACE])

    def _maybe_add_device(self, path, props):
        """Add device to discovered list if it looks like a Zhiyun light."""
        name = str(props.get("Name", props.get("Alias", "")))
        uuids = [str(u).lower() for u in props.get("UUIDs", [])]
        is_zhiyun = (
            ZY_SERVICE_UUID in uuids
            or name.upper().startswith("PL")  # PL103, PLM103, etc.
        )
        if not is_zhiyun:
            return

        # Skip duplicates
        for _, _, mac, _, _ in self._discovered:
            if mac == str(props.get("Address", "")):
                return

        name = str(props.get("Name", props.get("Alias", "Unknown")))
        mac = str(props.get("Address", "??:??:??:??:??:??"))
        rssi = int(props.get("RSSI", 0))

        # Extract mfid from ManufacturerData (device_id for ZYBL protocol)
        # In BlueZ, ManufacturerData is {company_id: data_bytes}.
        # The app reads mfid from raw adv bytes[0:2] which IS the company_id.
        mfid = None
        mfg_data = props.get("ManufacturerData", {})
        for company_id, data in mfg_data.items():
            mfid = int(company_id)
            break

        idx = len(self._discovered)
        self._discovered.append((str(path), name, mac, rssi, mfid))
        mfid_str = f"  mfid={mfid:#06x}" if mfid is not None else ""
        print(f"  [{idx}] {name}  {mac}  RSSI={rssi}{mfid_str}")

    # --- Connection ---

    def connect(self, target):
        """Connect to a device by scan index or MAC address.

        Args:
            target: int (scan index) or str (MAC address like AA:BB:CC:DD:EE:FF)
        """
        if self._state != STATE_DISCONNECTED:
            print(f"[ble] Already {self._state.lower()}, disconnect first.")
            return

        device_path = None

        if isinstance(target, int):
            if target < 0 or target >= len(self._discovered):
                print(f"[ble] Invalid index {target}. Run 'ble scan' first.")
                return
            device_path = self._discovered[target][0]
            mfid = self._discovered[target][4]
            if mfid is not None:
                print(f"[ble] Model ID (mfid): {mfid:#06x}")
            print(f"[ble] Connecting to {self._discovered[target][1]} ({self._discovered[target][2]})...")
        else:
            # MAC address — convert to BlueZ path
            mac_path = target.upper().replace(":", "_")
            device_path = f"{ADAPTER_PATH}/dev_{mac_path}"
            print(f"[ble] Connecting to {target}...")

        self._device_path = device_path
        self._state = STATE_CONNECTING

        # Watch for property changes (Connected, ServicesResolved)
        self._props_signal = self._bus.add_signal_receiver(
            self._on_properties_changed,
            dbus_interface=DBUS_PROPS_IFACE,
            signal_name="PropertiesChanged",
            path=device_path,
        )

        try:
            device = dbus.Interface(
                self._bus.get_object(BLUEZ_SERVICE, device_path),
                DEVICE_IFACE,
            )
            device.Connect(
                reply_handler=lambda: None,
                error_handler=self._on_connect_error,
            )
        except dbus.exceptions.DBusException as e:
            print(f"[ble] Connect failed: {e.get_dbus_message()}")
            self._cleanup()

    def _on_connect_error(self, error):
        print(f"[ble] Connect failed: {error.get_dbus_message()}")
        self._cleanup()

    def _on_properties_changed(self, interface, changed, invalidated):
        """Handle property changes on the connected device."""
        if interface != DEVICE_IFACE:
            return

        if "Connected" in changed:
            connected = bool(changed["Connected"])
            if connected and self._state == STATE_CONNECTING:
                self._state = STATE_RESOLVING
                print("[ble] Connected, resolving services...")
            elif not connected:
                print("[ble] Disconnected.")
                self._cleanup()

        if "ServicesResolved" in changed and bool(changed["ServicesResolved"]):
            if self._state == STATE_RESOLVING:
                self._resolve_characteristics()

    def _resolve_characteristics(self):
        """Find the Zhiyun write and read characteristics via ObjectManager."""
        try:
            om = dbus.Interface(
                self._bus.get_object(BLUEZ_SERVICE, "/"),
                DBUS_OM_IFACE,
            )
            objects = om.GetManagedObjects()
        except dbus.exceptions.DBusException as e:
            print(f"[ble] Failed to enumerate objects: {e.get_dbus_message()}")
            self.disconnect()
            return

        self._write_char_path = None
        self._read_char_path = None

        for path, ifaces in objects.items():
            if not str(path).startswith(self._device_path):
                continue

            if GATT_CHAR_IFACE in ifaces:
                uuid = str(ifaces[GATT_CHAR_IFACE].get("UUID", "")).lower()
                if uuid == ZY_WRITE_UUID:
                    self._write_char_path = str(path)
                elif uuid == ZY_READ_UUID:
                    self._read_char_path = str(path)

        if not self._write_char_path or not self._read_char_path:
            print("[ble] Could not find Zhiyun characteristics (0xFEE9 service).")
            print(f"[ble]   Write char: {self._write_char_path or 'NOT FOUND'}")
            print(f"[ble]   Read char:  {self._read_char_path or 'NOT FOUND'}")
            self.disconnect()
            return

        print(f"[ble] Found characteristics:")
        print(f"[ble]   Write: {self._write_char_path}")
        print(f"[ble]   Read:  {self._read_char_path}")

        # Start notifications on the read characteristic
        self._start_notify()

    def _start_notify(self):
        """Enable notifications on the read characteristic."""
        # Listen for Value changes on the read char
        self._notify_signal = self._bus.add_signal_receiver(
            self._on_char_properties_changed,
            dbus_interface=DBUS_PROPS_IFACE,
            signal_name="PropertiesChanged",
            path=self._read_char_path,
        )

        try:
            char = dbus.Interface(
                self._bus.get_object(BLUEZ_SERVICE, self._read_char_path),
                GATT_CHAR_IFACE,
            )
            char.StartNotify()
            print("[ble] Notifications enabled.")
        except dbus.exceptions.DBusException as e:
            print(f"[ble] StartNotify failed: {e.get_dbus_message()}")

        self._state = STATE_READY
        self._seq = 0
        print("[ble] Ready.")

    def _on_char_properties_changed(self, interface, changed, invalidated):
        """Handle notifications from the read characteristic."""
        if interface != GATT_CHAR_IFACE:
            return
        if "Value" not in changed:
            return

        value = bytes(changed["Value"])
        print(f"[ble] << {value.hex()}")

        parsed = zybl_parse(value)
        if parsed:
            seq, cid, payload = parsed
            info = parse_response(cid, payload)
            print(f"[ble]    {info}")
        else:
            print(f"[ble]    (not a valid ZYBL frame)")

    # --- Disconnection ---

    def disconnect(self):
        """Disconnect from the current device."""
        if self._state == STATE_DISCONNECTED:
            print("[ble] Not connected.")
            return

        if self._device_path:
            try:
                device = dbus.Interface(
                    self._bus.get_object(BLUEZ_SERVICE, self._device_path),
                    DEVICE_IFACE,
                )
                device.Disconnect()
            except dbus.exceptions.DBusException:
                pass

        self._cleanup()
        print("[ble] Disconnected.")

    def _cleanup(self):
        """Reset connection state and remove signal handlers."""
        if self._props_signal:
            self._props_signal.remove()
            self._props_signal = None
        if self._notify_signal:
            self._notify_signal.remove()
            self._notify_signal = None
        self._device_path = None
        self._write_char_path = None
        self._read_char_path = None
        self._device_id = None
        self._state = STATE_DISCONNECTED

    # --- Writing ---

    def write_raw(self, data):
        """Write raw bytes to the write characteristic (no ZYBL framing)."""
        if self._state != STATE_READY:
            print(f"[ble] Not ready (state={self._state}).")
            return

        print(f"[ble] >> {data.hex()}")
        try:
            char = dbus.Interface(
                self._bus.get_object(BLUEZ_SERVICE, self._write_char_path),
                GATT_CHAR_IFACE,
            )
            char.WriteValue(
                dbus.Array(data, signature="y"),
                dbus.Dictionary({"type": dbus.String("request")}, signature="sv"),
            )
        except dbus.exceptions.DBusException as e:
            print(f"[ble] Write failed: {e.get_dbus_message()}")

    def send_command(self, cid, payload=b""):
        """Build a ZYBL frame and write it."""
        self._seq += 1
        frame = zybl_frame(cid, payload, self._seq)
        self.write_raw(frame)

    # --- Status ---

    def status(self):
        """Print current connection state."""
        print(f"[ble] State: {self._state}")
        if self._device_path:
            print(f"[ble] Device: {self._device_path}")
        if self._write_char_path:
            print(f"[ble] Write char: {self._write_char_path}")
        if self._read_char_path:
            print(f"[ble] Read char:  {self._read_char_path}")
        if self._discovered:
            print(f"[ble] Discovered: {len(self._discovered)} device(s)")

    # --- High-level light control ---

    def _get_device_id(self):
        """Get device_id for control commands.

        Uses the value from CID 0x2005 query if available, otherwise 0.
        Note: mfid from BLE advertisement (e.g. 0x0905) is the MODEL id,
        not the device_id used in control payloads.
        """
        if self._device_id is not None:
            return self._device_id
        return 0

    def set_brightness(self, value):
        """Set brightness. value: 0-100 (percent)."""
        value = max(0.0, min(100.0, float(value)))
        payload = _control_payload(self._get_device_id(), struct.pack("<f", value))
        self.send_command(CID_BRIGHTNESS, payload)
        print(f"[ble] Setting brightness to {value:.0f}%")

    def get_brightness(self):
        """Query current brightness."""
        payload = _query_payload(self._get_device_id(), 4)
        self.send_command(CID_BRIGHTNESS, payload)

    def set_cct(self, kelvin):
        """Set color temperature in Kelvin (2700-6500)."""
        kelvin = max(2700, min(6500, int(kelvin)))
        payload = _control_payload(self._get_device_id(), struct.pack("<H", kelvin))
        self.send_command(CID_CCT, payload)
        print(f"[ble] Setting CCT to {kelvin}K")

    def get_cct(self):
        """Query current color temperature."""
        payload = _query_payload(self._get_device_id(), 2)
        self.send_command(CID_CCT, payload)

    def query_info(self):
        """Query device info (serial, model)."""
        self.send_command(CID_DEVICE_INFO)

    def query_device_id(self):
        """Query device ID via CID 0x2005 (no-payload command)."""
        self.send_command(CID_DEVICE_ID)
