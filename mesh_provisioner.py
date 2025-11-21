"""Bluetooth Mesh Provisioner for PL103 Video Light
Handles provisioning and configuration of mesh devices
"""

import asyncio
import logging
import json
import os
from pathlib import Path
from typing import Optional, Dict, List
from bleak import BleakScanner
import dbus
import dbus.mainloop.glib
from gi.repository import GLib

logger = logging.getLogger(__name__)


class MeshProvisioner:
    """Bluetooth Mesh provisioner for PL103 devices"""

    # Mesh Provisioning Service UUIDs
    MESH_PROV_UUID = "00001827-0000-1000-8000-00805f9b34fb"
    MESH_PROXY_UUID = "00001828-0000-1000-8000-00805f9b34fb"

    # Default mesh configuration
    CONFIG_DIR = Path.home() / ".config" / "zyvega" / "mesh"
    NETWORK_NAME = "zyvega_mesh"

    def __init__(self):
        self.bus = None
        self.network_path = None
        self.node_path = None
        self.config_file = self.CONFIG_DIR / f"{self.NETWORK_NAME}.json"

    def _ensure_config_dir(self):
        """Ensure configuration directory exists"""
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    async def scan_unprovisioned(self, timeout: float = 5.0) -> List[Dict]:
        """
        Scan for unprovisioned mesh devices

        Returns:
            List of device info dictionaries
        """
        logger.info(f"Scanning for unprovisioned mesh devices ({timeout}s)...")
        devices = await BleakScanner.discover(timeout=timeout)

        unprovisioned = []
        for device in devices:
            # Check if device advertises mesh provisioning service
            if hasattr(device, 'metadata') and device.metadata:
                uuids = device.metadata.get('uuids', [])
                service_data = device.metadata.get('service_data', {})

                # Check for mesh provisioning service
                if self.MESH_PROV_UUID in uuids or self.MESH_PROV_UUID.upper() in uuids:
                    # Extract UUID from service data if available
                    device_uuid = None
                    for svc_uuid, data in service_data.items():
                        if '1827' in svc_uuid:
                            device_uuid = data.hex() if isinstance(data, bytes) else str(data)

                    device_info = {
                        'name': device.name or 'Unknown',
                        'address': device.address,
                        'rssi': device.rssi if hasattr(device, 'rssi') else None,
                        'uuid': device_uuid,
                        'device': device
                    }

                    logger.info(f"Found unprovisioned device: {device_info['name']} ({device_info['address']})")
                    unprovisioned.append(device_info)

        return unprovisioned

    def _init_dbus(self):
        """Initialize D-Bus connection"""
        if self.bus is None:
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self.bus = dbus.SystemBus()
            logger.debug("D-Bus system bus initialized")

    def _call_meshctl(self, commands: List[str], timeout: int = 30) -> tuple[int, str, str]:
        """
        Call mesh-cfgclient with commands

        Args:
            commands: List of commands to execute
            timeout: Timeout in seconds

        Returns:
            (return_code, stdout, stderr)
        """
        import subprocess

        # Create command script
        cmd_script = "\n".join(commands) + "\nquit\n"

        logger.debug(f"Running mesh-cfgclient with commands: {commands}")

        try:
            process = subprocess.Popen(
                ['mesh-cfgclient'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            stdout, stderr = process.communicate(input=cmd_script, timeout=timeout)
            return process.returncode, stdout, stderr

        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            logger.error(f"mesh-cfgclient timed out after {timeout}s")
            return -1, stdout, stderr
        except Exception as e:
            logger.error(f"Failed to run mesh-cfgclient: {e}")
            return -1, "", str(e)

    def create_network(self) -> bool:
        """
        Create a new mesh network

        Returns:
            True if successful
        """
        self._ensure_config_dir()

        if self.config_file.exists():
            logger.info(f"Network configuration already exists: {self.config_file}")
            return self.load_network()

        logger.info(f"Creating new mesh network: {self.NETWORK_NAME}")

        commands = [
            "create"
        ]

        returncode, stdout, stderr = self._call_meshctl(commands)

        if returncode == 0:
            logger.info("Mesh network created successfully")

            # Save network configuration
            config = {
                'network_name': self.NETWORK_NAME,
                'created': True,
                'nodes': {}
            }

            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)

            return True
        else:
            logger.error(f"Failed to create mesh network: {stderr}")
            return False

    def load_network(self) -> bool:
        """
        Load existing mesh network

        Returns:
            True if successful
        """
        if not self.config_file.exists():
            logger.error(f"Network configuration not found: {self.config_file}")
            return False

        logger.info(f"Loading mesh network from: {self.config_file}")

        with open(self.config_file, 'r') as f:
            config = json.load(f)

        logger.info(f"Loaded network: {config.get('network_name')}")
        return True

    def provision_device(self, device_address: str, unicast_address: int = None) -> Optional[Dict]:
        """
        Provision a device

        Args:
            device_address: MAC address of device to provision
            unicast_address: Unicast address to assign (auto-assigned if None)

        Returns:
            Node information if successful, None otherwise
        """
        logger.info(f"Provisioning device: {device_address}")

        # Load or create network
        if not self.config_file.exists():
            if not self.create_network():
                return None
        else:
            if not self.load_network():
                return None

        # Auto-assign unicast address if not provided
        if unicast_address is None:
            with open(self.config_file, 'r') as f:
                config = json.load(f)

            # Find next available address (start from 0x0001)
            used_addresses = set(node.get('unicast_address') for node in config.get('nodes', {}).values())
            unicast_address = 1
            while unicast_address in used_addresses:
                unicast_address += 1

        logger.info(f"Assigning unicast address: 0x{unicast_address:04x}")

        # Provision via mesh-cfgclient
        commands = [
            "discover-unprovisioned on",
            f"provision {device_address}",
            "0",  # No OOB
            f"{unicast_address}"
        ]

        returncode, stdout, stderr = self._call_meshctl(commands, timeout=60)

        if returncode == 0 and "Provisioning success" in stdout:
            logger.info(f"Device provisioned successfully: {device_address}")

            # Update configuration
            with open(self.config_file, 'r') as f:
                config = json.load(f)

            node_info = {
                'address': device_address,
                'unicast_address': unicast_address,
                'provisioned': True
            }

            config['nodes'][device_address] = node_info

            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)

            return node_info
        else:
            logger.error(f"Failed to provision device: {stderr}")
            logger.debug(f"stdout: {stdout}")
            return None

    def configure_node(self, unicast_address: int) -> bool:
        """
        Configure a provisioned node

        Args:
            unicast_address: Node's unicast address

        Returns:
            True if successful
        """
        logger.info(f"Configuring node: 0x{unicast_address:04x}")

        commands = [
            "menu config",
            f"target {unicast_address:04x}",
            "composition-get",
            "appkey-add 0",
            "menu main"
        ]

        returncode, stdout, stderr = self._call_meshctl(commands, timeout=30)

        if returncode == 0:
            logger.info(f"Node configured successfully: 0x{unicast_address:04x}")
            return True
        else:
            logger.error(f"Failed to configure node: {stderr}")
            return False

    def remove_node(self, device_address: str) -> bool:
        """
        Remove a node from the mesh network

        Args:
            device_address: MAC address of device to remove

        Returns:
            True if successful
        """
        logger.info(f"Removing node: {device_address}")

        if not self.config_file.exists():
            logger.error("No network configuration found")
            return False

        with open(self.config_file, 'r') as f:
            config = json.load(f)

        node_info = config.get('nodes', {}).get(device_address)
        if not node_info:
            logger.error(f"Node not found in configuration: {device_address}")
            return False

        unicast_address = node_info.get('unicast_address')

        commands = [
            "menu config",
            f"target {unicast_address:04x}",
            "node-reset",
            "menu main"
        ]

        returncode, stdout, stderr = self._call_meshctl(commands, timeout=30)

        if returncode == 0:
            logger.info(f"Node removed successfully: {device_address}")

            # Update configuration
            del config['nodes'][device_address]

            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)

            return True
        else:
            logger.error(f"Failed to remove node: {stderr}")
            return False

    def destroy_network(self) -> bool:
        """
        Destroy the mesh network and remove all configuration

        Returns:
            True if successful
        """
        logger.info(f"Destroying mesh network: {self.NETWORK_NAME}")

        if self.config_file.exists():
            # Remove all nodes first
            with open(self.config_file, 'r') as f:
                config = json.load(f)

            for device_address in list(config.get('nodes', {}).keys()):
                self.remove_node(device_address)

            # Remove configuration file
            self.config_file.unlink()
            logger.info("Network configuration removed")

        return True

    def list_nodes(self) -> List[Dict]:
        """
        List all provisioned nodes

        Returns:
            List of node information dictionaries
        """
        if not self.config_file.exists():
            logger.warning("No network configuration found")
            return []

        with open(self.config_file, 'r') as f:
            config = json.load(f)

        return list(config.get('nodes', {}).values())

    async def setup_device(self, device_address: Optional[str] = None) -> bool:
        """
        Complete setup workflow: scan, provision, and configure

        Args:
            device_address: Device address (will scan if None)

        Returns:
            True if successful
        """
        # Scan for device if not provided
        if device_address is None:
            devices = await self.scan_unprovisioned()

            if not devices:
                logger.error("No unprovisioned devices found")
                return False

            # Use first PL103 device found
            pl103_devices = [d for d in devices if d['name'].startswith('PL103')]
            if not pl103_devices:
                logger.error("No PL103 devices found")
                return False

            device = pl103_devices[0]
            device_address = device['address']
            logger.info(f"Found PL103 device: {device['name']} ({device_address})")

        # Provision device
        node_info = self.provision_device(device_address)
        if not node_info:
            logger.error("Failed to provision device")
            return False

        # Configure node
        unicast_address = node_info['unicast_address']
        if not self.configure_node(unicast_address):
            logger.error("Failed to configure node")
            return False

        logger.info(f"Device setup complete: {device_address} @ 0x{unicast_address:04x}")
        return True
