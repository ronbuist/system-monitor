# system-monitor
Simple Raspberry Pi system monitor that publishes MQTT messages

To monitor my Raspberry Pi machine, I wanted a simple script that would send data through MQTT messages so I could receive those in Home Assistant. I looked around and found a few, but I ended up creating my own monitor. It monitors the following:

* CPU usage
* CPU temperature
* Memory usage
* Disk usage
* Network stats
* Fan status (for Raspberry Pi 4)

The script is capable of sending MQTT autodiscover topics, which automatically sets up the sensors in Home Assistant. The topics it uses to publish the status messages contains the hostname of the Raspberry Pi machine, so that every machine will have its own set of topics.

It requires the following libraries:

* json
* psutil
* paho-mqtt
* yaml

The libraries can either be installed through pip, or (in case of the standard Python 3 installation in Raspberry Pi OS), through apt. In the latter case, use commands like `sudo apt-get install python3-psutil` to install a library.

