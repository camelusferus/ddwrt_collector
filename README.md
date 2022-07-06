# DD-WRT Collector
Prometheus Monitoring of DDWRT Routers by parsing the endpoint (http://ROUTER_IP:8080/Info.live.htm) for the DD-WRT web ui live status page (http://ROUTER_IP:8080/Info.htm).

Developed with TP-Link WR941 (running DD-WRT v3.0-r44715) at hand.

This software is released under [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).

## Running
pip dependencies
```
pip install -r requirements.txt
```

Run with uwsgi
```
uwsgi --http 127.0.0.1:9920 --wsgi-file ddwrt_collector.py
```
When using authentication to the status page, create ``ddwrt_credentials.yml`` according to the sample.

Collect data by accessing: ``http://127.0.0.1:9920/collect?target=example.org``

When setting the ``extended`` switch in the config file, further metrics will be polled from the device, which are only available to logged users. (http://ROUTER_IP:8080/Status_Router.live.asp, http://ROUTER_IP:8080/Status_Internet.live.asp, http://ROUTER_IP:8080/Status_Lan.live.asp, http://ROUTER_IP:8080/Status_Wireless.live.asp)

Interface counters will be also polled from the device. For that interface endpoint will be discovered from different data source endpoints above. If some interface is not discovered, you can add it to the ``interface`` parameter of the host config.

