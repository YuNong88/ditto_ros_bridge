#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
import aiohttp
import asyncio
import json
import base64
import re
from threading import Thread
from ditto_ros_msgs.msg import AssetMetadata, Alert, Relationship, MachineStatus, TrafficData, WaterManagement, EnergyConsumption, ProductionLine, Temperature, Humidity, Pressure, Imu, EnergyConsumption, CropData, WaterManagement, TrafficData, EnvironmentalData, TrafficLight
from geometry_msgs.msg import Point
from std_msgs.msg import Float64, String

class DittoROS2Bridge(Node):
    def __init__(self):
        super().__init__('ditto_ros_bridge')
        
        self._topic_publishers = {}

        # Parameters
        self.declare_parameter('ditto_host', 'localhost')
        self.declare_parameter('ditto_port', 8080)
        self.declare_parameter('ditto_username', 'ditto')
        self.declare_parameter('ditto_password', 'ditto')
        self.declare_parameter('ditto_namespaces', 'org.smartcity,org.agriculture,com.manufacturing')
        self.declare_parameter('debug', True)
        
        self.host = self.get_parameter('ditto_host').value
        self.port = self.get_parameter('ditto_port').value
        self.namespaces = self.get_parameter('ditto_namespaces').value
        self.base_url = f"http://{self.host}:{self.port}/api/2"
        
        self.username = self.get_parameter('ditto_username').value
        self.password = self.get_parameter('ditto_password').value
        self.debug = self.get_parameter('debug').value
        
        # Start SSE thread
        self.sse_thread = Thread(target=self.run_sse)
        self.sse_thread.daemon = True
        self.sse_thread.start()

    def sanitize_topic_name(self, name: str) -> str:
        """Convert Ditto thingId to a valid ROS topic name."""
        # First, replace colons and dots with underscores
        name = name.replace(':', '_').replace('.', '_')
        
        # If the name doesn't start with '/', add it
        if not name.startswith('/'):
            name = '/' + name
            
        # Replace any remaining invalid characters with underscores
        # Only allow alphanumerics, '_', '~', '{', '}', and '/'
        return re.sub(r'[^0-9a-zA-Z_/~{}]', '_', name)

    def get_or_create_publisher(self, msg_type, topic_name: str):
        """Get or create a publisher for a specific topic."""
        # Sanitize the topic name
        topic_name = self.sanitize_topic_name(topic_name)
        
        if topic_name not in self._topic_publishers:  # Update reference
            self._topic_publishers[topic_name] = self.create_publisher(msg_type, topic_name, 10)
            self.get_logger().info(f"Created new publisher for topic: {topic_name}")
        
        return self._topic_publishers[topic_name]  # Update reference

    def run_sse(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.subscribe_to_changes())

    async def subscribe_to_changes(self):
        headers = {
            'Accept': 'text/event-stream',
            'Authorization': f'Basic {self.get_basic_auth()}'
        }
        sse_url = f"{self.base_url}/things?namespaces={self.namespaces}&fields=thingId,attributes,features"
        
        while rclpy.ok():
            try:
                self.get_logger().info(f"Connecting to SSE stream: {sse_url}")
                async with aiohttp.ClientSession() as session:
                    async with session.get(sse_url, headers=headers) as response:
                        if response.status != 200:
                            self.get_logger().error(f"Failed to connect to SSE stream. Status: {response.status}")
                            await asyncio.sleep(5)
                            continue
                        
                        self.get_logger().info("Connected to SSE stream. Waiting for events...")
                        async for event in self.sse_parse(response.content):
                            if self.debug:
                                self.get_logger().debug(f"Received event: {event}")
                            if 'data' in event:
                                try:
                                    thing_data = json.loads(event['data'])
                                    await self.process_thing(thing_data)
                                except json.JSONDecodeError:
                                    if event['data'] != "":  # Ignore empty data
                                        self.get_logger().warn(f"Received non-JSON data: {event['data']}")
            except Exception as e:
                self.get_logger().error(f"Error in SSE connection: {str(e)}")
            
            self.get_logger().info("SSE connection closed. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)  # Wait before reconnecting

    async def sse_parse(self, content):
        buffer = ""
        async for line in content:
            line = line.decode('utf-8').strip()
            if line:
                buffer += line + "\n"
            else:
                if buffer:
                    event = {}
                    for line in buffer.split("\n"):
                        if ":" in line:
                            key, value = line.split(":", 1)
                            event[key.strip()] = value.strip()
                    if event:
                        yield event
                    buffer = ""

    def get_basic_auth(self):
        return base64.b64encode(f"{self.username}:{self.password}".encode()).decode()

    async def process_thing(self, thing: dict):
        try:
            if self.debug:
                self.get_logger().debug(f"Processing thing: {json.dumps(thing, indent=2)}")
            
            thing_id = thing.get('thingId', '')
            attributes = thing.get('attributes', {})
            features = thing.get('features', {})

            # Process attributes (metadata and location)
            if 'location' in attributes:
                location = attributes['location']
                point_msg = Point()
                point_msg.x = float(location.get('longitude', 0.0))
                point_msg.y = float(location.get('latitude', 0.0))
                point_msg.z = float(location.get('elevation', 0.0))

                metadata_msg = AssetMetadata()
                metadata_msg.asset_id = thing_id
                metadata_msg.type = attributes.get('asset_type', '')
                metadata_msg.location = point_msg

                metadata_pub = self.get_or_create_publisher(AssetMetadata, f"{thing_id}/metadata")
                metadata_pub.publish(metadata_msg)

                self.get_logger().info(
                f"Published thing_id: {thing_id} : metadata_msg: {metadata_msg}"
                )

            # Process features (telemetry)
            if 'temperature' in features:
                temp = features['temperature'].get('properties', {})
                temp_msg = Temperature()
                temp_msg.temperature = float(temp.get('value', 0.0))
                pub = self.get_or_create_publisher(Temperature, f"{thing_id}/sensor/temperature")
                pub.publish(temp_msg)

                self.get_logger().info(
                f"Published thing_id: {thing_id} : temp_msg: {temp_msg}"
                )

            if 'traffic_light_status' in features:
                traffic_light = features['traffic_light_status'].get('properties', {})
                traffic_light_msg = TrafficLight()
                traffic_light_msg.current_state = traffic_light.get('current_state', 'unknown')
                traffic_light_msg.time_to_change = float(traffic_light.get('time_to_change', 0.0))
                pub = self.get_or_create_publisher(TrafficLight, f"{thing_id}/traffic_light_status")
                pub.publish(traffic_light_msg)

                self.get_logger().info(
                f"Published thing_id: {thing_id} : traffic_light_msg: {traffic_light_msg}"
                )

            if 'humidity' in features:
                hum = features['humidity'].get('properties', {})
                hum_msg = Humidity()
                hum_msg.humidity = float(hum.get('value', 0.0))
                pub = self.get_or_create_publisher(Humidity, f"{thing_id}/sensor/humidity")
                pub.publish(hum_msg)

                self.get_logger().info(
                f"Published thing_id: {thing_id} : hum_msg: {hum_msg}"
                )
                
            if 'pressure' in features:
                pre = features['pressure'].get('properties', {})
                pre_msg = Pressure()
                pre_msg.pressure = float(pre.get('value', 0.0))
                pub = self.get_or_create_publisher(Pressure, f"{thing_id}/sensor/pressure")
                pub.publish(pre_msg)

                self.get_logger().info(
                f"Published thing_id: {thing_id} : pre_msg: {pre_msg}"
                )

            if 'imu' in features:
                imu = features['imu'].get('properties', {})
                imu_msg = Imu()
                imu_msg.linear_acceleration_x = float(imu.get('accel_x', 0.0))
                imu_msg.linear_acceleration_y = float(imu.get('accel_y', 0.0))
                imu_msg.linear_acceleration_z = float(imu.get('accel_z', 0.0))
                imu_msg.angular_velocity_x = float(imu.get('gyro_x', 0.0))
                imu_msg.angular_velocity_y = float(imu.get('gyro_y', 0.0))
                imu_msg.angular_velocity_z = float(imu.get('gyro_z', 0.0))
                pub = self.get_or_create_publisher(Imu, f"{thing_id}/sensor/imu")
                pub.publish(imu_msg)
                
                self.get_logger().info(
                f"Published thing_id: {thing_id} : imu_msg: {imu_msg}"
                )

            # Process live messages (alerts)
            if 'alerts' in features:
                inbox = features['alerts'].get('properties', {})
                for message_id, message in inbox.items():
                    alert_msg = Alert()
                    alert_msg.message = f"{thing_id}:{message.get('type', 'unknown')}"
                    alert_msg.severity = int(message.get('severity', 0))
                    pub = self.get_or_create_publisher(Alert, f"{thing_id}/alerts")
                    pub.publish(alert_msg)
                    self.get_logger().info(
                    f"Published thing_id: {thing_id} : alert_msg: {alert_msg}"
                    )

            # Process relationships
            if 'relationships' in features:
                relationships = features['relationships'].get('properties', {})
                for rel_type, rel_data in relationships.items():
                    rel_msg = Relationship()
                    rel_msg.child_thing_id = thing_id
                    rel_msg.parent_thing_id = rel_data.get('target', '')
                    rel_msg.relationship_type = rel_type
                    pub = self.get_or_create_publisher(Relationship, f"{thing_id}/relationships")
                    pub.publish(rel_msg)
                    self.get_logger().info(
                    f"Published thing_id: {thing_id} : rel_msg: {rel_msg}"
                    )

            # Process machine status (for manufacturing)
            if 'status' in features:
                status = features['status'].get('properties', {})
                status_msg = MachineStatus()
                status_msg.machine_id = thing_id
                status_msg.status = status.get('value', '')
                status_msg.uptime = float(status.get('uptime', 0.0))
                status_msg.efficiency = float(status.get('efficiency', 0.0))
                pub = self.get_or_create_publisher(MachineStatus, f"{thing_id}/status")
                pub.publish(status_msg)
                self.get_logger().info(
                f"Published thing_id: {thing_id} : status_msg: {status_msg}"
                )
            
            if 'environment' in features:
                env = features['environment'].get('properties', {})
                env_msg = EnvironmentalData()
                env_msg.air_quality_index = float(env.get('aqi', 0.0))
                env_msg.noise_level = float(env.get('noise', 0.0))
                env_msg.light_intensity = float(env.get('light', 0.0))
                env_msg.co2_level = float(env.get('co2', 0.0))
                pub = self.get_or_create_publisher(EnvironmentalData, f"{thing_id}/sensor/environment")
                pub.publish(env_msg)
                self.get_logger().info(
                f"Published thing_id: {thing_id} : env_msg: {env_msg}"
                )

            # Process traffic data
            if 'traffic' in features:
                traffic = features['traffic'].get('properties', {})
                traffic_msg = TrafficData()
                traffic_msg.vehicle_count = int(traffic.get('count', 0))
                traffic_msg.average_speed = float(traffic.get('avg_speed', 0.0))
                traffic_msg.congestion_level = int(traffic.get('congestion', 0))
                pub = self.get_or_create_publisher(TrafficData, f"{thing_id}/traffic")
                pub.publish(traffic_msg)
                self.get_logger().info(
                f"Published thing_id: {thing_id} : traffic_msg: {traffic_msg}"
                )

            # Process crop data
            if 'crop' in features:
                crop = features['crop'].get('properties', {})
                crop_msg = CropData()
                crop_msg.crop_type = crop.get('type', '')
                crop_msg.soil_moisture = float(crop.get('moisture', 0.0))
                crop_msg.soil_ph = float(crop.get('ph', 0.0))
                crop_msg.growth_stage = float(crop.get('growth', 0.0))
                pub = self.get_or_create_publisher(CropData, f"{thing_id}/sensor/crop")
                pub.publish(crop_msg)
                self.get_logger().info(
                f"Published thing_id: {thing_id} : crop_msg: {crop_msg}"
                )

            # Process water management data
            if 'water' in features:
                water = features['water'].get('properties', {})
                water_msg = WaterManagement()
                water_msg.water_level = float(water.get('level', 0.0))
                water_msg.flow_rate = float(water.get('flow', 0.0))
                water_msg.turbidity = float(water.get('turbidity', 0.0))
                water_msg.valve_status = bool(water.get('valve_open', False))
                pub = self.get_or_create_publisher(WaterManagement, f"{thing_id}/sensor/water")
                pub.publish(water_msg)
                self.get_logger().info(
                f"Published thing_id: {thing_id} : water_msg: {water_msg}"
                )

            # Process energy consumption data
            if 'energy' in features:
                energy = features['energy'].get('properties', {})
                energy_msg = EnergyConsumption()
                energy_msg.total_consumption = float(energy.get('total', 0.0))
                energy_msg.renewable_percentage = float(energy.get('renewable', 0.0))
                energy_msg.grid_load = float(energy.get('grid_load', 0.0))
                pub = self.get_or_create_publisher(EnergyConsumption, f"{thing_id}/energy")
                pub.publish(energy_msg)
                self.get_logger().info(
                f"Published thing_id: {thing_id} : energy_msg: {energy_msg}"
                )

            # Process production line data
            if 'production' in features:
                prod = features['production'].get('properties', {})
                prod_msg = ProductionLine()
                prod_msg.line_id = thing_id
                prod_msg.units_produced = int(prod.get('units', 0))
                prod_msg.defect_count = int(prod.get('defects', 0))
                prod_msg.overall_equipment_effectiveness = float(prod.get('oee', 0.0))
                pub = self.get_or_create_publisher(ProductionLine, f"{thing_id}/production")
                pub.publish(prod_msg)
                self.get_logger().info(
                f"Published thing_id: {thing_id} : prod_msg: {prod_msg}"
                )

        except Exception as e:
            self.get_logger().error(f"Error processing thing: {str(e)}")
            if self.debug:
                self.get_logger().debug(f"Thing that caused error: {json.dumps(thing, indent=2)}")

def main(args=None):
    rclpy.init(args=args)
    bridge = DittoROS2Bridge()
    
    try:
        rclpy.spin(bridge)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()