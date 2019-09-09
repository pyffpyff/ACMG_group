from __future__ import absolute_import
from datetime import datetime, timedelta
import logging
import sys
import json
import random
import copy
import time
import atexit
import math
import operator

from volttron.platform.vip.agent import Agent, BasicCore, core, Core, PubSub, compat
from volttron.platform.agent import utils
from volttron.platform.messaging import headers as headers_mod

#from ACMGClasses.CIP import wrapper
from ACMGAgent.CIP import tagClient
from ACMGAgent.Resources.misc import listparse, schedule
from ACMGAgent.Resources.mathtools import graph
from ACMGAgent.Resources import resource, groups, control, customer
from ACMGAgent.Agent import HomeAgent

from . import settings
from zmq.backend.cython.constants import RATE
from __builtin__ import True
from bacpypes.vlan import Node
from twisted.application.service import Service
from ACMGAgent.Resources.resource import LeadAcidBattery
from ACMGAgent.CIP.tagClient import readTags
#from _pydev_imps._pydev_xmlrpclib import loads
utils.setup_logging()
_log = logging.getLogger(__name__)

''''the UtilityAgent class represents the owner of the distribution 
infrastructure and chief planner for grid operations'''
class UtilityAgent(Agent):
    resourcePool = []
    standardCustomerEnrollment = {"message_subject" : "customer_enrollment",
                                  "message_type" : "new_customer_query",
                                  "message_target" : "broadcast",
                                  "rereg": False,
                                  "info" : ["name","location","resources","customerType"]
                                  }
    
    standardDREnrollment = {"message_subject" : "DR_enrollment",
                            "message_target" : "broadcast",
                            "message_type" : "enrollment_query",
                            "info" : "name"
                            }
    
       

    uid = 0


    def __init__(self,config_path,**kwargs):
        super(UtilityAgent,self).__init__(**kwargs)
        self.config = utils.load_config(config_path)
        self._agent_id = self.config['agentid']
        self.state = "init"
        
        self.t0 = time.time()
        self.name = self.config["name"]
        self.resources = self.config["resources"]
        self.Resources = []
        self.groupList = []
        self.supplyBidList = []
        self.demandBidList = []
        self.reserveBidList = []
        self.FaultTag = []
        self.outstandingSupplyBids = []
        self.outstandingDemandBids = []
        
        self.powerfactorTag = "powerfactor"
        sys.path.append('/usr/lib/python2.7/dist-packages')
        sys.path.append('/usr/local/lib/python2.7/dist-packages')
        print(sys.path)
        import mysql.connector
                      
        #DATABASE STUFF
        self.dbconn = mysql.connector.connect(user='smartgrid',password='ugrid123',host='localhost',database='testdbase')

        cursor = self.dbconn.cursor()
                      
        #recreate database tables
        cursor.execute('DROP TABLE IF EXISTS infmeas')
        cursor.execute('DROP TABLE IF EXISTS faults')
        cursor.execute('DROP TABLE IF EXISTS customers')
        cursor.execute('DROP TABLE IF EXISTS bids')
        cursor.execute('DROP TABLE IF EXISTS prices')
        cursor.execute('DROP TABLE IF EXISTS drevents')
        cursor.execute('DROP TABLE IF EXISTS transactions')
        cursor.execute('DROP TABLE IF EXISTS resources')
        cursor.execute('DROP TABLE IF EXISTS appliances')
        cursor.execute('DROP TABLE IF EXISTS appstate')
        cursor.execute('DROP TABLE IF EXISTS resstate')
        cursor.execute('DROP TABLE IF EXISTS plans')
        cursor.execute('DROP TABLE IF EXISTS efficiency')
        cursor.execute('DROP TABLE IF EXISTS relayfaults')
        cursor.execute('DROP TABLE IF EXISTS topology')
        cursor.execute('DROP TABLE IF EXISTS consumption')
        
        cursor.execute('CREATE TABLE IF NOT EXISTS infmeas (logtime TIMESTAMP, et DOUBLE, period INT, signame TEXT, value DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS faults (logtime TIMESTAMP, et DOUBLE, duration DOUBLE, node TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS customers (logtime TIMESTAMP, et DOUBLE, customer_name TEXT, customer_location TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS bids (logtime TIMESTAMP, et DOUBLE, period INT, id BIGINT UNSIGNED, side TEXT, service TEXT, aux_service TEXT, resource_name TEXT, counterparty_name TEXT, accepted BOOLEAN, acc_for TEXT, orig_rate DOUBLE, settle_rate DOUBLE, orig_amount DOUBLE, settle_amount DOUBLE)') 
        cursor.execute('CREATE TABLE IF NOT EXISTS prices (logtime TIMESTAMP, et DOUBLE, period INT, node TEXT, rate REAL)')
        cursor.execute('CREATE TABLE IF NOT EXISTS drevents (logtime TIMESTAMP, et DOUBLE, period INT, type TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS transactions (logtime TIMESTAMP, et DOUBLE, period INT, account_holder TEXT, transaction_type TEXT, amount DOUBLE, balance DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS resources (logtime TIMESTAMP, et DOUBLE, name TEXT, type TEXT, owner TEXT, location TEXT, max_power DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS appliances (logtime TIMESTAMP, et DOUBLE, name TEXT, type TEXT, owner TEXT, max_power DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS appstate (logtime TIMESTAMP, et DOUBLE, period INT, name TEXT, state DOUBLE, power DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS resstate (logtime TIMESTAMP, et DOUBLE, period INT, name TEXT, state DOUBLE, connected BOOLEAN, reference_voltage DOUBLE, setpoint DOUBLE, inputV DOUBLE, inputI DOUBLE, outputV DOUBLE, outputI DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS plans (logtime TIMESTAMP, et DOUBLE, period INT, planning_time DOUBLE, planner TEXT, cost DOUBLE, action TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS efficiency (logtime TIMESTAMP, et DOUBLE, period INT, generation DOUBLE, consumption DOUBLE, loss DOUBLE, unaccounted DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS relayfaults (logtime TIMESTAMP, et DOUBLE, period INT, location TEXT, measured TEXT, resistance DOUBLE)')
        cursor.execute('CREATE TABLE IF NOT EXISTS topology (logtime TIMESTAMP, et DOUBLE, period INT, topology TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS consumption (logtime TIMESTAMP, et DOUBLE, period INT, name TEXT, power DOUBLE)')
        
        cursor.close()              
                      
        #register exit function
        atexit.register(self.exit_handler,self.dbconn)
        
        #build grid model objects from the agent's a priori knowledge of system
        #infrastructure relays
        self.relays =[ groups.Relay("MAIN_MAIN_USER","load"),
                       groups.Relay("COM_MAIN_USER", "load"),
                       groups.Relay("COM_BUS1_USER", "load"),
                       groups.Relay("COM_BUS1LOAD1_USER", "load"),
                       groups.Relay("COM_BUS1LOAD2_USER", "load"),
                       groups.Relay("COM_BUS1LOAD3_USER", "load"),
                       groups.Relay("COM_BUS1LOAD4_USER", "load"),
                       groups.Relay("COM_BUS1LOAD5_USER", "load"),
                       groups.Relay("COM_BUS2_USER", "load"),
                       groups.Relay("COM_BUS2LOAD1_USER", "load"),
                       groups.Relay("COM_BUS2LOAD2_USER", "load"),
                       groups.Relay("COM_BUS2LOAD3_USER", "load"),
                       groups.Relay("COM_BUS2LOAD4_USER", "load"),
                       groups.Relay("COM_BUS2LOAD5_USER", "load"),
                       groups.Relay("IND_MAIN_USER", "load"),
                       groups.Relay("IND_BUS1_USER", "load"),
                       groups.Relay("IND_BUS1LOAD1_USER", "load"),
                       groups.Relay("IND_BUS1LOAD2_USER", "load"),
                       groups.Relay("IND_BUS1LOAD3_USER", "load"),
                       groups.Relay("IND_BUS1LOAD4_USER", "load"),
                       groups.Relay("IND_BUS1LOAD5_USER", "load"),
                       groups.Relay("IND_BUS2_USER", "load"),
                       groups.Relay("IND_BUS2LOAD1_USER", "load"),
                       groups.Relay("IND_BUS2LOAD2_USER", "load"),
                       groups.Relay("IND_BUS2LOAD3_USER", "load"),
                       groups.Relay("IND_BUS2LOAD4_USER", "load"),
                       groups.Relay("IND_BUS2LOAD5_USER", "load"),
                       groups.Relay("RES_MAIN_USER", "load"),
                       groups.Relay("RES_BUS1_USER", "load"),
                       groups.Relay("RES_BUS1LOAD1_USER", "load"),
                       groups.Relay("RES_BUS1LOAD2_USER", "load"),
                       groups.Relay("RES_BUS1LOAD3_USER", "load"),
                       groups.Relay("RES_BUS1LOAD4_USER", "load"),
                       groups.Relay("RES_BUS1LOAD5_USER", "load"),
                       groups.Relay("RES_BUS2_USER", "load"),
                       groups.Relay("RES_BUS2LOAD1_USER", "load"),
                       groups.Relay("RES_BUS2LOAD2_USER", "load"),
                       groups.Relay("RES_BUS2LOAD3_USER", "load"),
                       groups.Relay("RES_BUS2LOAD4_USER", "load"),
                       groups.Relay("RES_BUS2LOAD5_USER", "load"),
                       groups.Relay("RES_BUS3_USER", "load"),
                       groups.Relay("RES_BUS3LOAD1_USER", "load"),
                       groups.Relay("RES_BUS3LOAD2_USER", "load"),
                       groups.Relay("RES_BUS3LOAD3_USER", "load"),
                       groups.Relay("RES_BUS3LOAD4_USER", "load"),
                       groups.Relay("RES_BUS3LOAD5_USER", "load"),
                       groups.Relay("RES_BUS4_USER", "load"),
                       groups.Relay("RES_BUS4LOAD1_USER", "load"),
                       groups.Relay("RES_BUS4LOAD2_USER", "load"),
                       groups.Relay("RES_BUS4LOAD3_USER", "load"),
                       groups.Relay("RES_BUS4LOAD4_USER", "load"),
                       groups.Relay("RES_BUS4LOAD5_USER", "load"),
                       
                       ]
                                              
        self.nodes = [ groups.Node("AC.MAIN.MAIN.MAIN"),
                       groups.Node("AC.COM.MAIN.MAIN"),
                       groups.Node("AC.COM.BUS1.MAIN"),
                       groups.Node("AC.COM.BUS1.LOAD1"),
                       groups.Node("AC.COM.BUS1.LOAD2"),
                       groups.Node("AC.COM.BUS1.LOAD3"),
                       groups.Node("AC.COM.BUS1.LOAD4"),
                       groups.Node("AC.COM.BUS1.LOAD5"),
                       groups.Node("AC.COM.BUS2.MAIN"),
                       groups.Node("AC.COM.BUS2.LOAD1"),
                       groups.Node("AC.COM.BUS2.LOAD2"),
                       groups.Node("AC.COM.BUS2.LOAD3"),
                       groups.Node("AC.COM.BUS2.LOAD4"),
                       groups.Node("AC.COM.BUS2.LOAD5"),
                       groups.Node("AC.IND.MAIN.MAIN"),
                       groups.Node("AC.IND.BUS1.MAIN"),
                       groups.Node("AC.IND.BUS1.LOAD1"),
                       groups.Node("AC.IND.BUS1.LOAD2"),
                       groups.Node("AC.IND.BUS1.LOAD3"),
                       groups.Node("AC.IND.BUS1.LOAD4"),
                       groups.Node("AC.IND.BUS1.LOAD5"),
                       groups.Node("AC.IND.BUS2.MAIN"),
                       groups.Node("AC.IND.BUS2.LOAD1"),
                       groups.Node("AC.IND.BUS2.LOAD2"),
                       groups.Node("AC.IND.BUS2.LOAD3"),
                       groups.Node("AC.IND.BUS2.LOAD4"),
                       groups.Node("AC.IND.BUS2.LOAD5"),
                       groups.Node("AC.RES.MAIN.MAIN"),
                       groups.Node("AC.RES.BUS1.MAIN"),
                       groups.Node("AC.RES.BUS1.LOAD1"),
                       groups.Node("AC.RES.BUS1.LOAD2"),
                       groups.Node("AC.RES.BUS1.LOAD3"),
                       groups.Node("AC.RES.BUS1.LOAD4"),
                       groups.Node("AC.RES.BUS1.LOAD5"),
                       groups.Node("AC.RES.BUS2.MAIN"),
                       groups.Node("AC.RES.BUS2.LOAD1"),
                       groups.Node("AC.RES.BUS2.LOAD2"),
                       groups.Node("AC.RES.BUS2.LOAD3"),
                       groups.Node("AC.RES.BUS2.LOAD4"),
                       groups.Node("AC.RES.BUS2.LOAD5"),
                       groups.Node("AC.RES.BUS3.MAIN"),
                       groups.Node("AC.RES.BUS3.LOAD1"),
                       groups.Node("AC.RES.BUS3.LOAD2"),
                       groups.Node("AC.RES.BUS3.LOAD3"),
                       groups.Node("AC.RES.BUS3.LOAD4"),
                       groups.Node("AC.RES.BUS3.LOAD5"),
                       groups.Node("AC.RES.BUS4.MAIN"),
                       groups.Node("AC.RES.BUS4.LOAD1"),
                       groups.Node("AC.RES.BUS4.LOAD2"),
                       groups.Node("AC.RES.BUS4.LOAD3"),
                       groups.Node("AC.RES.BUS4.LOAD4"),
                       groups.Node("AC.RES.BUS4.LOAD5"),
                       ]   
#        for node in self.nodes:
#            print "node name: {nodename}".format(nodename = node.name)
#            print node
            
                      
        self.zones=[groups.Zone("AC.MAIN.MAIN.groups.Zone",[self.nodes[0]]),
                    groups.Zone("AC.COM.MAIN.groups.Zone",[self.nodes[1]]),
                    groups.Zone("AC.COM.BUS1.groups.Zone",[self.nodes[2]]),
                    groups.Zone("AC.COM.BUS1.groups.Zone1",[self.nodes[3]]),
                    groups.Zone("AC.COM.BUS1.groups.Zone2",[self.nodes[4]]),
                    groups.Zone("AC.COM.BUS1.groups.Zone3",[self.nodes[5]]),
                    groups.Zone("AC.COM.BUS1.groups.Zone4",[self.nodes[6]]),
                    groups.Zone("AC.COM.BUS1.groups.Zone5",[self.nodes[7]]),
                    groups.Zone("AC.COM.BUS2.groups.Zone",[self.nodes[8]]),
                    groups.Zone("AC.COM.BUS2.groups.Zone1",[self.nodes[9]]),
                    groups.Zone("AC.COM.BUS2.groups.Zone2",[self.nodes[10]]),
                    groups.Zone("AC.COM.BUS2.groups.Zone3",[self.nodes[11]]),
                    groups.Zone("AC.COM.BUS2.groups.Zone4",[self.nodes[12]]),
                    groups.Zone("AC.COM.BUS2.groups.Zone5",[self.nodes[13]]),
                    groups.Zone("AC.IND.MAIN.groups.Zone",[self.nodes[14]]),
                    groups.Zone("AC.IND.BUS1.groups.Zone",[self.nodes[15]]),
                    groups.Zone("AC.IND.BUS1.groups.Zone1",[self.nodes[16]]),
                    groups.Zone("AC.IND.BUS1.groups.Zone2",[self.nodes[17]]),
                    groups.Zone("AC.IND.BUS1.groups.Zone3",[self.nodes[18]]),
                    groups.Zone("AC.IND.BUS1.groups.Zone4",[self.nodes[19]]),
                    groups.Zone("AC.IND.BUS1.groups.Zone5",[self.nodes[20]]),
                    groups.Zone("AC.IND.BUS2.groups.Zone",[self.nodes[21]]),
                    groups.Zone("AC.IND.BUS2.groups.Zone1",[self.nodes[22]]),
                    groups.Zone("AC.IND.BUS2.groups.Zone2",[self.nodes[23]]),
                    groups.Zone("AC.IND.BUS2.groups.Zone3",[self.nodes[24]]),
                    groups.Zone("AC.IND.BUS2.groups.Zone4",[self.nodes[25]]),
                    groups.Zone("AC.IND.BUS2.groups.Zone5",[self.nodes[26]]),
                    groups.Zone("AC.RES.MAIN.groups.Zone",[self.nodes[27]]),
                    groups.Zone("AC.RES.BUS1.groups.Zone",[self.nodes[28]]),
                    groups.Zone("AC.RES.BUS1.groups.Zone1",[self.nodes[29]]),
                    groups.Zone("AC.RES.BUS1.groups.Zone2",[self.nodes[30]]),
                    groups.Zone("AC.RES.BUS1.groups.Zone3",[self.nodes[31]]),
                    groups.Zone("AC.RES.BUS1.groups.Zone4",[self.nodes[32]]),
                    groups.Zone("AC.RES.BUS1.groups.Zone5",[self.nodes[33]]),
                    groups.Zone("AC.RES.BUS2.groups.Zone",[self.nodes[34]]),
                    groups.Zone("AC.RES.BUS2.groups.Zone1",[self.nodes[35]]),
                    groups.Zone("AC.RES.BUS2.groups.Zone2",[self.nodes[36]]),
                    groups.Zone("AC.RES.BUS2.groups.Zone3",[self.nodes[37]]),
                    groups.Zone("AC.RES.BUS2.groups.Zone4",[self.nodes[38]]),
                    groups.Zone("AC.RES.BUS2.groups.Zone5",[self.nodes[39]]),
                    groups.Zone("AC.RES.BUS3.groups.Zone",[self.nodes[40]]),
                    groups.Zone("AC.RES.BUS3.groups.Zone1",[self.nodes[41]]),
                    groups.Zone("AC.RES.BUS3.groups.Zone2",[self.nodes[42]]),
                    groups.Zone("AC.RES.BUS3.groups.Zone3",[self.nodes[43]]),
                    groups.Zone("AC.RES.BUS3.groups.Zone4",[self.nodes[44]]),
                    groups.Zone("AC.RES.BUS3.groups.Zone5",[self.nodes[45]]),
                    groups.Zone("AC.RES.BUS4.groups.Zone",[self.nodes[46]]),
                    groups.Zone("AC.RES.BUS4.groups.Zone1",[self.nodes[47]]),
                    groups.Zone("AC.RES.BUS4.groups.Zone2",[self.nodes[48]]),
                    groups.Zone("AC.RES.BUS4.groups.Zone3",[self.nodes[49]]),
                    groups.Zone("AC.RES.BUS4.groups.Zone4",[self.nodes[50]]),
                    groups.Zone("AC.RES.BUS4.groups.Zone5",[self.nodes[51]]),
                    
                    ]
        self.Edges = []
        
        #global index for checking relay consistency
        self.edgeindex = 0
        self.Edges.append(self.nodes[0].addEdge(self.nodes[1], "to", "COM_MAIN_CURRENT", [self.relays[1]]))
        self.Edges.append(self.nodes[0].addEdge(self.nodes[14], "to", "IND_MAIN_CURRENT", [self.relays[14]]))
        self.Edges.append(self.nodes[0].addEdge(self.nodes[27], "to", "RES_MAIN_CURRENT", [self.relays[27]]))
        self.Edges.append(self.nodes[1].addEdge(self.nodes[2], "to", "COM_BUS1_CURRENT", [self.relays[2]]))
        self.Edges.append(self.nodes[1].addEdge(self.nodes[8], "to", "COM_BUS2_CURRENT", [self.relays[8]]))
        self.Edges.append(self.nodes[2].addEdge(self.nodes[3], "to", "COM_B1L1_CURRENT", [self.relays[3]]))              
        self.Edges.append(self.nodes[2].addEdge(self.nodes[4], "to", "COM_B1L2_CURRENT", [self.relays[4]]))
        self.Edges.append(self.nodes[2].addEdge(self.nodes[5], "to", "COM_B1L3_CURRENT", [self.relays[5]]))              
        self.Edges.append(self.nodes[2].addEdge(self.nodes[6], "to", "COM_B1L4_CURRENT", [self.relays[6]]))              
        self.Edges.append(self.nodes[2].addEdge(self.nodes[7], "to", "COM_B1L5_CURRENT", [self.relays[7]]))
        self.Edges.append(self.nodes[8].addEdge(self.nodes[9], "to", "COM_B2L1_CURRENT", [self.relays[9]]))                                                        
        self.Edges.append(self.nodes[8].addEdge(self.nodes[10], "to", "COM_B2L2_CURRENT", [self.relays[10]]))
        self.Edges.append(self.nodes[8].addEdge(self.nodes[11], "to", "COM_B2L3_CURRENT", [self.relays[11]]))
        self.Edges.append(self.nodes[8].addEdge(self.nodes[12], "to", "COM_B2L4_CURRENT", [self.relays[12]]))
        self.Edges.append(self.nodes[8].addEdge(self.nodes[13], "to", "COM_B2L5_CURRENT", [self.relays[13]]))
        self.Edges.append(self.nodes[14].addEdge(self.nodes[15], "to", "IND_BUS1_CURRENT", [self.relays[15]]))
        self.Edges.append(self.nodes[14].addEdge(self.nodes[21], "to", "IND_BUS2_CURRENT", [self.relays[21]]))
        self.Edges.append(self.nodes[15].addEdge(self.nodes[16], "to", "IND_B1L1_CURRENT", [self.relays[16]]))
        self.Edges.append(self.nodes[15].addEdge(self.nodes[17], "to", "IND_B1L2_CURRENT", [self.relays[17]]))
        self.Edges.append(self.nodes[15].addEdge(self.nodes[18], "to", "IND_B1L3_CURRENT", [self.relays[18]]))
        self.Edges.append(self.nodes[15].addEdge(self.nodes[19], "to", "IND_B1L4_CURRENT", [self.relays[19]]))
        self.Edges.append(self.nodes[15].addEdge(self.nodes[20], "to", "IND_B1L5_CURRENT", [self.relays[20]]))
        self.Edges.append(self.nodes[21].addEdge(self.nodes[22], "to", "IND_B2L1_CURRENT", [self.relays[22]]))
        self.Edges.append(self.nodes[21].addEdge(self.nodes[23], "to", "IND_B2L2_CURRENT", [self.relays[23]]))
        self.Edges.append(self.nodes[21].addEdge(self.nodes[24], "to", "IND_B2L3_CURRENT", [self.relays[24]]))
        self.Edges.append(self.nodes[21].addEdge(self.nodes[25], "to", "IND_B2L4_CURRENT", [self.relays[25]]))
        self.Edges.append(self.nodes[21].addEdge(self.nodes[26], "to", "IND_B2L5_CURRENT", [self.relays[26]]))             
        self.Edges.append(self.nodes[27].addEdge(self.nodes[28], "to", "RES_BUS1_CURRENT", [self.relays[28]]))
        self.Edges.append(self.nodes[27].addEdge(self.nodes[34], "to", "RES_BUS2_CURRENT", [self.relays[34]]))
        self.Edges.append(self.nodes[27].addEdge(self.nodes[40], "to", "RES_BUS3_CURRENT", [self.relays[40]]))
        self.Edges.append(self.nodes[27].addEdge(self.nodes[46], "to", "RES_BUS4_CURRENT", [self.relays[46]]))
        self.Edges.append(self.nodes[28].addEdge(self.nodes[29], "to", "RES_B1L1_CURRENT", [self.relays[29]]))
        self.Edges.append(self.nodes[28].addEdge(self.nodes[30], "to", "RES_B1L2_CURRENT", [self.relays[30]]))
        self.Edges.append(self.nodes[28].addEdge(self.nodes[31], "to", "RES_B1L3_CURRENT", [self.relays[31]]))
        self.Edges.append(self.nodes[28].addEdge(self.nodes[32], "to", "RES_B1L4_CURRENT", [self.relays[32]]))
        self.Edges.append(self.nodes[28].addEdge(self.nodes[33], "to", "RES_B1L5_CURRENT", [self.relays[33]]))
        self.Edges.append(self.nodes[34].addEdge(self.nodes[35], "to", "RES_B2L1_CURRENT", [self.relays[35]]))
        self.Edges.append(self.nodes[34].addEdge(self.nodes[36], "to", "RES_B2L2_CURRENT", [self.relays[36]]))
        self.Edges.append(self.nodes[34].addEdge(self.nodes[37], "to", "RES_B2L3_CURRENT", [self.relays[37]]))
        self.Edges.append(self.nodes[34].addEdge(self.nodes[38], "to", "RES_B2L4_CURRENT", [self.relays[38]]))
        self.Edges.append(self.nodes[34].addEdge(self.nodes[39], "to", "RES_B2L5_CURRENT", [self.relays[39]]))
        self.Edges.append(self.nodes[40].addEdge(self.nodes[41], "to", "RES_B3L1_CURRENT", [self.relays[41]]))
        self.Edges.append(self.nodes[40].addEdge(self.nodes[42], "to", "RES_B3L2_CURRENT", [self.relays[42]]))
        self.Edges.append(self.nodes[40].addEdge(self.nodes[43], "to", "RES_B3L3_CURRENT", [self.relays[43]]))
        self.Edges.append(self.nodes[40].addEdge(self.nodes[44], "to", "RES_B3L4_CURRENT", [self.relays[44]]))
        self.Edges.append(self.nodes[40].addEdge(self.nodes[45], "to", "RES_B3L5_CURRENT", [self.relays[45]]))
        self.Edges.append(self.nodes[46].addEdge(self.nodes[47], "to", "RES_B4L1_CURRENT", [self.relays[47]]))
        self.Edges.append(self.nodes[46].addEdge(self.nodes[48], "to", "RES_B4L2_CURRENT", [self.relays[48]]))
        self.Edges.append(self.nodes[46].addEdge(self.nodes[49], "to", "RES_B4L3_CURRENT", [self.relays[49]]))
        self.Edges.append(self.nodes[46].addEdge(self.nodes[50], "to", "RES_B4L4_CURRENT", [self.relays[50]]))
        self.Edges.append(self.nodes[46].addEdge(self.nodes[51], "to", "RES_B4L5_CURRENT", [self.relays[51]]))

        self.connMatrix = [[0 for x in range(len(self.nodes))] for y in range(len(self.nodes))]
        
        self.initnode()
        
        #import list of utility resources and make into object
        resource.makeResource(self.resources,self.Resources,False)
        for res in self.Resources:
 #           resloc =  res.location
 #           loclist = resloc.split('.')
 #           if type(loclist) is list:
 #               style, branch, bus, load = loclist
 #               resloc = bus + load
            
            for node in self.nodes:
              if (res.location == node.name):
                 
                 node.addResource(res)
                 
            self.dbnewresource(res,self.dbconn,self.t0)
           
 #           for relays in self.relays:
 #               relayname = relays.tagName
 #               relbranch = relays.branch
 #               relload = relays.bus
 #               if relbranch == branch and relload == resloc :
 #                   relays.closeRelay()
 #                   print("close relay {relay}".format(relay = relays.tagName))
                    
          
                
        self.perceivedInsol = .75 #per unit
        self.customers = []
        self.DRparticipants = []
        
        #local storage to ease load on tag server
        self.tagCache = {}
        
        now = datetime.now()
        end = datetime.now() + timedelta(seconds = settings.ST_PLAN_INTERVAL)
        self.CurrentPeriod = control.Period(0,now,end,self)
        
        self.NextPeriod = control.Period(1,end,end + timedelta(seconds = settings.ST_PLAN_INTERVAL),self)
        
        self.bidstate = BidState()
        
        self.CurrentPeriod.printInfo(0)
        self.NextPeriod.printInfo(0)
        
        
        
        self.powerfactorTag = "powerfactor" 
        self.TotalVoltageList = ["MAIN_VOLTAGE","COM_MAIN_VOLTAGE","IND_MAIN_VOLTAGE","RES_MAIN_VOLTAGE"]
        self.TotalCurrentList = [   "MAIN_CURRENT",
                                    "COM_MAIN_CURRENT",
                                    "COM_BUS1_CURRENT",
                                    "COM_B1L1_CURRENT",
                                    "COM_B1L2_CURRENT",
                                    "COM_B1L3_CURRENT",
                                    "COM_B1L4_CURRENT",
                                    "COM_B1L5_CURRENT",
                                    "COM_BUS2_CURRENT",
                                    "COM_B2L1_CURRENT",
                                    "COM_B2L2_CURRENT",
                                    "COM_B2L3_CURRENT",
                                    "COM_B2L4_CURRENT",
                                    "COM_B2L5_CURRENT",
                                    "IND_MAIN_CURRENT",
                                    "IND_BUS1_CURRENT",
                                    "IND_B1L1_CURRENT",
                                    "IND_B1L2_CURRENT",
                                    "IND_B1L3_CURRENT",
                                    "IND_B1L4_CURRENT",
                                    "IND_B1L5_CURRENT",
                                    "IND_BUS2_CURRENT",
                                    "IND_B2L1_CURRENT",
                                    "IND_B2L2_CURRENT",
                                    "IND_B2L3_CURRENT",
                                    "IND_B2L4_CURRENT",
                                    "IND_B2L5_CURRENT",
                                    "RES_MAIN_CURRENT",
                                    "RES_BUS1_CURRENT",
                                    "RES_B1L1_CURRENT",
                                    "RES_B1L2_CURRENT",
                                    "RES_B1L3_CURRENT",
                                    "RES_B1L4_CURRENT",
                                    "RES_B1L5_CURRENT",
                                    "RES_BUS2_CURRENT",
                                    "RES_B2L1_CURRENT",
                                    "RES_B2L2_CURRENT",
                                    "RES_B2L3_CURRENT",
                                    "RES_B2L4_CURRENT",
                                    "RES_B2L5_CURRENT",
                                    "RES_BUS3_CURRENT",
                                    "RES_B3L1_CURRENT",
                                    "RES_B3L2_CURRENT",
                                    "RES_B3L3_CURRENT",
                                    "RES_B3L4_CURRENT",
                                    "RES_B3L5_CURRENT",
                                    "RES_BUS4_CURRENT",
                                    "RES_B4L1_CURRENT",
                                    "RES_B4L2_CURRENT",
                                    "RES_B4L3_CURRENT",
                                    "RES_B4L4_CURRENT",
                                    "RES_B4L5_CURRENT"]
        
    def initnode(self):
        self.relays[0].closeRelay()
        self.relays[1].closeRelay()
        self.relays[2].closeRelay()
        self.relays[3].openRelay()
        self.relays[4].openRelay()
        self.relays[5].openRelay()
        self.relays[6].openRelay()
        self.relays[7].openRelay()
        self.relays[8].closeRelay()
        self.relays[9].openRelay()
        self.relays[10].openRelay()
        self.relays[11].openRelay()
        self.relays[12].openRelay()
        self.relays[13].openRelay()
        self.relays[14].closeRelay()
        self.relays[15].closeRelay()
        self.relays[16].openRelay()
        self.relays[17].openRelay()
        self.relays[18].openRelay()
        self.relays[19].openRelay()
        self.relays[20].openRelay()
        self.relays[21].closeRelay()
        self.relays[22].openRelay()
        self.relays[23].openRelay()
        self.relays[24].openRelay()
        self.relays[25].openRelay()
        self.relays[26].openRelay()
        self.relays[27].closeRelay()
        self.relays[28].closeRelay()
        self.relays[29].openRelay()
        self.relays[30].openRelay()
        self.relays[31].openRelay()
        self.relays[32].openRelay()
        self.relays[33].openRelay()
        self.relays[34].closeRelay()
        self.relays[35].openRelay()
        self.relays[36].openRelay()
        self.relays[37].openRelay()
        self.relays[38].openRelay()
        self.relays[39].openRelay()
        self.relays[40].closeRelay()
        self.relays[41].openRelay()
        self.relays[42].openRelay()
        self.relays[43].openRelay()
        self.relays[44].openRelay()
        self.relays[45].openRelay()
        self.relays[46].closeRelay()
        self.relays[47].openRelay()
        self.relays[48].openRelay()
        self.relays[49].openRelay()
        self.relays[50].openRelay()
        self.relays[51].openRelay()

       
        
    def exit_handler(self,dbconn):
        print('UTILITY {me} exit handler'.format(me = self.name))
        
        #disconnect any connected loads
        for cust in self.customers:
            cust.disconnectCustomer()
        
        #disconnect all utility-owned sources
        for res in self.Resources:
            res.disconnectSource()
        
        #close database connection
        dbconn.close()    
        
    @Core.receiver('onstart')
    def setup(self,sender,**kwargs):
        _log.info(self.config['message'])
        self._agent_id = self.config['agentid']
        self.state = "setup"
        
        self.vip.pubsub.subscribe('pubsub','energymarket', callback = self.marketfeed)
        self.vip.pubsub.subscribe('pubsub','demandresponse',callback = self.DRfeed)
        self.vip.pubsub.subscribe('pubsub','customerservice',callback = self.customerfeed)
        self.vip.pubsub.subscribe('pubsub','weatherservice',callback = self.weatherfeed)
        
        #self.printInfo(2)
              
        self.discoverCustomers()
        #solicit bids for next period, this function schedules a delayed function call to process
        #the bids it has solicited
        self.solicitBids()
        
        #schedule planning period advancement
        self.core.schedule(self.NextPeriod.startTime,self.advancePeriod)
        
        #schedule first customer enrollment attempt
        sched = datetime.now() + timedelta(seconds = 4)            
        delaycall = self.core.schedule(sched,self.discoverCustomers)
        
        #schedule bid solicitation for first period
        sched = datetime.now() + timedelta(seconds = 11)
        self.core.schedule(sched,self.sendBidSolicitation)
        
        
        faultnode = self.faultDetector()
        if faultnode:
            subs = self.updateTopology(faultnode)
        else:
            subs = self.getTopology()
        if settings.DEBUGGING_LEVEL >= 2:
            print("for setup, UTILITY {me} THINKS THE TOPOLOGY IS {top}".format(me = self.name, top = subs))
            
        
        
    @Core.periodic(10)
    def getNowcast(self):
        mesdict = {}
        mesdict["message_sender"] = self.name
        mesdict["message_target"] = "Goddard"
        mesdict["message_subject"] = "nowcast"
        mesdict["message_type"] = "nowcast_request"
        #mesdict["requested_data"] = ["temperature"]
        
        mes = json.dumps(mesdict)
        self.vip.pubsub.publish("pubsub","weatherservice",{},mes)

    '''callback for weatherfeed topic'''
    def weatherfeed(self, peer, sender, bus, topic, headers, message):
        mesdict = json.loads(message)
        messageSubject = mesdict.get('message_subject',None)
        messageTarget = mesdict.get('message_target',None)
        messageSender = mesdict.get('message_sender',None)
        messageType = mesdict.get("message_type",None)
        #if we are the intended recipient
        if listparse.isRecipient(messageTarget,self.name):    
            if messageSubject == "nowcast":
                responses = mesdict.get("responses",None)
                if responses:
                    solar = responses["solar_irradiance"]
                    if solar:
                        self.perceivedInsol = solar

    '''callback for customer service topic. This topic is used to enroll customers
    and manage customer accounts.'''    
    def customerfeed(self, peer, sender, bus, topic, headers, message):
        #load json message
        try:
            mesdict = json.loads(message)
        except Exception as e:
            print("customerfeed message to {me} was not formatted properly".format(me = self))
        #determine intended recipient, ignore if not us    
        messageTarget = mesdict.get("message_target",None)
        if listparse.isRecipient(messageTarget,self.name):
            
            if settings.DEBUGGING_LEVEL >= 3:
                print(message)
            
            messageSubject = mesdict.get("message_subject",None)
            messageType = mesdict.get("message_type",None)
            messageSender = mesdict.get("message_sender",None)
            if messageSubject == "customer_enrollment":
                #if the message is a response to new customer solicitation
                if messageType == "new_customer_response":
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("UTILITY {me} RECEIVED A RESPONSE TO CUSTOMER ENROLLMENT SOLICITATION FROM {them}".format(me = self.name, them = messageSender))
                    
                    name, location, resources, customerType = mesdict.get("info")                        
                    
                    #create a new object to represent customer in our database 
                    dupobj = listparse.lookUpByName(name,self.customers)
                    if dupobj:
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("HOMEOWNER {me} has already registered {cust}".format(me = self.name, cust = name))
                        return
                    else:   
                        if customerType == "residential":
                            cust = customer.ResidentialCustomerProfile(name,location,resources,2)
                            self.customers.append(cust)
                        elif customerType == "commercial":
                            cust = customer.CommercialCustomerProfile(name,location,resources,5)
                            self.customers.append(cust)
                        elif customerType == "industrial":
                            cust = customer.IndustrialCustomerProfile(name,location,resources,5)
                            self.customers.append(cust)
                        
                        else:                        
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("HOMEOWNER {me} doesn't recognize customer type".format(me = self.name))
                                return
                            
                        self.dbnewcustomer(cust,self.dbconn,self.t0)
                            
                        #add customer to Node object
                        for node in self.nodes:
                            if cust.location.split(".")== node.name.split("."):
                                newnode, newrelay, newedge = node.addCustomer(cust)
                                
                                #add new graph objects to lists - this causes problems because we can't measure voltage at loads
                                #self.nodes.append(newnode)
                                #self.relays.append(newrelay)
                                #self.Edges.append(newedge)
                                
                                if node.group:
                                    node.group.customers.append(cust)
                                    print("-----test test-----")
                                    print("group {num} contains following customers: {cus}".format(num = node.group.name, cus = messageSender))
        
                        
                        for resource in resources:
                            print("NEW RESOURCE: {res}".format(res = resource))
                            foundmatch = False
                            for node in self.nodes:
                                if node.name.split(".") == resource["location"].split("."):
                                    resType = resource.get("type",None)
                                    if resType == "LeadAcidBattery":
                                        newres = customer.LeadAcidBatteryProfile(**resource)
                                    elif resType == "ACresource":
                                        newres = customer.GeneratorProfile(**resource)
                                    else:
                                        print("unsupported resource type")
                                    node.addResource(newres)
                                    cust.addResource(newres)
                                    if node.group:
                                        node.group.resources.append(newres)
                                    foundmatch = True
                            if not foundmatch:
                                print("couldn't find a match for {loc}".format(loc = resource["location"]))
                        
                        
                            
                        if settings.DEBUGGING_LEVEL >= 1:
                            print("\nNEW CUSTOMER ENROLLMENT ##############################")
                            print("UTILITY {me} enrolled customer {them}".format(me = self.name, them = name))
                            cust.printInfo(0)
                            if settings.DEBUGGING_LEVEL >= 3:
                                print("...and here's how they did it:\n {mes}".format(mes = message))
                            print("#### END ENROLLMENT NOTIFICATION #######################")
                        
                        resdict = {}
                        resdict["message_subject"] = "customer_enrollment"
                        resdict["message_type"] = "new_customer_confirm"
                        resdict["message_target"] = name
                        response = json.dumps(resdict)
                        self.vip.pubsub.publish(peer = "pubsub",topic = "customerservice", headers = {}, message = response)
                        
                        if settings.DEBUGGING_LEVEL >= 1:
                            print("let the customer {name} know they've been successfully enrolled by {me}".format(name = name, me = self.name))
                            
                        #one more try
                        sched = datetime.now() + timedelta(seconds = 2)
                        self.core.schedule(sched,self.sendBidSolicitation)
                        
                    
            elif messageSubject == "request_connection":
                #the utility has the final say in whether a load can connect or not
                #look up customer object by name
                cust = listparse.lookUpByName(messageSender,self.customers)
                if cust.permission:
                    cust.connectCustomer()  
                    
                              
                    for node in self.nodes:
                        if node.name == cust.location:
                            node.connectNode()
                            print("connectNode {nodename} to connected customer {cust}".format(nodename = node.name, cust = cust.name))
                        
       
                       
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("{me} GRANTING CONNECTION REQUEST. {their} MAY CONNECT IN PERIOD {per}".format(me = self.name, their = messageSender, per = self.CurrentPeriod.periodNumber))
                else:
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("{me} DENYING CONNECTION REQUEST. {their} HAS NO PERMISSION TO CONNECT IN PERIOD {per}".format(me = self.name, their = messageSender, per = self.CurrentPeriod.periodNumber))
            else:
                pass
    

    #called to send a DR enrollment message. when a customer has been enrolled
    #they can be called on to increase or decrease consumption to help the utility
    #meet its goals   
    def solicitDREnrollment(self, name = "broadcast"):
        mesdict = {}
        mesdict["message_subject"] = "DR_enrollment"
        mesdict["message_type"] = "enrollment_query"
        mesdict["message_target"] = name
        mesdict["message_sender"] = self.name
        mesdict["info"] = "name"
        
        message = json.dumps(mesdict)
        self.vip.pubsub.publish(peer = "pubsub", topic = "demandresponse", headers = {}, message = message)
        
        if settings.DEBUGGING_LEVEL >= 1:
            print("UTILITY {me} IS TRYING TO ENROLL {them} IN DR SCHEME".format(me = self.name, them = name))

    #the accountUpdate() function polls customer power consumption/production
    #and updates account balances according to their rate '''
    @Core.periodic(settings.ACCOUNTING_INTERVAL)
    def accountUpdate(self):
        #need more consideration
        print("UTILITY {me} ACCOUNTING ROUTINE".format(me = self.name))
        for group in self.groupList:
            for cust in group.customers:
                power = cust.measurePower()
                
                self.dbconsumption(cust,power,self.dbconn,self.t0)
                
                energy = power*settings.ACCOUNTING_INTERVAL
                balanceAdjustment = -energy*group.rate*cust.rateAdjustment
                if type(balanceAdjustment) is float or type(balanceAdjustment) is int:
                    if abs(balanceAdjustment) < 450 and abs(balanceAdjustment) > .001:
                        cust.customerAccount.adjustBalance(balanceAdjustment)
                        #update database
                        self.dbtransaction(cust,balanceAdjustment,"net home consumption",self.dbconn,self.t0)
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("The account of {holder} has been adjusted by {amt} units for net home consumption".format(holder = cust.name, amt = balanceAdjustment))
                else:
                    print("HOMEOWNER {me} RECEIVED NaN FOR POWER MEASUREMENT".format(me = self.name))
                
                
            
            for res in group.resources:
                if res.owner != self.name:
                    
                    cust = listparse.lookUpByName(res.owner,self.customers)
                    
                    if cust:
                        if res.location != cust.location:
                            print("resource {res} not co-located with {cust}".format(res = res.name, cust = cust.name))
                            #if resources are not colocated, we need to account for them separately
                            power = res.getDischargePower() - res.getChargePower()
                            energy = power*settings.ACCOUNTING_INTERVAL
                            balanceAdjustment = energy*group.rate*cust.rateAdjustment
                            
                            if type(balanceAdjustment) is float or type(balanceAdjustment) is int:
                                if abs(balanceAdjustment) < 450 and abs(balanceAdjustment) > .001:
                                    cust.customerAccount.adjustBalance(balanceAdjustment)
                            
                                    #update database
                                    self.dbtransaction(cust,balanceAdjustment,"remote resource",self.dbconn,self.t0)
                            
                        else:
                            print("TEMP DEBUG: resource {res} is co-located with {cust}".format(res = res.name, cust = cust.name))
                    else:
                        print("TEMP-DEBUG: can't find owner {own} for {res}".format(own = res.owner, res = res.name))

        print("current and voltage in period {num}:".format(num = self.CurrentPeriod.periodNumber))  
        for index in range(len(self.TotalCurrentList)):
        #    print("it is normal?")
            name = self.TotalCurrentList[index]
            value = tagClient.readTags([self.TotalCurrentList[index]],"load")
            print("{tag}: {value}".format(tag = name,value = value))
            

#        print("{tag}: {value}".format(tag = "MAIN_VOLTAGE",value = tagClient.readTags(["MAIN_VOLTAGE"],"load")))
        print("{tag}: {value}".format(tag = "COM_MAIN_VOLTAGE",value = tagClient.readTags(["COM_MAIN_VOLTAGE"],"load")))
        print("{tag}: {value}".format(tag = "IND_MAIN_VOLTAGE",value = tagClient.readTags(["IND_MAIN_VOLTAGE"],"load")))
        print("{tag}: {value}".format(tag = "RES_MAIN_VOLTAGE",value = tagClient.readTags(["RES_MAIN_VOLTAGE"],"load")))
                    
#        for number in range(len(self.TotalVoltageList)):
#            name = self.TotalVoltageList[number]
#            value = tagClient.readTags([self.TotalVoltageList[number]],"load")
#            print("{tag}: {value}".format(tag = name,value = value))
        print("{tag}: {value}".format(tag = self.powerfactorTag,value = tagClient.readTags([self.powerfactorTag],"load")))
     
   
    
    @Core.periodic(settings.LT_PLAN_INTERVAL)
    def planLongTerm(self):
        pass

    @Core.periodic(settings.ANNOUNCE_PERIOD_INTERVAL)
    def announcePeriod(self):    
        mesdict = {"message_sender" : self.name,
                   "message_target" : "broadcast",
                   "message_subject" : "announcement",
                   "message_type" : "period_announcement",
                   "period_number" : self.NextPeriod.periodNumber,
                   "start_time" : self.NextPeriod.startTime.isoformat(),
                   "end_time" : self.NextPeriod.endTime.isoformat()
                   }
        
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY {me} ANNOUNCING period {pn} starting at {t}".format(me = self.name, pn = mesdict["period_number"], t = mesdict["start_time"]))
        message = json.dumps(mesdict)
        self.vip.pubsub.publish("pubsub","energymarket",{},message)

    def announceRate(self, recipient, rate, period):
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY {me} ANNOUNCING RATE {rate} to {rec} for period {per}".format(me = self.name, rate = rate, rec = recipient.name, per = period.periodNumber))
        mesdict = {"message_sender" : self.name,
                   "message_subject" : "rate_announcement",
                   "message_target" : recipient.name,
                   "period_number" : period.periodNumber,
                   "rate" : rate
                   }
        message = json.dumps(mesdict)
        self.vip.pubsub.publish("pubsub","energymarket",{},message)

     #solicit bids for the next period
    def solicitBids(self):
        faultnode = self.faultDetector()
#        subs = self.getTopology()
        if faultnode:
            subs = self.updateTopology(faultnode)
        else:
            subs = self.getTopology()
#        self.printInfo(2)
        if settings.DEBUGGING_LEVEL >= 2:
            print("for bid solicitation, UTILITY {me} THINKS THE TOPOLOGY IS {top}".format(me = self.name, top = subs))
        
        self.announceTopology()
        
        #first we have to find out how much it will cost to get power
        #from various sources, both those owned by the utility and by 
        #customers
        
        #clear the bid list in preparation for receiving new bids
        self.supplyBidList = []
        self.reserveBidList = []
        self.demandBidList = []
        
        #send bid solicitations to all customers who are known to have resources
        self.sendBidSolicitation()
        
        sched = datetime.now() + timedelta(seconds = settings.BID_SUBMISSION_INTERVAL)            
        delaycall = self.core.schedule(sched,self.planShortTerm)
        
    #sends bid solicitation without rescheduling call to planning function or finding topology
    def sendBidSolicitation(self):
        if settings.DEBUGGING_LEVEL >=2 :
            print("\nUTILITY {me} IS ASKING FOR BIDS FOR PERIOD {per}".format(me = self.name, per = self.NextPeriod.periodNumber))
        
        self.bidstate.acceptall()
        for group in self.groupList:
            print("---send bid solicitation---")
            group.printInfo()
            for cust in group.customers:
                #cust.printInfo()
                # ask about consumption
                mesdict = {}
                mesdict["message_sender"] = self.name
                mesdict["message_subject"] = "bid_solicitation"
                mesdict["side"] = "demand"
                mesdict["message_target"] = cust.name
                mesdict["period_number"] = self.NextPeriod.periodNumber
                mesdict["solicitation_id"] = self.uid
                self.uid += 1
                
                mess = json.dumps(mesdict)
                self.vip.pubsub.publish(peer = "pubsub", topic = "energymarket", headers = {}, message = mess)
                    
                if settings.DEBUGGING_LEVEL >= 2:
                    print("UTILITY {me} SOLICITING CONSUMPTION BIDS FROM {them}".format(me = self.name, them = cust.name))
                    
                
                if cust.resources:
                    #ask about bulk power
                    mesdict = {}
                    mesdict["message_sender"] = self.name
                    mesdict["message_subject"] = "bid_solicitation"
                    mesdict["side"] = "supply"
                    mesdict["service"] = "power"
                    mesdict["message_target"] = cust.name
                    mesdict["period_number"] = self.NextPeriod.periodNumber
                    mesdict["solicitation_id"] = self.uid
                    self.uid += 1
                    
                    mess = json.dumps(mesdict)
                    self.vip.pubsub.publish(peer = "pubsub", topic = "energymarket", headers = {}, message = mess)
                    
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("UTILITY {me} SOLICITING BULK POWER BIDS FROM {them}".format(me = self.name, them = cust.name))
                    
                    #ask about reserves                    
                    mesdict["solicitation_id"] = self.uid
                    mesdict["service"] = "reserve"
                    self.uid += 1
                    
                    mess = json.dumps(mesdict)
                    self.vip.pubsub.publish(peer = "pubsub", topic = "energymarket", headers = {}, message = mess)
                    
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("UTILITY {me} SOLICITING RESERVE POWER BIDS FROM {them}".format(me = self.name, them = cust.name))

    #determines whether the bid belongs in the auction for the group
    def bidforgroup(self,bid,group):
     #   print("group is {group}".format(group = group.name))
        for resource in group.resources:
            print(resource.name)
        for customer in group.customers:
            print(customer.name)
       
        if bid.resourceName:
            #if the bid is for a specific device, we are interested in the location of the device
            name = bid.resourceName
        #    print("bid's resource is: {reso}".format(reso = name))            
            for res in group.resources:
                if name == res.name:
         #           print("resource's name is: {rname}".format(rname = res.name))
                    return True
        
        else:
            #otherwise, we are interested in the membership of the home
            name = bid.counterparty
        #    print("bid's counterparty is: {cou}".format(cou = name))
            for cust in group.customers:
                if name == cust.name:
         #           print("customer's name is: {cname}".format(cname = cust.name))
                    return True
                
        return False
        

    def planShortTerm(self):
        if settings.DEBUGGING_LEVEL >= 2:
            print("\nUTILITY {me} IS FORMING A NEW SHORT TERM PLAN FOR PERIOD {per}".format(me = self.name,per = self.NextPeriod.periodNumber))
        
        #tender bids for the utility's own resources

#need more consideration!!!





#rate for two resources need more consideration
#from demand equation, rate should have negative relation with demand amount 
#how totalsupply comes? it comes from bids.amount and how it 
        
       
        for res in self.Resources:
            newbid = None
           
            if type(res) is resource.ACresource:
                amount = res.maxDischargePower
                rate = res.fuelCost + 0.01*random.randint(0,9)
                newbid = control.SupplyBid(**{"resource_name": res.name, "side":"supply", "service":"power", "amount": amount, "rate":rate, "counterparty":self.name, "period_number": self.NextPeriod.periodNumber})
                if newbid:
                    print("UTILITY {me} ADDING OWN BID {id} TO LIST".format(me = self.name, id = newbid.uid))
                    self.supplyBidList.append(newbid)
                    self.outstandingSupplyBids.append(newbid)
                    
                    #write to database
                    self.dbnewbid(newbid,self.dbconn,self.t0)
            
            elif type(res) is resource.LeadAcidBattery:
                amount = res.SOC
                print("battery totally have {am} power".format(am = amount))
#                rate = max(control.ratecalc(res.capCost,.05,res.amortizationPeriod,.05),res.capCost/res.cyclelife) + 0.005*amount + 0.01*random.randint(0,9)
                rate = 0.01*random.randint(0,9)
                newbid = control.SupplyBid(**{"resource_name": res.name, "side":"supply", "service":"reserve", "amount": amount, "rate":rate, "counterparty": self.name, "period_number": self.NextPeriod.periodNumber})
                if newbid:
                    print("UTILITY {me} ADDING OWN BID {id} TO LIST".format(me = self.name, id = newbid.uid))
                    self.reserveBidList.append(newbid)
                    
                    #write to database
                    self.dbnewbid(newbid,self.dbconn,self.t0)
            
            else:
                print("trying to plan for an unrecognized resource type")
            
            
        for group in self.groupList:
            group.demandBidList = []
            group.supplyBidList = []
            print("this is bid solicitaion for group {num}".format(num = group.name))
            #??how to get total power of every load 
            maxLoad = 0
            for bid in self.demandBidList:
            #    print("this is demand bid: {db} ".format(db = bid.counterparty))
                if self.bidforgroup(bid,group):
             #       print("bid is in this group")
                    group.demandBidList.append(bid)
                    maxLoad += bid.amount
            print("maxLoad:{maxLoad}".format(maxLoad = maxLoad))  
            maxSupply = 0
            for bid in self.supplyBidList:
            #    print("this is supply bid: {sb} ".format(sb = bid.counterparty))
                if self.bidforgroup(bid, group):
            #        print("bid is in this group")
                    group.supplyBidList.append(bid)
                    maxSupply += bid.amount
            print("maxSupply:{maxSupply}".format(maxSupply = maxSupply))    
            for bid in self.reserveBidList:
            #    print("this is reserve bid: {sb} ".format(sb = bid.resourceName))
                if len(self.groupList)>1:
                    if group.name == "group2":
            #        print("bid is in this group")
                        group.reserveBidList.append(bid)
                else:
                        group.reserveBidList.append(bid)
            reserveneed = 0
            if maxLoad > maxSupply:
                reserveneed = 1
            
            #sort array of supplier bids by rate from low to high
            group.supplyBidList.sort(key = operator.attrgetter("rate"))
            #sort array of consumer bids by rate from high to low
            group.demandBidList.sort(key = operator.attrgetter("rate"),reverse = True)
                   
            if settings.DEBUGGING_LEVEL >= 2:
                print("\n\nPLANNING for GROUP {group} for PERIOD {per}: worst case load is {max}".format(group = group.name, per = self.NextPeriod.periodNumber, max = maxLoad))
                print(">>here are the supply bids:")
                for bid in group.supplyBidList:                    
                    bid.printInfo(0)
                print(">>here are the reserve bids:")
                for bid in group.reserveBidList:                    
                    bid.printInfo(0)          
                print(">>here are the demand bids:")          
                for bid in group.demandBidList:                    
                    bid.printInfo(0)
            
            qrem = 0                #leftover part of bid
            supplyindex = 0
            demandindex = 0
            partialsupply = False
            partialdemand = False
            sblen = len(group.supplyBidList)
            rblen = len(group.reserveBidList)
            dblen = len(group.demandBidList)
            
            
            while supplyindex < sblen and demandindex < dblen:
                
                supbid = group.supplyBidList[supplyindex]
                dembid = group.demandBidList[demandindex]
                
                print("supplybid rate: {sup}".format(sup = supbid.rate))
                print("demandbid rate: {dem}".format(dem = dembid.rate))
                if settings.DEBUGGING_LEVEL >= 2:
                    print("\ndemand index: {di}".format(di = demandindex))
                    print("supply index: {si}".format(si = supplyindex))
                    
                if dembid.rate >= supbid.rate:
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("demand rate {dr} > supply rate {sr}".format(dr = dembid.rate, sr = supbid.rate))
                        
                    group.rate = dembid.rate
                    if partialsupply:
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("partial supply bid: {qr} remaining".format(qr = qrem))
                        
                        if qrem > dembid.amount:                            
                            qrem -= dembid.amount
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("still {qr} remaining in supply bid".format(qr = qrem))
                            partialsupply = True
                            
                            dembid.partialdemand = False
                            partialdemand = False
                            dembid.leftamount = 0
                            dembid.accepted = True
                            demandindex += 1
                        elif qrem < dembid.amount:        
                            qrem = dembid.amount - qrem
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("exhausted supply bid, now {qr} left in demand bid".format(qr = qrem))
                            partialsupply = False
                            dembid.partialdemand = True
                            partialdemand = True
                            dembid.leftamount = qrem
                            supbid.accepted = True
                            supplyindex += 1                            
                        else:
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("exact match in bids")
                            qrem = 0
                            partialsupply = False
                            dembid.partialdemand = False
                            partialdemand = False
                            dembid.leftamount = 0     
                            supbid.accepted = True   
                            dembid.accepted = True 
                            supplyindex += 1
                            demandindex += 1       
                    elif partialdemand:
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("partial demand bid: {qr} remaining".format(qr = qrem))
                            
                        if qrem > supbid.amount:
                            qrem -= supbid.amount
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("still {qr} remaining in supply bid".format(qr = qrem))
                            partialsupply = False
                            dembid.partialdemand = True
                            partialdemand = True
                            dembid.leftamount = qrem
                            supbid.accepted = True
                            supplyindex += 1
                        elif qrem < supbid.amount:
                            qrem = supbid.amount - qrem
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("exhausted demand bid, now {qr} left in supply bid".format(qr = qrem))
                            partialsupply = True
                            dembid.partialdemand = False
                            partialdemand = False
                            dembid.accepted = True
                            demandindex += 1
                        else:
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("exact match in bids")
                            qrem = 0
                            partialsupply = False
                            dembid.partialdemand = False
                            partialdemand = False
                            supbid.accepted = True   
                            dembid.accepted = True 
                            supplyindex += 1
                            demandindex += 1
                    else:
                        if settings.DEBUGGING_LEVEL >= 2:
                                print("no partial bids")
                                
                        if dembid.amount > supbid.amount:
                            qrem = dembid.amount - supbid.amount
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("{qr} remaining in demand bid".format(qr = qrem))
                            dembid.partialdemand = True
                            paritaldemand = True
                            dembid.leftamount = qrem
                            partialsupply = False
                            supbid.accepted = True
                            dembid.accepted = True
                            supplyindex += 1
                        elif dembid.amount < supbid.amount:
                            qrem = supbid.amount - dembid.amount
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("{qr} remaining in supply bid".format(qr = qrem))
                            dembid.partialdemand = False
                            partialdemand = False
                            partialsupply = True
                            supbid.accepted = True
                            dembid.accepted = True
                            demandindex += 1
                        else:
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("bids match exactly")
                            qrem = 0
                            partialsupply = False
                            partialdeand = False
                            supbid.accepted = True
                            dembid.accepted = True
                            supplyindex += 1
                            demandindex += 1
                        
                else:
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("PAST EQ PRICE! demand rate {dr} < supply rate {sr}".format(dr = dembid.rate, sr = supbid.rate))
                    if partialsupply:
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("still partial supply bid to take care of")
                        supbid.accepted = True
                        supbid.modified = True
                        supbid.amount -= qrem
                        dembid.accepted = False
                        partialsupply = False
                        dembid.partialdemand = False
                        partialdemand = False
                    elif partialdemand:
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("still partial demand bid to take care of")
                        dembid.accepted = True
                        dembid.modified = True
                        dembid.amount -= qrem
                        supbid.accepted = False
                        partialsupply = False
                        dembid.partialdemand = False
                        partialdemand = False
                    else:
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("reject and skip...")
                        supbid.accepted = False
                        dembid.accepted = False
                    supplyindex += 1
                    demandindex += 1
            
            while supplyindex < sblen:
                supbid = group.supplyBidList[supplyindex]
                if settings.DEBUGGING_LEVEL >= 2:
                    print(" out of loop, still cleaning up supply bids {si}".format(si = supplyindex))
                if partialsupply:
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("partial supply bid to finish up")
                    supbid.accepted = True
                    supbid.modified = True
                    supbid.amount -= qrem
                    partialsupply = False
                    dembid.partialdemand = False
                    partialdemand = False
                else:
                    if supbid.auxilliaryService:
                        if supbid.auxilliaryService == "reserve":
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("UTILITY {me} placing rejected power bid {bid} in reserve list".format(me = self.name, bid = supbid.uid))
                                
                            
                            group.supplyBidList.remove(supbid)
                            sblen = len(group.supplyBidList)
                            group.reserveBidList.append(supbid)
                            supbid.service = "reserve"
                    else:
                        supbid.accepted = False
                supplyindex += 1
                
            while demandindex < dblen:
                dembid = group.demandBidList[demandindex]
                if settings.DEBUGGING_LEVEL >= 2:
                    print(" out of loop, still cleaning up demand bids {di}".format(di = demandindex))
                if partialdemand:
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("partial demand bid to finish up")
                    dembid.accepted = True
                    dembid.modified = True
                    dembid.amount -= qrem
                    partialsupply = False
                    dembid.partialdemand = True
                    partialdemand = False
                    dembid.leftamount = qrem
                else:
                    dembid.accepted = False
                demandindex += 1
            
            totalsupply = 0
            #notify the counterparties of the terms on which they will supply power
            for bid in group.supplyBidList:
                #print("total supply contains:")
                if bid.accepted:
                    print(bid.resourceName)
                    totalsupply += bid.amount
                    bid.rate = group.rate
                    self.sendBidAcceptance(bid, group.rate)
                    res = listparse.lookUpByName(bid.resourceName,group.resources)
                    '''location = res.location
                    loclist = location.split('.')
                    grid, branch, bus, load = loclist
                    if load == "MAIN":
                        relayname = "{branch}_{bus}_USER".format(branch = branch, bus = bus, load = load)
                    else:
                        relayname = "{branch}_{bus}{load}_USER".format(branch = branch, bus = bus, load = load)
                    for relay in self.relays:
                        if relay.tagName == relayname:
                            relay.closeRelay()
                            print("close relay for connected resource")
                    '''
                    #update bid's entry in database
                    self.dbupdatebid(bid,self.dbconn,self.t0)
                    
                    #self.NextPeriod.plan.addBid(bid)
                    self.NextPeriod.supplybidmanager.acceptedbids.append(bid)
                    
                    
                    #give customer permission to connect if resource is co-located
                    cust = listparse.lookUpByName(bid.counterparty,self.customers)
                    if cust:
                        if res.location == cust.location:
                            cust.permission = True   
                else:
                    self.sendBidRejection(bid, group.rate)   
                    #update bid's entry in database
                    self.dbupdatebid(bid,self.dbconn,self.t0)
                    
            totaldemand = 0        
            for bid in group.demandBidList:
                #look up customer object corresponding to bid
                cust = listparse.lookUpByName(bid.counterparty,self.customers)
                if bid.accepted:
                    totaldemand += bid.amount
                    
            print("start battery supply")
            group.reserveBidList.sort(key = operator.attrgetter("rate"))
            totalreserve = 0
            leftbidlist = []         
           
            for bid in group.reserveBidList:
                bid.printInfo(0)
                print("maxLoad ({ml})- totalsupply({ts}): {tr}".format( ml = maxLoad,ts = totalsupply, tr = maxLoad-totalsupply))
                if totalreserve < (maxLoad - totalsupply) and (maxLoad - totalsupply) > 0.001 and bid.amount > 0.022:
                    totalreserve += (bid.amount - 0.02)
                    print("totalreserve = {tr}".format(tr = totalreserve))
                    
                    for leftbid in group.demandBidList:
                        #amount = 0
                        print("leftbid in demandBidlist")
                        leftbid.printInfo(0)
                        print("leftbid accepted {accept}".format(accept = leftbid.accepted))
                        print("leftbid left amount: {leftamount}".format(leftamount = leftbid.leftamount))
                        
                        if leftbid.accepted == 0 :
                            leftbid.leftamount = leftbid.amount
                            leftbidlist.append(leftbid) 
                            print("create leftbid list")
                        elif leftbid.partialdemand == True:
                            leftbid.acceptedamount = leftbid.amount
                            print("amount: {am}".format(am=leftbid.acceptedamount))
                            leftbid.amount = leftbid.leftamount
                            leftbidlist.append(leftbid)
                            print("create leftbid list")
                        else:
                            pass 
                    #for bid in leftbidlist:
                    #    bid.printInfo(0)   
                           
                    leftbidlist.sort(key=operator.attrgetter("rate"),reverse = True)
                    leftlen = len(leftbidlist)
                    leftindex = 0
                    partialreserve = False
                    qrem = totalreserve
                    print("leftindex: {li}".format(li=leftindex))
                    print("leftlen: {len}".format(len=leftlen))
                    while leftindex<leftlen:    
                        leftbid = leftbidlist[leftindex]
                    #    print("leftbid:")  
                        leftbid.printInfo()   
                        print("bid rate(reserve):{rate1}".format(rate1 = bid.rate))
                        print("leftbid rate(demand):{rate2}".format(rate2 = leftbid.rate))                  
                        if bid.rate < leftbid.rate:
                            group.rate = leftbid.rate
                            print("reserve bid rate < leftbid rate")                            
                            if qrem > leftbid.amount:
                                qrem -= leftbid.amount
                                leftbid.accepted = True
                                leftindex += 1
                                leftbid.amount = leftbid.acceptedamount + leftbid.leftamount
                                print("reserve still left {q}".format(q=qrem))
                            else:
                                if qrem > 0:
                                    leftbid.accepted = True
                                    leftbid.amount = qrem + leftbid.acceptedamount
                                    qrem = 0
                                    leftindex += 1
                                    print("partial reserve")
                                else:
                                    leftbid.accepted = False
                                    leftindex += 1
                                    print("reserve is used up")
                        else:
                            leftbid.accepted = False
                            leftindex += 1
                    bid.amount = bid.amount - qrem - 0.02
                    if bid.amount > 0.01:
                        bid.accepted = True  
                        bid.rate = group.rate 
                                
                        print("reserve bid accepted")
                        print("bid amount = {ba}, bid rate = {rate}".format(ba = bid.amount, rate = bid.rate))                                        
                    else:
                        bid.accepted = False 
                                    
                else: 
                    bid.accepted = False
            for leftbid in leftbidlist:
                if leftbid.accepted == True:
            #        print("pay attention here!!!")
                    #for Res in self.Resources:
                    #    if type(Res)==resource.LeadAcidBattery:
                    #        state = Res.statebehaviorcheck(SOC,leftbid.amount)
                    #        if state == False:
                    #            leftbid.amount = SOC - 0.02
                    #        else:
                    #            break  
                        
                    print("leftbid amount:{la}".format(la = leftbid.amount))
                    print("leftbid left amount:{lla}".format(lla = leftbid.leftamount))
                    self.sendBidAcceptance(leftbid, group.rate)
                    #update bid's entry in database
                    self.dbupdatebid(leftbid,self.dbconn,self.t0)
                                        
                    #self.NextPeriod.plan.addConsumption(bid)
                    self.NextPeriod.demandbidmanager.readybids.append(leftbid)
                                            
                    #give customer permission to connect
                    cust.permission = True 
                            
                    
            for bid in group.reserveBidList:
                if bid.accepted:
                    self.sendBidAcceptance(bid,group.rate)
                    
                    #update bid's entry in database
                    self.dbupdatebid(bid,self.dbconn,self.t0)
                    res = listparse.lookUpByName(bid.resourceName,group.resources)
                    '''location = res.location
                    loclist = location.split('.')
                    grid, branch, bus, load = loclist
                    if load == "MAIN":
                        relayname = "{branch}_{bus}_USER".format(branch = branch, bus = bus, load = load)
                    else:
                        relayname = "{branch}_{bus}{load}_USER".format(branch = branch, bus = bus, load = load)
                    for relay in self.relays:
                        if relay.tagName == relayname:
                            relay.closeRelay()
                            print("close relay for connected resource")
                    '''
                    #self.NextPeriod.plan.addBid(bid)
                    self.NextPeriod.supplybidmanager.acceptedbids.append(bid)
                else:
                    self.sendBidRejection(bid,group.rate)
                #update bid's entry in database
                self.dbupdatebid(bid,self.dbconn,self.t0)
                #give customer permission to connect if resource is co-located
                cust = listparse.lookUpByName(bid.counterparty,self.customers)
                if cust:
                    if res.location == cust.location:
                        cust.permission = True
                    
            self.bidstate.reserveonly()
            
            #notify the counterparties of the terms on which they will consume power
            for bid in group.demandBidList:
                #look up customer object corresponding to bid
                cust = listparse.lookUpByName(bid.counterparty,self.customers)
                if bid.accepted:
                    totaldemand += bid.amount
                    bid.rate = group.rate
                                        
                    self.sendBidAcceptance(bid, group.rate)
                    print("send demand bid acceptance")
                    #update bid's entry in database
                    self.dbupdatebid(bid,self.dbconn,self.t0)
                        
                    #self.NextPeriod.plan.addConsumption(bid)
                    self.NextPeriod.demandbidmanager.readybids.append(bid)
                        
                    #give customer permission to connect
                    cust.permission = True 
                    print("customer {cus} get permission".format(cus = cust.name))                   
                    
                else:
                    self.sendBidRejection(bid, group.rate)
                    #update bid's entry in database
                    self.dbupdatebid(bid,self.dbconn,self.t0)
                    #customer does not have permission to connect
                    cust.permission = False
                                   
            #announce rates for next period
            for cust in group.customers:
                self.announceRate(cust,group.rate,self.NextPeriod)
        
        
        for plan in self.NextPeriod.plans:
            print("next period supply bid manager:")
            self.NextPeriod.plan.printInfo(0)



        def sendDR(self,target,type,duration):
            mesdict = {"message_subject" : "DR_event",
                       "message_sender" : self.name,
                       "message_target" : target,
                       "event_id" : random.getrandbits(32),
                       "event_duration": duration,
                       "event_type" : type
                        }
            message = json.dumps(mesdict)
            self.vip.pubsub.publish("pubsub","demandresponse",{},message)

        '''scheduled initially in init, the advancePeriod() function makes the period for
        which we were planning into the period whose plan we are carrying out at the times
        specified in the period objects. it schedules another call to itself each time and 
        also runs the enactPlan() function to actuate the planned actions for the new
        planning period ''' 


    def advancePeriod(self):
        self.bidstate.acceptnone()
        #make next period the current period and create new object for next period
        self.CurrentPeriod = self.NextPeriod
        self.NextPeriod = control.Period(self.CurrentPeriod.periodNumber+1,self.CurrentPeriod.endTime,self.CurrentPeriod.endTime + timedelta(seconds = settings.ST_PLAN_INTERVAL),self)
        
        if settings.DEBUGGING_LEVEL >= 1:
            print("UTILITY AGENT {me} moving into new period:".format(me = self.name))
            self.CurrentPeriod.printInfo(0)
        
        #call enactPlan
        print("now start enact this period plan")
     #   print("-------test test-------")
        self.enactPlan()
     #   subs = self.getTopology()
     #   self.printInfo(2)
        
     #   self.announceTopology()
      
     #   print("-------test test-------")
        #solicit bids for next period, this function schedules a delayed function call to process
        #the bids it has solicited
        print("now start solicit bids for next period")
        self.solicitBids()
                
        #schedule next advancePeriod call
        self.core.schedule(self.NextPeriod.startTime,self.advancePeriod)
        self.announcePeriod()
        
        #determine distribution system efficiency
        #self.efficiencyAssessment()
        
        #reset customer permissions
        #for cust in self.customers:
        #    cust.permission = False
        
        #responsible for enacting the plan which has been defined for a planning period
    def enactPlan(self):
     
        #which resources are being used during this period? keep track with this list
        involvedResources = []
        #change setpoints
        grouprate = 0
        #if self.CurrentPeriod.plans:
        for elem in self.Resources:
            if type(elem) is resource.LeadAcidBattery:
                SOC = elem.SOC
                print("my current SOC: {soc}".format(soc=SOC))
         
        if self.CurrentPeriod.supplybidmanager.acceptedbids:
            #plan = self.CurrentPeriod.plans[0]
            if settings.DEBUGGING_LEVEL >= 2:
                print("UTILITY {me} IS ENACTING ITS PLAN FOR PERIOD {per}".format(me = self.name, per = self.CurrentPeriod.periodNumber))
                     
            self.CurrentPeriod.supplybidmanager.printInfo()    
            for bid in self.CurrentPeriod.supplybidmanager.acceptedbids:
                if bid.counterparty == self.name:                    
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("UTILITY {me} IS ACTUATING BID {bid}".format(me = self.name, bid = bid.uid))
                    
                    bid.printInfo(0)
                    res = listparse.lookUpByName(bid.resourceName,self.Resources)
                    location = res.location
                    
                    for node in self.nodes:
                        if node.name == res.location:
                            node.connectNode()
                            print("connectNode {nodename} to connected resource {res}".format(nodename = node.name, res=res.name))
                    
             #       loclist = location.split('.')
             #       grid, branch, bus, load = loclist
             #       if load == "MAIN":
             #           relayname = "{branch}_{bus}_USER".format(branch = branch, bus = bus, load = load)
             #       else:
             #           relayname = "{branch}_{bus}{load}_USER".format(branch = branch, bus = bus, load = load)
             #       for relay in self.relays:
             #           if relay.tagName == relayname:
             #               relay.closeRelay()
             #               print("close relay {relayname} for connected resource {res}".format(relayname = relayname, res=bid.resourceName))
                               
                    if res is not None:
                        involvedResources.append(res)
                        print("involved resource: {res}".format(res = res.name))
                        #if the resource is already connected, change the setpoint
                        if res.connected == True:
                            if settings.DEBUGGING_LEVEL >= 2:
                                print(" Resource {rname} is already connected".format(rname = res.name))
                            if bid.service == "power":
                                #res.DischargeChannel.ramp(bid.amount)
                                #res.DischargeChannel.changeSetpoint(bid.amount)
                                res.setDisposition(bid.amount, 0)
                                grouprate = bid.rate
                                if settings.DEBUGGING_LEVEL >= 2:
                                    print("Power resource {rname} setpoint to {amt}".format(rname = res.name, amt = bid.amount))
                            elif bid.service == "reserve":
                                #res.DischargeChannel.ramp(.1)            
                                #res.DischargeChannel.changeReserve(bid.amount,-.2)
                                while bid.amount > 0:
                                    setpointamount = SOC - bid.amount
                                    print("SOC is : {soc}, bid amount is : {ba}, set point amount is: {spa}".format(soc = SOC, ba = bid.amount, spa = setpointamount))
                                    for Res in self.Resources:
                                        if type(Res) == resource.LeadAcidBattery:
                                            state = Res.statebehaviorcheck(SOC,bid.amount)
                                    if state == True:
                                        res.setSOC(setpointamount)
                                        grouprate = bid.rate
                                        break
                                    else:
                                        bid.amount -= 0.01
                                bid.service = "power"
                                self.dbupdatebid(bid,self.dbconn,self.t0)
                                print("now bid service is: {ser}".format(ser=bid.service))
                                if settings.DEBUGGING_LEVEL >= 2:
                                    print("Committed resource {rname} as a reserve with setpoint: {amt}".format(rname = res.name, amt = bid.amount))
                            
                            #    res.setDisposition(bid.amount,-0.2)
                            #    grouprate = bid.rate
                            #    if settings.DEBUGGING_LEVEL >= 2:
                            #        print("Reserve resource {rname} setpoint to {amt}".format(rname = res.name, amt = bid.amount))
                        #if the resource isn't connected, connect it and ramp up power
                        else:
                            if bid.service == "power":
                                #res.connectSourceSoft("Preg",bid.amount)
                                #res.DischargeChannel.connectWithSet(bid.amount,0)
                                res.setDisposition(bid.amount,0)
                                grouprate = bid.rate
                                if settings.DEBUGGING_LEVEL >= 2:
                                    print("Connecting resource {rname} with setpoint: {amt}".format(rname = res.name, amt = bid.amount))
                            elif bid.service == "reserve":
                                #res.connectSourceSoft("Preg",.1)
                                #res.DischargeChannel.connectWithSet(bid.amount, -.2)
                                while bid.amount > 0:
                                    setpointamount = SOC - bid.amount
                                    print("SOC is : {soc}, bid amount is : {ba}, set point amount is: {spa}".format(soc = SOC, ba = bid.amount, spa = setpointamount))
                                    for Res in self.Resources:
                                        if type(Res) == resource.LeadAcidBattery:
                                            state = Res.statebehaviorcheck(SOC,bid.amount)
                                    if state == True:
                                        res.setSOC(setpointamount)
                                        grouprate = bid.rate
                                        break
                                    else:
                                        bid.amount -= 0.01
                                bid.service = "power"
                                self.dbupdatebid(bid,self.dbconn,self.t0)
                                print("now bid service is: {ser}".format(ser=bid.service))
                                if settings.DEBUGGING_LEVEL >= 2:
                                    print("Committed resource {rname} as a reserve with setpoint: {amt}".format(rname = res.name, amt = bid.amount))
                            res.connected = True    
            #disconnect resources that aren't being used anymore
            for res in self.Resources:
                if res not in involvedResources:
                    print("{res} resource not involved".format(res = res.name))
                    print("resource connection: {con}".format(con = res.connected))
                    if res.connected == True:
                        #res.disconnectSourceSoft()
                        res.DischargeChannel.disconnect()
                        res.connected = False
                        location = res.location
                        
                        for node in self.nodes:
                            if node.name == res.location:
                                node.isolateNode()
                                print("disconnectNode {nodename} for disconnected resource {res}".format(nodename = node.name, res=res.name))
                    
                        
                        
    #                    loclist = location.split('.')
    #                    grid, branch, bus, load = loclist
    #                    if load == "MAIN":
    #                        relayname = "{branch}_{bus}_USER".format(branch = branch, bus = bus, load = load)
    #                    else:
    #                        relayname = "{branch}_{bus}{load}_USER".format(branch = branch, bus = bus, load = load)
    #                    for relay in self.relays:
    #                        if relay.tagName == relayname:
    #                            relay.openRelay()
    #                            print("open relay {relayname} for disconnected resource {res}".format(relayname = relayname, res=res.name))
                     
                    
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("Resource {rname} no longer required and is being disconnected".format(rname = res.name))
        
        for elem in self.Resources:
            if type(elem) is resource.LeadAcidBattery:
                SOC = elem.SOC
                        
        for elem in self.Resources:
            if type(elem) is resource.ACresource:
                ACmax = elem.maxDischargePower
        
        for bid in self.supplyBidList:
            if bid.resourceName == "ACresource":
                chargerate = bid.rate
                print("charge rate is : {cr}".format(cr = chargerate))
                if bid.accepted == True:
                    leftamount = ACmax - bid.amount
                else:
                    leftamount = ACmax
                print("AC max is: {acmax}, bid amount is: {bidamount}, left amount is: {leftamount}".format(acmax = ACmax, bidamount=bid.amount,leftamount=leftamount))
                
        for elem in self.Resources:
            if type(elem) is resource.LeadAcidBattery:
                SOC = elem.SOC
                print("current SOC before charging is: {soc}".format(soc=SOC))
                chargeamount = 0
                if elem.SOC < .6:
                    for bid in self.reserveBidList:
                        if bid.accepted == False:
                        
                            if (leftamount+elem.SOC) > 0.98:
                                #print("bid amount: {bidamount}".format(bidamount=bid.amount))
                                chargeamount = 0.98 - elem.SOC
                                elem.setSOC(0.98)
                                print("charging battery to 0.98")
                                print("battery SOC now is: {soc}".format(soc=elem.SOC))
                                for bid in self.reserveBidList:
                                    
                                    bid.amount = 0.98 - SOC
                                    if bid.amount > 0:
                                        bid.accepted = True
                                        bid.rate = chargerate
                                        self.sendBidAcceptance(bid,chargerate)
                                        #update to database
                                        self.dbupdatebid(bid,self.dbconn,self.t0)
                                    print("updatebid")
                                                                
                            else:
                                elem.setSOC(leftamount+elem.SOC)
                                print("charging battery to {la}".format(la = leftamount))
                                print("battery SOC now is: {soc}".format(soc=elem.SOC))
                                chargeamount = leftamount
                                bid.amount = leftamount
                                if bid.amount > 0:
                                    bid.accepted = True
                                    bid.rate = chargerate
                                    self.sendBidAcceptance(bid,chargerate)
                                    #update to database
                                    self.dbupdatebid(bid,self.dbconn,self.t0)
                                print("updatebid")
                                
                            res = listparse.lookUpByName(bid.resourceName,self.Resources)
                            location = res.location
                            loclist = location.split('.')
                            grid, branch, bus, load = loclist
                            if load == "MAIN":
                                relayname = "{branch}_{bus}_USER".format(branch = branch, bus = bus, load = load)
                            else:
                                relayname = "{branch}_{bus}{load}_USER".format(branch = branch, bus = bus, load = load)
                                for relay in self.relays:
                                    if relay.tagName == relayname:
                                        relay.closeRelay()
                                        print("close relay {relayname} for connected resource {res}".format(relayname = relayname, res=bid.resourceName))
                            elem.connected = True
                            print("battery connection: {con}".format(con = elem.connected))
                            involvedResources.append(elem)
                        else:
                            break  
                    for bid in self.supplyBidList:
                        if bid.resourceName == "ACresource":
                            bid.amount += chargeamount
                            self.sendBidAcceptance(bid,bid.rate)
                            #update to database
                            self.dbupdatebid(bid,self.dbconn,self.t0)
                                
        
            
        Cap = self.CapNumber()
        tagClient.writeTags(["TOTAL_CAP_DEMAND"], [Cap], "load")
        print("capacitor number:")
        print(Cap)        
        #need to put into database also

    '''   def repairgrid(self):
        print("Utility {nam} attempting to merge as many groups as possible".format(nam = self.name))
        tookaction = False
        
        #look for connections in between nodes of nonfaulted groups
        pastouters = []
        for outergroup in self.groupList:
            pastouters.append(outergroup)
            if outergroup.hasGroundFault():
                pass
            else:
                for innergroup in self.groupList:
                    #avoid treating reciprocal relationships as unique
                    if innergroup not in pastouters:
                        #don't try to reconnect to faulted groups
                        if innergroup.hasGroundFault():
                            pass
                        #outer and inner groups are not the same and neither is faulted                    
                        else:
                            print("temp debug: looking at groups {og} and {ig}".format(og = outergroup.name, ig = innergroup.name))
                            remedialactions = []
                            
                            #look through all nodes in the group                        
                            for node in outergroup.nodes:
                                #are any nodes in the other group?
                                for edge in node.terminatingedges:
                                    if edge.startNode in innergroup.nodes:
                                        #found a pair of nodes
                                        print("UTILITY {nam} found a connection between groups {og} and {ig} in edge {edg}".format(nam = self.name, og = outergroup.name, ig = innergroup.name, edg = edge.name))
                                        #edge.closeRelays()
                                        #return True
                                        
                                        action = faults.GroupMerger(edge)
                                        remedialactions.append(action)
                                
                                for edge in node.originatingedges:
                                    if edge.endNode in innergroup.nodes:
                                        #found a pair of nodes
                                        print("UTILITY {nam} found a connection between groups {og} and {ig} in edge {edg}".format(nam = self.name, og = outergroup.name, ig = innergroup.name, edg = edge.name))
                                        #edge.closeRelays()
                                        #return True
                                        
                                        action = faults.GroupMerger(edge)
                                        remedialactions.append(action)
                            
                                #if any edges may be closed to join innergroup and outergroup
                                if remedialactions:
                                    #determine which of the remedial actions is best
                                    bestaction = None
                                    for action in remedialactions:
                                        if bestaction:
                                            if action.utilafter > bestaction.utilafter:
                                                bestaction = action
                                        else:
                                            bestaction = action
                                            
                                    print("UTILITY {nam} chose {edg} as best connection between {og} and {ig}".format(nam = self.name, edg = bestaction.edge.name, og = outergroup.name, ig = innergroup.name))
                                                
                                    bestaction.edge.closeRelays()
                                    tookaction = True
                                    
        return tookaction
                                        
    
    def groundFaultHandler(self,*argv):
        fault = argv[0]
        zone = argv[1]
        if fault is None:
            fault = zone.newGroundFault()
            
            self.dbgroundfaultevent(fault,"newly suspected fault",self.dbconn,self.t0)
            
                            
            #is an existing node in the zone already persistently faulted?
            for node in zone.nodes:
                for exfault in node.faults:
                    if exfault is not fault:
                        if exfault.__class__.__name__ == "GroundFault":
                            print("utility agent looking up existing fault {id}".format(id=exfault.uid))
                            if exfault.state == "persistent":
                                if node in exfault.faultednodes:
                                    
                                    if node not in fault.isolatednodes:
                                        fault.isolatednodes.append(node)
                                        
                                    if node not in fault.faultednodes:
                                        fault.faultednodes.append(node)
                                        
                                    if node not in fault.persistentnodes:
                                        fault.persistentnodes.append(node)
                                    
            if settings.DEBUGGING_LEVEL >= 2:
                fault.printInfo()

        if fault.state == "suspected":
            iunaccounted = zone.sumCurrents()
            if abs(iunaccounted) > settings.UNACCOUNTED_CURRENT_THRESHOLD:                
                
                #pick a node to isolate first - lowest priority first
                zone.rebuildpriorities()
                selnode = zone.nodeprioritylist[0]
                
                fault.isolateNode(selnode)
                
                if settings.DEBUGGING_LEVEL >= 1:
                    print("FAULT {id}: unaccounted current {cur} indicates ground fault({sta}). Isolating node {nod}".format(id = fault.uid, cur = iunaccounted, sta = fault.state, nod = selnode.name))
                                
                
                #update fault state
                fault.state = "unlocated"
                #reschedule ground fault handler
                schedule.msfromnow(self,1000,self.groundFaultHandler,fault,zone)
                
                self.dbgroundfaultevent(fault,"suspected fault confirmed",self.dbconn,self.t0,iunaccounted)
            else:
                #no problem
                 
                if settings.DEBUGGING_LEVEL >= 1:
                    print("FAULT {id}: suspected fault resolved".format(id = fault.uid))
                
                fault.cleared()
                
                self.dbgroundfaultevent(fault,"suspected fault resolved",self.dbconn,self.t0,iunaccounted)
               
                            
        elif fault.state == "unlocated":
            #check zone to see if fault condition persists
            iunaccounted = zone.sumCurrents()
            if abs(iunaccounted) > settings.UNACCOUNTED_CURRENT_THRESHOLD:
                zone.rebuildpriorities()
                selnode = None
                for node in zone.nodeprioritylist:
                    if node not in fault.isolatednodes:
                        selnode = node
                        break
                #there is another node to try in the zone
                if selnode:
                    if settings.DEBUGGING_LEVEL >= 1:
                        print("FAULT {id}: unaccounted current of {cur} indicates ground fault still unlocated. Isolating node {sel}".format(id = fault.uid, cur = iunaccounted, sel = selnode.name))
                        if settings.DEBUGGING_LEVEL >= 2:
                            fault.printInfo()
                                
                    fault.isolateNode(selnode)
                                
                    #reschedule ground fault handler
                    schedule.msfromnow(self,1000,self.groundFaultHandler,fault,zone)
                    
                    self.dbgroundfaultevent(fault,"attempting to locate",self.dbconn,self.t0,iunaccounted)
                    
                else:
                    print("FAULT {id}: unaccounted current of {cur} and we are out of nodes.".format(cur=iunaccounted, id= fault.uid))
                    fault.state == "unlocatable"
                    schedule.msfromnow(self,5000,self.groundFaultHandler,fault,zone)
                    
                    self.dbgroundfaultevent(fault,"unable to locate",self.dbconn,self.t0,iunaccounted)
                    
            else:
                #the previously isolated node probably contained the fault
                faultednode = fault.isolatednodes[-1]
                fault.faultednodes.append(faultednode)
                print("just added node {nam} to faulted nodes".format(nam = faultednode.name))
                
                fault.state = "located"
                #nodes in zone that are not marked faulted can be restored
                for node in fault.isolatednodes:
                    if node not in fault.faultednodes:
                        fault.restorenode(node)
                        
                if settings.DEBUGGING_LEVEL >= 1:
                    print("FAULT: located at {nod}. restoring other unfaulted nodes".format(nod = faultednode.name))
                    if settings.DEBUGGING_LEVEL >= 2:
                        fault.printInfo()
                        
                #reschedule
                schedule.msfromnow(self,1000,self.groundFaultHandler,fault,zone)
                
                self.dbgroundfaultevent(fault,"fault located",self.dbconn,self.t0,iunaccounted)
                
        elif fault.state == "located":
            #at least one faulted node has been located and isolated but there may be others
            iunaccounted = zone.sumCurrents()
            if abs(iunaccounted) > settings.UNACCOUNTED_CURRENT_THRESHOLD:
                #there is another faulted node, go back and find it
                fault.state = "unlocated"
                
                if settings.DEBUGGING_LEVEL >= 1:
                    print("FAULT: there are multiple faults in this zone. go back and find some more.")
                    if settings.DEBUGGING_LEVEL >= 2:
                        fault.printInfo()
                
                self.dbgroundfaultevent(fault,"suspect multiple faults",self.dbconn,self.t0,iunaccounted)
                
                self.groundFaultHandler(fault,zone)
                
            else:
                
                if settings.DEBUGGING_LEVEL >= 1:
                    print("FAULT: looks like we've isolated all faulted nodes and only faulted nodes.")
                
                #we seem to have isolated the faulted node(s)
                if fault.reclose:
                    fault.state = "reclose"                    
                    
                    if settings.DEBUGGING_LEVEL >= 1:
                        print("FAULT: going to reclose. count: {rec}".format(rec = fault.reclosecounter))
                        
                    self.dbgroundfaultevent(fault,"no other faults",self.dbconn,self.t0,iunaccounted)
                    
                else:
                    #our reclose limit has been met
                    fault.state = "persistent"
                    if settings.DEBUGGING_LEVEL >= 1:
                        print("FAULT: no more reclosing, fault is persistent.")
                
                #schedule next call
                schedule.msfromnow(self,1000,self.groundFaultHandler,fault,zone)
                
                
        elif fault.state == "reclose":
            
            if settings.DEBUGGING_LEVEL >= 1:
                print("reclosing on fault {id}".format(id = fault.uid))
                fault.printInfo()
                
            fault.reclosezone()
#             for node in zone.nodes:
#                 fault.reclosenode(node)
                
            fault.state = "suspected"
            schedule.msfromnow(self,1200,self.groundFaultHandler,fault,zone)
            self.dbgroundfaultevent(fault,"reclosing",self.dbconn,self.t0)
            
        elif fault.state == "unlocatable":
            #fault can't be located because the current imbalance can't be eliminated for whatever reason
            iunaccounted = zone.sumCurrents()
            if abs(iunaccounted) > settings.UNACCOUNTED_CURRENT_THRESHOLD:
                print("fault {id} is still unclocatable".format(id = fault.id))
                fault.printInfo()                
                schedule.msfromnow(self,5000,self.groundFaultHandler,fault,zone)
            else:
                #maybe the fault has abated or maybe we just can't tell that it hasn't
                fault.state = "located"
                schedule.msfromnow(self,1000,self.groundFaultHandler,fault,zone)
                
                self.dbgroundfaultevent(fault,"unlocatable fault now isolated",self.dbconn,self.t0,iunaccounted)
                
        elif fault.state == "persistent":
            #fault hasn't resolved on its own, need to send a crew to clear fault
            self.dbgroundfaultevent(fault,"fault deemed persistent",self.dbconn,self.t0)
            
            #revoke permission for customers on faulted nodes to connect
            for node in fault.faultednodes:
                #lock node
                node.locknode()
                for cust in node.customers:
                    cust.permission = False
                    
            #detect topology and begin remediation for unfaulted isolated groups
            self.getTopology()
            self.repairgrid()
                
        elif fault.state == "multiple":
            #this isn't used currently
            zone.isolateZone()
        elif fault.state == "cleared":
            fault.cleared()
            
            self.dbgroundfaultevent(fault,"fault cleared",self.dbconn,self.t0)
            
            if settings.DEBUGGING_LEVEL >= 2:
                print("GROUND FAULT {id} has been cleared".format(id = fault.uid))
        else:
            print("Error, unrecognized fault state in {id}: {state}".format(id = fault.uid, state = fault.state))
                
    
#     #monitor sensor and transducer accuracy - test one at a time to limit network impact
#     @Core.periodic(settings.SWITCH_FAULT_INTERVAL)  
#     def switchStateDetector(self):
#         #wrap when we reach the end
#         if self.edgeindex > len(self.Edges)-1:
#             self.edgeindex = 0
#             
#         edge = self.Edges[self.edgeindex]
#         
#         #if edge.startNode.faults or edge.endNode.faults:
#         
#         retdict = edge.checkConsistency()
#         
#         measurement = retdict["measured_state"]
#         resistance = retdict["resistance"]
#         
#         if retdict["reliable"]:
#             if retdict["discrepancy"]:
#                 print("UTILITY {me} REPORTS DISCREPANCY BETWEEN MEASURED RELAY STATE {sta} and MODEL STATE {msta} ON {nam}".format(me = self.name, sta = measurement, msta = not measurement, nam = edge.name))
#                 self.dbrelayfault(edge.name,measurement,resistance,self.dbconn,self.t0)
#             else:
#                 print("UTILITY {me} REPORTS MEASURED RELAY STATE {sta} ON {nam} IS CONSISTENT WITH MODEL".format(me = self.name, sta = measurement, nam = edge.name))        
#         else:
#             #can't use data, good resistance measurement couldn't be obtained
#             print("UTILITY {me} REPORTS UNUSABLE RESISTANCE DATA FOR {loc}".format(me = self.name, loc = edge.name))
#             

#    monitor for and remediate fault conditions'''
    @Core.periodic(settings.FAULT_DETECTION_INTERVAL)
    def faultDetector(self):
        if settings.DEBUGGING_LEVEL >= 2:
            print("running fault detection subroutine")
        faultnode = None
  #      if self.CurrentPeriod.periodNumber == 3:
  #          print(self.CurrentPeriod.periodNumber)
  #          print("set a fault in period 3")
  #          tagClient.writeTags(["RES_MAIN_FAULT"],[True],"scenario")
  #          print(tagClient.readTags(["RES_MAIN_FAULT"], "scenario"))
  #          tagClient.writeTags(["RES_MAIN_FAULT"],[1],"scenario")
  #          print(tagClient.readTags(["RES_MAIN_FAULT"], "scenario"))
        for node in self.nodes:
            location = node.name
            localist = location.split('.')
            
            if type(localist) is list:
                grid, branch, bus, load = localist
            if load == "MAIN":
                self.FaultTag = "{branch}_{bus}_FAULT".format(branch = branch, bus = bus, load = load)
                print(self.FaultTag)
            else:
                self.FaultTag = "{branch}_{bus}_{load}_FAULT".format(branch = branch, bus = bus, load = load)
                print(self.FaultTag)
            faultcondition = tagClient.readTags([self.FaultTag], "scenario")
            print("fault condition:{iso}".format(iso=node.isolated))    
            if faultcondition == 1:
                print("{node} has ground fault, need to isolate this node".format(node = node.name))
                #self.dbrelayfault(location,faultcondition,self.dbconn,self.t0)
                faultnode = node
                if node.isolated == False:
                    
                                       
                    print("isolate this node")
                    node.isolateNode()
                    
                    node.isolated = True
                    print("fault condition:{iso}".format(iso=node.isolated))
                elif node.isolated == True:
                    print("node has already been isolated")
            else:
                print("No faults detected in {me}!".format(me = node.name))
        for relay in self.relays:
            relay.printInfo()
        
        #find if a new subgroup is needed
        
        '''if faultnode:
            loclist = faultnode.name.split('.')
            if type(loclist) is list:
                ac, type, bus, load = loclist
                if load == "MAIN":
                    
                 
        '''
        #update topology
        if faultnode:
            subs = self.updateTopology(faultnode)
            self.printInfo(2)
            self.announceTopology()
                
        #then reconnect the distributed resources
        if faultnode:
            for res in self.Resources:
                
                if type(res) is resource.LeadAcidBattery:
                    if (res.location != faultnode.name):
                        res.location = faultnode.name
                        
            
                            
        return faultnode        
    '''            
        nominal = True        
        #look for brownouts
        for node in self.nodes:
            try:
                voltage = node.getVoltage()
                if voltage < settings.VOLTAGE_LOW_EMERGENCY_THRESHOLD:
                    node.voltageLow = True
                    node.group.voltageLow = True
                    nominal = False
                    if settings.DEBUGGING_LEVEL >= 1:
                        print("!{me} detected emergency low voltage at node {nod} belonging to {grp}".format(me = self.name, nod = node.name, grp = node.group.name))
                else:
                    node.voltageLow = False
                    
                self.dbinfmeas(node.voltageTag,voltage,self.dbconn,self.t0)
            except AttributeError:
                #can't do anything but we don't really care
                pass
                
                
        for zone in self.zones:
            skip = False
            if zone.faults:
                for fault in zone.faults:
                    if fault.__class__.__name__ == "GroundFault":
                        print("zone {nam} already has a ground fault".format(nam=zone.name))
                        if fault.state != "persistent":
                            print("fault is still in process")
                            skip = True
            
            if skip:
                print("Not checking zone {nam} because it is already known to be faulted".format(nam = zone.name))
                continue
            
            currentsum = zone.sumCurrents()
            if abs(currentsum) > settings.UNACCOUNTED_CURRENT_THRESHOLD:
                zonenominal = False
                #there is a mismatch and probably a line-ground fault
                nominal = False
                                
 #               self.groundFaultHandler(None,zone)
                
                if settings.DEBUGGING_LEVEL >= 1:
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("unaccounted current of {tot}".format(tot = currentsum))
                    print("Probable line-ground Fault in {zon}.  Isolating node.".format(zon = zone.name))
            else:
                if settings.DEBUGGING_LEVEL >=2:
                    print("unaccounted current of {tot} in {zon}. Probably nothing wrong".format(tot = currentsum, zon = zone.name))
                
        if nominal:
            if settings.DEBUGGING_LEVEL >= 2:
                print("No faults detected by {me}!".format(me = self.name))
    
    @Core.periodic(settings.SECONDARY_VOLTAGE_INTERVAL)
    def voltageMonitor(self):
        for group in self.groupList:
            for node in group.nodes:
                voltage = node.getVoltage()
                #print("measuring a voltage")
                
                self.dbinfmeas(node.voltageTag,voltage,self.dbconn,self.t0)
    
    @Core.periodic(settings.INF_CURRENT_MEASUREMENT_INTERVAL)
    def currentMonitor(self):
        taglist = []
        for edge in self.Edges:
            if edge.currentTag:
                taglist.append(edge.currentTag)
                
        retdict = {}
        if taglist:
            retdict = tagClient.readTags(taglist, "load")
            
        if retdict:
            for key in retdict:
                self.dbinfmeas(key,retdict[key],self.dbconn,self.t0)
    
    def groupEfficiencyAssessment(self,group):            
        loads = 0
        sources = 0
        losses = 0
        for cust in group.customers:
            loads += cust.measurePower()
            
        for res in group.resources:
            if res.owner != self.name:
                cust = listparse.lookUpByName(res.owner,self.customers)
                #cust will be None if the resource belongs to the utility
                if cust:
                    if res.location != cust.location:
                        #if resources are not colocated, we need to account for them separately
                        sources += res.getDischargePower() - res.getChargePower()
            else:
                sources += res.getOutputRegPower() - res.getInputUnregPower()
        
        waste = sources - loads
        
        expedges = []
        for node in group.nodes:
            for edge in node.edges:
                if edge not in expedges:
                    expedges.append(edge)
                    if edge.currentTag and edge in self.Edges:
                        losses += edge.getPowerDissipation()
        
        unaccounted = waste - losses
        
        return loads, sources, losses 
              
    
    @Core.periodic(settings.INF_EFF_MEASUREMENT_INTERVAL)        
    def efficiencyAssessment(self):
        #net load power consumption
        loads = 0
        #net source power consumption
        sources = 0
        #line losses
        losses = 0  
        
        for group in self.groupList:
            grouploads, groupsources, grouplosses = self.groupEfficiencyAssessment(group)
            loads += grouploads
            sources += groupsources
            losses += grouplosses
            #print('TEMP DEBUG: loads: {loads}, sources: {sources}, losses: {losses}'.format(loads = grouploads, sources = groupsources, losses = grouplosses))
        
        #difference
        waste = sources - loads
                
        #unaccounted losses
        unaccounted = waste - losses
        
        #write to database
        self.dbnewefficiency(sources,loads,losses,unaccounted,self.dbconn,self.t0)
        
    '''            
    def sendBidAcceptance(self,bid,rate):
        mesdict = {}
        mesdict["message_subject"] = "bid_acceptance"
        mesdict["message_target"] = bid.counterparty
        mesdict["message_sender"] = self.name
        
        mesdict["amount"] = bid.amount
        
        if bid.__class__.__name__ == "SupplyBid":
            mesdict["side"] = bid.side
            mesdict["service"] = bid.service
        elif bid.__class__.__name__ == "DemandBid":
            mesdict["side"] = bid.side
        else:
            mesdict["side"] = "unspecified"
            

            
            
        mesdict["rate"] = rate        
        mesdict["period_number"] = bid.periodNumber
        mesdict["uid"] = bid.uid
        
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY AGENT {me} sending bid acceptance to {them} for {uid}".format(me = self.name, them = bid.counterparty, uid = bid.uid))
        
        mess = json.dumps(mesdict)
        self.vip.pubsub.publish(peer = "pubsub",topic = "energymarket",headers = {}, message = mess)
        
    def sendBidRejection(self,bid,rate):
        mesdict = {}
        mesdict["message_subject"] = "bid_rejection"
        mesdict["message_target"] = bid.counterparty
        mesdict["message_sender"] = self.name
        
        mesdict["amount"] = bid.amount
        mesdict["rate"] = rate        
        if bid.__class__.__name__ == "SupplyBid":
            mesdict["side"] = "supply"
            mesdict["service"] = bid.service
        elif bid.__class__.__name__ == "DemandBid":
            mesdict["side"] = "demand"
        else:
            mesdict["side"] = "unspecified"
        mesdict["period_number"] = bid.periodNumber
        mesdict["uid"] = bid.uid
        
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY AGENT {me} sending bid rejection to {them} for {uid}".format(me = self.name, them = bid.counterparty, uid = bid.uid))
        
        mess = json.dumps(mesdict)
        self.vip.pubsub.publish(peer = "pubsub",topic = "energymarket",headers = {}, message = mess)
    
    '''solicit participation in DR scheme from all customers who are not
    currently participants'''
    @Core.periodic(settings.DR_SOLICITATION_INTERVAL)
    def DREnrollment(self):
        if settings.DEBUGGING_LEVEL >= 2:
            print("\nUTILITY {me} TRYING TO ENROLL CUSTOMERS IN A DR SCHEME".format(me = self.name))
        for entry in self.customers:
            if entry.DRenrollee == False:
                self.solicitDREnrollment(entry.name)
    
    '''broadcast message in search of new customers'''
    @Core.periodic(settings.CUSTOMER_SOLICITATION_INTERVAL)
    def discoverCustomers(self):
        if settings.DEBUGGING_LEVEL >= 2:
            print("\nUTILITY {me} TRYING TO FIND CUSTOMERS".format(me = self.name))
        mesdict = self.standardCustomerEnrollment
        mesdict["message_sender"] = self.name
        message = json.dumps(mesdict)
        self.vip.pubsub.publish(peer = "pubsub", topic = "customerservice", headers = {}, message = message)
        
        if settings.DEBUGGING_LEVEL >= 1:
            print(message)
            
    '''find out how much power is available from utility owned resources for a group at the moment'''    
    def getAvailableGroupPower(self,group):
        #first check to see what the grid topology is
        total = 0
        for elem in group.resources:
            if elem is LeadAcidBattery:
                if elem.SOC < .2:
                    total += 0
                elif elem.SOC > .4:
                    total += 20
            else:
                pass
        return total
    
            
    def getAvailableGroupDR(self,group):
        pass

    def getMaxGroupLoad(self,group):
        #print("MAX getting called for {group}".format(group = group.name))
        total = 0
        #print(group.customers)
        for load in group.customers:
            total += load.maxDraw
            #print("adding {load} to max load".format(load = load.maxDraw))
        return total
    
    ''' assume that power consumption won't change much between one short term planning
    period and the next'''
    def getExpectedGroupLoad(self,group):
        #print("EXP getting called for {group}".format(group = group.name))
        total = 0
        #print(group.customers)
        for load in group.customers:
            total += load.getPower()
            #print("adding {load} to expected load".format(load = load.getPower()))
        return total
    
    '''update agent's knowledge of the current grid topology'''
    def getTopology(self):
        self.rebuildConnMatrix()
        subs = graph.findDisjointSubgraphs(self.connMatrix)
        if len(subs) >= 1:
            del self.groupList[:]
            for i in range(1,len(subs)+1):
                #create a new group class for each disjoint subgraph
                self.groupList.append(groups.Group("group{i}".format(i = i),[],[],[]))
            for index,sub in enumerate(subs):
                #for concision
                cGroup = self.groupList[index]
                for node in sub:
                    cNode = self.nodes[node]
                    cGroup.addNode(cNode)
        else:
            print("got a weird number of disjoint subgraphs in utilityagent.getTopology()")
            
        self.dbtopo(str(subs),self.dbconn,self.t0)
        
        return subs
    
    def announceTopology(self):
        ngroups = len(self.groupList)
        groups = []
        for group in self.groupList:
            membership = []
            for node in group.nodes:
                membership.append(node.name)
            groups.append(membership)
                
        for group in self.groupList:
            for cust in group.customers:
                mesdict = {}
                mesdict["message_sender"] = self.name
                mesdict["message_target"] = cust.name
                mesdict["message_subject"] = "group_announcement"
                mesdict["your_group"] = group.name
                mesdict["group_membership"] = groups
                
                mess = json.dumps(mesdict)
                self.vip.pubsub.publish(peer = "pubsub", topic = "customerservice", headers = {}, message = mess)
    
    '''builds the connectivity matrix for the grid's infrastructure'''
    def rebuildConnMatrix(self):
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY {me} REBUILDING CONNECTIVITY MATRIX".format(me = self.name))
#        print("enumerate: " )
#        print(enumerate)
        for i,origin in enumerate(self.nodes):
#            print(origin.name)
            for edge in origin.originatingedges:
#                print("edge.name " + edge.name)
                for j, terminus in enumerate(self.nodes):
#                    print("terminus.name " + terminus.name)
                    if edge.endNode is terminus:
                        print("            terminus match! {i},{j}".format(i = i, j = j))
                        if edge.checkRelaysClosed():
                            self.connMatrix[i][j] = 1
                            self.connMatrix[j][i] = 1
                            print("                closed!")
                        else:
                            self.connMatrix[i][j] = 0
                            self.connMatrix[j][i] = 0                    
                            print("                open!")
        

                        
        if settings.DEBUGGING_LEVEL >= 2:
            print("UTILITY {me} HAS FINISHED REBUILDING CONNECTIVITY MATRIX".format(me = self.name))
            print("{mat}".format(mat = self.connMatrix))
     
    '''update agent's knowledge of the current grid topology after fault detection'''
    def updateTopology(self, faultnode):
        self.rebuildConnMatrix()
        
    #    subs = graph.findDisjointSubgraphs(self.connMatrix) ??? how to separate a subgroup after fault ???
        subs = self.faultRebuild(self.connMatrix, faultnode)
        print("grouplist contains following parameters: {subs}".format(subs = subs))
        if len(subs) >= 1:
            del self.groupList[:]
            for i in range(1,len(subs)+1):
                #create a new group class for each disjoint subgraph
                self.groupList.append(groups.Group("group{i}".format(i = i),[],[],[]))
            
            for index,sub in enumerate(subs):
                #for concision
                cGroup = self.groupList[index]
                for node in sub:
                    cNode = self.nodes[node]
                    cGroup.addNode(cNode)
        else:
            print("got a weird number of disjoint subgraphs in utilityagent.getTopology()")
            
        self.dbtopo(str(subs),self.dbconn,self.t0)
        return subs
     
    def faultRebuild(self, matrix, faultnode): 
        dim = len(matrix)
        location = faultnode.name
        print("rebuild topology under fault node {fn}".format(fn = faultnode.name))
        groups = []
        group = []
        expandlist = []
        num = 0
        
        for node in self.nodes:
            if node.name == faultnode.name:
                break
            else:
                num = num + 1
        print("number of fault node is : {num}".format(num = num))
                
        for g in range (2):
            if g == 0:
                unexamined = range(num)
                while len(unexamined) > 0:
                    group = []
                    expandlist.append(unexamined[0])
                    while len(expandlist) > 0:
                        row = expandlist[0]
                        for i in range(num):
                            if matrix[row][i] == 1 or matrix[row][i] == 0:
                                if i not in expandlist and i in unexamined:
                                        expandlist.append(i)
                        unexamined.remove(row)
                        expandlist.remove(row)
                        group.append(row)
                    groups.append(group)
                print("build group one")
            #    print(groups.name)
            #    print(group.customers)
            if g == 1:
                left = dim - num -1
                unexamined = range(num + 1,dim)
                while len(unexamined) > 0:
                    group = []
                    expandlist.append(unexamined[0])
                    while len(expandlist) > 0:
                        row = expandlist[0]
                        for i in range(num + 1,dim):
                            if matrix[row][i] == 1 or matrix[row][i] == 0:
                                if i not in expandlist and i in unexamined:
                                        expandlist.append(i)
                        unexamined.remove(row)
                        expandlist.remove(row)
                        group.append(row)
                    groups.append(group)
                print("build group two")
            #    print(groups.name)
            #    print(group.customers)
                   
                
        #    print("-----test test-----")
            print("groups:{groups}".format(groups = groups) )  
        return groups
    
        
    def marketfeed(self, peer, sender, bus, topic, headers, message):
        print("TEMP DEBUG - UTILITY: {mes}".format(mes = message))
        mesdict = json.loads(message)
        messageSubject = mesdict.get("message_subject",None)
        messageTarget = mesdict.get("message_target",None)
        messageSender = mesdict.get("message_sender",None)        
        
        if listparse.isRecipient(messageTarget,self.name):            
            if settings.DEBUGGING_LEVEL >= 2:
                print("\nUTILITY {me} RECEIVED AN ENERGYMARKET MESSAGE: {type}".format(me = self.name, type = messageSubject))
            if messageSubject == "bid_response":
                side = mesdict.get("side",None)
                print("side: {sid}".format(sid = side))
        
                rate =  mesdict.get("rate",None)
                amount = mesdict.get("amount",None)
                period = mesdict.get("period_number",None)
                uid = mesdict.get("uid",None)
                resourceName = mesdict.get("resource_name",None)
                
                #switch counterparty
                mesdict["counterparty"] = messageSender
                
                if side == "supply":
                    service = mesdict.get("service",None)
                    auxilliaryService = mesdict.get("auxilliary_service",None)
                    newbid = control.SupplyBid(**mesdict)
                    if service == "power":
                        self.supplyBidList.append(newbid)
                    elif service == "reserve":                  
                        self.reserveBidList.append(newbid)
                    #write to database
                    self.dbnewbid(newbid,self.dbconn,self.t0)
                elif side == "demand":
                    newbid = control.DemandBid(**mesdict)
                    self.demandBidList.append(newbid)
                    #write to database
                    self.dbnewbid(newbid,self.dbconn,self.t0)
                
                if settings.DEBUGGING_LEVEL >= 1:
                    print("UTILITY {me} RECEIVED A {side} BID#{id} FROM {them}".format(me = self.name, side = side,id = uid, them = messageSender ))
                    if settings.DEBUGGING_LEVEL >= 2:
                        newbid.printInfo(0)
            elif messageSubject == "bid_acceptance":
                pass
                #dbupdatebid()
            else:
                print("UTILITY {me} RECEIVED AN UNSUPPORTED MESSAGE TYPE: {msg} ON THE energymarket TOPIC".format(me = self.name, msg = messageSubject))
            #ask for topology again after receiving market feed message
            faultnode = self.faultDetector()
            if faultnode:
                subs = self.updateTopology(faultnode)
            else:
                subs = self.getTopology()
            self.printInfo(2)
            if settings.DEBUGGING_LEVEL >= 2:
                print("for market feed, UTILITY {me} THINKS THE TOPOLOGY IS {top}".format(me = self.name, top = subs))
            
            self.announceTopology()
            
            
                
    '''callback for demandresponse topic'''
    def DRfeed(self, peer, sender, bus, topic, headers, message):
        mesdict = json.loads(message)
        messageSubject = mesdict.get("message_subject",None)
        messageTarget = mesdict.get("message_target",None)
        messageSender = mesdict.get("message_sender",None)
        if listparse.isRecipient(messageTarget,self.name):
            if messageSubject == "DR_enrollment":
                messageType = mesdict.get("message_type",None)
                if messageType == "enrollment_reply":
                    if mesdict.get("opt_in"):
                        custobject = listparse.lookUpByName(messageSender,self.customers)
                        self.DRparticipants.append(custobject)
                        
                        resdict = {}
                        resdict["message_target"] = messageSender
                        resdict["message_subject"] = "DR_enrollment"
                        resdict["message_type"] = "enrollment_confirm"
                        resdict["message_sender"] = self.name
                        
                        response = json.dumps(resdict)
                        self.vip.pubsub.publish("pubsub","demandresponse",{},response)
                        
                        if settings.DEBUGGING_LEVEL >= 1:
                            print("ENROLLMENT SUCCESSFUL! {me} enrolled {them} in DR scheme".format(me = self.name, them = messageSender))
    
    @Core.periodic(settings.RESOURCE_MEASUREMENT_INTERVAL)
    def resourceMeasurement(self):
        for res in self.Resources:
            self.dbupdateresource(res,self.dbconn,self.t0)
    
    def measurePF(self):
        return tagClient.readTags([self.powerfactorTag],"grid")
    
    def CapNumber(self):
        IndCurrent = tagClient.readTags(["IND_MAIN_CURRENT"],"load")
        IndVoltage = tagClient.readTags(["IND_MAIN_VOLTAGE"],"load")
        IndPower = IndCurrent * IndVoltage
        #c is the capacity reactance of each capacitance
        c = 1 / (60 *2 * math.pi * 0.000018)
        powerfactor = tagClient.readTags([self.powerfactorTag],"grid")
        if powerfactor < 0.9:
            Q = IndPower * powerfactor
            Qgoal = IndPower * 0.9
            Qneed = Qgoal - Q
        Cap = 24*24/Qneed
        CapNumber = float(int(round(Cap / c)))
        return CapNumber
    
            
    def dbconsumption(self,cust,pow,dbconn,t0):
        command = 'INSERT INTO consumption (logtime, et, period, name, power) VALUES ("{time}",{et},{per},"{name}",{pow})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = self.CurrentPeriod.periodNumber,name = cust.name, pow = pow)
        self.dbwrite(command,dbconn)
                
    def dbupdateresource(self,res,dbconn,t0):
        ch = res.DischargeChannel
        meas = tagClient.readTags([ch.unregVtag, ch.unregItag, ch.regVtag, ch.regItag],"source")
        command = 'INSERT INTO resstate (logtime, et, period, name, connected, reference_voltage, setpoint, inputV, inputI, outputV, outputI) VALUES ("{time}",{et},{per},"{name}",{conn},{refv},{setp},{inv},{ini},{outv},{outi})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = self.CurrentPeriod.periodNumber, name = res.name, conn = int(res.connected), refv = ch.refVoltage, setp = ch.setpoint, inv = meas[ch.unregVtag], ini = meas[ch.unregItag] , outv = meas[ch.regVtag], outi = meas[ch.regItag])
        self.dbwrite(command,dbconn)
    
    def dbnewcustomer(self,newcust,dbconn,t0):
        cursor = dbconn.cursor()
        command = 'INSERT INTO customers VALUES ("{time}",{et},"{name}","{location}")'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, name = newcust.name, location = newcust.location)
        cursor.execute(command)
        dbconn.commit()
        cursor.close()
        
    def dbinfmeas(self,signal,value,dbconn,t0):
        command = 'INSERT INTO infmeas (logtime, et, period, signame, value) VALUES ("{time}",{et},{per},"{sig}",{val})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0,per = self.CurrentPeriod.periodNumber,sig = signal,val = value)
        self.dbwrite(command,dbconn)
        
    def dbtopo(self,topo,dbconn,t0):
        command = 'INSERT INTO topology (logtime, et, period, topology) VALUES("{time}",{et},{per},"{top}")'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0,per = self.CurrentPeriod.periodNumber, top = topo)
        self.dbwrite(command,dbconn)
        
    def dbnewbid(self,newbid,dbconn,t0):
        if hasattr(newbid,"service"):
            if hasattr(newbid,"auxilliary_service"):
                command = 'INSERT INTO bids (logtime, et, period, id, side, service, aux_service, resource_name, counterparty_name, orig_rate, orig_amount) VALUES ("{time}",{et},{per},{id},"{side}","{serv}","{aux}","{resname}","{cntrname}",{rate},{amt})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = newbid.periodNumber,id = newbid.uid, side = newbid.side, serv = newbid.service, aux = newbid.auxilliary_service, resname = newbid.resourceName, cntrname = newbid.counterparty, rate = newbid.rate, amt = newbid.amount) 
            else:
                command = 'INSERT INTO bids (logtime, et, period, id, side, service, resource_name, counterparty_name, orig_rate, orig_amount) VALUES ("{time}",{et},{per},{id},"{side}","{serv}","{resname}","{cntrname}",{rate},{amt})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = newbid.periodNumber,id = newbid.uid, side = newbid.side, serv = newbid.service, resname = newbid.resourceName, cntrname = newbid.counterparty, rate = newbid.rate, amt = newbid.amount) 
        else:
            command = 'INSERT INTO bids (logtime, et, period, id, side, resource_name, counterparty_name, orig_rate, orig_amount) VALUES ("{time}",{et},{per},{id},"{side}","{resname}","{cntrname}",{rate},{amt})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = newbid.periodNumber,id = newbid.uid, side = newbid.side, resname = newbid.resourceName, cntrname = newbid.counterparty, rate = newbid.rate, amt = newbid.amount) 
        self.dbwrite(command,dbconn)
        
    def dbupdatebid(self,bid,dbconn,t0):
        if bid.accepted:
            if hasattr(bid,"service"):
                command = 'UPDATE bids SET accepted="{acc}",acc_for="{accfor}",settle_rate={rate},settle_amount={amt} WHERE id={id}'.format(acc = int(bid.accepted), accfor = bid.service, rate = bid.rate, amt = bid.amount, id = bid.uid)
            else:
                command = 'UPDATE bids SET accepted="{acc}",settle_rate={rate},settle_amount={amt} WHERE id={id}'.format(acc = int(bid.accepted), rate = bid.rate, amt = bid.amount, id = bid.uid)
        else:
            command = 'UPDATE bids SET accepted={acc} WHERE id={id}'.format(acc = int(bid.accepted), id = bid.uid)
        self.dbwrite(command,dbconn)
        
    def dbtransaction(self,cust,amt,type,dbconn,t0):
        command = 'INSERT INTO transactions VALUES("{time}",{et},{per},"{name}","{type}",{amt},{bal})'.format(time = datetime.utcnow().isoformat(),et = time.time()-t0,per = self.CurrentPeriod.periodNumber,name = cust.name,type = type, amt = amt, bal = cust.customerAccount.accountBalance )
        self.dbwrite(command,dbconn)
       
    def dbnewresource(self, newres, dbconn, t0):
        command = 'INSERT INTO resources VALUES("{time}",{et},"{name}","{type}","{owner}","{loc}", {pow})'.format(time = datetime.utcnow().isoformat(), et = time.time()-t0, name = newres.name, type = newres.__class__.__name__,owner = newres.owner, loc = newres.location, pow = newres.maxDischargePower)
        self.dbwrite(command,dbconn)
         
    def dbnewefficiency(self,generation,consumption,losses,unaccounted,dbconn, t0):
        command = 'INSERT INTO efficiency VALUES("{time}",{et},{per},{gen},{con},{loss},{unacc})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = self.CurrentPeriod.periodNumber, gen = generation, con = consumption, loss = losses, unacc = unaccounted)
        self.dbwrite(command,dbconn)
        
    def dbrelayfault(self,location,measurement,dbconn,t0):
        command = 'INSERT INTO relayfaults VALUES("{time}",{et},{per},{loc},{meas},{res})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = self.CurrentPeriod.periodNumber, loc = location, meas = measurement, res = resistance)
        self.dbwrite(command,dbconn)
            
    def dbwrite(self,command,dbconn):
        try:
            cursor = dbconn.cursor()
            cursor.execute(command)
            dbconn.commit()
            cursor.close()
        except Exception as e:
            print("dbase error")
            print(command)
            print(e)

    '''prints information about the utility and its assets'''
    def printInfo(self,verbosity):
        print("\n************************************************************************")
        print("~~SUMMARY OF UTILITY KNOWLEDGE~~")
        print("UTILITY NAME: {name}".format(name = self.name))
        
        print("--LIST ALL {n} UTILITY OWNED RESOURCES------".format(n = len(self.Resources)))
        for res in self.Resources:
            res.printInfo(1)
        print("--END RESOURCES LIST------------------------")
        print("--LIST ALL {n} CUSTOMERS----------------".format(n=len(self.customers)))
        for cust in self.customers:
            print("---ACCOUNT BALANCE FOR {them}: {bal} Credits".format(them = cust.name, bal = cust.customerAccount.accountBalance))
            cust.printInfo(1)
        print("--END CUSTOMER LIST---------------------")
        if verbosity > 1:
            print("--LIST ALL {n} DR PARTICIPANTS----------".format(n = len(self.DRparticipants)))
            for part in self.DRparticipants:
                part.printInfo(1)
            print("--END DR PARTICIPANTS LIST--------------")
            print("--LIST ALL {n} GROUPS------------------".format(n = len(self.groupList)))
            for group in self.groupList:
                group.printInfo(1)
            print("--END GROUPS LIST----------------------")
        print("~~~END UTILITY SUMMARY~~~~~~")
        print("*************************************************************************")


    '''get tag value by name, but use the tag client only if the locally cached
    value is too old, as defined in seconds by threshold'''
    def getLocalPreferred(self,tags,threshold, plc):
        reqlist = []
        outdict = {} 
        indict = {}
        
        for tag in tags:
            try:
                #check cache to see if the tag is fresher than the threshold
                val, time = self.tagCache.get(tag,[None,None])
                #how much time has passed since the tag was last read from the server?
                diff = datetime.now()-time
                #convert to seconds
                et = diff.total_seconds()                
            except Exception:
                val = None
                et = threshold
                
            #if it's too old, add it to the list to be requested from the server    
            if et > threshold or val is None:
                reqlist.append(tag)
            #otherwise, add it to the output
            else:
                outdict[tag] = val
                
        #if there are any tags to be read from the server get them all at once
        if reqlist:
            indict = tagClient.readTags(reqlist,plc)
            if len(indict) == 1:
                outdict[reqlist[0]] = indict[reqlist[0]]
            else:
                for updtag in indict:
                    #then update the cache
                    self.tagCache[updtag] = (indict[updtag], datetime.now())
                    #then add to the output
                    outdict[updtag] = indict[updtag]
            
            #output should be an atom if possible (i.e. the request was for 1 tag
            if len(outdict) == 1:
                return outdict[tag]
            else:
                return outdict
    
    '''get tag by name from tag server'''
    def getTag(self,tag, plc):
         return tagClient.readTags([tag],plc)[tag]
        
    '''open an infrastructure relay. note that the logic is backward. this is
    because I used the NC connection of the SPDT relays for these'''
    def openInfRelay(self,rname):
        tagClient.writeTags([rname],[True],"load")
        
class BidState(object):
    def __init__(self):
        self.reservepolicy = False
        self.supplypolicy = False
        self.demandpolicy = False
        
        self.ignorelist = []
        
    def acceptall(self):
        self.reservepolicy = True
        self.supplypolicy = True
        self.demandpolicy = True
        
    def reserveonly(self):
        self.reservepolicy = True
        self.supplypolicy = False
        self.demandpolicy = False
        
    def acceptnone(self):
        self.reservepolicy = False
        self.supplypolicy = False
        self.demandpolicy = False
        
    def addtoignore(self,name):
        self.ignorelist.append(name)
    



        
def main(argv = sys.argv):
    try:
        utils.vip_main(UtilityAgent)
    except Exception as e:
        _log.exception('unhandled exception')
        
if __name__ == '__main__':
    sys.exit(main())



