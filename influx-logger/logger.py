import os
import time
import logging
import requests
from datetime import datetime
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.exceptions import InfluxDBError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Read and validate environment variables
api_url = os.getenv('API_URL', 'http://localhost:5000/getstatus')
influx_url = os.getenv('INFLUXDB_URL')
influx_token = os.getenv('INFLUXDB_TOKEN')
influx_org = os.getenv('INFLUXDB_ORG')
influx_bucket = os.getenv('INFLUXDB_BUCKET')
location = os.getenv('LOCATION', 'thermostat')
use_influxdb = os.getenv('USE_INFLUXDB', 'false').lower() == 'true'

# Validate essential environment variables
if use_influxdb and (not influx_url or not influx_token or not influx_org or not influx_bucket):
    logger.error("InfluxDB is enabled, but one or more InfluxDB environment variables are missing.")
    exit(1)

# Set up InfluxDB connection if use_influxdb is True
if use_influxdb:
    client = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
    write_api = client.write_api(write_options=WritePrecision.S)
else:
    client = None
    write_api = None

def convert_timestamp(timestamp_str):
    try:
        # Try parsing ISO format first
        dt = datetime.fromisoformat(timestamp_str)
    except ValueError:
        # If that fails, try a different format (adjust as needed)
        dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
    return dt

def log_data():
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch data from API: %s", e)
        return

    try:
        data = response.json()
    except ValueError as e:
        logger.error("Failed to parse JSON response: %s", e)
        return

    if use_influxdb:
        points = []
        try:
            # Create points for average data
            climate_point = Point("climate") \
                .tag("location", location) \
                .field("temperature", float(data['average_temperature'])) \
                .field("humidity", float(data['average_humidity'])) \
                .field("state", data['systemState']) \
                .field("pidValue", float(data.get('pidValue', 0))) \
                .time(datetime.utcnow(), WritePrecision.S)
            points.append(climate_point)

            # Create point for PID data
            pid_point = Point("PID") \
                .tag("location", location) \
                .field("setpoint", float(data['setTemperature'])) \
                .field("pidValue", float(data['pidValue'])) \
                .field("state", data['systemState']) \
                .field("Kp", float(data['Kp'])) \
                .field("Ki", float(data['Ki'])) \
                .field("Kd", float(data['Kd'])) \
                .time(datetime.utcnow(), WritePrecision.S)
            points.append(pid_point)

            # Create points for each individual sensor
            sensor_data = data.get('sensorData', {})
            for sensor, reading in sensor_data.items():
                timestamp = convert_timestamp(reading['timestamp'])
                sensor_point = Point("sensor_data") \
                    .tag("location", location) \
                    .tag("sensor", sensor) \
                    .field("temperature", float(reading['temperature'])) \
                    .field("humidity", float(reading['humidity'])) \
                    .time(timestamp, WritePrecision.S)
                points.append(sensor_point)

            # Write all points as a batch
            write_api.write(bucket=influx_bucket, org=influx_org, record=points)
            logger.info("Data written to InfluxDB successfully.")
        except (KeyError, ValueError, InfluxDBError) as e:
            logger.error("Error processing or writing data to InfluxDB: %s", e)
    else:
        logger.info("InfluxDB logging is disabled. Data: %s", data)

if __name__ == '__main__':
    try:
        while True:
            log_data()
            time.sleep(60)  # Log every 60 seconds
    except KeyboardInterrupt:
        logger.info("Script terminated by user.")
    finally:
        if client:
            client.close()
            logger.info("InfluxDB client closed.")
