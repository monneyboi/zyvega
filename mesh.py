"""Bluetooth Mesh interface for PL103 Video Light using D-Bus directly"""

import asyncio
import logging
import struct
import json
import secrets
from pathlib import Path
from typing import Optional, List

from dbus_next.aio import MessageBus
from dbus_next import BusType, Variant

logger = logging.getLogger(__name__)


class ZyvegaMesh:
    """Unified mesh interface - provisioning and control"""

    # Constants
    OOB_KEY = bytes.fromhex("CABF7E4AC8B9E254372BBD6146D318BB")
    ZHIYUN_VENDOR_ID = 0x0905
    ZHIYUN_DEVICE_ID = 0x0380

    # Vendor opcodes
    VENDOR_OPCODE_COLOR_TEMP = 0x1002
    VENDOR_OPCODE_RGB = 0x1003

    # Configuration
    CONFIG_DIR = Path.home() / ".config" / "zyvega" / "mesh"
    NETWORK_NAME = "zyvega_mesh"

    # D-Bus paths
    MESH_SERVICE = "org.bluez.mesh"
    MESH_PATH = "/org/bluez/mesh"

    def __init__(self):
        self.config_file = self.CONFIG_DIR / f"{self.NETWORK_NAME}.json"
        self._bus: Optional[MessageBus] = None
        self._token: Optional[int] = None
        self._node_path: Optional[str] = None
        self._unprovisioned_devices: List[dict] = []

    def _ensure_config_dir(self):
        """Ensure configuration directory exists"""
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> dict:
        """Load mesh network configuration"""
        if not self.config_file.exists():
            return {"network_name": self.NETWORK_NAME, "nodes": {}}
        with open(self.config_file, "r") as f:
            return json.load(f)

    def _save_config(self, config: dict):
        """Save mesh network configuration"""
        self._ensure_config_dir()
        with open(self.config_file, "w") as f:
            json.dump(config, f, indent=2)

    async def _get_bus(self) -> MessageBus:
        """Get D-Bus system connection"""
        if self._bus is None:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        return self._bus

    async def _get_interface(self, path: str, interface: str):
        """Get a D-Bus interface proxy"""
        bus = await self._get_bus()
        introspection = await bus.introspect(self.MESH_SERVICE, path)
        proxy = bus.get_proxy_object(self.MESH_SERVICE, path, introspection)
        return proxy.get_interface(interface)

    # --- Network Management ---

    async def create_network(self) -> bool:
        """Create a new mesh network"""
        try:
            network_iface = await self._get_interface(
                self.MESH_PATH, "org.bluez.mesh.Network1"
            )

            app_uuid = secrets.token_bytes(16)

            # CreateNetwork returns token
            token = await network_iface.call_create_network(
                app_uuid,
                {}  # options
            )

            self._token = token
            self._node_path = f"/org/bluez/mesh/node{token:016x}"

            config = self._load_config()
            config["token"] = token
            config["app_uuid"] = app_uuid.hex()
            config["created"] = True
            self._save_config(config)

            logger.info(f"Created mesh network with token: {token}")
            return True

        except Exception as e:
            logger.error(f"Failed to create network: {e}")
            return False

    async def _attach_network(self) -> bool:
        """Attach to existing mesh network"""
        config = self._load_config()
        token = config.get("token")
        app_uuid_hex = config.get("app_uuid")

        if token is None or app_uuid_hex is None:
            logger.debug("No network token/uuid found")
            return False

        try:
            network_iface = await self._get_interface(
                self.MESH_PATH, "org.bluez.mesh.Network1"
            )

            app_uuid = bytes.fromhex(app_uuid_hex)
            node_path = await network_iface.call_attach(app_uuid, token)

            self._token = token
            self._node_path = node_path
            logger.info(f"Attached to network, node path: {node_path}")
            return True

        except Exception as e:
            logger.debug(f"Failed to attach (may need to create): {e}")
            return False

    async def _ensure_network(self) -> bool:
        """Ensure we're connected to the mesh network"""
        if await self._attach_network():
            return True
        return await self.create_network()

    async def list_nodes(self) -> List[dict]:
        """List all provisioned mesh nodes"""
        config = self._load_config()
        return list(config.get("nodes", {}).values())

    async def remove_node(self, unicast: int) -> bool:
        """Remove a node from the mesh network"""
        try:
            if not await self._ensure_network():
                return False

            # Send Config Node Reset
            await self._send_dev_key_message(
                unicast,
                bytes([0x80, 0x49])  # CONFIG_NODE_RESET opcode
            )

            # Update config
            config = self._load_config()
            nodes = config.get("nodes", {})
            for key, node in list(nodes.items()):
                if node.get("unicast_address") == unicast:
                    del nodes[key]
                    break

            self._save_config(config)
            logger.info(f"Removed node 0x{unicast:04x}")
            return True

        except Exception as e:
            logger.error(f"Failed to remove node: {e}")
            return False

    # --- Provisioning ---

    async def scan_unprovisioned(self, timeout: float = 5.0) -> List[dict]:
        """Scan for unprovisioned mesh devices"""
        try:
            if not await self._ensure_network():
                logger.error("Failed to connect to mesh network")
                return []

            self._unprovisioned_devices = []

            mgmt_iface = await self._get_interface(
                self._node_path, "org.bluez.mesh.Management1"
            )

            # Set up signal handler for scan results
            bus = await self._get_bus()

            def handle_message(msg):
                if msg.member == "ScanResult" and msg.path == self._node_path:
                    rssi, data, options = msg.body
                    if len(data) >= 16:
                        uuid = data[:16].hex().upper()
                        oob = int.from_bytes(data[16:18], "big") if len(data) >= 18 else 0
                        device_info = {
                            "uuid": uuid,
                            "rssi": rssi,
                            "oob": oob,
                            "name": options.get("name", Variant("s", "Unknown")).value if isinstance(options.get("name"), Variant) else options.get("name", "Unknown"),
                        }
                        # Deduplicate
                        if not any(d["uuid"] == uuid for d in self._unprovisioned_devices):
                            self._unprovisioned_devices.append(device_info)
                            logger.info(f"Found unprovisioned device: {uuid}")

            bus.add_message_handler(handle_message)

            # Start scan
            await mgmt_iface.call_unprovisioned_scan(int(timeout))
            await asyncio.sleep(timeout + 0.5)

            bus.remove_message_handler(handle_message)
            return self._unprovisioned_devices

        except Exception as e:
            logger.error(f"Scan failed: {e}")
            return []

    async def provision(self, uuid: str) -> Optional[int]:
        """
        Provision a device by UUID.

        Args:
            uuid: Device UUID from scan

        Returns:
            Unicast address if successful, None otherwise
        """
        try:
            if not await self._ensure_network():
                return None

            mgmt_iface = await self._get_interface(
                self._node_path, "org.bluez.mesh.Management1"
            )

            config = self._load_config()

            # Determine next unicast address
            nodes = config.get("nodes", {})
            used_addrs = {n.get("unicast_address", 0) for n in nodes.values()}
            used_addrs.add(1)  # Reserve for provisioner
            unicast = 2
            while unicast in used_addrs:
                unicast += 1

            uuid_bytes = bytes.fromhex(uuid.replace("-", ""))

            # Add node with static OOB
            await mgmt_iface.call_add_node(
                uuid_bytes,
                {
                    "static-oob": Variant("ay", list(self.OOB_KEY)),
                }
            )

            # Wait for provisioning to complete
            await asyncio.sleep(5)

            # Store node info
            nodes[uuid] = {
                "uuid": uuid,
                "unicast_address": unicast,
                "provisioned": True,
            }
            config["nodes"] = nodes
            self._save_config(config)

            logger.info(f"Provisioned device {uuid} at 0x{unicast:04x}")
            return unicast

        except Exception as e:
            logger.error(f"Provisioning failed: {e}")
            return None

    async def configure(self, unicast: int) -> bool:
        """
        Configure a provisioned node (get composition, add app key).

        Args:
            unicast: Node unicast address

        Returns:
            True if successful
        """
        try:
            if not await self._ensure_network():
                return False

            # Get composition data (opcode 0x8008, page 0)
            await self._send_dev_key_message(
                unicast,
                bytes([0x80, 0x08, 0x00])
            )
            await asyncio.sleep(1)

            # Add app key (opcode 0x00, net_idx=0, app_idx=0, key=zeros)
            app_key_msg = bytes([0x00]) + struct.pack("<HH", 0, 0)[:3] + bytes(16)
            await self._send_dev_key_message(unicast, app_key_msg)
            await asyncio.sleep(0.5)

            logger.info(f"Configured node 0x{unicast:04x}")
            return True

        except Exception as e:
            logger.error(f"Configuration failed: {e}")
            return False

    async def _send_dev_key_message(self, dest: int, data: bytes):
        """Send a message using device key"""
        node_iface = await self._get_interface(
            self._node_path, "org.bluez.mesh.Node1"
        )

        await node_iface.call_dev_key_send(
            dest,   # destination
            True,   # remote
            0,      # net_index
            list(data)
        )

    # --- Control ---

    def _get_target_unicast(self, unicast: Optional[int] = None) -> int:
        """Get target unicast address"""
        if unicast is not None:
            return unicast

        config = self._load_config()
        nodes = list(config.get("nodes", {}).values())
        if not nodes:
            raise RuntimeError("No provisioned nodes. Run 'mesh setup' first.")
        return nodes[0]["unicast_address"]

    async def _send_mesh_message(self, dest: int, data: bytes, app_idx: int = 0):
        """Send a mesh message using app key"""
        if not await self._ensure_network():
            raise RuntimeError("Failed to connect to mesh network")

        node_iface = await self._get_interface(
            self._node_path, "org.bluez.mesh.Node1"
        )

        await node_iface.call_send(
            dest,       # destination
            app_idx,    # app_index
            list(data)  # data
        )

    async def set_power(self, unicast: Optional[int], on: bool) -> bool:
        """
        Turn light on or off.

        Args:
            unicast: Target node address (uses first node if None)
            on: True for on, False for off

        Returns:
            True if successful
        """
        try:
            dest = self._get_target_unicast(unicast)

            # Generic OnOff Set Unacknowledged (opcode 0x8203)
            data = struct.pack("<HBB", 0x8203, 1 if on else 0, 0)

            await self._send_mesh_message(dest, data)
            logger.info(f"Set power {'on' if on else 'off'} for 0x{dest:04x}")
            return True

        except Exception as e:
            logger.error(f"Failed to set power: {e}")
            return False

    async def set_brightness(self, unicast: Optional[int], percent: int) -> bool:
        """
        Set brightness level.

        Args:
            unicast: Target node address (uses first node if None)
            percent: Brightness percentage (0-100)

        Returns:
            True if successful
        """
        try:
            if not 0 <= percent <= 100:
                raise ValueError("Brightness must be 0-100")

            dest = self._get_target_unicast(unicast)

            # Convert percentage to Generic Level (-32768 to 32767)
            level = int((percent / 100.0) * 65535 - 32768)

            # Generic Level Set Unacknowledged (opcode 0x8206)
            data = struct.pack("<HhB", 0x8206, level, 0)

            await self._send_mesh_message(dest, data)
            logger.info(f"Set brightness to {percent}% for 0x{dest:04x}")
            return True

        except Exception as e:
            logger.error(f"Failed to set brightness: {e}")
            return False

    async def set_color_temp(self, unicast: Optional[int], kelvin: int) -> bool:
        """
        Set color temperature.

        Args:
            unicast: Target node address (uses first node if None)
            kelvin: Color temperature (2700-6500K)

        Returns:
            True if successful
        """
        try:
            if not 2700 <= kelvin <= 6500:
                raise ValueError("Color temperature must be 2700-6500K")

            dest = self._get_target_unicast(unicast)

            # Vendor message: 3-byte opcode + payload
            # Opcode format: 0xC0 | (opcode_low & 0x3F), then company ID (little-endian)
            opcode = bytes([
                0xC0 | (self.VENDOR_OPCODE_COLOR_TEMP & 0x3F),
                self.ZHIYUN_VENDOR_ID & 0xFF,
                (self.ZHIYUN_VENDOR_ID >> 8) & 0xFF
            ])

            params = struct.pack(">HH", self.ZHIYUN_DEVICE_ID, kelvin)
            data = opcode + params

            await self._send_mesh_message(dest, data)
            logger.info(f"Set color temp to {kelvin}K for 0x{dest:04x}")
            return True

        except Exception as e:
            logger.error(f"Failed to set color temperature: {e}")
            return False

    async def set_rgb(
        self, unicast: Optional[int], brightness: int, r: int, g: int, b: int
    ) -> bool:
        """
        Set RGB color.

        Args:
            unicast: Target node address (uses first node if None)
            brightness: Brightness percentage (0-100)
            r: Red value (0-255)
            g: Green value (0-255)
            b: Blue value (0-255)

        Returns:
            True if successful
        """
        try:
            if not 0 <= brightness <= 100:
                raise ValueError("Brightness must be 0-100")
            if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
                raise ValueError("RGB values must be 0-255")

            dest = self._get_target_unicast(unicast)

            # Convert brightness to device value (0-10000)
            brightness_value = int(brightness * 100)

            # Vendor message opcode
            opcode = bytes([
                0xC0 | (self.VENDOR_OPCODE_RGB & 0x3F),
                self.ZHIYUN_VENDOR_ID & 0xFF,
                (self.ZHIYUN_VENDOR_ID >> 8) & 0xFF
            ])

            params = struct.pack(">HH3B", self.ZHIYUN_DEVICE_ID, brightness_value, r, g, b)
            data = opcode + params

            await self._send_mesh_message(dest, data)
            logger.info(f"Set RGB to ({r},{g},{b}) at {brightness}% for 0x{dest:04x}")
            return True

        except Exception as e:
            logger.error(f"Failed to set RGB: {e}")
            return False

    # --- High-level setup ---

    async def setup_device(self, target_uuid: Optional[str] = None) -> bool:
        """
        Complete setup workflow: scan, provision, and configure.

        Args:
            target_uuid: Device UUID to provision (scans if None)

        Returns:
            True if successful
        """
        if target_uuid is None:
            devices = await self.scan_unprovisioned(timeout=5.0)

            if not devices:
                logger.error("No unprovisioned devices found")
                return False

            target_uuid = devices[0]["uuid"]
            logger.info(f"Found device: {target_uuid}")

        unicast = await self.provision(target_uuid)
        if unicast is None:
            logger.error("Provisioning failed")
            return False

        if not await self.configure(unicast):
            logger.error("Configuration failed")
            return False

        logger.info(f"Device setup complete: {target_uuid} at 0x{unicast:04x}")
        return True
