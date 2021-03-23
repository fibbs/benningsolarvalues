#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: ai:hls:ts=4:sw=4:expandtab

"""
Data collector to fetch data from a "Benning Solar" photovoltaic inverter and publish them to a MQTT topic
"""
__description__ = 'Data collector to fetch data from a "Benning Solar" photovoltaic inverter and publish them to a MQTT topic'
__author__     = "Christian Anton"
__copyright__  = "Copyright 2021"
__license__    = "MIT"
__version__    = "0.2.0"
__maintainer__ = "Christian Anton"
__status__     = "Development"


###############################################################################
# imports
import sys
import logging
from logging.handlers import SysLogHandler
import os
import socket
import pwd
import argparse
# import xml.etree.ElementTree as ET
import json
import csv
# from time import ctime
import time, traceback
import requests
from requests.exceptions import ConnectionError
import yaml
import paho.mqtt.client as mqtt

# disable requests warnings for self-signed certificates
# from requests.packages.urllib3.exceptions import InsecureRequestWarning
# requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


###############################################################################
# configuration
loglevel = logging.INFO


###############################################################################
# functions
def parse_arguments():
    global loglevel
    parser = argparse.ArgumentParser(description=__description__)
    parser.add_argument('-d', '--debug', action='store_true', help='set log level to DEBUG')
    parser.add_argument('-c', '--config', type=str, default=find_default_config_path('config.yaml'), help='config file')
    # parser.add_argument('somearg', type=str, default='bla', help='this is something important')
    args = parser.parse_args()
    if args.debug:
        loglevel = logging.DEBUG
    return args


def find_default_config_path(config_file_name):
    samedir_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), config_file_name)
    return samedir_config_path


def read_config(config_path):
    logging.info('reading configuration file %s' % config_path)
    if not os.path.isfile(config_path):
        logging.error('config file %s does not exist' % config_path)
        sys.exit(254)
    with open(config_path, "r") as ymlfile:
        config = yaml.load(ymlfile, Loader=yaml.FullLoader)
    return config


def set_up_logging(loglevel, log_to_foreground, program_name=None):
    if log_to_foreground is True:
        logging.basicConfig(level=loglevel, format='%(asctime)s %(levelname)s:%(message)s')

    else:
        # logging.basicConfig(level=loglevel, format='%(asctime)s %(levelname)s:%(message)s')
        class ContextFilter(logging.Filter):
            hostname = socket.gethostname()
            username = pwd.getpwuid(os.getuid())[0]

            def filter(self, record):
                record.hostname = ContextFilter.hostname
                record.username = ContextFilter.username
                return True

        syslog_socket = "/dev/log"
        if 'darwin' == sys.platform:
            # Hint for macOS:
            # To configure syslog to writhe DEBUG messages to /var/log/system.log
            # - the agent must be started in daemon mode
            # - the agent must be started with debug option
            # - add the following line must be added to /etc/asl.conf:
            # ? [= Sender EdwardAgent] [<= Level debug] file system.log
            # - syslogd must be restarted
            syslog_socket = '/var/run/syslog'

        syslog_format_string = '[%(process)d]: [%(levelname)s][%(funcName)s] %(message)s'
        rootLogger = logging.getLogger()
        rootLogger.setLevel(loglevel)

        syslogFilter = ContextFilter()
        rootLogger.addFilter(syslogFilter)

        if program_name is None:
            program_name = os.path.basename(__file__)
        syslog = SysLogHandler(address=syslog_socket, facility=SysLogHandler.LOG_USER)
        syslogFormatter = logging.Formatter('%s %s' % (program_name, syslog_format_string), datefmt='%b %d %H:%M:%S')
        syslog.setFormatter(syslogFormatter)
        rootLogger.addHandler(syslog)


class Error(Exception):
    pass

class BenningSolarMetricsWorker:
    def __init__(self):
        self.mqtt_connect()
        self.every(config['repeat'], self.do_repeat)

    def mqtt_connect(self):
        def on_publish(client,userdata,result):
            logging.info('data published, result: {}'.format(result))

        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_publish = on_publish
        try:
            self.mqtt_client.connect(config['mqtt']['host'], 1883, 60)
        except Exception as error:
            logging.error('error occurred connecting to mqtt: {}'.format(error))
            pass
            self.mqtt_connected = False
        else:
            self.mqtt_connected = True

        # self.mqtt_client.loop_start()

    def every(self, delay, task):
        #next_time = time.time() + delay
        next_time = time.time()
        while True:
            time.sleep(max(0, next_time - time.time()))
            try:
                task()
            except Exception:
                #traceback.print_exc()
                logging.exception("Problem while executing repetitive task.")
            # skip tasks if we are behind schedule:
            next_time += (time.time() - next_time) // delay * delay + delay

    def do_repeat(self):
        url = 'http://{}/getallentries.cgi?firstOid=10000&lastOid=20000'.format(config['benning']['host'])
        auth = (config['benning']['username'], config['benning']['password'])
        logging.info('fetch all values from {}'.format(url))
        try:
            res = requests.get(url, auth=auth)
        except ConnectionError as error:
            logging.error('received error during http fetch: {}'.format(error))
            pass
        else:
            if res.status_code == 200:
                res.encoding = 'UTF-8'
                # I have received data in this format
                # 11305;SystemState.Global.Measurement.ACFrequency;F;50.023193;"AC Netz Frequenz";1.000000;Hz;2;1;2;1;0.000000;0.000000;0;0;0
                # 11306;SystemState.Global.Measurement.ACFrequencyMin;F;50.019291;"AC Netz Frequenz min";1.000000;Hz;2;1;3;1;0.000000;0.000000;0;0;0
                # 11307;SystemState.Global.Measurement.ACFrequencyMax;F;50.036274;"AC Netz Frequenz max";1.000000;Hz;2;1;4;1;0.000000;0.000000;0;0;0
                # ....

                allmetrics = []
                linecnt = 0
                for line in res.text.splitlines():
                    linecnt += 1
                    v = line.split(';')

                    # handling multipliers and values
                    rawvalue = v[3]
                    multiplier = v[5]
                    if multiplier != '1.000000':
                        value = float(rawvalue) * float(multiplier)
                    else:
                        value = float(rawvalue)
                    
                    # handle units containing prefixes
                    unit = v[6]
                    unit_zabbix = unit
                    if unit == 'mA':
                        value = value * 1000
                        unit = 'A'
                        unit_zabbix = 'A'

                    if unit == 'kWh':
                        unit_zabbix = '!kWh'

                    if unit == 'h':
                        unit = 's'
                        unit_zabbix = 's'
                        value = value * 3600

                    if v[1] == 'SystemState_persistent.Global.LastSystemBackupTimestamp':
                        unit_zabbix = 'unixtime'

                    if v[1] == 'SystemState_persistent.SolarPortal.LastSendProtocolTimestamp':
                        unit_zabbix = 'unixtime'

                    metric = {'id':v[0], 'descriptor':v[1], 'type':v[2], 'value':value, 'description':v[4].strip('"'), 'unit': unit, 'unit_zabbix': unit_zabbix}
                    allmetrics.append(metric)

                logging.info('processed {} values'.format(linecnt))

                # now publish this whole thing as a json structure to MQTT
                if self.mqtt_connected:
                    self.mqtt_client.publish(config['mqtt']['topic'], json.dumps(allmetrics))
                else:
                    logging.warning('mqtt is not connected, trying reconnect in order to be able to publish next time')
                    self.mqtt_connect()
                
            else:
                raise Error('error fetching data: {}'.format(res.text))

def main():
    BenningSolarMetricsWorker()

if __name__ == '__main__':
    args = parse_arguments()
    log_to_foreground = True
    if sys.stdin.isatty():
       log_to_foreground = True
       if int(loglevel) >= int(logging.INFO):
            loglevel = logging.INFO
    set_up_logging(loglevel, log_to_foreground)
    logging.info('starting benningsolarvalues version {}'.format(__version__))
    config = read_config(args.config)
    sys.exit(main())