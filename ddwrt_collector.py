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
extended_successes = Counter('extended_success_requests', 'Request to which extended data could be replied')
unreachable_requests = Counter('unreachable_requests', 'Requests for which the target was unreachable')
unservable_requests = Counter('unservable_requests', 'Requests for which no data was served, e.g. due to failed authentication')

class UnsuccessfulHTTP(Exception):
    pass

def getDDwrtData(url,auth):
    r = requests.get(url,timeout=5,auth=auth)
    if r.status_code != 200:
        raise UnsuccessfulHTTP
    regex_out = re.findall('\{(\w+)::([^\}]*)\}', r.text)
    result = {}
    for line in regex_out:
        result[line[0]] = line[1]
    return result

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
    extended = True if 'extended' in config["targets"][target] and config["targets"][target]["extended"] and auth_configured else False
    interfaces = set(config["targets"][target]["interfaces"]) if "interfaces" in config["targets"][target] else set()
    try:
        ddwrt_data = getDDwrtData("http://" + target + ":" + port + "/Info.live.htm", auth)
    except requests.exceptions.ConnectionError:
        unreachable_requests.inc()
        return "Target not reachable/resolvable", 400, {'Conte6nt-Type': 'text/plain; charset=utf-8'}
    except UnsuccessfulHTTP:
        unservable_requests.inc()
        return "Status page not reachable", 400, {'Content-Type': 'text/plain; charset=utf-8'}
    # fill the prometheus registry
    mac_addresses = Info("ddwrt_mac_addresses", "MAC Address", registry=registry)
    mac_addresses.info(
        {'lan_mac': ddwrt_data['lan_mac'], 'wan_mac': ddwrt_data['wan_mac'], 'wl_mac': ddwrt_data['wl_mac']})
    ip_addresses = Info("ddwrt_ip_addresses", "IP Addresses", registry=registry)
    ip_addresses.info({'lan_ip': ddwrt_data['lan_ip'], 'wan_ip': ddwrt_data['wan_ipaddr']})
    wifi_channel = Gauge("ddwrt_wifi_channel", "WiFi Channel", registry=registry)
    wifi_channel.set(int(ddwrt_data['wl_channel'].split()[0]))
    wifi_freq = Gauge("ddwrt_wifi_freq", "WiFi Frequency", registry=registry)
    wifi_freq.set(int(ddwrt_data['wl_channel'].split()[1][1:]))
    wifi_width = Gauge("ddwrt_wifi_width", "WiFi Channel Width", registry=registry)
    wifi_width.set(int(ddwrt_data['wl_channel'].split()[3][2:-1]))
    wifi_tx_power = Gauge('ddwrt_wifi_tx_power', 'Transmit Power', registry=registry)
    wifi_tx_power.set(ddwrt_data['wl_xmit'].split()[0])
    wifi_rate = Gauge('ddwrt_wifi_rate', 'WiFi Rate', registry=registry)
    wifi_rate.set(ddwrt_data['wl_rate'].split()[0])
    # wireless packet stats
    wifi_data = {}
    for wifi_stat in list(filter(None, ddwrt_data['packet_info'].split(";"))):
        wifi_data[wifi_stat.split("=")[0]] = wifi_stat.split("=")[1]
    wifi_good_packets_rx = Gauge('ddwrt_wifi_good_packets_rx', "Good WiFi Received Packets", registry=registry)
    wifi_good_packets_rx.set(int(wifi_data['SWRXgoodPacket']))
    wifi_bad_packets_rx = Gauge('ddwrt_wifi_bad_packets_rx', "Bad WiFi Received Packets", registry=registry)
    wifi_bad_packets_rx.set(int(wifi_data['SWRXerrorPacket']))
    wifi_good_packets_tx = Gauge('ddwrt_wifi_good_packets_tx', "Good WiFi Transmit Packets", registry=registry)
    wifi_good_packets_tx.set(int(wifi_data['SWTXgoodPacket']))
    wifi_bad_packets_tx = Gauge('ddwrt_wifi_bad_packets_tx', "Bad WiFi Transmit Packets", registry=registry)
    wifi_bad_packets_tx.set(int(wifi_data['SWTXerrorPacket']))
    mem_list = ddwrt_data['mem_info'].split(',')[-105:]
    mem_infos = {mem_list[i].strip("':"): int(mem_list[i + 1].strip("'")) for i in range(0, len(mem_list), 3)}
    memory_stat = Gauge('ddwrt_memory_stat', 'Memory Statistics in Kilobytes', ['type'], registry=registry)
    for key in mem_infos.keys():
        memory_stat.labels(key).set(mem_infos[key])
    # count members in the active_wireless list
    clients = sum([1 for piece in ddwrt_data['active_wireless'].split(",") if piece.strip("'") == "ath0"])
    wifi_clients = Gauge('ddwrt_wifi_client_count', 'Current connect wifi devices', registry=registry)
    wifi_clients.set(clients)
    nvram_used = Gauge('ddwrt_nvram_used', 'NVRAM used in Kilobytes', registry=registry)
    nvram_used.set(ddwrt_data['nvram'].split()[0])
    nvram_total = Gauge('ddwrt_nvram_total', 'NVRAM total in Kilobytes', registry=registry)
    nvram_total.set(ddwrt_data['nvram'].split()[3])
    # reengineer uptime command output to processor load of 1,5,15 minutes
    load = Gauge('ddwrt_load', 'System load of the last x minutes', ['timeframe'], registry=registry)
    load.labels("15min").set(ddwrt_data['uptime'].split()[-1].strip(","))
    load.labels("5min").set(ddwrt_data['uptime'].split()[-2].strip(","))
    load.labels("1min").set(ddwrt_data['uptime'].split()[-3].strip(","))
    # reengineer uptime command output to uptime
    updays = int(ddwrt_data['uptime'].split()[2]) if "day" in ddwrt_data['uptime'] else 0
    uphours = 0 if "min" in ddwrt_data['uptime'] else int(ddwrt_data["uptime"].split()[-6][0:-4])
    upminutes = int(ddwrt_data['uptime'].split()[-7]) if "min" in ddwrt_data['uptime'] else int(
        ddwrt_data["uptime"].split()[-6][-3:-1])
    system_upminutes = Gauge('ddwrt_system_upminutes', "Minutes since system boot", registry=registry)
    system_upminutes.set(upminutes + uphours * 60 + updays * 1440)
    successes.inc()
    extended_access = Gauge('ddwrt_extended_access', "-1 not run (perhaps not configured), 0 run but not reachable (perhaps wrong auth), 1 run and reachable", registry=registry)
    extended_access.set(-1)
    if extended:
        try:
            router_data = getDDwrtData("http://" + target + ":" + port + "/Status_Router.live.asp", auth)
        except UnsuccessfulHTTP:
            extended_access.set(0)
            return generate_latest(registry).decode("utf-8"), 200, {'Content-Type': 'text/plain; charset=utf-8'}
        extended_access.set(1)
        conntrack_counter = Gauge('ddwrt_conntrack_counter', "Conntrack Counter",registry=registry)
        conntrack_counter.set(int(router_data['ip_conntrack']))
        lan_data = getDDwrtData("http://" + target + ":" + port + "/Status_Lan.live.asp", auth)
        wireless_data = getDDwrtData("http://" + target + ":" + port + "/Status_Wireless.live.asp", auth)
        networking_data = getDDwrtData("http://" + target + ":" + port + "/Networking.live.asp", auth)
        # discover network interfaces
        for i in range(4, len(lan_data["arp_table"].split(",")), 5):
            interfaces.add(lan_data["arp_table"].split(",")[i].strip("' "))
        for i in range(0,len(networking_data["bridges_table"].split(",")),3):
            interfaces.add(networking_data["bridges_table"].split(",")[i].strip("'"))
        for i in range(2,len(networking_data["bridges_table"].split(",")),3):
            interfaces |= set(networking_data["bridges_table"].split(",")[i].strip("'").split())
        for i in range(2, len(wireless_data["active_wireless"].split(",")), 15):
            interfaces.add(wireless_data["active_wireless"].split(",")[i].strip("'"))

        wireless_quality = Gauge('ddwrt_wireless_quality', "Wireless quality in percent", registry=registry)
        wireless_quality.set(int(wireless_data['wl_quality'][:-1]))

        network_receive_bytes = Gauge('ddwrt_network_receive_bytes', 'Network device statistic receive_bytes.',
                                      ['interface'], registry=registry)
        network_receive_compressed = Gauge('ddwrt_network_receive_compressed',
                                           'Network device statistic receive_compressed.', ['interface'],
                                           registry=registry)
        network_receive_drop = Gauge('ddwrt_network_receive_drop', 'Network device statistic receive_drop.',
                                     ['interface'], registry=registry)
        network_receive_errs = Gauge('ddwrt_network_receive_errs', 'Network device statistic receive_errs.',
                                     ['interface'], registry=registry)
        network_receive_fifo = Gauge('ddwrt_network_receive_fifo', 'Network device statistic receive_fifo.',
                                     ['interface'], registry=registry)
        network_receive_frame = Gauge('ddwrt_network_receive_frame', 'Network device statistic receive_frame.',
                                      ['interface'], registry=registry)
        network_receive_multicast = Gauge('ddwrt_network_receive_multicast',
                                          'Network device statistic receive_multicast.', ['interface'],
                                          registry=registry)
        network_receive_packets = Gauge('ddwrt_network_receive_packets', 'Network device statistic receive_packets.',
                                        ['interface'], registry=registry)
        network_transmit_bytes = Gauge('ddwrt_network_transmit_bytes', 'Network device statistic transmit_bytes.',
                                       ['interface'], registry=registry)
        network_transmit_carrier = Gauge('ddwrt_network_transmit_carrier', 'Network device statistic transmit_carrier.',
                                         ['interface'], registry=registry)
        network_transmit_colls = Gauge('ddwrt_network_transmit_colls', 'Network device statistic transmit_colls.',
                                       ['interface'], registry=registry)
        network_transmit_compressed = Gauge('ddwrt_network_transmit_compressed',
                                            'Network device statistic transmit_compressed.', ['interface'],
                                            registry=registry)
        network_transmit_drop = Gauge('ddwrt_network_transmit_drop', 'Network device statistic transmit_drop.',
                                      ['interface'], registry=registry)
        network_transmit_errs = Gauge('ddwrt_network_transmit_errs', 'Network device statistic transmit_errs.',
                                      ['interface'], registry=registry)
        network_transmit_fifo = Gauge('ddwrt_network_transmit_fifo', 'Network device statistic transmit_fifo.',
                                      ['interface'], registry=registry)
        network_transmit_packets = Gauge('ddwrt_network_transmit_packets', 'Network device statistic transmit_packets.',
                                         ['interface'], registry=registry)

        finished_interfaces = []
        interfaces = list(filter(None, interfaces))
        for ifname in interfaces:
            if_stat_req = requests.get("http://" + target + ":" + port + "/fetchif.cgi?"+ifname,auth=auth).text.splitlines()
            if len(if_stat_req) == 1:
                continue
            if_stat = if_stat_req[1].split()
            if_name = if_stat[0][:-1]
            if if_name in finished_interfaces:
                continue
            network_receive_bytes.labels(if_name).set(if_stat[1])
            network_receive_packets.labels(if_name).set(if_stat[2])
            network_receive_errs.labels(if_name).set(if_stat[3])
            network_receive_drop.labels(if_name).set(if_stat[4])
            network_receive_fifo.labels(if_name).set(if_stat[5])
            network_receive_frame.labels(if_name).set(if_stat[6])
            network_receive_compressed.labels(if_name).set(if_stat[7])
            network_receive_multicast.labels(if_name).set(if_stat[8])
            network_transmit_bytes.labels(if_name).set(if_stat[9])
            network_transmit_packets.labels(if_name).set(if_stat[10])
            network_transmit_errs.labels(if_name).set(if_stat[11])
            network_transmit_drop.labels(if_name).set(if_stat[12])
            network_transmit_fifo.labels(if_name).set(if_stat[13])
            network_transmit_colls.labels(if_name).set(if_stat[14])
            network_transmit_carrier.labels(if_name).set(if_stat[15])
            network_transmit_compressed.labels(if_name).set(if_stat[16])
            finished_interfaces.append(if_name)
        extended_successes.inc()

    return generate_latest(registry).decode("utf-8"), 200, {'Content-Type': 'text/plain; charset=utf-8'}


application = app
