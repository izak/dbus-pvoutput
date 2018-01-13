#!/usr/bin/python -u

import sys, os
import logging
from functools import partial
from collections import Mapping
from datetime import datetime
import dbus
from dbus.mainloop.glib import DBusGMainLoop
import gobject
import requests

INTERVAL = 300000
PVOUTPUT = "https://pvoutput.org/service/r2/addstatus.jsp"
APIKEY = "YOUR_API_KEY_HERE"
SYSTEMID = "12345"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

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

    DBusGMainLoop(set_as_default=True)
    conn = dbus.SystemBus()

    generators = smart_dict()
    consumers = smart_dict()
    stats = smart_dict()

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

    # Periodic work
    def _upload():
        energy_generated = sum(filter(None, generators.itervalues()))
        energy_consumed = sum(filter(None, consumers.itervalues()))

        logger.info("EG: %.2f, EC: %.2f, PG: %.2f, PC: %.2f", energy_generated,
            energy_consumed, stats.pg, stats.pc)

        # Post the values to pvoutput
        now = datetime.now()
        payload = {
            "d": now.strftime("%Y%m%d"),
            "t": now.strftime("%H:%M"),
            "v1": int(energy_generated*1000),
            "v2": int(stats.pg),
            "v3": int(energy_consumed*1000),
            "v4": int(stats.pc),
            "c1": 1
        }
        result = requests.post(PVOUTPUT,
            headers={
                "X-Pvoutput-Apikey": APIKEY,
                "X-Pvoutput-SystemId": SYSTEMID
            }, data=payload)
        print result

        return True

    _upload()
    gobject.timeout_add(INTERVAL, _upload)

    gobject.MainLoop().run()


if __name__ == "__main__":
    main()
