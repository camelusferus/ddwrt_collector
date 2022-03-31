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
uwsgi --http 127.0.0.1:8000 --wsgi-file ddwrt_collector.py
```
When using authentication to the status page, create ``ddwrt_credentials.yml`` according to the sample.

Collect data by accessing: ``http://127.0.0.1:8000/collect?target=example.org``


## Future
- Use additional metrics from further status pages only shown to logged-in users (http://ROUTER_IP:8080/Status_Router.live.asp, http://ROUTER_IP:8080/Status_Internet.live.asp, http://ROUTER_IP:8080/Status_Lan.live.asp, http://ROUTER_IP:8080/Status_Wireless.live.asp)
