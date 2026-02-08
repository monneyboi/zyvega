"""Bluetooth Mesh controller for Zhiyun Vega PL103 lights.

Uses the BlueZ bluetooth-mesh daemon (bluetooth-meshd) via D-Bus.
No external dependencies — only stdlib + system GLib/dbus bindings.
"""

import json
import os
import struct
import sys
import uuid as uuid_mod
from pathlib import Path

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop

# BlueZ mesh D-Bus constants
MESH_SERVICE = "org.bluez.mesh"
MESH_PATH = "/org/bluez/mesh"
MESH_NETWORK_IFACE = "org.bluez.mesh.Network1"
MESH_NODE_IFACE = "org.bluez.mesh.Node1"
MESH_MANAGEMENT_IFACE = "org.bluez.mesh.Management1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROPS_IFACE = "org.freedesktop.DBus.Properties"

# Our D-Bus interfaces
APP_IFACE = "org.bluez.mesh.Application1"
PROV_IFACE = "org.bluez.mesh.Provisioner1"
AGENT_IFACE = "org.bluez.mesh.ProvisionAgent1"
ELEMENT_IFACE = "org.bluez.mesh.Element1"

# Our D-Bus object paths
APP_PATH = "/com/zyvega"
AGENT_PATH = "/com/zyvega/agent"
ELEMENT_PATH = "/com/zyvega/ele00"

# Mesh config opcodes (Config Model, Bluetooth SIG)
OP_COMPOSITION_DATA_GET = 0x8008
OP_COMPOSITION_DATA_STATUS = 0x0002
OP_APPKEY_ADD = 0x0000
OP_APPKEY_STATUS = 0x8003
OP_MODEL_APP_BIND = 0x803D
OP_MODEL_APP_STATUS = 0x803E
OP_NODE_RESET = 0x8049
OP_NODE_RESET_STATUS = 0x804A

# Config paths
CONFIG_DIR = Path.home() / ".config" / "zyvega"
TOKEN_PATH = CONFIG_DIR / "token"
NODES_PATH = CONFIG_DIR / "nodes.json"


def _opcode_bytes(opcode):
    """Encode a mesh opcode to bytes (1 or 2 byte SIG opcodes)."""
    if opcode < 0x80:
        return bytes([opcode])
    elif opcode < 0x4000:
        # 2-byte opcode: big-endian
        return struct.pack(">H", opcode)
    else:
        # 3-byte opcode (vendor): handled separately
        return struct.pack(">H", opcode)  # placeholder


def _parse_opcode(data):
    """Parse opcode from access layer payload. Returns (opcode, param_bytes)."""
    if len(data) == 0:
        return None, b""
    first = data[0]
    if first >> 7 == 0:
        # 1-byte opcode
        return first, data[1:]
    elif first >> 6 == 0b10:
        # 2-byte opcode
        if len(data) < 2:
            return None, b""
        opcode = (data[0] << 8) | data[1]
        return opcode, data[2:]
    else:
        # 3-byte vendor opcode
        if len(data) < 3:
            return None, b""
        opcode = (data[0] << 16) | (data[1] << 8) | data[2]
        return opcode, data[3:]


class MeshApplication(dbus.service.Object):
    """Exports Application1, Provisioner1, and ObjectManager interfaces."""

    def __init__(self, bus, controller):
        self._controller = controller
        super().__init__(bus, APP_PATH)

    def get_properties(self):
        return {
            APP_IFACE: {
                "CompanyID": dbus.UInt16(0x05F1),  # Nordic Semiconductor placeholder
                "ProductID": dbus.UInt16(0x0001),
                "VersionID": dbus.UInt16(0x0001),
                "CRPL": dbus.UInt16(32768),
            },
            PROV_IFACE: {},
        }

    def get_element_properties(self):
        return {
            ELEMENT_IFACE: {
                "Index": dbus.Byte(0),
                "Models": dbus.Array([
                    dbus.Struct((dbus.UInt16(0x0001), dbus.Dictionary({}, signature="sv")), signature="qa{sv}"),  # Config Client
                ], signature="(qa{sv})"),
                "VendorModels": dbus.Array([], signature="(qqa{sv})"),
                "Location": dbus.UInt16(0x0000),
            }
        }

    # --- ObjectManager ---

    @dbus.service.method(DBUS_OM_IFACE, in_signature="", out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        objects = {
            APP_PATH: self.get_properties(),
            ELEMENT_PATH: self.get_element_properties(),
            AGENT_PATH: {
                AGENT_IFACE: {
                    "Capabilities": dbus.Array([], signature="s"),
                }
            },
        }
        return objects

    # --- Application1 ---

    @dbus.service.method(APP_IFACE, in_signature="t", out_signature="")
    def JoinComplete(self, token):
        print(f"[app] JoinComplete: token={token:#018x}")
        self._controller._on_join_complete(token)

    @dbus.service.method(APP_IFACE, in_signature="s", out_signature="")
    def JoinFailed(self, reason):
        print(f"[app] JoinFailed: {reason}")

    # --- Provisioner1 ---

    @dbus.service.method(PROV_IFACE, in_signature="naya{sv}", out_signature="")
    def ScanResult(self, rssi, data, options):
        self._controller._on_scan_result(rssi, bytes(data), options)

    @dbus.service.method(PROV_IFACE, in_signature="y", out_signature="qq")
    def RequestProvData(self, count):
        return self._controller._on_request_prov_data(count)

    @dbus.service.method(PROV_IFACE, in_signature="ayqy", out_signature="")
    def AddNodeComplete(self, uuid, unicast, count):
        self._controller._on_add_node_complete(bytes(uuid), unicast, count)

    @dbus.service.method(PROV_IFACE, in_signature="ays", out_signature="")
    def AddNodeFailed(self, uuid, reason):
        self._controller._on_add_node_failed(bytes(uuid), reason)


class MeshElement(dbus.service.Object):
    """Exports Element1 interface on /com/zyvega/ele00."""

    def __init__(self, bus, controller):
        self._controller = controller
        super().__init__(bus, ELEMENT_PATH)

    @dbus.service.method(ELEMENT_IFACE, in_signature="qqvay", out_signature="")
    def MessageReceived(self, source, key_index, destination, data):
        self._controller._on_message_received(source, key_index, destination, bytes(data))

    @dbus.service.method(ELEMENT_IFACE, in_signature="qbqay", out_signature="")
    def DevKeyMessageReceived(self, source, remote, net_index, data):
        self._controller._on_dev_key_message_received(source, remote, net_index, bytes(data))

    @dbus.service.method(ELEMENT_IFACE, in_signature="", out_signature="")
    def UpdateModelConfiguration(self):
        pass


class MeshProvisionAgent(dbus.service.Object):
    """Exports ProvisionAgent1 — NoOOB, so capabilities are empty.

    The daemon reads Capabilities via Properties.GetAll on this object
    during provisioning, so we must implement the Properties interface.
    """

    PROPERTIES = {
        AGENT_IFACE: {
            "Capabilities": dbus.Array([], signature="s"),
        }
    }

    def __init__(self, bus):
        super().__init__(bus, AGENT_PATH)

    @dbus.service.method(DBUS_PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        return self.PROPERTIES.get(interface, {})[prop]

    @dbus.service.method(DBUS_PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.PROPERTIES.get(interface, {})

    @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
    def Cancel(self):
        print("[agent] Provisioning cancelled")


class MeshController:
    """Manages the mesh network lifecycle: create/attach, scan, provision, configure."""

    def __init__(self, bus):
        self._bus = bus
        self._mesh_net = dbus.Interface(
            bus.get_object(MESH_SERVICE, MESH_PATH),
            MESH_NETWORK_IFACE,
        )
        self._token = None
        self._node_path = None
        self._node_iface = None
        self._mgmt_iface = None

        # Scan results: list of (rssi, uuid_bytes, options)
        self._scan_results = []
        # Provisioned nodes: {uuid_hex: {unicast, company_id, vendor_model_id, configured}}
        self._nodes = {}
        # Next unicast address to assign
        self._next_unicast = 0x0002

        # Pending provisioning state
        self._prov_uuid = None

        # Register our D-Bus objects
        self._app = MeshApplication(bus, self)
        self._element = MeshElement(bus, self)
        self._agent = MeshProvisionAgent(bus)

        # Load persisted state
        self._load_state()

    def _load_state(self):
        """Load token and node database from disk."""
        if TOKEN_PATH.exists():
            try:
                text = TOKEN_PATH.read_text().strip()
                self._token = int(text, 16)
            except (ValueError, OSError):
                self._token = None

        if NODES_PATH.exists():
            try:
                self._nodes = json.loads(NODES_PATH.read_text())
                # Compute next unicast from existing nodes
                for info in self._nodes.values():
                    uc = info.get("unicast", 0)
                    if uc >= self._next_unicast:
                        self._next_unicast = uc + 1
            except (json.JSONDecodeError, OSError):
                self._nodes = {}

    def _save_token(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(f"{self._token:016x}\n")

    def _save_nodes(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        NODES_PATH.write_text(json.dumps(self._nodes, indent=2) + "\n")

    def _setup_node_interfaces(self):
        """Set up Node1 and Management1 interfaces after attach/create."""
        if not self._node_path:
            return
        node_obj = self._bus.get_object(MESH_SERVICE, self._node_path)
        self._node_iface = dbus.Interface(node_obj, MESH_NODE_IFACE)
        self._mgmt_iface = dbus.Interface(node_obj, MESH_MANAGEMENT_IFACE)

    # --- Network lifecycle ---

    def initialize(self):
        """Attach to existing network or create a new one."""
        if self._token is not None:
            print(f"[mesh] Attaching to existing network (token={self._token:#018x})...")
            self._attach()
        else:
            print("[mesh] Creating new mesh network...")
            self._create_network()

    def _attach(self):
        """Attach to existing network using stored token (async)."""
        self._mesh_net.Attach(
            dbus.ObjectPath(APP_PATH),
            dbus.UInt64(self._token),
            reply_handler=self._on_attach_reply,
            error_handler=self._on_attach_error,
        )

    def _on_attach_reply(self, node_path, config):
        """Attach succeeded — we have our node path."""
        self._node_path = node_path
        print(f"[mesh] Attached to node: {self._node_path}")
        self._setup_node_interfaces()

    def _on_attach_error(self, error):
        """Attach failed — fall back to creating a new network."""
        print(f"[mesh] Attach failed: {error.get_dbus_message()}")
        print("[mesh] Will create a new network.")
        self._token = None
        self._create_network()

    def _create_network(self):
        """Create a new mesh network (async). Token arrives via JoinComplete."""
        app_uuid = uuid_mod.uuid4().bytes
        self._mesh_net.CreateNetwork(
            dbus.ObjectPath(APP_PATH),
            dbus.Array(app_uuid, signature="y"),
            reply_handler=lambda: print("[mesh] CreateNetwork accepted, waiting for JoinComplete..."),
            error_handler=lambda e: print(f"[mesh] CreateNetwork failed: {e.get_dbus_message()}"),
        )

    def _on_join_complete(self, token):
        """Called by Application1.JoinComplete — network created."""
        self._token = token
        self._save_token()
        print(f"[mesh] Network created! Token: {self._token:#018x}")
        # Now attach to get the node path
        self._attach()

    # --- Scanning ---

    def start_scan(self, seconds=10):
        """Start scanning for unprovisioned devices."""
        if not self._mgmt_iface:
            print("[mesh] Not attached to network yet.")
            return
        self._scan_results.clear()
        options = {"Seconds": dbus.UInt16(seconds)}
        try:
            self._mgmt_iface.UnprovisionedScan(dbus.Dictionary(options, signature="sv"))
            print(f"[mesh] Scanning for {seconds} seconds...")
        except dbus.exceptions.DBusException as e:
            print(f"[mesh] Scan failed: {e.get_dbus_message()}")

    def _on_scan_result(self, rssi, data, options):
        """Called by Provisioner1.ScanResult — unprovisioned device found."""
        # data is the UUID (16 bytes)
        uuid_hex = data.hex()
        # Check if we already have this device
        for i, (_, existing_data, _) in enumerate(self._scan_results):
            if existing_data == data:
                # Update RSSI
                self._scan_results[i] = (rssi, data, options)
                return

        idx = len(self._scan_results)
        self._scan_results.append((rssi, data, options))
        oob_info = int.from_bytes(data[16:18], "big") if len(data) > 16 else 0
        print(f"  [{idx}] UUID={uuid_hex[:32]}  RSSI={rssi}  OOB={oob_info:#06x}")

    # --- Provisioning ---

    def provision_device(self, index):
        """Provision a scanned device by its scan index."""
        if not self._mgmt_iface:
            print("[mesh] Not attached to network yet.")
            return
        if index < 0 or index >= len(self._scan_results):
            print(f"[mesh] Invalid scan index {index}. Run 'scan' first.")
            return

        _, data, _ = self._scan_results[index]
        uuid_bytes = data[:16]
        self._prov_uuid = uuid_bytes
        uuid_hex = uuid_bytes.hex()
        print(f"[mesh] Provisioning device UUID={uuid_hex}...")

        options = dbus.Dictionary({}, signature="sv")
        self._mgmt_iface.AddNode(
            dbus.Array(uuid_bytes, signature="y"),
            options,
            reply_handler=lambda: print("[mesh] AddNode accepted, provisioning in progress..."),
            error_handler=self._on_add_node_call_error,
        )

    def _on_add_node_call_error(self, error):
        print(f"[mesh] AddNode failed: {error.get_dbus_message()}")
        self._prov_uuid = None

    def _on_request_prov_data(self, count):
        """Called by Provisioner1.RequestProvData — assign unicast address."""
        unicast = self._next_unicast
        print(f"[mesh] RequestProvData: assigning unicast={unicast:#06x} (elements={count})")
        return dbus.UInt16(0), dbus.UInt16(unicast)  # net_index=0

    def _on_add_node_complete(self, uuid_bytes, unicast, count):
        """Called by Provisioner1.AddNodeComplete — provisioning succeeded."""
        uuid_hex = uuid_bytes.hex()
        print(f"[mesh] Provisioned! UUID={uuid_hex} unicast={unicast:#06x} elements={count}")

        self._nodes[uuid_hex] = {
            "unicast": unicast,
            "count": count,
            "company_id": None,
            "vendor_model_id": None,
            "configured": False,
        }
        self._next_unicast = unicast + count
        self._save_nodes()
        self._prov_uuid = None

    def _on_add_node_failed(self, uuid_bytes, reason):
        """Called by Provisioner1.AddNodeFailed — provisioning failed."""
        uuid_hex = uuid_bytes.hex()
        print(f"[mesh] Provisioning failed: UUID={uuid_hex} reason={reason}")
        self._prov_uuid = None

    # --- Configuration ---

    def configure_device(self, unicast):
        """Start configuration: get composition data from provisioned node."""
        if not self._node_iface:
            print("[mesh] Not attached to network yet.")
            return

        # Find the node
        node_info = None
        for info in self._nodes.values():
            if info["unicast"] == unicast:
                node_info = info
                break
        if node_info is None:
            print(f"[mesh] No provisioned node at unicast {unicast:#06x}")
            return

        print(f"[mesh] Getting Composition Data from {unicast:#06x}...")
        # Config Composition Data Get: opcode 0x8008, param = page 0
        data = _opcode_bytes(OP_COMPOSITION_DATA_GET) + bytes([0x00])
        try:
            self._node_iface.DevKeySend(
                dbus.ObjectPath(ELEMENT_PATH),
                dbus.UInt16(unicast),
                dbus.Boolean(True),  # remote
                dbus.UInt16(0),      # net_index
                dbus.Dictionary({}, signature="sv"),
                dbus.Array(data, signature="y"),
            )
        except dbus.exceptions.DBusException as e:
            print(f"[mesh] DevKeySend failed: {e.get_dbus_message()}")

    def _add_app_key(self, unicast):
        """Create app key locally and distribute to node."""
        print(f"[mesh] Creating and adding AppKey to {unicast:#06x}...")
        try:
            self._mgmt_iface.CreateAppKey(
                dbus.UInt16(0),  # net_index
                dbus.UInt16(0),  # app_index
            )
        except dbus.exceptions.DBusException as e:
            msg = e.get_dbus_message()
            if "Already Exists" in msg or "AlreadyExists" in msg:
                pass  # Key already exists, that's fine
            else:
                print(f"[mesh] CreateAppKey failed: {msg}")
                return

        # AddAppKey distributes the key to the remote node
        try:
            self._node_iface.AddAppKey(
                dbus.ObjectPath(ELEMENT_PATH),
                dbus.UInt16(unicast),
                dbus.UInt16(0),  # app_index
                dbus.UInt16(0),  # net_index
                dbus.Boolean(False),  # update
            )
            print("[mesh] AddAppKey sent, waiting for status...")
        except dbus.exceptions.DBusException as e:
            print(f"[mesh] AddAppKey failed: {e.get_dbus_message()}")

    def _bind_model(self, unicast, company_id, model_id):
        """Bind app key to vendor model on the node."""
        print(f"[mesh] Binding AppKey to vendor model {company_id:#06x}:{model_id:#06x} on {unicast:#06x}...")
        # ModelAppBind: opcode 0x803D
        # Params: element_addr (2 LE) + app_key_index (2 LE) + model_id (vendor: company_id 2 LE + model_id 2 LE)
        payload = struct.pack("<HH", unicast, 0)  # element addr, app key index
        payload += struct.pack("<HH", company_id, model_id)  # vendor model
        data = _opcode_bytes(OP_MODEL_APP_BIND) + payload
        try:
            self._node_iface.DevKeySend(
                dbus.ObjectPath(ELEMENT_PATH),
                dbus.UInt16(unicast),
                dbus.Boolean(True),
                dbus.UInt16(0),
                dbus.Dictionary({}, signature="sv"),
                dbus.Array(data, signature="y"),
            )
            print("[mesh] ModelAppBind sent, waiting for status...")
        except dbus.exceptions.DBusException as e:
            print(f"[mesh] ModelAppBind failed: {e.get_dbus_message()}")

    def _on_dev_key_message_received(self, source, remote, net_index, data):
        """Handle config messages (composition data status, appkey status, etc.)."""
        opcode, params = _parse_opcode(data)
        if opcode is None:
            print(f"[config] Empty message from {source:#06x}")
            return

        if opcode == OP_COMPOSITION_DATA_STATUS:
            self._handle_composition_data(source, params)
        elif opcode == OP_APPKEY_STATUS:
            status = params[0] if len(params) > 0 else 0xFF
            if status == 0:
                print(f"[config] AppKey added successfully to {source:#06x}")
                # Now bind the vendor model
                for info in self._nodes.values():
                    if info["unicast"] == source and info.get("company_id") is not None:
                        self._bind_model(source, info["company_id"], info["vendor_model_id"])
                        break
            else:
                print(f"[config] AppKey status from {source:#06x}: error={status:#04x}")
        elif opcode == OP_MODEL_APP_STATUS:
            status = params[0] if len(params) > 0 else 0xFF
            if status == 0:
                print(f"[config] Model bound successfully on {source:#06x}")
                for info in self._nodes.values():
                    if info["unicast"] == source:
                        info["configured"] = True
                        self._save_nodes()
                        break
                print("[config] Device fully configured! Ready for control commands.")
            else:
                print(f"[config] ModelAppBind status from {source:#06x}: error={status:#04x}")
        elif opcode == OP_NODE_RESET_STATUS:
            print(f"[config] Node {source:#06x} reset confirmed")
        else:
            print(f"[config] DevKey message from {source:#06x}: opcode={opcode:#06x} params={params.hex()}")

    def _handle_composition_data(self, source, params):
        """Parse Composition Data Status response."""
        if len(params) < 11:
            print(f"[config] Composition data too short ({len(params)} bytes)")
            return

        page = params[0]
        # Composition Data page 0 format:
        # CID (2) + PID (2) + VID (2) + CRPL (2) + Features (2) + Elements...
        cid = struct.unpack_from("<H", params, 1)[0]
        pid = struct.unpack_from("<H", params, 3)[0]
        vid = struct.unpack_from("<H", params, 5)[0]
        crpl = struct.unpack_from("<H", params, 7)[0]
        features = struct.unpack_from("<H", params, 9)[0]

        print(f"\n{'='*60}")
        print(f"  Composition Data (page {page}) from {source:#06x}")
        print(f"{'='*60}")
        print(f"  Company ID (CID):  {cid:#06x}")
        print(f"  Product ID (PID):  {pid:#06x}")
        print(f"  Version ID (VID):  {vid:#06x}")
        print(f"  CRPL:              {crpl}")
        print(f"  Features:          {features:#06x}")
        print(f"    Relay:    {'yes' if features & 0x01 else 'no'}")
        print(f"    Proxy:    {'yes' if features & 0x02 else 'no'}")
        print(f"    Friend:   {'yes' if features & 0x04 else 'no'}")
        print(f"    Low Power:{'yes' if features & 0x08 else 'no'}")

        # Parse elements
        offset = 11
        elem_idx = 0
        vendor_models = []
        while offset < len(params):
            if offset + 4 > len(params):
                break
            loc = struct.unpack_from("<H", params, offset)[0]
            num_s = params[offset + 2]  # number of SIG models
            num_v = params[offset + 3]  # number of vendor models
            offset += 4

            print(f"\n  Element {elem_idx} (location={loc:#06x}):")

            # SIG models (2 bytes each)
            sig_models = []
            for _ in range(num_s):
                if offset + 2 > len(params):
                    break
                model_id = struct.unpack_from("<H", params, offset)[0]
                sig_models.append(model_id)
                offset += 2
            if sig_models:
                print(f"    SIG models: {', '.join(f'{m:#06x}' for m in sig_models)}")

            # Vendor models (4 bytes each: company_id + model_id)
            for _ in range(num_v):
                if offset + 4 > len(params):
                    break
                v_cid = struct.unpack_from("<H", params, offset)[0]
                v_mid = struct.unpack_from("<H", params, offset + 2)[0]
                vendor_models.append((v_cid, v_mid))
                offset += 4
            if vendor_models:
                for v_cid, v_mid in vendor_models:
                    print(f"    Vendor model: company={v_cid:#06x} model={v_mid:#06x}")

            elem_idx += 1

        print(f"{'='*60}\n")

        # Store discovered vendor model info
        if vendor_models:
            v_cid, v_mid = vendor_models[0]  # use first vendor model
            for info in self._nodes.values():
                if info["unicast"] == source:
                    info["company_id"] = v_cid
                    info["vendor_model_id"] = v_mid
                    self._save_nodes()
                    break
            print(f"[config] Discovered vendor model: company={v_cid:#06x} model={v_mid:#06x}")
            # Proceed with app key distribution
            self._add_app_key(source)
        else:
            print("[config] No vendor models found in composition data!")

    def _on_message_received(self, source, key_index, destination, data):
        """Handle app-level messages (vendor model responses)."""
        if len(data) < 3:
            print(f"[msg] Short message from {source:#06x}: {data.hex()}")
            return

        # Vendor messages: first byte has 0xC0 set
        if data[0] & 0xC0 == 0xC0:
            opcode_6bits = data[0] & 0x3F
            company_id = data[1] | (data[2] << 8)  # little-endian
            payload = data[3:]
            # Reconstruct the command ID from the 6-bit opcode
            # The app uses 0x1001, 0x1002, etc. — the low byte is the 6-bit opcode portion
            print(f"[msg] Vendor message from {source:#06x}: "
                  f"opcode_bits={opcode_6bits:#04x} company={company_id:#06x} "
                  f"payload={payload.hex()}")
            self._handle_vendor_response(source, opcode_6bits, company_id, payload)
        else:
            opcode, params = _parse_opcode(data)
            print(f"[msg] SIG message from {source:#06x}: opcode={opcode:#06x} params={params.hex()}")

    def _handle_vendor_response(self, source, opcode_bits, company_id, payload):
        """Interpret vendor model responses."""
        # We'll flesh this out once we know the actual format
        # For now, just dump the raw data
        print(f"[vendor] Response from {source:#06x}: op={opcode_bits:#04x} data={payload.hex()}")

    # --- Utility ---

    def list_nodes(self):
        """Print provisioned nodes."""
        if not self._nodes:
            print("No provisioned nodes.")
            return
        print(f"\n{'UUID':<34} {'Unicast':>8} {'CID':>8} {'Model':>8} {'Status'}")
        print("-" * 75)
        for uuid_hex, info in self._nodes.items():
            cid = f"{info['company_id']:#06x}" if info.get("company_id") is not None else "?"
            mid = f"{info['vendor_model_id']:#06x}" if info.get("vendor_model_id") is not None else "?"
            status = "configured" if info.get("configured") else "provisioned"
            print(f"  {uuid_hex[:32]}  {info['unicast']:#06x}  {cid:>8}  {mid:>8}  {status}")
        print()

    def reset_network(self):
        """Delete the local network and start fresh."""
        if self._token is not None:
            try:
                self._mesh_net.Leave(dbus.UInt64(self._token))
                print("[mesh] Left network.")
            except dbus.exceptions.DBusException as e:
                print(f"[mesh] Leave failed: {e.get_dbus_message()}")

        self._token = None
        self._node_path = None
        self._node_iface = None
        self._mgmt_iface = None
        self._nodes.clear()
        self._scan_results.clear()
        self._next_unicast = 0x0002

        if TOKEN_PATH.exists():
            TOKEN_PATH.unlink()
        if NODES_PATH.exists():
            NODES_PATH.unlink()

        print("[mesh] Network reset. Run the program again to create a new network.")
