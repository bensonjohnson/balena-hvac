from flask import Flask, request, jsonify
import RPi.GPIO as GPIO
from simple_pid import PID
import time
import board
import busio
import adafruit_sht31d
from flask_cors import CORS
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
    sensor_data[sensor_name] = [data for data in sensor_data[sensor_name] if data['timestamp'] > current_time - timedelta(hours=1)]

# Initialize Redis connection
redis_client = redis.StrictRedis(host='redis', port=6379, db=0)

# GPIO Pins (Use BCM numbering)
coolingRelayPin = 18 #purple
heatingRelayPin = 23 #blue
fanRelayPin = 24 #green

# Setup GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(coolingRelayPin, GPIO.OUT)
GPIO.setup(heatingRelayPin, GPIO.OUT)
GPIO.setup(fanRelayPin, GPIO.OUT)
time.sleep(0.1)
GPIO.output(coolingRelayPin, GPIO.LOW)
GPIO.output(heatingRelayPin, GPIO.LOW)
GPIO.output(fanRelayPin, GPIO.LOW)

# Initialize the I2C bus
i2c = busio.I2C(board.SCL, board.SDA)
# Initialize the SHT31D sensor
sensor = adafruit_sht31d.SHT31D(i2c)

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

@app.route('/getstatus', methods=['GET'])
def get_status():
    if selected_mode == 'average':
        average_temperature, average_humidity = get_sensor_data('average')
    else:
        average_temperature, average_humidity = get_sensor_data('specific', selected_sensor_name)

    if average_temperature is None:
        return jsonify({'error': 'No sensor data available for the selected mode'}), 404

    pid_value = pid(average_temperature)
    system_state = "off" if manual_override else "on"
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
        'selectedSensor': selected_sensor_name
    })


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

# Manual override flag
manual_override = False

# Retrieve the state from Redis or default to "off"
system_state = redis_client.get('system_state')
if system_state:
    manual_override = (system_state.decode('utf-8').lower() == 'off')
else:
    manual_override = True

@app.route('/toggle_system', methods=['POST'])
def toggle_system():
    global manual_override
    data = request.get_json()
    state = data.get('state', 'off').lower()

    if state == 'on':
        manual_override = False
        redis_client.set('system_state', 'on')
        response_message = 'PID control reactivated'
    elif state == 'off':
        manual_override = True
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        redis_client.set('system_state', 'off')
        response_message = 'System turned off, manual override activated'
    else:
        return jsonify({'error': 'Invalid state specified'}), 400

    return jsonify({'message': response_message}), 200

@app.route('/off', methods=['POST'])
def turn_off():
    global manual_override
    manual_override = True
    GPIO.output(coolingRelayPin, GPIO.LOW)
    GPIO.output(heatingRelayPin, GPIO.LOW)
    GPIO.output(fanRelayPin, GPIO.LOW)
    redis_client.set('system_state', 'off')
    return jsonify({'message': 'All systems turned off'}), 200

@app.route('/manual_control', methods=['POST'])
def manual_control():
    global manual_override
    data = request.get_json()
    mode = data.get('mode', 'off').lower()
    manual_override = True
    if mode == 'off':
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        redis_client.set('system_state', 'off')
        response_message = 'All systems turned off'
    elif mode == 'cooling':
        GPIO.output(coolingRelayPin, GPIO.HIGH)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        redis_client.set('system_state', 'cooling')
        response_message = 'Cooling mode activated'
    elif mode == 'heating':
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.HIGH)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        redis_client.set('system_state', 'heating')
        response_message = 'Heating mode activated'
    else:
        manual_override = False
        return jsonify({'error': 'Invalid mode specified'}), 400
    return jsonify({'message': response_message}), 200

def is_summer():
    current_month = datetime.now().month
    # summer is June, July, and August
    return current_month in [6, 7, 8]

def is_winter():
    current_month = datetime.now().month
    # winter is Dec., Jan, Feb
    return current_month in [12, 1, 2]

def adjust_relays(pid_output, average_temperature):
    global manual_override
    if manual_override:
        return "Manual override active"

    # If it's summer or winter and its ourside the threshold, do nothing
    if is_summer() and average_temperature < setpointTempF:
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        return "Summer mode: No heating"
    if is_winter() and average_temperature > setpointTempF:
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        return "winter mode: No Cooling"

    if average_temperature < setpointTempF - pid_output:
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.HIGH)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        return "Heating"
    elif average_temperature > setpointTempF + pid_output:
        GPIO.output(coolingRelayPin, GPIO.HIGH)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        return "Cooling"
    else:
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
        return "Off"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)