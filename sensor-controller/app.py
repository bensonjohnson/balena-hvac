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
from threading import Lock
import atexit
import logging

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Locks for thread safety
sensor_data_lock = Lock()
mode_lock = Lock()
override_lock = Lock()
setpoint_lock = Lock()

# Shared system state
class SystemState:
    def __init__(self):
        self.sensor_data = {}
        self.selected_mode = 'average'
        self.selected_sensor_name = None
        self.manual_override = False
        self.setpoint_temp_f = 70.0

system_state = SystemState()

# GPIO control abstraction
class RelayController:
    def __init__(self, cooling_pin, heating_pin, fan_pin):
        self.cooling_pin = cooling_pin
        self.heating_pin = heating_pin
        self.fan_pin = fan_pin

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.cooling_pin, GPIO.OUT)
        GPIO.setup(self.heating_pin, GPIO.OUT)
        GPIO.setup(self.fan_pin, GPIO.OUT)
        time.sleep(0.1)
        GPIO.output(self.cooling_pin, GPIO.LOW)
        GPIO.output(self.heating_pin, GPIO.LOW)
        GPIO.output(self.fan_pin, GPIO.LOW)

    def set_cooling(self, state):
        GPIO.output(self.cooling_pin, state)

    def set_heating(self, state):
        GPIO.output(self.heating_pin, state)

    def set_fan(self, state):
        GPIO.output(self.fan_pin, state)

    def turn_off_all(self):
        GPIO.output(self.cooling_pin, GPIO.LOW)
        GPIO.output(self.heating_pin, GPIO.LOW)
        GPIO.output(self.fan_pin, GPIO.LOW)

# Initialize RelayController
relay_controller = RelayController(cooling_pin=18, heating_pin=23, fan_pin=24)

# Register GPIO cleanup function
def cleanup_gpio():
    GPIO.cleanup()

atexit.register(cleanup_gpio)

# Initialize Redis connection with error handling
try:
    redis_client = redis.StrictRedis(host='redis', port=6379, db=0)
    redis_client.ping()
except redis.ConnectionError as e:
    logger.error("Redis connection failed: %s", e)
    redis_client = None

# PID controller setup
pid = PID(0.5, 0.1, 0.01, setpoint=system_state.setpoint_temp_f)
pid.output_limits = (-10, 10)  # Adjusted output limits

# Function to update setpoint temperature
def update_setpoint(new_setpoint):
    with setpoint_lock:
        system_state.setpoint_temp_f = new_setpoint
        pid.setpoint = new_setpoint
        if redis_client:
            redis_client.set('set_temperature', new_setpoint)

# Function to store sensor data
def store_sensor_data(sensor_name, temperature, humidity):
    current_time = datetime.now()
    with sensor_data_lock:
        if sensor_name not in system_state.sensor_data:
            system_state.sensor_data[sensor_name] = []
        system_state.sensor_data[sensor_name].append({
            'timestamp': current_time,
            'temperature': temperature,
            'humidity': humidity
        })

        # Remove data older than 1 hour
        system_state.sensor_data[sensor_name] = [
            data for data in system_state.sensor_data[sensor_name]
            if data['timestamp'] > current_time - timedelta(hours=1)
        ]

# Initialize the I2C bus and sensor
i2c = busio.I2C(board.SCL, board.SDA)
sensor = adafruit_sht31d.SHT31D(i2c)

# Function to read sensor data
def read_sensor_data():
    while True:
        try:
            temperature_c = sensor.temperature
            humidity = sensor.relative_humidity
            temperature_f = round(temperature_c * (9.0 / 5.0) + 32, 2)
            store_sensor_data('internal_sensor', temperature_f, humidity)
            time.sleep(60)
        except Exception as e:
            logger.error("Error reading sensor data: %s", e)
            time.sleep(60)

# Start sensor reading thread
sensor_thread = threading.Thread(target=read_sensor_data)
sensor_thread.daemon = True
sensor_thread.start()

# Retrieve the set temperature from Redis or use default
if redis_client:
    try:
        set_temp = redis_client.get('set_temperature')
        if set_temp:
            update_setpoint(float(set_temp))
    except Exception as e:
        logger.error("Error retrieving set temperature from Redis: %s", e)
else:
    logger.info("Redis not available, using default setpoint temperature.")

# Flask routes
@app.route('/getstatus', methods=['GET'])
def get_status():
    with mode_lock:
        selected_mode = system_state.selected_mode
        selected_sensor_name = system_state.selected_sensor_name

    if selected_mode == 'average':
        average_temperature, average_humidity = get_sensor_data('average')
    else:
        average_temperature, average_humidity = get_sensor_data('specific', selected_sensor_name)

    if average_temperature is None:
        return jsonify({'error': 'No sensor data available for the selected mode'}), 404

    pid_value = pid(average_temperature)
    system_state_str = adjust_relays(pid_value, average_temperature)
    with setpoint_lock:
        setpoint_temp_f = system_state.setpoint_temp_f

    # Return only recent data
    with sensor_data_lock:
        recent_data = {sensor: readings[-1] for sensor, readings in system_state.sensor_data.items() if readings}

    return jsonify({
        'average_temperature': average_temperature,
        'average_humidity': average_humidity,
        'setTemperature': setpoint_temp_f,
        'pidValue': pid_value,
        'systemState': system_state_str,
        'Kp': pid.Kp,
        'Ki': pid.Ki,
        'Kd': pid.Kd,
        'sensorData': recent_data,
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
        new_setpoint = float(data['setpoint'])
        update_setpoint(new_setpoint)
        logger.info("PID parameters updated: Kp=%s, Ki=%s, Kd=%s, Setpoint=%s", pid.Kp, pid.Ki, pid.Kd, new_setpoint)
        return jsonify({'message': 'PID parameters updated successfully'}), 200
    except KeyError as e:
        logger.error("Missing key in PID update data: %s", e)
        return jsonify({'error': f'Missing key in data: {str(e)}'}), 400
    except ValueError as e:
        logger.error("Invalid value in PID update data: %s", e)
        return jsonify({'error': f'Invalid value for PID parameters: {str(e)}'}), 400
    except Exception as e:
        logger.error("Error updating PID parameters: %s", e)
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500

@app.route('/submit_sensor_data', methods=['POST'])
def submit_sensor_data():
    try:
        data = request.get_json()
        store_sensor_data(data['sensorname'], float(data['Temperature']), float(data['Humidity']))
        return jsonify({'message': 'Sensor data received'})
    except KeyError as e:
        logger.error("Missing key in sensor data: %s", e)
        return jsonify({'error': f'Missing key in data: {str(e)}'}), 400
    except ValueError as e:
        logger.error("Invalid value in sensor data: %s", e)
        return jsonify({'error': f'Invalid value for sensor data: {str(e)}'}), 400
    except Exception as e:
        logger.error("Error submitting sensor data: %s", e)
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500

@app.route('/set_mode', methods=['POST'])
def set_mode():
    data = request.get_json()
    new_mode = data.get('mode', 'average').lower()
    sensor_name = data.get('sensor_name')

    with mode_lock, sensor_data_lock:
        if new_mode == 'average':
            system_state.selected_mode = 'average'
            system_state.selected_sensor_name = None
            return jsonify({'message': 'Mode set to average'}), 200
        elif sensor_name in system_state.sensor_data:
            system_state.selected_mode = 'specific'
            system_state.selected_sensor_name = sensor_name
            return jsonify({'message': f'Mode set to follow sensor {sensor_name}'}), 200
        else:
            return jsonify({'error': 'Invalid mode or sensor name'}), 400

def get_sensor_data(mode, sensor_name=None):
    total_temp = total_hum = count = 0
    current_time = datetime.now()

    with sensor_data_lock:
        if mode == 'average':
            for readings in system_state.sensor_data.values():
                for data in readings:
                    if data['timestamp'] > current_time - timedelta(minutes=1):
                        total_temp += data['temperature']
                        total_hum += data['humidity']
                        count += 1
        else:
            if sensor_name in system_state.sensor_data:
                for data in system_state.sensor_data[sensor_name]:
                    if data['timestamp'] > current_time - timedelta(minutes=1):
                        total_temp += data['temperature']
                        total_hum += data['humidity']
                        count += 1

    if count == 0:
        return None, None
    return total_temp / count, total_hum / count

@app.route('/settemp', methods=['POST'])
def set_temp():
    try:
        data = request.get_json()
        new_temp = float(data['settemp'])
        update_setpoint(new_temp)
        if redis_client:
            redis_client.set('set_temperature', new_temp)
        logger.info("Set temperature updated to %s °F", new_temp)
        return jsonify({'message': f'Set temperature updated to {new_temp} °F'}), 200
    except KeyError as e:
        logger.error("Missing key in settemp data: %s", e)
        return jsonify({'error': f'Missing key in data: {str(e)}'}), 400
    except ValueError as e:
        logger.error("Invalid value in settemp data: %s", e)
        return jsonify({'error': f'Invalid value for set temperature: {str(e)}'}), 400
    except Exception as e:
        logger.error("Error setting temperature: %s", e)
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500

@app.route('/toggle_system', methods=['POST'])
def toggle_system():
    data = request.get_json()
    state = data.get('state', 'off').lower()

    with override_lock:
        if state == 'on':
            system_state.manual_override = False
            response_message = 'PID control reactivated'
        elif state == 'off':
            system_state.manual_override = True
            relay_controller.turn_off_all()
            response_message = 'System turned off, manual override activated'
        else:
            return jsonify({'error': 'Invalid state specified'}), 400

    logger.info("System toggled: %s", response_message)
    return jsonify({'message': response_message}), 200

@app.route('/manual_control', methods=['POST'])
def manual_control():
    data = request.get_json()
    mode = data.get('mode', 'off').lower()

    with override_lock:
        system_state.manual_override = True

    if mode == 'off':
        relay_controller.turn_off_all()
        response_message = 'All systems turned off'
    elif mode == 'cooling':
        relay_controller.set_cooling(GPIO.HIGH)
        relay_controller.set_heating(GPIO.LOW)
        relay_controller.set_fan(GPIO.HIGH)
        response_message = 'Cooling mode activated'
    elif mode == 'heating':
        relay_controller.set_cooling(GPIO.LOW)
        relay_controller.set_heating(GPIO.HIGH)
        relay_controller.set_fan(GPIO.HIGH)
        response_message = 'Heating mode activated'
    else:
        with override_lock:
            system_state.manual_override = False
        return jsonify({'error': 'Invalid mode specified'}), 400

    logger.info("Manual control: %s", response_message)
    return jsonify({'message': response_message}), 200

def adjust_relays(pid_output, average_temperature):
    with override_lock:
        manual_override = system_state.manual_override

    if manual_override:
        return "Manual override active"

    # Adjust control logic using pid_output directly
    if pid_output > 1:  # Threshold can be adjusted as needed
        # Need heating
        relay_controller.set_cooling(GPIO.LOW)
        relay_controller.set_heating(GPIO.HIGH)
        relay_controller.set_fan(GPIO.HIGH)
        return "Heating"
    elif pid_output < -1:
        # Need cooling
        relay_controller.set_cooling(GPIO.HIGH)
        relay_controller.set_heating(GPIO.LOW)
        relay_controller.set_fan(GPIO.HIGH)
        return "Cooling"
    else:
        # Within acceptable range, turn off
        relay_controller.set_cooling(GPIO.LOW)
        relay_controller.set_heating(GPIO.LOW)
        relay_controller.set_fan(GPIO.LOW)
        return "Holding"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
