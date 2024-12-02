from flask import Flask, request, jsonify
from flask_cors import CORS
import RPi.GPIO as GPIO
from simple_pid import PID
import time
import board
import busio
import adafruit_sht31d
from datetime import datetime, timedelta
import threading
import redis

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Sensor data storage
sensor_data = {}

# Mode can be 'average' or a specific sensor name
selected_mode = 'average'
selected_sensor_name = None

# Initialize Redis connection
redis_client = redis.StrictRedis(host='redis', port=6379, db=0)

# GPIO Pins (Use BCM numbering)
coolingRelayPin = 18  # purple
heatingRelayPin = 23  # blue
fanRelayPin = 24      # green

# Setup GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(coolingRelayPin, GPIO.OUT)
GPIO.setup(heatingRelayPin, GPIO.OUT)
GPIO.setup(fanRelayPin, GPIO.OUT)
time.sleep(0.1)
GPIO.output(coolingRelayPin, GPIO.LOW)
GPIO.output(heatingRelayPin, GPIO.LOW)
GPIO.output(fanRelayPin, GPIO.LOW)

# Initialize the I2C bus and sensor
i2c = busio.I2C(board.SCL, board.SDA)
sensor = adafruit_sht31d.SHT31D(i2c)

def store_sensor_data(sensor_name, temperature, humidity):
    current_time = datetime.now()
    if sensor_name not in sensor_data:
        sensor_data[sensor_name] = []
    sensor_data[sensor_name].append({
        'timestamp': current_time,
        'temperature': temperature,
        'humidity': humidity
    })

    # Remove data older than 1 hour
    sensor_data[sensor_name] = [
        data for data in sensor_data[sensor_name]
        if data['timestamp'] > current_time - timedelta(hours=1)
    ]

def read_sensor_data():
    while True:
        temperature_c = sensor.temperature
        humidity = sensor.relative_humidity
        temperature_f = round(temperature_c * (9.0 / 5.0) + 32, 2)
        store_sensor_data('internal_sensor', temperature_f, humidity)
        time.sleep(60)

sensor_thread = threading.Thread(target=read_sensor_data)
sensor_thread.daemon = True
sensor_thread.start()

# Retrieve the set temperature from Redis or use default
setpointTempF = float(redis_client.get('set_temperature') or 70.0)

# PID setup
pid = PID(0.5, 0.1, 0.01, setpoint=setpointTempF)
pid.output_limits = (0, 1)

# Initialize PID control state
pid_enabled = True  # Assume PID control is enabled by default

# Retrieve PID control state from Redis if it exists
pid_enabled_redis = redis_client.get('pid_enabled')
if pid_enabled_redis is not None:
    pid_enabled = pid_enabled_redis.decode('utf-8') == 'True'

# Ensure relays are set appropriately at startup
if not pid_enabled:
    GPIO.output(coolingRelayPin, GPIO.LOW)
    GPIO.output(heatingRelayPin, GPIO.LOW)
    GPIO.output(fanRelayPin, GPIO.LOW)

@app.route('/getstatus', methods=['GET'])
def get_status():
    try:
        if selected_mode == 'average':
            average_temperature, average_humidity = get_sensor_data('average')
        else:
            average_temperature, average_humidity = get_sensor_data('specific', selected_sensor_name)

        pid_value = pid(average_temperature) if average_temperature is not None else "N/A"

        # Retrieve the stored system state from Redis
        system_state = redis_client.get('system_state')
        if system_state:
            system_state = system_state.decode('utf-8')
        else:
            system_state = "Off"  # Default to "Off" if no state is stored

        return jsonify({
            'average_temperature': average_temperature,
            'average_humidity': average_humidity,
            'setTemperature': setpointTempF,
            'pidValue': pid_value,
            'systemState': system_state,
            'Kp': pid.Kp,
            'Ki': pid.Ki,
            'Kd': pid.Kd,
            'sensorData': sensor_data,
            'selectedMode': selected_mode,
            'selectedSensor': selected_sensor_name,
            'pidEnabled': pid_enabled
        })
    except Exception as e:
        print(f"Error in /getstatus: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/pid', methods=['POST'])
def update_pid():
    try:
        data = request.get_json()
        pid.Kp = float(data['Kp'])
        pid.Ki = float(data['Ki'])
        pid.Kd = float(data['Kd'])
        pid.setpoint = float(data['setpoint'])
        redis_client.set('set_temperature', pid.setpoint)
        return jsonify({'message': 'PID parameters updated successfully'}), 200
    except KeyError as e:
        return jsonify({'error': f'Missing key in data: {str(e)}'}), 400
    except ValueError as e:
        return jsonify({'error': f'Invalid value for PID parameters: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500

@app.route('/submit_sensor_data', methods=['POST'])
def submit_sensor_data():
    try:
        data = request.get_json()
        store_sensor_data(data['sensorname'], float(data['Temperature']), float(data['Humidity']))
        return jsonify({'message': 'Sensor data received'})
    except KeyError as e:
        return jsonify({'error': f'Missing key in data: {str(e)}'}), 400
    except ValueError as e:
        return jsonify({'error': f'Invalid value for sensor data: {str(e)}'}), 400

@app.route('/set_mode', methods=['POST'])
def set_mode():
    global selected_mode, selected_sensor_name
    data = request.get_json()
    new_mode = data.get('mode', 'average').lower()
    sensor_name = data.get('sensor_name')

    if new_mode == 'average':
        selected_mode = 'average'
        selected_sensor_name = None
        return jsonify({'message': 'Mode set to average'}), 200
    elif sensor_name in sensor_data:
        selected_mode = 'specific'
        selected_sensor_name = sensor_name
        return jsonify({'message': f'Mode set to follow sensor {sensor_name}'}), 200
    else:
        return jsonify({'error': 'Invalid mode or sensor name'}), 400

def get_sensor_data(mode, sensor_name=None):
    total_temp = total_hum = count = 0
    current_time = datetime.now()

    if mode == 'average':
        for readings in sensor_data.values():
            for data in readings:
                if data['timestamp'] > current_time - timedelta(minutes=1):
                    total_temp += data['temperature']
                    total_hum += data['humidity']
                    count += 1
    else:
        if sensor_name in sensor_data:
            for data in sensor_data[sensor_name]:
                if data['timestamp'] > current_time - timedelta(minutes=1):
                    total_temp += data['temperature']
                    total_hum += data['humidity']
                    count += 1

    if count == 0:
        return None, None
    return total_temp / count, total_hum / count

@app.route('/settemp', methods=['POST'])
def set_temp():
    global setpointTempF
    new_temp = float(request.form['settemp'])
    setpointTempF = new_temp
    pid.setpoint = new_temp
    redis_client.set('set_temperature', new_temp)
    return jsonify({'message': 'Set temperature updated to {} °F'.format(new_temp)})

@app.route('/toggle_system', methods=['POST'])
def toggle_system():
    global pid_enabled
    data = request.get_json()
    state = data.get('state', 'off').lower()

    if state == 'on':
        pid_enabled = True
        redis_client.set('pid_enabled', 'True')
        redis_client.set('system_state', 'PID Control')
        response_message = 'PID control activated'
    elif state == 'off':
        pid_enabled = False
        redis_client.set('pid_enabled', 'False')
        # Turn off all relays when PID is disabled
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        redis_client.set('system_state', 'System Off')
        response_message = 'System turned off'
    else:
        return jsonify({'error': 'Invalid state specified'}), 400

    return jsonify({'message': response_message}), 200

def adjust_relays(pid_output, average_temperature):
    global pid_enabled
    if not pid_enabled:
        # PID control is disabled; do not adjust relays
        return "System Off"

    # Automatic control (PID active)
    if average_temperature < setpointTempF - pid_output:
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.HIGH)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        redis_client.set('system_state', 'Heating')
        return "Heating"
    elif average_temperature > setpointTempF + pid_output:
        GPIO.output(coolingRelayPin, GPIO.HIGH)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        redis_client.set('system_state', 'Cooling')
        return "Cooling"
    else:
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        redis_client.set('system_state', 'Idle')
        return "Idle"

def control_loop():
    while True:
        if selected_mode == 'average':
            average_temperature, _ = get_sensor_data('average')
        else:
            average_temperature, _ = get_sensor_data('specific', selected_sensor_name)

        if average_temperature is not None:
            pid_value = pid(average_temperature)
            adjust_relays(pid_value, average_temperature)
        else:
            # No valid temperature data; turn off all relays
            GPIO.output(coolingRelayPin, GPIO.LOW)
            GPIO.output(heatingRelayPin, GPIO.LOW)
            GPIO.output(fanRelayPin, GPIO.LOW)
            redis_client.set('system_state', 'No Data')

        time.sleep(5)  # Adjust the sleep time as needed

control_thread = threading.Thread(target=control_loop)
control_thread.daemon = True
control_thread.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
