# Importera standard-moduler
import xml.etree.ElementTree as etree
import io
import sys
import re
import time
import os
import subprocess
import configparser
import logging
import collections


# Import requests och crash nicely if it fails.
try:
    import requests
except ImportError:
    print("python-requests is required, 'pip install requests'")
    sys.exit(1)

# Configure the logger
logformat = logging.Formatter("%(levelname)s [%(asctime)s] %(message)s")
rootLogger = logging.getLogger()

# If passing the debug parameter we will log som debuginfo.
if "--debug" in sys.argv:
    rootLogger.setLevel(logging.DEBUG)

fileHandler = logging.FileHandler("pytg.log",mode="w")

fileHandler.setFormatter(logformat)
rootLogger.addHandler(fileHandler)

consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logformat)
rootLogger.addHandler(consoleHandler)

def main():  
    # boolean for brokenSettings
    brokenSettings = False
    
    # Configure the configparser
    settings = configparser.ConfigParser(allow_no_value=True, delimiters=('='))
    settings.optionxform = str # Make the configparser keep Case of the values from the .ini-file
    settings.read('settings.ini') # The ini-file
 
    # Try to read the values from settings.ini
    try:
        username = settings["auth"]["username"]
        password = settings["auth"]["password"]
    except:
        createdefault(settings, "auth")
        logging.error("settings.ini did not contain any auth-settings so I have created some defaults")
        logging.debug("",exc_info=True)
        brokenSettings = True
    
    # Add the publisher first in the cmservers list
    try:
        cmservers = [ value for key,value in settings.items("cmservers") if key == "publisher"]
        # Add the additional servers after.
        cmservers += [ value for key,value in settings.items("cmservers") if key != "publisher"]
    except:
        createdefault(settings, "cmservers")
        logging.error("settings.ini did not contain any cmserver-settings so I have created some defaults")
        logging.debug("",exc_info=True)
        brokenSettings = True

    try:
        devices = settings.items("devices")
    except:
        createdefault(settings, "devices")
        logging.error("settings.ini did not contain any device-settings so I have created some defaults")
        logging.debug("",exc_info=True)
        brokenSettings = True

    try:
        rrdtool = settings["paths"]["rrdtool"]
    except:
        createdefault(settings, "paths")
        logging.error("settings.ini did not contain any path-settings so I have created some defaults")
        logging.debug("",exc_info=True)
        brokenSettings = True

    try:
        companyname = settings["html"]["companyname"]
        companylogo = settings["html"]["companylogo"]
    except:
        createdefault(settings, "html")
        logging.error("settings.ini did not contain any path-settings so I have created some defaults")
        logging.debug("",exc_info=True)
        brokenSettings = True
        
    if brokenSettings:
        logging.critical("Check your settings.ini. It contains alot of default values which will not work")
        sys.exit(1)

    if not os.path.exists(rrdtool):
       logging.critical("Cannot find rrdtool.exe at {} will not continue".format(rrdtool))
       sys.exit(1)

    # Create the output paths
    imagepath = "images"
    dbpath = "databases"
    if not os.path.exists(imagepath):
        os.makedirs(imagepath)
        
    if not os.path.exists(dbpath):
        os.makedirs(dbpath)
    
    if "--runonce" in sys.argv and "--loop" not in sys.argv:
        print("Running once")
        print("Getting results")
        result = soaprequest(cmservers, username, password, devices)
        print("Updating DB and making graph")
        makerrdgraph(result, rrdtool)
        print("Done")
                
    elif "--loop" in sys.argv and "--runonce" not in sys.argv:
            print("Running until interrupted")
            while(True):
                print("Getting results")
                result = soaprequest(cmservers, username, password, devices)
                print("Updating DB and making graph")
                makerrdgraph(result,rrdtool)
                print("Sleeping for 60")
                time.sleep(60)
    
    else:
        print("pyTG: python Traffic Graphics\n"
              "\n"
              "--runonce\n"
              "Runs the program once.\n"
              "Useful if running from cron/schedueled tasks.\n"
              "\n"
              "--loop\n"
              "Runs the program until interrupted.\n"
              "Useful if sending to background or testing\n"
              "\n"
              "Everything else shows this prompt.\n")

def createdefault(settings, what):
    if what == "auth":
        # Auth settings
        settings["auth"] = {"username": "USERNAME", "password": "PASSWORD"}
    if what == "cmservers":
        # Server settings
        settings["cmservers"] = {"publisher": "127.0.0.1", "subscriber1": "SERVERNAME"}
    if what == "devices":
        # device settings
        settings["devices"] = {"Name_of_MGCP_GW": "Cisco MGCP Gateways",
                               "Name_of_SIP_Trunk": "Cisco SIP",
                               "Another_SIP_Trunk": "Cisco SIP",
                               "# Not supported ATM: Name_of_MGCP_GW::S0_SU0_DS1-0": "Cisco MGCP PRI Device",
                              }
    if what == "paths":
        # Paths
        settings["paths"] = {"rrdtool": "rrdtool\rrdtool.exe",
                            }
    if what == "html":
        # html
        settings["html"] = {"companyname": "World, INC",
                             "companylogo": "image.png",
                            }
    
    # Write the settings to file
    with open('settings.ini', 'w') as settingsfile:
        settings.write(settingsfile)

def soaprequest(cmservers=None,username=None,password=None, devices=None):
    # Because soap. Is used in requests
    headers = {"SOAPAction": "perfmonCollectCounterData"}
    # This is where we send the soap.
    location = "https://{}/perfmonservice/services/PerfmonPort".format(cmservers[0]) 
    # We put the result in a key:value dictionary
    result = {}
    # We make a set for the counters
    counters = set()
    # We loop through devices to figure out which counters are being used
    # Can probably be done better
    for key,value in devices:
        if value == "Cisco SIP":
            counters.add(value)
        if value == "Cisco MGCP Gateways":
            counters.add(value)
        if value == "Cisco MGCP PRI Device":
            counters.add(value)


    # We loop through all counters and all servers. I.e we send 1-SOAP * n-counter * n-server.
    # E.g (Cisco MGCP + Cisco SIP counter) * (Publisher + Subscriber) = 4 SOAP requests.
    # All is sent to the Publisher but adresses the different servers.
    for counter in counters:
        for server in cmservers:
           # We set location to the current server to even the load.
           location = "https://{}/perfmonservice/services/PerfmonPort".format(server)
           # Since we loop through all servers the value server and counter will have different content everytime
           perfmonCollectCounterData = createSoapRequest(server, counter)       
           # Sends perfmonCollectCounterData to the server and puts the answer in r. r is of the type response
           r = requests.post(location, data=perfmonCollectCounterData, auth=(username, password), verify=False, headers=headers)
           # XML magic. io.BytesIO make etree.parse belive that r.content is a file. etree.parse creates an etree(xml) object
           tree = etree.parse(io.BytesIO(r.content))
           # Find the root in the xml.
           root = tree.getroot()
           # Create a list of all counts of ArrayCounterInfo in the answer
           items = root.findall(".//ArrayOfCounterInfo")
       
           # Since ArrayCounterInfo is an array of counters and hence the only object
           # in the items-list we only iterate the first object in the items-list.
           for item in items[0]:
               try:
                   # If we are looking at a SIP device we want to use CallsInProgress
                   # If we are looking at a MGCP GW we want to use PRIChannelsActive
                   # If we are looking at a MGCP PRI we want to use CallsActive
                   if counter == "Cisco SIP":
                       currentDevice = re.search(r"^.*Cisco SIP\((.*)\)\\CallsInProgress$",item.findtext("Name")).group(1)
                   if counter == "Cisco MGCP Gateways":
                       currentDevice = re.search(r"^.*Cisco MGCP Gateways\((.*)\)\\PRIChannelsActive$",item.findtext("Name")).group(1)
                   if counter == "Cisco MGCP PRI Device":
                       currentDevice = re.search(r"^.*Cisco MGCP PRI Device\((.*)\)\\CallsActive$",item.findtext("Name")).group(1)
               except:
                   currentDevice = None
               # Check if currentDevice exist in the list of devices (devices) and in that case add the value to result.
               if [device[0] for device in devices if device[0] == currentDevice]:
                   if currentDevice in result:
                       result[currentDevice] += int(item.findtext("Value"))
                   else:
                       result[currentDevice] = int(item.findtext("Value"))
                  
    # Return dict. So that other functions can use the valuse
    logging.info(result)
    ordered_result = collections.OrderedDict(sorted(result.items()))
    logging.info(ordered_result)
    #return result
    return ordered_result

def makerrdgraph(currentValue=None,rrdtool=None):
    #html definitions
    index_top = """<!DOCTYPE html>
       <html>
       <head>
       <link rel="stylesheet" type="text/css" href="index.css">
       <meta http-equiv="refresh" content="60">
       <title>pyTG {0}</title>
       </head>
       <body>
       """.format(time.strftime("%a, %d %b %Y %H.%M.%S %Z"))
    index_middle = ""
    index_bottom = """
        <footer>
        <p>Last update: {0}</p>
        </footer>
        </body></html>
        """.format(time.strftime("%a, %d %b %Y %H.%M.%S %Z"))
    
    for kv in currentValue.items():
        # Create an RRD databse if none is existing.
        if not os.path.isfile("./databases/{0}.rrd".format(kv[0])):
            subprocess.run((r"{} create databases/{}.rrd "
                               "--step 60 "
                               "DS:CallsInProgress:GAUGE:120:0:5000 "
                               "RRA:AVERAGE:0.5:1:1440 "
                               "RRA:AVERAGE:0.5:60:168 "
                               "RRA:AVERAGE:0.5:1440:365 ")
                            .format(rrdtool, kv[0]), shell=True, check=True)

        # Uppdate the database
        subprocess.run(r"{} update databases\{}.rrd N:{}".format(rrdtool,kv[0],kv[1]), shell=True, check=True)

        # Create the graphs
        day_graph = 'images/{0}_1D.png --start end-12h --title "{0} - Calls In Progress 12 Hours"'.format(kv[0])
        week_graph = 'images/{0}_1W.png --start end-1w --title "{0} - Calls In Progress 1 Week"'.format(kv[0])
        month_graph = 'images/{0}_1M.png --start end-1m --title "{0} - Calls In Progress 1 Month"'.format(kv[0])
        year_graph = 'images/{0}_1Y.png --start end-1y --title "{0} - Calls In Progress 1 Year"'.format(kv[0])
        graph_settings = ('--vertical-label "Calls" '
                          '--width 1200 '
                          '--height 400 '
                          '--full-size-mode '
                          #'--slope-mode '
                          '--lower-limit=0 '
                          '--alt-autoscale-max '
                          'DEF:CIP=databases/{0}.rrd:CallsInProgress:AVERAGE '
                          'VDEF:Maximum=CIP,MAXIMUM '
                          'VDEF:Average=CIP,AVERAGE '
                          'VDEF:CallsInProgress=CIP,LAST '
                          'AREA:CIP#00FF00:Current\: '
                          'GPRINT:CallsInProgress:%.0lf\l '
                          'LINE2:Maximum#FF0000:Maximum\: '
                          'GPRINT:Maximum:%.0lf\l '
                          'LINE2:Average#0000FF:Average\: '
                          'GPRINT:Average:%.0lf\l '
                          'COMMENT:"Last update\: {1} "'
                          ).format(kv[0], time.strftime("%a, %d %b %Y %H.%M.%S %Z"))

        try:
            subprocess.run(r"{} graph {} {}".format(rrdtool, day_graph, graph_settings), shell=True, check=True)
            subprocess.run(r"{} graph {} {}".format(rrdtool, week_graph, graph_settings), shell=True, check=True)
            subprocess.run(r"{} graph {} {}".format(rrdtool, month_graph, graph_settings), shell=True, check=True)
            subprocess.run(r"{} graph {} {}".format(rrdtool, year_graph, graph_settings), shell=True, check=True)
        except:
            logging.debug("{} {} {}".format(rrdtool, day_graph, graph_settings ,exc_info=True))
        device_top = index_top
        device_bottom = index_bottom
        index_middle += '<p class="index">{0}</br><a href="{0}.html"><img src="{0}_1D.png" alt="{0}" class="index"></a></p>\n'.format(kv[0])
        device_middle = '''
                        <p class="device"><img src="{0}_1D.png" alt="{0}"></p>
                        <p class="device"><img src="{0}_1W.png" alt="{0}"></p>
                        <p class="device"><img src="{0}_1M.png" alt="{0}"></p>
                        <p class="device"><img src="{0}_1Y.png" alt="{0}"></p>
                        '''.format(kv[0])
        with open('images/{0}.html'.format(kv[0]), 'w') as file:
            file.write(device_top)
            file.write(device_middle)
            file.write(device_bottom)
            
    with open('images/index.html', 'w') as file:
        file.write(index_top)
        file.write(index_middle)
        file.write(index_bottom)

def createSoapRequest(server=None, counter=None):
     # Function that returns a usable soap-request.
     return '''
            <!--Perfmon API - perfmonCollectCounterData - Request-->
            <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:soap="http://schemas.cisco.com/ast/soap">
            <soapenv:Header/>
            <soapenv:Body>
            <soap:perfmonCollectCounterData>
            <soap:Host>{0}</soap:Host>
            <soap:Object>{1}</soap:Object>
            </soap:perfmonCollectCounterData>
            </soapenv:Body>
            </soapenv:Envelope>'''.format(server, counter)
    
if __name__ == "__main__":
    main()
