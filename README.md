# dbus-pvoutput

This is a simple python script that collects data from dbus on your Victron
CCGX or Venus-GX, and posts it to pvoutput.org. This is mostly a proof of 
concept designed to work on my particular single-phase setup. Extending for 
other systems is left as an exercise to the user.

## Assumptions, shortcuts and todos

* Assumes single phase system
* Assumes no PV inverters
* Assumes there is a grid meter
* Should also work for off-grid systems without a grid meter.
* On pvoutput, battery charge and discharge shows up as export and import.

## Other caveats

* It uses the timezone information from the CCGX settings, not the system
  timezone.
* You have to add your APIKEY and SYSTEMID in the config.ini
* If you want to enable weather information, register on 
  openweathermap.org then add apikey and cityid in config.ini
