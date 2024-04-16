from influxdb_client import InfluxDBClient, Point
import requests
import time
import os

# Read environment variables
api_url = os.getenv('API_URL', 'http://localhost:5000/getstatus')
influx_url = os.getenv('INFLUXDB_URL', 'http://localhost:8086')
influx_token = os.getenv('INFLUXDB_TOKEN', 'YourInfluxDBTokenHere')
influx_org = os.getenv('INFLUXDB_ORG', 'YourOrgName')
influx_bucket = os.getenv('INFLUXDB_BUCKET', 'YourBucketName')
location = os.getenv('LOCATION', 'thermostat')
use_influxdb = os.getenv('USE_INFLUXDB', 'false').lower() == 'true'



# Set up InfluxDB connection only if use_influxdb is True
if use_influxdb:
    client = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org, verify_ssl=False)
    write_api = client.write_api()

def log_data():
    response = requests.get(api_url)
    if response.status_code == 200:
        data = response.json()
        if use_influxdb:
            point = Point("climate").tag("location", location).field("temperature", float(data['temperature'])).field("humidity", float(data['humidity'])).field("state", data['systemState'])
            write_api.write(bucket=influx_bucket, org=influx_org, record=point)
        else:
            print("InfluxDB logging is disabled. Data:", data)
    else:
        print("Failed to fetch data from API. Status code:", response.status_code)

if __name__ == '__main__':
    while True:
        log_data()
        time.sleep(60)  # Log every 60 seconds