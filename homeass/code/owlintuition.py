"""
Support for OWL Intuition Network OWL.
For more details about this platform, please refer to the documentation at
https://github.com/glpatcern/domotica/blob/master/homeass/code/sensor.owlintuition.markdown
"""

import asyncio
import socket
from xml.etree import ElementTree as ET
from datetime import timedelta
from select import select
from functools import reduce

import logging
import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (CONF_NAME, CONF_PORT,
    CONF_MONITORED_CONDITIONS, CONF_MODE, CONF_HOST)
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.entity import Entity
import homeassistant.helpers.config_validation as cv
from homeassistant.util import Throttle

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = 'OWL Intuition'
MODE_MONO = 'monophase'
MODE_TRI = 'triphase'

SENSOR_ELECTRICITY_BATTERY = 'battery'
SENSOR_ELECTRICITY_RADIO = 'radio'
SENSOR_ELECTRICITY_POWER = 'power'
SENSOR_ELECTRICITY_ENERGY_TODAY = 'energy_today'
SENSOR_SOLAR_GPOWER = 'solargen'
SENSOR_SOLAR_GENERGY_TODAY = 'solargen_today'
SENSOR_SOLAR_EPOWER = 'solarexp'
SENSOR_SOLAR_EENERGY_TODAY = 'solarexp_today'
SENSOR_HOTWATER_BATTERY = 'hotwater_battery'
SENSOR_HOTWATER_RADIO = 'hotwater_radio'
SENSOR_HOTWATER_CURRENT = 'hotwater_current'
SENSOR_HOTWATER_REQUIRED = 'hotwater_required'
SENSOR_HOTWATER_AMBIENT = 'hotwater_ambient'
SENSOR_HOTWATER_STATE = 'hotwater_state'
SENSOR_HEATING_BATTERY = 'heating_battery'
SENSOR_HEATING_RADIO = 'heating_radio'
SENSOR_HEATING_CURRENT = 'heating_current'
SENSOR_HEATING_REQUIRED = 'heating_required'
SENSOR_HEATING_STATE = 'heating_state'
SENSOR_RELAYS_RADIO = 'relays_radio'

#
# Sensors selected by defining which classes to monitor
#
OWLCLASS_WEATHER = 'weather'
OWLCLASS_ELECTRICITY = 'electricity'
OWLCLASS_SOLAR = 'solar'
OWLCLASS_HOTWATER = 'hot_water'
OWLCLASS_HEATING = 'heating'
OWLCLASS_RELAYS = 'relays'

OWL_CLASSES = [ OWLCLASS_WEATHER, OWLCLASS_ELECTRICITY, OWLCLASS_SOLAR,
                OWLCLASS_HOTWATER, OWLCLASS_HEATING, OWLCLASS_RELAYS ]

BATTERY_SENSORS = [ SENSOR_ELECTRICITY_BATTERY, SENSOR_HOTWATER_BATTERY, SENSOR_HEATING_BATTERY ]
RADIO_SENSORS = [ SENSOR_ELECTRICITY_RADIO, SENSOR_HOTWATER_RADIO, SENSOR_HEATING_RADIO, SENSOR_RELAYS_RADIO ]

SENSOR_TYPES = {
    SENSOR_ELECTRICITY_BATTERY: ['Electricity Battery', None, 'mdi:battery', OWLCLASS_ELECTRICITY],
    SENSOR_ELECTRICITY_RADIO: ['Electricity Radio', 'dBm', 'mdi:signal', OWLCLASS_ELECTRICITY],
    SENSOR_ELECTRICITY_POWER: ['Electricity Power', 'W', 'mdi:flash', OWLCLASS_ELECTRICITY],
    SENSOR_ELECTRICITY_ENERGY_TODAY: ['Electricity Today', 'kWh', 'mdi:flash', OWLCLASS_ELECTRICITY],
    SENSOR_SOLAR_GPOWER: ['Solar Generating', 'W', 'mdi:flash', OWLCLASS_SOLAR],
    SENSOR_SOLAR_GENERGY_TODAY: ['Solar Generated Today', 'kWh', 'mdi:flash', OWLCLASS_SOLAR],
    SENSOR_SOLAR_EPOWER: ['Solar Exporting', 'W', 'mdi:flash', OWLCLASS_SOLAR],
    SENSOR_SOLAR_EENERGY_TODAY: ['Solar Exported Today', 'kWh', 'mdi:flash', OWLCLASS_SOLAR],
    SENSOR_HOTWATER_BATTERY: ['Hotwater Battery', None, 'mdi:battery', OWLCLASS_HOTWATER],
    SENSOR_HOTWATER_RADIO: ['Hotwater Radio', 'dBm', 'mdi:signal', OWLCLASS_HOTWATER],
    SENSOR_HOTWATER_CURRENT: ['Hotwater Temperature', '°C', 'mdi:thermometer', OWLCLASS_HOTWATER],
    SENSOR_HOTWATER_REQUIRED: ['Hotwater Required', '°C', 'mdi:thermostat', OWLCLASS_HOTWATER],
    SENSOR_HOTWATER_AMBIENT: ['Hotwater Ambient', '°C', 'mdi:thermometer', OWLCLASS_HOTWATER],
    SENSOR_HOTWATER_STATE: ['Hotwater State', '', 'mdi:information-outline', OWLCLASS_HOTWATER],
    SENSOR_HEATING_BATTERY: ['Heating Battery', None, 'mdi:battery', OWLCLASS_HEATING],
    SENSOR_HEATING_RADIO: ['Heating Radio', 'dBm', 'mdi:signal', OWLCLASS_HEATING],
    SENSOR_HEATING_CURRENT: ['Heating Temperature', '°C', 'mdi:thermometer', OWLCLASS_HEATING],
    SENSOR_HEATING_REQUIRED: ['Heating Required', '°C', 'mdi:thermostat', OWLCLASS_HEATING],
    SENSOR_HEATING_STATE: ['Heating State', None, 'mdi:information-outline', OWLCLASS_HEATING],
    SENSOR_RELAYS_RADIO: ['Relays Radio', 'dBm', 'mdi:signal', OWLCLASS_RELAYS],
}

HEATING_STATE = [   'Standby',                      # 0
                    'Comfort (Runing)',             # 1
                    '',                             # 2
                    '',                             # 3
                    'Comfort (Up To Temperature)',  # 4
                    'Comfort (Warm Up)',            # 5
                    'Comfort (Cool Down)',          # 6
                    'Standby (Running)']            # 7
HOTWATER_STATE = [  'Standby',                      # 0
                    'Running',                      # 1
                    '',                             # 2
                    '',                             # 3
                    'Up To Temperature',            # 4
                    'Warm Up',                      # 5
                    'Cool Down',                    # 6
                    'Standby (Running)' ]           # 7

DEFAULT_MONITORED = [ OWLCLASS_ELECTRICITY ]

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_PORT): cv.port,
    vol.Optional(CONF_HOST, default='localhost'): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_MODE, default=MODE_MONO):
        vol.In([MODE_MONO, MODE_TRI]),
    vol.Optional(CONF_MONITORED_CONDITIONS, default=DEFAULT_MONITORED):
        vol.All(cv.ensure_list, [vol.In(OWL_CLASSES)])
})

SOCK_TIMEOUT = 60


@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the OWL Intuition Sensors."""
    hostname = config.get(CONF_HOST)
    if hostname == 'localhost':
        # Perform a reverse lookup to make sure we listen to the correct IP
        hostname = socket.gethostbyname(socket.getfqdn())
    # Try and estimate an appropriate refresh interval: if only the electricity
    # module is present, 60 seconds is OK, otherwise double the rate.
    # All of this won't be needed with an async listener...
    # 
    # Need to reconsider this with new number of sensors? Set to 20 secs?
    #
    #nbclasses = len(reduce(lambda c, s: c | {SENSOR_TYPES[s][3]}, \
    #                       config.get(CONF_MONITORED_CONDITIONS), set()))
    #secs = (60/nbclasses - 2)
    secs = 20
    owldata = OwlData((hostname, config.get(CONF_PORT)), \
                      timedelta(seconds=(secs)))

    # Ideally an async listener loop as follows would be a better solution,
    # but it crashes HA!
    #owljob = hass.loop.create_datagram_endpoint(
    #             lambda: OwlStateUpdater(hass.loop), \
    #             local_addr=(hostname, config.get(CONF_PORT)))
    #hass.async_add_job(owljob)

    dev = []
    # Iterate through the possible sensors and add if class is monitored
    for sensor in SENSOR_TYPES:
        if SENSOR_TYPES[sensor][3] in config.get(CONF_MONITORED_CONDITIONS):
            dev.append(OwlIntuitionSensor(owldata, config.get(CONF_NAME), sensor))
            _LOGGER.debug("Adding sensor %s",sensor)
    
    # Handle 3 phase sensors
    if config.get(CONF_MODE) == MODE_TRI:
        if OWLCLASS_ELECTRICITY in config.get(CONF_MONITORED_CONDITIONS):
            for phase in range(1, 4):
                dev.append(OwlIntuitionSensor(owldata, config.get(CONF_NAME),
                                              SENSOR_ELECTRICITY_POWER, phase))
        if OWLCLASS_SOLAR in config.get(CONF_MONITORED_CONDITIONS):
            for phase in range(1, 4):
                dev.append(OwlIntuitionSensor(owldata, config.get(CONF_NAME),
                                              SENSOR_SOLAR_EENERGY_TODAY, phase))
    async_add_devices(dev, True)


class OwlData:
    """A class to retrieve data from the OWL station via UDP.
    The callback method can be used by an event loop in a thread-safe way
    when new data is received. However, async loops crash HA!
    The update() method is instead fully synchronous.
    """

    def __init__(self, localaddr, refreshinterval):
        """Prepare an empty dictionary"""
        self.data = {}
        self._localaddr = localaddr
        # Updates are sent every 60 seconds by each module
        self.update = Throttle(refreshinterval)(self._update)

    def _update(self):
        """Retrieve the latest data by listening to the periodic UDP message"""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(SOCK_TIMEOUT)
            try:
                sock.bind(self._localaddr)
            except socket.error as se:
                _LOGGER.error("Unable to bind: %s", se)
                return

            readable, _, _ = select([sock], [], [], SOCK_TIMEOUT)
            if not readable:
                _LOGGER.warning(
                    "Timeout (%s second(s)) waiting for data on port %s.",
                    SOCK_TIMEOUT, self._config.get(CONF_PORT))
                return

            data, _ = sock.recvfrom(1024)
            self.on_data_received(data.decode('utf-8'))

    def on_data_received(self, xmldata):
        """Callback when new data is received: store it in the dict"""
        try:
            xml = ET.fromstring(xmldata)
            self.data[xml.tag] = xml
            _LOGGER.debug("Packet received type %s",xml.tag)

        except ET.ParseError as pe:
            _LOGGER.error("Unable to parse received data: %s", pe)

    def get(self, owlclass):
        """Facade for the internal dictionary's get method"""
        return self.data.get(owlclass)


class OwlIntuitionSensor(Entity):
    """Implementation of the OWL Intuition Power Meter sensors."""

    def __init__(self, owldata, sensor_name, sensor_type, phase=0):
        """Set all the config values if they exist and get initial state."""
        self._owldata = owldata
        if(phase > 0):
            self._name = '{} {} P{}'.format(
                sensor_name,
                SENSOR_TYPES[sensor_type][0],
                phase)
        else:
            self._name = '{} {}'.format(
                sensor_name,
                SENSOR_TYPES[sensor_type][0])
        self._sensor_type = sensor_type
        self._phase = phase
        self._owl_class = SENSOR_TYPES[sensor_type][3]
        self._state = None

    @property
    def name(self):
        """Return the name of this sensor."""
        return self._name

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity."""
        return SENSOR_TYPES[self._sensor_type][1]

    @property
    def icon(self):
        """Return the icon for this entity."""
        return SENSOR_TYPES[self._sensor_type][2]

    @property
    def state(self):
        """Return the current value for this sensor."""
        return self._state

    def update(self):
        """Retrieve the latest value for this sensor."""
        self._owldata.update()
        xml = self._owldata.get(self._owl_class)
        if xml is None:
            return
        xml_ver = xml.attrib.get('ver')

        if (xml.tag == OWLCLASS_HEATING or xml.tag == OWLCLASS_HOTWATER or xml.tag == OWLCLASS_RELAYS):        
            # Only supports first zone currently
            xml = xml.find('zones/zone')

        # Radio & Battery sensors
        if self._sensor_type in RADIO_SENSORS:
            self._state = int(xml.find('signal').attrib['rssi'])
        elif self._sensor_type == SENSOR_ELECTRICITY_BATTERY:
            # Battery in % for OWLCLASS_ELECTRICITY, mV for others
            # strip off the '%'
            batt_lvl = int(xml.find("battery").attrib['level'][:-1])
            if batt_lvl > 90:
                self._state = 'High'
            elif batt_lvl > 30:
                self._state = 'Medium'
            elif batt_lvl > 10:
                self._state = 'Low'
            else:
                self._state = 'Very Low'
        elif self._sensor_type in BATTERY_SENSORS:
            # 2670mV = 66%
            # 2780mV = 76%
            batt_lvl = int(xml.find("battery").attrib['level'])
            if batt_lvl > 2900:
                self._state = 'High'
            elif batt_lvl > 2500:
                self._state = 'Medium'
            elif batt_lvl > 2300:
                self._state = 'Low'
            else:
                self._state = 'Very Low'            

        # Electricity sensors
        elif self._sensor_type == SENSOR_ELECTRICITY_POWER:
            if self._phase == 0:
                # xml_ver undefined for older version
                if xml_ver is None:
                    self._state = int(float(xml.find('chan/curr').text))
                else:
                    self._state = int(float(xml.find('property/current/watts').text))
            else:
                # TODO: Older xml format not handled yet for tri-phase
                self._state = int(float(xml.find('channels').
                                      findall('chan')[self._phase-1].
                                      find('curr').text))
        elif self._sensor_type == SENSOR_ELECTRICITY_ENERGY_TODAY:
            if self._phase == 0:
                # xml_ver undefined for older version
                if xml_ver is None:
                    self._state = round(float(xml.find('chan/day').text)/1000,2)
                else:
                    self._state = round(float(xml.find('property/day/wh').text)/1000, 2)
            else:
                # TODO: Older xml format not handled yet for tri-phase
                self._state = round(float(xml.find('channels').
                                        findall('chan')[self._phase-1].
                                        find('day').text)/1000, 2)
        # Solar sensors
        elif self._sensor_type == SENSOR_SOLAR_GPOWER:
            self._state = int(float(xml.find('current/generating').text))
        elif self._sensor_type == SENSOR_SOLAR_EPOWER:
            self._state = int(float(xml.find('current/exporting').text))
        elif self._sensor_type == SENSOR_SOLAR_GENERGY_TODAY:
            self._state = round(float(xml.find('day/generated').text)/1000, 2)
        elif self._sensor_type == SENSOR_SOLAR_EENERGY_TODAY:
            self._state = round(float(xml.find('day/exported').text)/1000, 2)
	  		
        # Hotwater sensors
        if self._sensor_type == SENSOR_HOTWATER_CURRENT:
            self._state = round(float(xml.find('temperature/current').text),1)
        elif self._sensor_type == SENSOR_HOTWATER_REQUIRED:
            self._state = float(xml.find('temperature/required').text)
        elif self._sensor_type == SENSOR_HOTWATER_AMBIENT:
            self._state = float(xml.find('temperature/ambient').text)
        elif self._sensor_type == SENSOR_HOTWATER_STATE:
            # Heating state reported in version 2 and up
            if xml_ver is not None:
                self._state = HOTWATER_STATE[int(xml.find('temperature').attrib['state'])]

        # Heating Sensors
        elif self._sensor_type == SENSOR_HEATING_CURRENT:
            self._state = round(float(xml.find('temperature/current').text),1)
        elif self._sensor_type == SENSOR_HEATING_REQUIRED:
            self._state = float(xml.find('temperature/required').text)
        elif self._sensor_type == SENSOR_HEATING_STATE:
            # Heating state reported in version 2 and up
            if xml_ver is not None:
                self._state = HEATING_STATE[int(xml.find('temperature').attrib['state'])]

class OwlStateUpdater(asyncio.DatagramProtocol):
    """An helper class for the async UDP listener loop.
    More info at:
    https://docs.python.org/3/library/asyncio-protocol.html"""

    def __init__(self, eloop):
        """Boiler-plate initialisation"""
        self.eloop = eloop
        self.transport = None

    def connection_made(self, transport):
        """Boiler-plate connection made metod"""
        self.transport = transport

    def datagram_received(self, packet, addr_unused):
        """Get the last received datagram and notify the
        OwlData singleton for storing it if relevant"""
        xmldata = packet.decode('utf-8')
        root = xmldata[1:xmldata.find(' ')]
        
        _LOGGER.debug("datagram received type %s",root)
        
        if root in OWL_CLASSES:
            # do not call here that method, but instead leave
            # the event loop do it when convenient AND thread-safe!
            self.eloop.call_soon_threadsafe(
                owldata.on_data_received, xmldata)
        else:
            _LOGGER.warning("Unsupported type '%s' in data: %s", \
                            root, packet)

    def error_received(self, exc):
        """Boiler-plate error received method"""
        _LOGGER.error("Received error %s", exc)

    def cleanup(self):
        """Boiler-plate cleanup method"""
        if self.transport:
            self.transport.close()
        self.transport = None
