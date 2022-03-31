#!/usr/bin/env python3
# ddwrt_collector
# 
# file: ddwrt_collector.py
#
# Copyright 2022 Nils Trampel (camelusferus)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
import requests
import sys
import yaml
from os.path import exists
from flask import Flask, request
from prometheus_client import CollectorRegistry, Gauge, generate_latest, Info, make_wsgi_app, Counter
from werkzeug.middleware.dispatcher import DispatcherMiddleware

config_file = sys.path[0] + "/ddwrt_credentials.yml"
if exists(config_file):
    with open(config_file, "r") as yaml_file:
        config = yaml.safe_load(yaml_file)
else:
    config = {"targets": {}}

app = Flask(__name__)

# Add prometheus wsgi middleware to route /metrics requests
app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {
    '/metrics': make_wsgi_app()
})

successes = Counter('successful_requests', 'Requests for which data could be served')
unreachable_requests = Counter('unreachable_requests', 'Requests for which the target was unreachable')
unservable_requests = Counter('unservable_requests', 'Requests for which no data was served, e.g. due to failed authentication')

@app.route('/collect')
def my_route():
    registry = CollectorRegistry()
    # get necessary config parameters
    if 'target' not in request.args:
        return "Target missing", 400, {'Content-Type': 'text/plain; charset=utf-8'}
    target = request.args.get('target', default=None, type=str)
    configured = target in config["targets"]
    port_configured = configured and 'port' in config["targets"][target] and\
        int(config["targets"][target]["port"]) in range(65536)
    auth_configured = configured and {"user", "pass"} <= config["targets"][target].keys()
    port = str(config["targets"][target]["port"]) if port_configured else "8080"
    auth = (str(config["targets"][target]["user"]), str(config["targets"][target]["pass"])) \
        if auth_configured else ("admin", "admin")
    try:
        r = requests.get("http://" + target + ":" + port + "/Info.live.htm", timeout=5, auth=auth)
    except requests.exceptions.ConnectionError:
        unreachable_requests.inc()
        return "Target not reachable/resolvable", 400, {'Content-Type': 'text/plain; charset=utf-8'}
    if r.status_code != 200:
        unservable_requests.inc()
        return "Status page not reachable", 400, {'Content-Type': 'text/plain; charset=utf-8'}
    # use regex from ddwrt status page to unpack the data
    # (https://github.com/mirror/dd-wrt/blob/master/src/router/kromo/dd-wrt/common.js#L961)
    regex_out = re.findall('\{(\w+)::([^\}]*)\}', r.text)
    ddwrt_data = {}
    for line in regex_out:
        ddwrt_data[line[0]] = line[1]
    # fill the prometheus registry
    mac_addresses = Info("mac_addresses", "MAC Address", registry=registry)
    mac_addresses.info(
        {'lan_mac': ddwrt_data['lan_mac'], 'wan_mac': ddwrt_data['wan_mac'], 'wl_mac': ddwrt_data['wl_mac']})
    ip_addresses = Info("ip_addresses", "IP Addresses", registry=registry)
    ip_addresses.info({'lan_ip': ddwrt_data['lan_ip'], 'wan_ip': ddwrt_data['wan_ipaddr']})
    wifi_channel = Gauge("wifi_channel", "WiFi Channel", registry=registry)
    wifi_channel.set(int(ddwrt_data['wl_channel'].split()[0]))
    wifi_freq = Gauge("wifi_freq", "WiFi Frequency", registry=registry)
    wifi_freq.set(int(ddwrt_data['wl_channel'].split()[1][1:]))
    wifi_width = Gauge("wifi_width", "WiFi Channel Width", registry=registry)
    wifi_width.set(int(ddwrt_data['wl_channel'].split()[3][2:-1]))
    wifi_tx_power = Gauge('wifi_tx_power', 'Transmit Power', registry=registry)
    wifi_tx_power.set(ddwrt_data['wl_xmit'].split()[0])
    wifi_rate = Gauge('wifi_rate', 'WiFi Rate', registry=registry)
    wifi_rate.set(ddwrt_data['wl_rate'].split()[0])
    # wireless packet stats
    wifi_data = {}
    for wifi_stat in list(filter(None, ddwrt_data['packet_info'].split(";"))):
        wifi_data[wifi_stat.split("=")[0]] = wifi_stat.split("=")[1]
    wifi_good_packets_rx_total = Gauge('wifi_good_packets_rx_total', "Good WiFi Received Packets", registry=registry)
    wifi_good_packets_rx_total.set(int(wifi_data['SWRXgoodPacket']))
    wifi_bad_packets_rx = Gauge('wifi_bad_packets_rx', "Bad WiFi Received Packets", registry=registry)
    wifi_bad_packets_rx.set(int(wifi_data['SWRXerrorPacket']))
    wifi_good_packets_tx = Gauge('wifi_good_packets_tx', "Good WiFi Transmit Packets", registry=registry)
    wifi_good_packets_tx.set(int(wifi_data['SWTXgoodPacket']))
    wifi_bad_packets_tx = Gauge('wifi_bad_packets_tx', "Bad WiFi Transmit Packets", registry=registry)
    wifi_bad_packets_tx.set(int(wifi_data['SWTXerrorPacket']))
    mem_list = ddwrt_data['mem_info'].split(',')[-105:]
    mem_infos = {mem_list[i].strip("':"): int(mem_list[i + 1].strip("'")) for i in range(0, len(mem_list), 3)}
    memory_stat = Gauge('memory_stat', 'Memory Statistics in Kilobytes', ['type'], registry=registry)
    for key in mem_infos.keys():
        memory_stat.labels(key).set(mem_infos[key])
    # count members in the active_wireless list
    clients = sum([1 for piece in ddwrt_data['active_wireless'].split(",") if piece.strip("'") == "ath0"])
    wifi_clients = Gauge('wifi_client', 'Current connect wifi devices', registry=registry)
    wifi_clients.set(clients)
    nvram_used = Gauge('nvram_used', 'NVRAM used in Kilobytes', registry=registry)
    nvram_used.set(ddwrt_data['nvram'].split()[0])
    nvram_total = Gauge('nvram_total', 'NVRAM total in Kilobytes', registry=registry)
    nvram_total.set(ddwrt_data['nvram'].split()[3])
    # reengineer uptime command output to processor load of 1,5,15 minutes
    load = Gauge('load', 'System load of the last x minutes', ['timeframe'], registry=registry)
    load.labels("15min").set(ddwrt_data['uptime'].split()[-1].strip(","))
    load.labels("5min").set(ddwrt_data['uptime'].split()[-2].strip(","))
    load.labels("1min").set(ddwrt_data['uptime'].split()[-3].strip(","))
    # reengineer uptime command output to uptime
    updays = int(ddwrt_data['uptime'].split()[2]) if "days" in ddwrt_data['uptime'] else 0
    uphours = 0 if "min" in ddwrt_data['uptime'] else int(ddwrt_data["uptime"].split()[-6][0:-4])
    upminutes = ddwrt_data['uptime'].split()[-7] if "min" in ddwrt_data['uptime'] else int(
        ddwrt_data["uptime"].split()[-6][-3:-1])
    system_upminutes = Gauge('system_upminutes', "Minutes since system boot", registry=registry)
    system_upminutes.set(upminutes + uphours * 60 + updays * 1440)
    successes.inc()
    return generate_latest(registry).decode("utf-8"), 200, {'Content-Type': 'text/plain; charset=utf-8'}


application = app
