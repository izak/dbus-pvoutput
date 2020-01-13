#!/usr/bin/python -u

import sys, os
import logging
from functools import partial
from collections import Mapping
from datetime import datetime
import dbus
from dbus.mainloop.glib import DBusGMainLoop
import gobject
import ConfigParser
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def get_weather_active(config):    
    return config.get('dvoutput.org','useweather')

def get_interval(config):    
    return config.get('dvoutput.org','INTERVAL')

def get_pvoutput(config):    
    return config.get('dvoutput.org','PVOUTPUT')

def get_pvoutput_api(config):    
    return config.get('dvoutput.org','APIKEY')

def get_pvoutput_systemid(config):   
    return config.get('dvoutput.org','SYSTEMID')

def get_api_key(config):    
    return config.get('openweathermap','api')

def get_city_id(config):    
    return config.get('openweathermap','cityid')

def requests_retry_session(
    retries=3,
    backoff_factor=0.3,
    status_forcelist=(500, 502, 504),
    session=None,
):
    session = session or requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def get_weather(api_key, city_id):
    url = "https://api.openweathermap.org/data/2.5/weather?id={}&units=metric&appid={}".format(city_id, api_key)    
    r = requests.get(url)
    return r.json()

def find_services(bus, tp):
    return [str(service) for service in bus.list_names() \
        if service.startswith('com.victronenergy.{}'.format(tp))]

class smart_dict(dict):
    """ Dictionary that can be accessed via attributes. """
    def __getattr__(self, k):
        try:
            v = self[k]
            if isinstance(v, Mapping):
                return self.__class__(v)
            return v
        except KeyError:
            raise AttributeError(k)
    def __setattr__(self, k, v):
        self[k] = v

dbus_int_types = (dbus.Int32, dbus.UInt32, dbus.Byte, dbus.Int16, dbus.UInt16,
        dbus.UInt32, dbus.Int64, dbus.UInt64)

def unwrap_dbus_value(val):
    """Converts D-Bus values back to the original type. For example if val is
       of type DBus.Double, a float will be returned."""
    if isinstance(val, dbus_int_types):
        return int(val)
    if isinstance(val, dbus.Double):
        return float(val)
    return None

def set_state(state, key, v):
    state[key] = value = unwrap_dbus_value(v["Value"])

def query(conn, service, path):
    return conn.call_blocking(service, path, None, "GetValue", '', [])

def track(conn, state, service, path, target):
    # Initialise state
    state[target] = value = unwrap_dbus_value(query(conn, service, path))

    # And track it
    conn.add_signal_receiver(partial(set_state, state, target),
            dbus_interface='com.victronenergy.BusItem',
            signal_name='PropertiesChanged',
            path=path,
            bus_name=service)



def main():
    logging.basicConfig(level=logging.INFO)
    config = ConfigParser.ConfigParser()
    config.read('config.ini')

    api_key = get_api_key(config)
    city_id = get_city_id(config)
    useweather = int(get_weather_active(config))
    if useweather == 1:
        weather = get_weather(api_key, city_id)
    else:
        weather = None    
    #print(weather) 

    INTERVAL=int(get_interval(config))
    PVOUTPUT=get_pvoutput(config)
    APIKEY=get_pvoutput_api(config)
    SYSTEMID=get_pvoutput_systemid(config)
    DBusGMainLoop(set_as_default=True)
    conn = dbus.SystemBus()

    generators = smart_dict()
    consumers = smart_dict()
    stats = smart_dict()
    voltages = smart_dict()

    # Set the user timezone
    if 'TZ' not in os.environ:
        tz = query(conn, "com.victronenergy.settings", "/Settings/System/TimeZone")
        if tz is not None:
            os.environ['TZ'] = tz

    # Find solarcharger services
    solarchargers = find_services(conn, 'solarcharger')
    logger.info("Found solarchargers at %s", ', '.join(solarchargers))

    # Find grid meters
    meters = find_services(conn, 'grid')
    logger.info("Found grid meters at %s", ', '.join(meters))

    # Find vebus service
    vebus = str(query(conn, "com.victronenergy.system", "/VebusService"))
    logger.info("Found vebus at %s", vebus)
  
    # Track solarcharger yield
    for charger in solarchargers:
        track(conn, generators, charger, "/Yield/User", charger)

    # Track grid consumption
    for meter in meters:
        track(conn, consumers, meter, "/Ac/L1/Energy/Forward", meter)

    # Track vebus consumption, from battery to input and output
    track(conn, consumers, vebus, "/Energy/InverterToAcOut", "c1")
    track(conn, consumers, vebus, "/Energy/InverterToAcIn1", "c2")

    # Track power values
    track(conn, stats, "com.victronenergy.system", "/Ac/Consumption/L1/Power", "pc")
    track(conn, stats, "com.victronenergy.system", "/Dc/Pv/Power", "pg")

    track(conn, voltages, vebus, "/Ac/Out/L1/V", "vo" )
    
    # Periodic work
    def _upload():

        if useweather == 1:
            try:        
              weather = get_weather(api_key, city_id)
            except:
              weather = None
              pass	   
        else:
            weather = None

        energy_generated = sum(filter(None, generators.itervalues()))
        energy_consumed = sum(filter(None, consumers.itervalues()))    

        logger.info("EG: %.2f, EC: %.2f, PG: %.2f, PC: %.2f, VO: %.2f", energy_generated,
            energy_consumed, stats.pg, stats.pc, voltages.vo)

        # Post the values to pvoutput
        now = datetime.now()        

        if (weather is not None):
            payload = {
                "d": now.strftime("%Y%m%d"),
                "t": now.strftime("%H:%M"),
                "v1": int(energy_generated*1000),
                "v2": int(stats.pg),
                "v3": int(energy_consumed*1000),
                "v4": int(stats.pc),
                "v5": float(weather['main']['temp']),
                "v6": float(voltages.vo),
                "c1": 1
            }
        else:
            payload = {
                "d": now.strftime("%Y%m%d"),
                "t": now.strftime("%H:%M"),
                "v1": int(energy_generated*1000),
                "v2": int(stats.pg),
                "v3": int(energy_consumed*1000),
                "v4": int(stats.pc),     
                "v6": float(voltages.vo),           
                "c1": 1
            }
        
        try:            
            requests_retry_session().post(PVOUTPUT,
                headers={
                    "X-Pvoutput-Apikey": APIKEY,
                    "X-Pvoutput-SystemId": SYSTEMID
                }, data=payload)
        except:
            print("Fail on Post")
            pass
        return True

    _upload()
    gobject.timeout_add(INTERVAL, _upload)

    gobject.MainLoop().run()

if __name__ == "__main__":
    main()
