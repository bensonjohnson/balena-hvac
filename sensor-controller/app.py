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


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Sensor data storage
sensor_data = {}

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

# PID setup
setpointTempF = 68.0  # Default setpoint
pid = PID(0.5, 0.1, 0.01, setpoint=setpointTempF)
pid.output_limits = (0, 1) 

@app.route('/getstatus', methods=['GET'])
def get_status():
    average_temperature, average_humidity = get_average_sensor_data()
    if average_temperature is None:
        return jsonify({'error': 'No sensor data available'}), 404

    pid_value = pid(average_temperature)
    system_state = adjust_relays(pid_value, average_temperature)
    return jsonify({
        'average_temperature': average_temperature,
        'average_humidity': average_humidity,
        'setTemperature': setpointTempF,
        'pidValue': pid_value,
        'systemState': system_state,
        'Kp': pid.Kp,
        'Ki': pid.Ki,
        'Kd': pid.Kd,
        'sensorData': sensor_data
    })

@app.route('/pid', methods=['POST'])
def update_pid():
    try:
        data = request.get_json()
        pid.Kp = float(data['Kp'])
        pid.Ki = float(data['Ki'])
        pid.Kd = float(data['Kd'])
        pid.setpoint = float(data['setpoint'])
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
    
def get_average_sensor_data():
    total_temp = total_hum = count = 0
    current_time = datetime.now()
    for readings in sensor_data.values():
        for data in readings:
            if data['timestamp'] > current_time - timedelta(minutes=3):
                total_temp += data['temperature']
                total_hum += data['humidity']
                count += 1
    if count == 0:
        return None, None
    return total_temp / count, total_hum / count 


# Manual override flag
manual_override = False

@app.route('/toggle_system', methods=['POST'])
def toggle_system():
    global manual_override
    data = request.get_json()
    state = data.get('state', 'off').lower()

    if state == 'on':
        manual_override = False
        response_message = 'PID control reactivated'
    elif state == 'off':
        manual_override = True
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.LOW)
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
        response_message = 'All systems turned off'
    elif mode == 'cooling':
        GPIO.output(coolingRelayPin, GPIO.HIGH)
        GPIO.output(heatingRelayPin, GPIO.LOW)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        response_message = 'Cooling mode activated'
    elif mode == 'heating':
        GPIO.output(coolingRelayPin, GPIO.LOW)
        GPIO.output(heatingRelayPin, GPIO.HIGH)
        GPIO.output(fanRelayPin, GPIO.HIGH)
        response_message = 'Heating mode activated'
    else:
        manual_override = False
        return jsonify({'error': 'Invalid mode specified'}), 400
    return jsonify({'message': response_message}), 200

def adjust_relays(pid_output, average_temperature):
    global manual_override
    if manual_override:
        return "Manual override active"
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
