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

#from DCMGClasses.CIP import wrapper
from ACMGAgent.CIP import tagClient
from ACMGAgent.Resources.misc import listparse, schedule
from ACMGAgent.Resources.mathtools import graph
from ACMGAgent.Resources import resource, groups, control, customer

from . import settings
from zmq.backend.cython.constants import RATE
from __builtin__ import True
from bacpypes.vlan import Node
from twisted.application.service import Service
#from _pydev_imps._pydev_xmlrpclib import loads
utils.setup_logging()
_log = logging.getLogger(__name__)


class test(Agent):
    def __init__(self,config_path,**kwargs):
        super(test,self).__init__(**kwargs)
        self.dbsafe = False
        self.current = [    "COM_MAIN_CURRENT",
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
        self.currentTag = []
        self.voltageTag = []
        self.config = utils.load_config(config_path)
        self._agent_id = self.config['agentid']
        
        
        '''Main method called by the eggsecutable'''
    #    sys.path.append('/home/pyff/.local/lib/python2.7/site-packages/')
        sys.path.append('/usr/lib/python2.7/dist-packages')
        sys.path.append('/usr/local/lib/python2.7/dist-packages')
        print(sys.path)
        import mysql.connector
                          
            #DATABASE STUFF
        dbconn = mysql.connector.connect(user='smartgrid',password='ugrid123',host='localhost',database='test1')
        for Itag in self.current:
            self.currentTag = Itag
            print (self.currentTag)
            loclist = self.currentTag.split('_')
            print (loclist)
           
            if type(loclist) is list:
                self.branch, self.bus, self.load = loclist
                print (self.branch)
                
                if self.branch == "COM":
                    self.voltageTag = "COM_MAIN_VOLTAGE"
                    print(self.voltageTag)
                elif self.branch == "RES":
                    self.voltageTag == "RES_MAIN_VOLTAGE"
                    print(self.voltageTag)
                elif self.branch == "IND":
                    self.voltageTag == "IND_MAIN_VOLTAGE"
                    print(self.voltageTag)
                
                totalavail = self.measureNetPower()
                print("TOTAL POWER AVAILABLE TO HOMEOWNER: {pow}".format(pow = totalavail))
            
                
    def measureVoltage(self):
    #    print (self.voltageTag)
        return tagClient.readTags([self.voltageTag],"load")
        
    def measureCurrent(self):
    #    print(self.currentTag)
        return tagClient.readTags([self.currentTag],"load")
    
    def measurePower(self):
    #    print(self.measureVoltage())
    #    print(self.measureCurrent())
        I = self.measureCurrent()
        V = self.measureVoltage()
        P = I * V
        return P
    
    def measurePF(self):
        return tagClient.readTags([powerfactorTag],"grid")
    
    
    def measureNetPower(self):
        net = self.measurePower()
        return net
    
    
   
 
def main(argv = sys.argv):
    try:
        utils.vip_main(test)
    except Exception as e:
        _log.exception('unhandled exception')
        
if __name__ == '__main__':
    sys.exit(main())
    
    
    