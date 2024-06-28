#include <Wire.h>
#include <Adafruit_SHT31.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoOTA.h>

// WiFi credentials
const char* ssid = "Posix";
const char* password = "RickSanchez";

// Server URL
const char* serverUrl = "http://10.0.0.36/api/submit_sensor_data";
const char* sensorName = "office";

// Initialize the SHT31 sensor
Adafruit_SHT31 sht31 = Adafruit_SHT31();

void setup() {
  Serial.begin(115200);
  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("WiFi connected");
  Serial.println("IP address: ");
  Serial.println(WiFi.localIP());

  if (!sht31.begin(0x44)) {   // Set to 0x45 depending on your wiring
    Serial.println("Couldn't find SHT31");
    while (1) delay(1);
  }

  // Initialize OTA
  ArduinoOTA.onStart([]() {
    String type;
    if (ArduinoOTA.getCommand() == U_FLASH) {
      type = "sketch";
    } else { // U_SPIFFS
      type = "filesystem";
    }
    Serial.println("Start updating " + type);
  });

  ArduinoOTA.onEnd([]() {
    Serial.println("\nEnd");
  });

  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    Serial.printf("Progress: %u%%\r", (progress / (total / 100)));
  });

  ArduinoOTA.onError([](ota_error_t error) {
    Serial.printf("Error[%u]: ", error);
    if (error == OTA_AUTH_ERROR) {
      Serial.println("Auth Failed");
    } else if (error == OTA_BEGIN_ERROR) {
      Serial.println("Begin Failed");
    } else if (error == OTA_CONNECT_ERROR) {
      Serial.println("Connect Failed");
    } else if (error == OTA_RECEIVE_ERROR) {
      Serial.println("Receive Failed");
    } else if (error == OTA_END_ERROR) {
      Serial.println("End Failed");
    }
  });

  ArduinoOTA.begin();
}

void loop() {
  ArduinoOTA.handle();  // Handle OTA updates

  float t = sht31.readTemperature();
  float h = sht31.readHumidity();

  if (!isnan(t)) {  // check if 'isnan' returned true
    Serial.print("Temp *C = "); Serial.println(t);
    Serial.print("Hum. % = "); Serial.println(h);
  } else {
    Serial.println("Failed to read temperature or humidity");
  }

  float temperatureF = t * 9.0 / 5.0 + 32;  // Convert celsius to fahrenheit

  if(WiFi.status() == WL_CONNECTED){
    HTTPClient http;
    http.begin(serverUrl);
    http.addHeader("Content-Type", "application/json");
    
    String httpRequestData = String("{\"sensorname\":\"") + sensorName + "\",\"Temperature\":" + String(temperatureF) + ",\"Humidity\":" + String(h) + "}";
    int httpResponseCode = http.POST(httpRequestData);

    if (httpResponseCode>0) {
      String response = http.getString();
      Serial.println("Code: " + String(httpResponseCode)); //Print return code
      Serial.println("Response: " + response); //Print request answer
    } else {
      Serial.print("Error on sending POST: ");
      Serial.println(httpResponseCode);
    }

    http.end();
  }

  delay(15000);  // Delay between readings
}
