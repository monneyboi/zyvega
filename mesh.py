"""Bluetooth Mesh interface for PL103 Video Light using D-Bus"""

import asyncio
import logging
import struct
import json
import secrets
from pathlib import Path
from typing import Optional, List

from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, dbus_property
from dbus_next.constants import PropertyAccess
from dbus_next import BusType, Variant

logger = logging.getLogger(__name__)


class ObjectManager(ServiceInterface):
    """D-Bus ObjectManager interface required by BlueZ"""

    def __init__(self, objects: dict):
        super().__init__("org.freedesktop.DBus.ObjectManager")
        self._objects = objects

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":
        return self._objects


class MeshApplication(ServiceInterface):
    """D-Bus Application interface for BlueZ mesh"""

    def __init__(self):
        super().__init__("org.bluez.mesh.Application1")
        self._token = None

    @method()
    def JoinComplete(self, token: "t"):
        logger.info(f"JoinComplete: token={token}")
        self._token = token

    @method()
    def JoinFailed(self, reason: "s"):
        logger.error(f"JoinFailed: {reason}")

    @dbus_property(access=PropertyAccess.READ)
    def CompanyID(self) -> "q":
        return 0x05F1  # Linux Foundation

    @dbus_property(access=PropertyAccess.READ)
    def ProductID(self) -> "q":
        return 0x0001

    @dbus_property(access=PropertyAccess.READ)
    def VersionID(self) -> "q":
        return 0x0001


class MeshProvisionAgent(ServiceInterface):
    """D-Bus Provisioning Agent for BlueZ mesh - NoOOB mode"""

    def __init__(self):
        super().__init__("org.bluez.mesh.ProvisionAgent1")

    @method()
    def PrivateKey(self) -> "ay":
        return secrets.token_bytes(32)

    @method()
    def PublicKey(self) -> "ay":
        return secrets.token_bytes(64)

    @method()
    def DisplayString(self, value: "s"):
        logger.info(f"Display: {value}")

    @method()
    def DisplayNumeric(self, type: "s", number: "u"):
        logger.info(f"Display {type}: {number}")

    @method()
    def PromptNumeric(self, type: "s") -> "u":
        return 0

    @method()
    def PromptStatic(self, type: "s") -> "ay":
        # NoOOB: return 16 zero bytes if somehow called
        logger.info(f"PromptStatic: returning zeros (NoOOB)")
        return bytes(16)

    @method()
    def Cancel(self):
        logger.info("Provisioning cancelled")

    @dbus_property(access=PropertyAccess.READ)
    def Capabilities(self) -> "as":
        return []  # Empty = NoOOB only


class MeshElement(ServiceInterface):
    """D-Bus Element interface"""

    def __init__(self, index: int):
        super().__init__("org.bluez.mesh.Element1")
        self._index = index

    @method()
    def MessageReceived(self, source: "q", key_index: "q", destination: "v", data: "ay"):
        logger.debug(f"Message from 0x{source:04x}: {bytes(data).hex()}")

    @method()
    def DevKeyMessageReceived(self, source: "q", remote: "b", net_index: "q", data: "ay"):
        logger.debug(f"DevKey message from 0x{source:04x}: {bytes(data).hex()}")

    @method()
    def UpdateModelConfiguration(self, model_id: "q", config: "a{sv}"):
        pass

    @dbus_property(access=PropertyAccess.READ)
    def Index(self) -> "y":
        return self._index

    @dbus_property(access=PropertyAccess.READ)
    def Models(self) -> "aq":
        # Generic OnOff Client, Generic Level Client
        return [0x1001, 0x1003]


class ZyvegaMesh:
    """Unified mesh interface - provisioning and control"""

    # Constants
    ZHIYUN_VENDOR_ID = 0x0905
    ZHIYUN_DEVICE_ID = 0x0380
    VENDOR_OPCODE_COLOR_TEMP = 0x1002
    VENDOR_OPCODE_RGB = 0x1003

    CONFIG_DIR = Path.home() / ".config" / "zyvega" / "mesh"
    MESH_SERVICE = "org.bluez.mesh"
    MESH_PATH = "/org/bluez/mesh"
    APP_PATH = "/org/zyvega/mesh"

    def __init__(self):
        self.config_file = self.CONFIG_DIR / f"network.json"
        self._bus: Optional[MessageBus] = None
        self._token: Optional[int] = None
        self._node_path: Optional[str] = None
        self._app_registered = False

    def _ensure_config_dir(self):
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> dict:
        if not self.config_file.exists():
            return {"nodes": {}}
        with open(self.config_file, "r") as f:
            return json.load(f)

    def _save_config(self, config: dict):
        self._ensure_config_dir()
        with open(self.config_file, "w") as f:
            json.dump(config, f, indent=2)

    async def _get_bus(self) -> MessageBus:
        if self._bus is None:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        return self._bus

    async def _register_application(self):
        """Register our application objects on D-Bus"""
        if self._app_registered:
            return

        bus = await self._get_bus()

        ele_path = self.APP_PATH + "/ele00"
        agent_path = self.APP_PATH + "/agent"

        # Build managed objects for ObjectManager
        managed_objects = {
            self.APP_PATH: {
                "org.bluez.mesh.Application1": {
                    "CompanyID": Variant("q", 0x05F1),
                    "ProductID": Variant("q", 0x0001),
                    "VersionID": Variant("q", 0x0001),
                }
            },
            ele_path: {
                "org.bluez.mesh.Element1": {
                    "Index": Variant("y", 0),
                    "Models": Variant("aq", [0x1001, 0x1003]),
                }
            },
            agent_path: {
                "org.bluez.mesh.ProvisionAgent1": {
                    "Capabilities": Variant("as", []),  # NoOOB
                }
            },
        }

        # Export ObjectManager
        obj_mgr = ObjectManager(managed_objects)
        bus.export(self.APP_PATH, obj_mgr)

        # Export application
        self._app = MeshApplication()
        bus.export(self.APP_PATH, self._app)

        # Export provisioning agent
        agent = MeshProvisionAgent()
        bus.export(agent_path, agent)

        # Export element
        element = MeshElement(0)
        bus.export(ele_path, element)

        self._app_registered = True
        logger.debug("Registered mesh application on D-Bus")

    async def _get_interface(self, path: str, interface: str):
        bus = await self._get_bus()
        introspection = await bus.introspect(self.MESH_SERVICE, path)
        proxy = bus.get_proxy_object(self.MESH_SERVICE, path, introspection)
        return proxy.get_interface(interface)

    async def create_network(self) -> bool:
        """Create a new mesh network"""
        try:
            await self._register_application()

            network_iface = await self._get_interface(
                self.MESH_PATH, "org.bluez.mesh.Network1"
            )

            app_uuid = secrets.token_bytes(16)
            logger.debug(f"Creating network with app_path={self.APP_PATH}, uuid={app_uuid.hex()}")

            # CreateNetwork(object app, array{byte} uuid) -> returns void, result via JoinComplete
            await network_iface.call_create_network(
                self.APP_PATH,
                app_uuid
            )

            # Wait for JoinComplete callback
            await asyncio.sleep(2)

            if self._app._token is None:
                logger.error("JoinComplete not received")
                return False

            token = self._app._token

            self._token = token
            self._node_path = f"/org/bluez/mesh/node{token:016x}"

            config = self._load_config()
            config["token"] = token
            config["app_uuid"] = app_uuid.hex()
            self._save_config(config)

            logger.info(f"Created mesh network, token: {token}")
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
            return False

        try:
            await self._register_application()

            network_iface = await self._get_interface(
                self.MESH_PATH, "org.bluez.mesh.Network1"
            )

            # Attach(object app, uint64 token) -> (object node, dict config)
            result = await network_iface.call_attach(self.APP_PATH, token)
            node_path, node_config = result

            self._token = token
            self._node_path = node_path
            logger.info(f"Attached to network: {node_path}")
            return True

        except Exception as e:
            logger.debug(f"Attach failed: {e}")
            return False

    async def _ensure_network(self) -> bool:
        if self._node_path:
            return True
        if await self._attach_network():
            return True
        return await self.create_network()

    async def list_nodes(self) -> List[dict]:
        config = self._load_config()
        return list(config.get("nodes", {}).values())

    async def remove_node(self, unicast: int) -> bool:
        try:
            if not await self._ensure_network():
                return False

            await self._send_dev_key_message(unicast, bytes([0x80, 0x49]))

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

    async def scan_unprovisioned(self, timeout: float = 5.0) -> List[dict]:
        """Scan for unprovisioned mesh devices"""
        try:
            if not await self._ensure_network():
                return []

            devices = []
            mgmt_iface = await self._get_interface(
                self._node_path, "org.bluez.mesh.Management1"
            )

            bus = await self._get_bus()

            def on_signal(msg):
                if msg.member == "ScanResult":
                    rssi, data, options = msg.body
                    if len(data) >= 16:
                        uuid = bytes(data[:16]).hex().upper()
                        if not any(d["uuid"] == uuid for d in devices):
                            devices.append({
                                "uuid": uuid,
                                "rssi": rssi,
                                "name": options.get("Name", "Unknown"),
                            })
                            logger.info(f"Found: {uuid}")

            bus.add_message_handler(on_signal)

            await mgmt_iface.call_unprovisioned_scan(int(timeout))
            await asyncio.sleep(timeout + 0.5)

            bus.remove_message_handler(on_signal)
            return devices

        except Exception as e:
            logger.error(f"Scan failed: {e}")
            return []

    async def provision(self, uuid: str) -> Optional[int]:
        try:
            if not await self._ensure_network():
                return None

            mgmt_iface = await self._get_interface(
                self._node_path, "org.bluez.mesh.Management1"
            )

            config = self._load_config()
            nodes = config.get("nodes", {})
            used = {n.get("unicast_address", 0) for n in nodes.values()}
            used.add(1)
            unicast = 2
            while unicast in used:
                unicast += 1

            uuid_bytes = bytes.fromhex(uuid.replace("-", ""))

            await mgmt_iface.call_add_node(uuid_bytes, {})  # NoOOB

            await asyncio.sleep(8)

            nodes[uuid] = {"uuid": uuid, "unicast_address": unicast, "provisioned": True}
            config["nodes"] = nodes
            self._save_config(config)

            logger.info(f"Provisioned {uuid} at 0x{unicast:04x}")
            return unicast

        except Exception as e:
            logger.error(f"Provisioning failed: {e}")
            return None

    async def configure(self, unicast: int) -> bool:
        try:
            if not await self._ensure_network():
                return False

            # Composition data get
            await self._send_dev_key_message(unicast, bytes([0x80, 0x08, 0x00]))
            await asyncio.sleep(1)

            # Add app key
            msg = bytes([0x00]) + struct.pack("<I", 0)[:3] + bytes(16)
            await self._send_dev_key_message(unicast, msg)
            await asyncio.sleep(0.5)

            logger.info(f"Configured 0x{unicast:04x}")
            return True

        except Exception as e:
            logger.error(f"Configuration failed: {e}")
            return False

    async def _send_dev_key_message(self, dest: int, data: bytes):
        node_iface = await self._get_interface(
            self._node_path, "org.bluez.mesh.Node1"
        )
        await node_iface.call_dev_key_send(dest, True, 0, data)

    async def _send_mesh_message(self, dest: int, data: bytes, app_idx: int = 0):
        if not await self._ensure_network():
            raise RuntimeError("No mesh network")

        node_iface = await self._get_interface(
            self._node_path, "org.bluez.mesh.Node1"
        )
        await node_iface.call_send(dest, app_idx, data)

    def _get_target(self, unicast: Optional[int]) -> int:
        if unicast is not None:
            return unicast
        config = self._load_config()
        nodes = list(config.get("nodes", {}).values())
        if not nodes:
            raise RuntimeError("No nodes. Run 'setup' first.")
        return nodes[0]["unicast_address"]

    async def set_power(self, unicast: Optional[int], on: bool) -> bool:
        try:
            dest = self._get_target(unicast)
            data = struct.pack("<HBB", 0x8203, 1 if on else 0, 0)
            await self._send_mesh_message(dest, data)
            return True
        except Exception as e:
            logger.error(f"set_power failed: {e}")
            return False

    async def set_brightness(self, unicast: Optional[int], percent: int) -> bool:
        try:
            dest = self._get_target(unicast)
            level = int((percent / 100.0) * 65535 - 32768)
            data = struct.pack("<HhB", 0x8206, level, 0)
            await self._send_mesh_message(dest, data)
            return True
        except Exception as e:
            logger.error(f"set_brightness failed: {e}")
            return False

    async def set_color_temp(self, unicast: Optional[int], kelvin: int) -> bool:
        try:
            dest = self._get_target(unicast)
            opcode = bytes([
                0xC0 | (self.VENDOR_OPCODE_COLOR_TEMP & 0x3F),
                self.ZHIYUN_VENDOR_ID & 0xFF,
                (self.ZHIYUN_VENDOR_ID >> 8) & 0xFF
            ])
            params = struct.pack(">HH", self.ZHIYUN_DEVICE_ID, kelvin)
            await self._send_mesh_message(dest, opcode + params)
            return True
        except Exception as e:
            logger.error(f"set_color_temp failed: {e}")
            return False

    async def set_rgb(self, unicast: Optional[int], brightness: int, r: int, g: int, b: int) -> bool:
        try:
            dest = self._get_target(unicast)
            opcode = bytes([
                0xC0 | (self.VENDOR_OPCODE_RGB & 0x3F),
                self.ZHIYUN_VENDOR_ID & 0xFF,
                (self.ZHIYUN_VENDOR_ID >> 8) & 0xFF
            ])
            params = struct.pack(">HH3B", self.ZHIYUN_DEVICE_ID, brightness * 100, r, g, b)
            await self._send_mesh_message(dest, opcode + params)
            return True
        except Exception as e:
            logger.error(f"set_rgb failed: {e}")
            return False

    async def setup_device(self, target_uuid: Optional[str] = None) -> bool:
        if target_uuid is None:
            devices = await self.scan_unprovisioned(timeout=5.0)
            if not devices:
                logger.error("No devices found")
                return False
            target_uuid = devices[0]["uuid"]
            logger.info(f"Found: {target_uuid}")

        unicast = await self.provision(target_uuid)
        if unicast is None:
            return False

        return await self.configure(unicast)
