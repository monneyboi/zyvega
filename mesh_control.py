"""Bluetooth Mesh Control for PL103 Video Light
Sends control commands through the mesh network
"""

import logging
import struct
import pexpect
from typing import Optional, List
from pathlib import Path
import json

logger = logging.getLogger(__name__)


class MeshLightController:
    """Control PL103 lights through Bluetooth Mesh"""

    # Generic OnOff Model
    GENERIC_ONOFF_MODEL = 0x1000

    # Generic Level Model
    GENERIC_LEVEL_MODEL = 0x1002

    # Light Lightness Model
    LIGHT_LIGHTNESS_MODEL = 0x1300

    # Zhiyun Vendor Model IDs (need to discover from composition data)
    ZHIYUN_VENDOR_ID = 0x0905  # 2309 decimal from manufacturer data

    CONFIG_DIR = Path.home() / ".config" / "zyvega" / "mesh"
    NETWORK_NAME = "zyvega_mesh"

    def __init__(self):
        self.config_file = self.CONFIG_DIR / f"{self.NETWORK_NAME}.json"
        self.default_node = None

    def _load_config(self) -> dict:
        """Load mesh network configuration"""
        if not self.config_file.exists():
            raise RuntimeError(f"Mesh network not configured. Run 'mesh setup' first.")

        with open(self.config_file, 'r') as f:
            return json.load(f)

    def _get_target_node(self, address: Optional[str] = None) -> dict:
        """
        Get target node information

        Args:
            address: MAC address of node (uses first node if None)

        Returns:
            Node info dict with unicast_address
        """
        config = self._load_config()
        nodes = config.get('nodes', {})

        if not nodes:
            raise RuntimeError("No provisioned nodes found. Run 'mesh setup' first.")

        if address:
            node = nodes.get(address)
            if not node:
                raise ValueError(f"Node {address} not found in mesh network")
            return node
        else:
            # Use first node as default
            return list(nodes.values())[0]

    def _run_meshctl_commands(self, commands: List[str], timeout: int = 30) -> tuple[bool, str]:
        """
        Run mesh-cfgclient commands interactively

        Args:
            commands: List of commands to execute
            timeout: Timeout in seconds

        Returns:
            (success, output)
        """
        logger.debug(f"Running mesh-cfgclient commands: {commands}")

        try:
            child = pexpect.spawn('mesh-cfgclient', timeout=timeout, encoding='utf-8')

            # Wait for initial prompt
            child.expect(r'\[mesh-cfgclient\]>', timeout=10)
            output = child.before + child.after

            for cmd in commands:
                logger.debug(f"Sending: {cmd}")
                child.sendline(cmd)
                child.expect(r'\[mesh-cfgclient\]>', timeout=10)
                output += child.before + child.after

            child.sendline('quit')
            child.expect(pexpect.EOF, timeout=5)
            output += child.before if child.before else ''
            child.close()

            return True, output

        except pexpect.exceptions.TIMEOUT:
            logger.error("mesh-cfgclient timed out")
            return False, ""
        except Exception as e:
            logger.error(f"Failed to run mesh-cfgclient: {e}")
            return False, str(e)

    def _send_generic_level(self, dest_addr: int, level: int, tid: int = 0) -> bool:
        """
        Send Generic Level Set message

        Args:
            dest_addr: Destination address
            level: Level value (-32768 to 32767, or 0 to 65535 unsigned)
            tid: Transaction ID

        Returns:
            True if successful
        """
        # Generic Level Set opcode (unacknowledged)
        opcode = 0x8206

        # Pack level as signed 16-bit little-endian + TID
        payload = struct.pack('<hB', level, tid & 0xFF)

        return self._send_mesh_message(dest_addr, self.GENERIC_LEVEL_MODEL, opcode, payload)

    def _send_vendor_message(self, dest_addr: int, vendor_opcode: int, payload: bytes) -> bool:
        """
        Send vendor-specific message

        Args:
            dest_addr: Destination address
            vendor_opcode: Vendor-specific opcode
            payload: Message payload

        Returns:
            True if successful
        """
        # Vendor model message format includes company ID
        # Opcode format: 0xC0 | (vendor_opcode & 0x3F) for 2-byte opcodes
        opcode = 0xC0 | (vendor_opcode & 0x3F)

        # Prepend company ID to payload
        full_payload = struct.pack('<H', self.ZHIYUN_VENDOR_ID) + payload

        return self._send_mesh_message(dest_addr, 0, opcode, full_payload)

    def set_brightness(self, brightness: int, address: Optional[str] = None) -> bool:
        """
        Set brightness via mesh Generic Level model

        Args:
            brightness: Brightness percentage (0-100)
            address: Target device address (uses default if None)

        Returns:
            True if successful
        """
        if not 0 <= brightness <= 100:
            raise ValueError("Brightness must be between 0 and 100")

        node = self._get_target_node(address)
        dest_addr = node['unicast_address']

        # Convert brightness percentage to Generic Level (-32768 to 32767)
        # 0% = -32768, 100% = 32767
        level = int((brightness / 100.0) * 65535 - 32768)

        logger.info(f"Setting brightness to {brightness}% (level={level}) on node 0x{dest_addr:04x}")

        # Use level menu for brightness control
        commands = [
            "menu level",
            f"target {dest_addr:04x}",
            f"set {level}",
            "back"
        ]

        success, output = self._run_meshctl_commands(commands)
        logger.debug(f"Brightness output: {output}")
        return success

    def set_color_temp(self, kelvin: int, address: Optional[str] = None) -> bool:
        """
        Set color temperature via vendor model

        Args:
            kelvin: Color temperature (2700-6500K)
            address: Target device address

        Returns:
            True if successful
        """
        if not 2700 <= kelvin <= 6500:
            raise ValueError("Color temperature must be between 2700K and 6500K")

        node = self._get_target_node(address)
        dest_addr = node['unicast_address']

        # Use vendor model with Zhiyun protocol
        # Command format from reverse engineering
        device_id = 0x0380
        payload = struct.pack('>HH', device_id, kelvin)

        logger.info(f"Setting color temp to {kelvin}K on node 0x{dest_addr:04x}")

        # Vendor opcode for color temp (from CMD_COLOR_TEMP = 0x1002)
        return self._send_vendor_message(dest_addr, 0x1002, payload)

    def set_rgb(self, brightness: int, r: int, g: int, b: int,
                address: Optional[str] = None) -> bool:
        """
        Set RGB color via vendor model

        Args:
            brightness: Brightness percentage (0-100)
            r: Red value (0-255)
            g: Green value (0-255)
            b: Blue value (0-255)
            address: Target device address

        Returns:
            True if successful
        """
        if not 0 <= brightness <= 100:
            raise ValueError("Brightness must be between 0 and 100")
        if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
            raise ValueError("RGB values must be between 0 and 255")

        node = self._get_target_node(address)
        dest_addr = node['unicast_address']

        # Convert brightness to value (0-10000)
        brightness_value = int(brightness * 100)

        # Use vendor model with Zhiyun protocol
        device_id = 0x0380
        payload = struct.pack('>HH3B', device_id, brightness_value, r, g, b)

        logger.info(f"Setting RGB to ({r},{g},{b}) at {brightness}% on node 0x{dest_addr:04x}")

        # Vendor opcode for RGB (from CMD_RGB = 0x1003)
        return self._send_vendor_message(dest_addr, 0x1003, payload)

    def set_power(self, on: bool, address: Optional[str] = None) -> bool:
        """
        Turn light on/off via Generic OnOff model

        Args:
            on: True for on, False for off
            address: Target device address

        Returns:
            True if successful
        """
        node = self._get_target_node(address)
        dest_addr = node['unicast_address']

        logger.info(f"Turning light {'on' if on else 'off'} on node 0x{dest_addr:04x}")

        commands = [
            "menu onoff",
            f"target {dest_addr:04x}",
            "on" if on else "off",
            "back"
        ]

        success, output = self._run_meshctl_commands(commands)
        logger.debug(f"Power output: {output}")
        return success

    def list_nodes(self) -> List[dict]:
        """
        List all available nodes

        Returns:
            List of node info dictionaries
        """
        config = self._load_config()
        return list(config.get('nodes', {}).values())
