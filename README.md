## Local Purple Air Integration
This is an integration for home assistant that works integrates with local
polling on your PurpleAir devices on your local network.

_Code adapted from https://github.com/johnboiles/home-assistant-purpleair_

It creates one device per Purple Air device registered and several sensors
for each several readings.

### Installation

#### HACS
Just add this repo https://github.com/gvillo/home-assistant-purpleair to `Custom Repositories` and install it

#### Manual
Simply copy the `/purpleair` directory in to your config's
`custom_components` directory (you may need to create it), restart Home
Assistant, and add the integration via the UI (it's simple!).

To register a new purple air device:
1. Add "Purple Air" Integration.
2. Enter the IP address of your local purple air device
3. Give it a name.

#### Current Sensors
This will create 12 (or more) entities per device:
* Particulate Matter 1.0
* Particulate Matter 2.5
* Particulate Matter 10
* Air Quality Index (EPA)
* Air Quality Index (LRAPA)
* Humidity (Adjusted sensor: +4%)
* Temperature in Farenheit (Adjusted sensor: -8F)
* Temperature in Celsius (Adjusted)
* Dewpoint in Farenheit (Adjusted sensor: re-calculated to take temp & humidity adjustments)
* Dewpoint in Celsius (Adjusted)
* Pressure
* RSSI
* VOC
* Particulate Matter 2.5 Aqi Raw value (sensor A)
* Particulate Matter 2.5 Aqi Raw value (sensor B -- only for devices that have two)
* PM 2.5 Confidence Level (Good, Questionable or Severe).

Sensor data on PurpleAir is updated every 60 seconds.

##### Adjusted Sensors
In a similar manner to the actual purple air website, some sensors are adjusted manually to take into
account the fact that the housing itself increases the temperature and has reduced humidity. Calculated
sensors are marked above.

This component is licensed under the MIT license, so feel free to copy,
enhance, and redistribute as you see fit.
