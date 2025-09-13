#!/usr/bin/env python3
"""
Raspberry Pi System Monitor with MQTT and Home Assistant Auto-Discovery
Monitors CPU usage, temperature, memory, disk usage, and network stats
"""

import json
import time
import socket
import psutil
import paho.mqtt.client as mqtt
from datetime import datetime
import logging
import argparse
import sys
import os
import yaml
from pathlib import Path

# Configure logging (will be updated based on config)
logger = logging.getLogger(__name__)

class SystemMonitor:
    def __init__(self, config, debug_override=False):
        # Configure logging based on config, but allow debug override
        if debug_override:
            log_level = 'DEBUG'
        else:
            log_level = config.get('logging', {}).get('level', 'INFO').upper()

        numeric_level = getattr(logging, log_level, logging.INFO)
        logging.basicConfig(level=numeric_level, format='%(asctime)s - %(levelname)s - %(message)s')

        # MQTT settings
        self.mqtt_broker = config['mqtt']['broker']
        self.mqtt_port = config['mqtt'].get('port', 1883)
        self.mqtt_user = config['mqtt'].get('username')
        self.mqtt_pass = config['mqtt'].get('password')

        # Monitor settings
        self.update_interval = config['monitor'].get('update_interval', 60)
        self.ha_discovery = config['monitor'].get('home_assistant_discovery', True)

        # GPIO settings for fan monitoring
        self.fan_enabled = config['monitor'].get('fan_monitoring', {}).get('enabled', False)
        self.fan_gpio_pin = config['monitor'].get('fan_monitoring', {}).get('gpio_pin', 14)

        # Check if fan monitoring is possible
        if self.fan_enabled:
            self._check_pinctrl_availability()

        # Get hostname for topic structure
        self.hostname = socket.gethostname()
        self.base_topic = f"system_monitor/{self.hostname}"

        # MQTT client setup
        self.client = mqtt.Client()
        if self.mqtt_user and self.mqtt_pass:
            self.client.username_pw_set(self.mqtt_user, self.mqtt_pass)

        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect

        # Track discovery messages sent
        self.discovery_sent = False


    def _check_pinctrl_availability(self):
        """Check if pinctrl is available for GPIO monitoring"""
        try:
            import subprocess
            # Test if pinctrl command is available
            result = subprocess.run(['pinctrl', 'get', str(self.fan_gpio_pin)], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                logger.info(f"Fan monitoring enabled on GPIO pin {self.fan_gpio_pin} (using pinctrl)")
            else:
                logger.warning(f"pinctrl command failed for GPIO pin {self.fan_gpio_pin}")
                self.fan_enabled = False
        except FileNotFoundError:
            logger.warning("pinctrl command not found. Install with: sudo apt install raspi-gpio")
            self.fan_enabled = False
        except Exception as e:
            logger.warning(f"Failed to check pinctrl availability: {e}")
            self.fan_enabled = False


    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"Connected to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")
            if self.ha_discovery and not self.discovery_sent:
                self.send_discovery_messages()
                self.discovery_sent = True
        else:
            logger.error(f"Failed to connect to MQTT broker: {rc}")

    def on_disconnect(self, client, userdata, rc):
        logger.warning(f"Disconnected from MQTT broker: {rc}")

    def get_cpu_temperature(self):
        """Get CPU temperature (RPi specific)"""
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = float(f.read().strip()) / 1000.0
                return round(temp, 1)
        except:
            return None

    def get_fan_status(self):
        """Get fan status using pinctrl command"""
        if not self.fan_enabled:
            return None

        try:
            import subprocess
            result = subprocess.run(['pinctrl', 'get', str(self.fan_gpio_pin)], 
                                  capture_output=True, text=True, timeout=5)

            if result.returncode == 0:
                # Parse pinctrl output to get pin level
                # Output format examples:
                # "14: ip    -- | hi // GPIO14 = input"
                # "14: op -- pn | lo // GPIO14 = output"
                output = result.stdout.strip()
                logger.debug(f"pinctrl output for pin {self.fan_gpio_pin}: {output}")

                if '| hi' in output.lower():
                    return True
                elif '| lo' in output.lower():
                    return False
                else:
                    logger.warning(f"Could not parse pinctrl output for pin {self.fan_gpio_pin}: {output}")
                    return None
            else:
                logger.error(f"pinctrl command failed: {result.stderr}")
                return None

        except subprocess.TimeoutExpired:
            logger.error(f"pinctrl command timed out for GPIO pin {self.fan_gpio_pin}")
            return None
        except Exception as e:
            logger.error(f"Error reading fan GPIO pin {self.fan_gpio_pin} with pinctrl: {e}")
            return None

    def get_system_metrics(self):
        """Collect all system metrics"""
        metrics = {}

        # CPU metrics
        metrics['cpu_percent'] = round(psutil.cpu_percent(interval=1), 1)
        metrics['cpu_temp'] = self.get_cpu_temperature()
        metrics['load_avg'] = os.getloadavg()[0] if hasattr(os, 'getloadavg') else None

        # Memory metrics
        memory = psutil.virtual_memory()
        metrics['memory_percent'] = round(memory.percent, 1)
        metrics['memory_used_gb'] = round(memory.used / (1024**3), 2)
        metrics['memory_total_gb'] = round(memory.total / (1024**3), 2)

        # Disk metrics (root partition)
        disk = psutil.disk_usage('/')
        metrics['disk_percent'] = round(disk.percent, 1)
        metrics['disk_used_gb'] = round(disk.used / (1024**3), 2)
        metrics['disk_total_gb'] = round(disk.total / (1024**3), 2)

        # Network metrics
        net_io = psutil.net_io_counters()
        metrics['network_bytes_sent'] = net_io.bytes_sent
        metrics['network_bytes_recv'] = net_io.bytes_recv

        # System uptime
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        metrics['uptime_hours'] = round(uptime_seconds / 3600, 1)

        # Fan status (if enabled)
        if self.fan_enabled:
            metrics['fan_status'] = self.get_fan_status()

        # Timestamp
        metrics['timestamp'] = datetime.now().isoformat()

        return metrics

    def send_discovery_messages(self):
        """Send Home Assistant discovery messages for all sensors"""
        logger.info("Sending Home Assistant discovery messages...")

        device_info = {
            "identifiers": [f"system_monitor_{self.hostname}"],
            "name": f"System Monitor {self.hostname}",
            "model": "Raspberry Pi",
            "manufacturer": "System Monitor Script",
            "sw_version": "1.0.0"
        }

        sensors = [
            {
                "name": "CPU Usage",
                "key": "cpu_percent",
                "unit": "%",
                "icon": "mdi:cpu-64-bit",
                "device_class": None,
                "state_class": "measurement"
            },
            {
                "name": "CPU Temperature",
                "key": "cpu_temp",
                "unit": "°C",
                "icon": "mdi:thermometer",
                "device_class": "temperature",
                "state_class": "measurement"
            },
            {
                "name": "Load Average",
                "key": "load_avg",
                "unit": None,
                "icon": "mdi:chart-line",
                "device_class": None,
                "state_class": "measurement"
            },
            {
                "name": "Memory Usage",
                "key": "memory_percent",
                "unit": "%",
                "icon": "mdi:memory",
                "device_class": None,
                "state_class": "measurement"
            },
            {
                "name": "Memory Used",
                "key": "memory_used_gb",
                "unit": "GB",
                "icon": "mdi:memory",
                "device_class": None,
                "state_class": "measurement"
            },
            {
                "name": "Disk Usage",
                "key": "disk_percent",
                "unit": "%",
                "icon": "mdi:harddisk",
                "device_class": None,
                "state_class": "measurement"
            },
            {
                "name": "Disk Used",
                "key": "disk_used_gb",
                "unit": "GB",
                "icon": "mdi:harddisk",
                "device_class": None,
                "state_class": "measurement"
            },
            {
                "name": "Network Bytes Sent",
                "key": "network_bytes_sent",
                "unit": "B",
                "icon": "mdi:upload-network",
                "device_class": None,
                "state_class": "total_increasing"
            },
            {
                "name": "Network Bytes Received",
                "key": "network_bytes_recv",
                "unit": "B",
                "icon": "mdi:download-network",
                "device_class": None,
                "state_class": "total_increasing"
            },
            {
                "name": "Uptime",
                "key": "uptime_hours",
                "unit": "h",
                "icon": "mdi:clock-outline",
                "device_class": "duration",
                "state_class": "total_increasing"
            }
        ]

        for sensor in sensors:
            config = {
                "name": f"{self.hostname} {sensor['name']}",
                "unique_id": f"system_monitor_{self.hostname}_{sensor['key']}",
                "state_topic": f"{self.base_topic}/state",
                "value_template": "{{ value_json." + sensor['key'] + " }}",
                "icon": sensor['icon'],
                "device": device_info
            }

            if sensor['unit']:
                config["unit_of_measurement"] = sensor['unit']
            if sensor['device_class']:
                config["device_class"] = sensor['device_class']
            if sensor['state_class']:
                config["state_class"] = sensor['state_class']

            discovery_topic = f"homeassistant/sensor/system_monitor_{self.hostname}_{sensor['key']}/config"

            self.client.publish(discovery_topic, json.dumps(config), retain=True)
            time.sleep(0.1)  # Small delay between messages

        # Add fan binary sensor if enabled
        if self.fan_enabled:
            fan_config = {
                "name": f"{self.hostname} Case Fan",
                "unique_id": f"system_monitor_{self.hostname}_fan_status",
                "state_topic": f"{self.base_topic}/state",
                "value_template": "{% if value_json.fan_status %}ON{% else %}OFF{% endif %}",
                "payload_on": "ON",
                "payload_off": "OFF",
                "icon": "mdi:fan",
                "device": device_info
            }

            fan_discovery_topic = f"homeassistant/binary_sensor/system_monitor_{self.hostname}_fan_status/config"
            self.client.publish(fan_discovery_topic, json.dumps(fan_config), retain=True)
            time.sleep(0.1)

        logger.info("Home Assistant discovery messages sent")

    def publish_metrics(self, metrics):
        """Publish metrics to MQTT"""
        # Publish as single JSON payload
        state_topic = f"{self.base_topic}/state"
        self.client.publish(state_topic, json.dumps(metrics), retain=True)

        # Also publish individual metrics for easier consumption
        for key, value in metrics.items():
            if value is not None:
                topic = f"{self.base_topic}/{key}"
                self.client.publish(topic, str(value), retain=True)

    def run(self):
        """Main monitoring loop"""
        try:
            logger.info(f"Starting system monitor for {self.hostname}")
            self.client.connect(self.mqtt_broker, self.mqtt_port, 60)
            self.client.loop_start()

            while True:
                try:
                    metrics = self.get_system_metrics()
                    self.publish_metrics(metrics)

                    log_msg = (f"Published metrics: CPU={metrics['cpu_percent']}%, "
                              f"Temp={metrics['cpu_temp']}°C, "
                              f"Memory={metrics['memory_percent']}%, "
                              f"Disk={metrics['disk_percent']}%")

                    # Add fan status to log if enabled
                    if self.fan_enabled and metrics.get('fan_status') is not None:
                        fan_state = "ON" if metrics['fan_status'] else "OFF"
                        log_msg += f", Fan={fan_state}"

                    logger.info(log_msg)

                    time.sleep(self.update_interval)

                except KeyboardInterrupt:
                    logger.info("Received interrupt signal, shutting down...")
                    break
                except Exception as e:
                    logger.error(f"Error in monitoring loop: {e}")
                    time.sleep(10)  # Wait before retrying

        except Exception as e:
            logger.error(f"Failed to start monitoring: {e}")
        finally:
            self.client.loop_stop()
            self.client.disconnect()
            logger.info("System monitor stopped")

def load_config(config_file):
    """Load configuration from YAML file"""
    try:
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)

        # Validate required sections
        if 'mqtt' not in config:
            raise ValueError("Missing 'mqtt' section in config file")
        if 'broker' not in config['mqtt']:
            raise ValueError("Missing 'broker' in mqtt configuration")

        # Set defaults for optional sections
        if 'monitor' not in config:
            config['monitor'] = {}

        return config

    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_file}")
        sys.exit(1)
    except yaml.YAMLError as e:
        logger.error(f"Error parsing YAML configuration: {e}")
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

def create_sample_config(config_file):
    """Create a sample configuration file"""
    from collections import OrderedDict

    # Use OrderedDict to maintain order
    sample_config = OrderedDict([
        ('mqtt', OrderedDict([
            ('broker', '192.168.1.100'),
            ('port', 1883),
            ('username', None),  # Optional
            ('password', None)   # Optional
        ])),
        ('monitor', OrderedDict([
            ('update_interval', 60),
            ('home_assistant_discovery', True)
        ])),
        ('logging', OrderedDict([
            ('level', 'INFO')  # DEBUG, INFO, WARNING, ERROR, CRITICAL
        ]))
    ])

    try:
        with open(config_file, 'w') as f:
            yaml.dump(sample_config, f, default_flow_style=False, indent=2, sort_keys=False)
        logger.info(f"Sample configuration file created: {config_file}")
        print(f"\nSample configuration file created at: {config_file}")
        print("Please edit this file with your MQTT broker details and run the script again.")
        return True
    except Exception as e:
        logger.error(f"Failed to create sample config: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Raspberry Pi System Monitor with MQTT')
    parser.add_argument('--config', '-c', default='config.yaml', 
                       help='Configuration file path (default: config.yaml)')
    parser.add_argument('--create-config', action='store_true',
                       help='Create a sample configuration file and exit')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')

    # Legacy command line options (override config file)
    parser.add_argument('--broker', help='MQTT broker hostname or IP (overrides config)')
    parser.add_argument('--port', type=int, help='MQTT broker port (overrides config)')
    parser.add_argument('--username', help='MQTT username (overrides config)')
    parser.add_argument('--password', help='MQTT password (overrides config)')
    parser.add_argument('--interval', type=int, help='Update interval in seconds (overrides config)')
    parser.add_argument('--no-discovery', action='store_true', 
                       help='Disable Home Assistant discovery (overrides config)')

    args = parser.parse_args()

    # Set up early debug logging if requested
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

    # Create sample config if requested
    if args.create_config:
        create_sample_config(args.config)
        sys.exit(0)

    # Check for required dependencies
    try:
        import paho.mqtt.client
        import psutil
        import yaml
    except ImportError as e:
        logger.error(f"Missing required dependency: {e}")
        logger.error("Install with: pip install paho-mqtt psutil pyyaml")
        sys.exit(1)

    # Load configuration
    if not os.path.exists(args.config):
        logger.error(f"Configuration file not found: {args.config}")
        print(f"\nConfiguration file '{args.config}' not found.")
        print(f"Create a sample configuration file with: python3 {sys.argv[0]} --create-config")
        sys.exit(1)

    config = load_config(args.config)

    # Override config with command line arguments if provided
    if args.broker:
        config['mqtt']['broker'] = args.broker
    if args.port:
        config['mqtt']['port'] = args.port
    if args.username:
        config['mqtt']['username'] = args.username
    if args.password:
        config['mqtt']['password'] = args.password
    if args.interval:
        config['monitor']['update_interval'] = args.interval
    if args.no_discovery:
        config['monitor']['home_assistant_discovery'] = False

    monitor = SystemMonitor(config, debug_override=args.debug)
    monitor.run()

if __name__ == "__main__":
    main()
