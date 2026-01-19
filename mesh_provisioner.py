"""Bluetooth Mesh Provisioner for PL103 Video Light
Handles provisioning and configuration of mesh devices
"""

import asyncio
import logging
import json
import os
import time
import pexpect
from pathlib import Path
from typing import Optional, Dict, List
from bleak import BleakScanner

logger = logging.getLogger(__name__)


class MeshProvisioner:
    """Bluetooth Mesh provisioner for PL103 devices"""

    # Mesh Provisioning Service UUIDs
    MESH_PROV_UUID = "00001827-0000-1000-8000-00805f9b34fb"
    MESH_PROXY_UUID = "00001828-0000-1000-8000-00805f9b34fb"

    # OOB key from ZYLink app (found in decompiled APK)
    OOB_KEY = "CABF7E4AC8B9E254372BBD6146D318BB"

    # Default mesh configuration
    CONFIG_DIR = Path.home() / ".config" / "zyvega" / "mesh"
    NETWORK_NAME = "zyvega_mesh"
    # mesh-cfgclient's default config location (it always writes here)
    MESHCTL_CONFIG_DIR = Path.home() / ".config" / "meshcfg"

    def __init__(self):
        self.bus = None
        self.network_path = None
        self.node_path = None
        self.config_file = self.CONFIG_DIR / f"{self.NETWORK_NAME}.json"
        # mesh-cfgclient's own config file (uses its default location)
        self.meshctl_config = self.MESHCTL_CONFIG_DIR / "config_db.json"

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

        unprovisioned = {}

        def detection_callback(device, advertisement_data):
            # Check if device advertises mesh provisioning service
            service_uuids = advertisement_data.service_uuids or []
            service_data = advertisement_data.service_data or {}

            if self.MESH_PROV_UUID in service_uuids or self.MESH_PROV_UUID.upper() in service_uuids:
                # Extract UUID from service data if available
                device_uuid = None
                for svc_uuid, data in service_data.items():
                    if '1827' in svc_uuid:
                        device_uuid = data.hex() if isinstance(data, bytes) else str(data)

                device_info = {
                    'name': device.name or 'Unknown',
                    'address': device.address,
                    'rssi': advertisement_data.rssi,
                    'uuid': device_uuid,
                    'device': device
                }

                if device.address not in unprovisioned:
                    logger.info(f"Found unprovisioned device: {device_info['name']} ({device_info['address']})")
                    unprovisioned[device.address] = device_info

        scanner = BleakScanner(detection_callback=detection_callback)
        await scanner.start()
        await asyncio.sleep(timeout)
        await scanner.stop()

        return list(unprovisioned.values())

    def _run_meshctl_interactive(self, commands: List[str], timeout: int = 60) -> tuple[bool, str]:
        """
        Run mesh-cfgclient interactively using pexpect

        Args:
            commands: List of command strings
            timeout: Timeout in seconds

        Returns:
            (success, output)
        """
        self._ensure_config_dir()

        logger.debug(f"Starting mesh-cfgclient interactive session")

        try:
            child = pexpect.spawn('mesh-cfgclient', timeout=timeout, encoding='utf-8')
            child.logfile_read = None  # Can set to sys.stdout for debugging

            # Wait for initial connection and prompt
            child.expect(r'\[mesh-cfgclient\]>', timeout=10)
            logger.debug("mesh-cfgclient ready")

            output = child.before + child.after
            provisioning_started = False

            for cmd in commands:
                # Handle delay pseudo-command
                if cmd.startswith('DELAY:'):
                    delay = int(cmd.split(':')[1])
                    logger.debug(f"Waiting {delay} seconds...")
                    time.sleep(delay)
                    continue

                logger.debug(f"Sending: {cmd}")
                child.sendline(cmd)

                # Check if this is a provision command
                if cmd.startswith('provision '):
                    provisioning_started = True
                    # Wait specifically for OOB prompt or completion
                    while True:
                        idx = child.expect([
                            r'\[mesh-agent\]#.*:',  # OOB key prompt
                            r'Provisioning success',
                            r'Provisioning failed',
                            r'not found',
                            pexpect.TIMEOUT,
                            pexpect.EOF
                        ], timeout=60)

                        output += child.before + (child.after if isinstance(child.after, str) else '')

                        if idx == 0:  # OOB key prompt
                            logger.debug(f"OOB key requested, sending: {self.OOB_KEY}")
                            child.sendline(self.OOB_KEY)
                            # Continue waiting for result
                        elif idx == 1:  # Provisioning success
                            logger.info("Provisioning succeeded!")
                            # Wait for prompt
                            child.expect(r'\[mesh-cfgclient\]>', timeout=10)
                            output += child.before + child.after
                            break
                        elif idx == 2:  # Provisioning failed
                            logger.error("Provisioning failed")
                            child.sendline('quit')
                            child.close()
                            return False, output
                        else:  # Timeout, EOF, or not found
                            logger.error(f"Provisioning error (idx={idx})")
                            child.sendline('quit')
                            child.close()
                            return False, output
                else:
                    # Regular command - wait for prompt
                    idx = child.expect([
                        r'\[mesh-cfgclient\]>',
                        pexpect.TIMEOUT,
                        pexpect.EOF
                    ], timeout=30)

                    output += child.before + (child.after if isinstance(child.after, str) else '')

                    if idx != 0:
                        logger.error("Command failed or timed out")
                        child.close()
                        return False, output

            # Clean exit
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

    def _ensure_network(self) -> bool:
        """
        Ensure mesh network exists, create if needed.

        Returns:
            True if network is ready
        """
        if self.meshctl_config.exists():
            logger.debug(f"Mesh network config exists: {self.meshctl_config}")
            return True

        logger.info("No mesh network found, creating one...")
        return self.create_network()

    def create_network(self) -> bool:
        """
        Create a new mesh network

        Returns:
            True if successful
        """
        self._ensure_config_dir()

        if self.meshctl_config.exists():
            logger.info(f"Mesh network already exists: {self.meshctl_config}")
            return True

        logger.info(f"Creating new mesh network: {self.NETWORK_NAME}")

        success, output = self._run_meshctl_interactive(["create"], timeout=30)
        logger.debug(f"Create network output:\n{output}")

        if success or self.meshctl_config.exists():
            logger.info("Mesh network created successfully")

            # Save our app's configuration
            config = {
                'network_name': self.NETWORK_NAME,
                'created': True,
                'nodes': {}
            }

            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)

            return True
        else:
            logger.error(f"Failed to create mesh network")
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

    def provision_device(self, device_uuid: str, device_address: str = None, unicast_address: int = None) -> Optional[Dict]:
        """
        Provision a device

        Args:
            device_uuid: Mesh UUID of device to provision (from scan)
            device_address: MAC address for tracking (optional)
            unicast_address: Unicast address to assign (auto-assigned if None)

        Returns:
            Node information if successful, None otherwise
        """
        logger.info(f"Provisioning device: {device_uuid}")

        # Ensure mesh network exists
        if not self._ensure_network():
            logger.error("Failed to ensure mesh network exists")
            return None

        # Ensure our app config exists
        self._ensure_config_dir()
        if not self.config_file.exists():
            config = {
                'network_name': self.NETWORK_NAME,
                'created': True,
                'nodes': {}
            }
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)

        # Auto-assign unicast address if not provided
        # Start from 0x0002 since 0x0001 is typically the provisioner
        if unicast_address is None:
            with open(self.config_file, 'r') as f:
                config = json.load(f)

            used_addresses = set(node.get('unicast_address') for node in config.get('nodes', {}).values())
            used_addresses.add(1)  # Reserve 0x0001 for provisioner
            unicast_address = 2
            while unicast_address in used_addresses:
                unicast_address += 1

        logger.info(f"Assigning unicast address: 0x{unicast_address:04x}")

        # Provision via mesh-cfgclient using interactive session
        # Use DELAY:N to indicate a delay between commands
        commands = [
            "discover-unprovisioned on",
            "DELAY:3",  # Give time for device to be discovered
            f"provision {device_uuid}",
        ]

        success, output = self._run_meshctl_interactive(commands, timeout=90)
        logger.debug(f"Provisioning output:\n{output}")

        if success and "Provisioning success" in output:
            logger.info(f"Device provisioned successfully: {device_uuid}")

            # Update configuration
            with open(self.config_file, 'r') as f:
                config = json.load(f)

            node_info = {
                'uuid': device_uuid,
                'address': device_address,
                'unicast_address': unicast_address,
                'provisioned': True
            }

            key = device_address or device_uuid
            config['nodes'][key] = node_info

            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)

            return node_info
        else:
            logger.error(f"Failed to provision device")
            logger.debug(f"Output: {output}")
            return None

    def configure_node(self, unicast_address: int) -> bool:
        """
        Configure a provisioned node (add app key, get composition)

        Args:
            unicast_address: Node's unicast address

        Returns:
            True if successful
        """
        if not self._ensure_network():
            logger.error("Failed to ensure mesh network exists")
            return False

        logger.info(f"Configuring node: 0x{unicast_address:04x}")

        commands = [
            "menu config",
            f"target {unicast_address:04x}",
            "composition-get",
            "appkey-add 0",
            "back"
        ]

        success, output = self._run_meshctl_interactive(commands, timeout=30)
        logger.debug(f"Configure output:\n{output}")

        if success:
            logger.info(f"Node configured successfully: 0x{unicast_address:04x}")
            return True
        else:
            logger.error(f"Failed to configure node")
            return False

    def remove_node(self, device_address: str) -> bool:
        """
        Remove a node from the mesh network

        Args:
            device_address: MAC address of device to remove

        Returns:
            True if successful
        """
        if not self._ensure_network():
            logger.error("Failed to ensure mesh network exists")
            return False

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
            "back"
        ]

        success, output = self._run_meshctl_interactive(commands, timeout=30)
        logger.debug(f"Remove node output:\n{output}")

        if success:
            logger.info(f"Node removed successfully: {device_address}")

            del config['nodes'][device_address]
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)

            return True
        else:
            logger.error(f"Failed to remove node")
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
            device_uuid = device.get('uuid')
            logger.info(f"Found PL103 device: {device['name']} ({device_address})")

            if not device_uuid:
                logger.error("Device UUID not found in scan data")
                return False

            # Remove trailing zeros from UUID if present (mesh-cfgclient expects 32 hex chars)
            device_uuid = device_uuid[:32].upper()
        else:
            # If address provided directly, we need to scan to get the UUID
            logger.error("Direct address provisioning not supported - use scan first")
            return False

        # Provision device
        node_info = self.provision_device(device_uuid, device_address)
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
