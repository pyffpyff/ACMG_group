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

from volttron.platform.vip.agent import Agent, Core, PubSub, compat, RPC
from volttron.platform.agent import utils
from volttron.platform.messaging import headers as headers_mod

from ACMGAgent.CIP import tagClient
from ACMGAgent.Resources.misc import listparse
from ACMGAgent.Resources.mathtools import combin
from ACMGAgent.Resources import resource, customer, optimization,control
from ACMGAgent.Resources.demand import appliances, human


from . import settings
from zmq.backend.cython.constants import RATE
from __builtin__ import False
from lib2to3.btm_utils import rec_test
from ACMGAgent.Resources.control import Forecast
#from services.core.WeatherAgent.weather.weatheragent import temperature
utils.setup_logging()
_log = logging.getLogger(__name__)

class HomeAgent(Agent):
    def __init__(self,config_path,**kwargs):
        super(HomeAgent,self).__init__(**kwargs)
        self.dbsafe = False
        
        self.config = utils.load_config(config_path)
        self._agent_id = self.config['agentid']
        #read from config structure
        self.name = self.config["name"]
        self.location = self.config["location"]
        self.resources = self.config["resources"]
        self.appliances = self.config["appliances"]
        self.refload = float(self.config["refload"])
        self.winlength = self.config["windowlength"]
        self.preferences =self.config["preference_manager"]
        #the following variables 
        self.FREGpart = bool(self.config["FREGpart"])
        self.DRpart = bool(self.config["DRpart"])
        
        self.t0 = time.time()
        
        #asset lists
        self.Resources = []
        self.Appliances = []
        self.Devices = []
        
        #name of utility enrolled with
        self.utilityName = None        
        self.currentSpot = None
        
        loclist = self.location.split('.')
        if type(loclist) is list:
            if loclist[0] == "AC":
                self.grid, self.branch, self.bus, self.load = loclist
            elif loclist[0] == "DC":
                pass
            else:
                print("the first part of the location path should be AC or DC")
        
        self.branchClass = self.branch
        self.busnumber = self.bus[-1]
        self.loadNumber = self.load[-1]
        
        
        if self.bus == "MAIN":
            self.relayTag = "{branch}_MAIN_USER".format(branch = self.branchClass)
        elif self.bus != "MAIN":
            if self.load == "MAIN":
                self.relayTag = "{branch}_BUS{bus}_USER".format(branch = self.branchClass, bus = self.busnumber)
            else:
                self.relayTag = "{branch}_BUS{bus}LOAD{load}_USER".format(branch = self.branchClass, bus = self.busnumber, load = self.loadNumber)
               
        self.voltageTag = "{branch}_MAIN_VOLTAGE".format(branch = self.branchClass)
        
        if self.bus == "MAIN":
            self.currentTag = "{branch}_MAIN_CURRENT".format(branch = self.branchClass)
        elif self.bus != "MAIN":
            if self.load == "MAIN":
                self.currentTag = "{branch}_BUS{bus}_CURRENT".format(branch = self.branchClass, bus = self.busnumber)
            else:
                self.currentTag = "{branch}_B{bus}L{load}_CURRENT".format(branch = self.branchClass, bus = self.busnumber, load = self.loadNumber)
        

        self.powerfactorTag = "powerfactor" #?tagname
         
        #add dist-packages to python path for mysql module
        #    sys.path.append('/home/pyff/.local/lib/python2.7/site-packages/')
        sys.path.append('/usr/lib/python2.7/dist-packages')
        sys.path.append('/usr/local/lib/python2.7/dist-packages')
        print(sys.path)
        import mysql.connector
                      
        #DATABASE STUFF
        self.dbconn = mysql.connector.connect(user='smartgrid',password='ugrid123',host='localhost',database='testdbase')

        #create resource objects for resources
        resource.makeResource(self.resources,self.Resources,False)
        
        #create database entries for resources
        for res in self.Resources:
            self.dbnewresource(res,self.dbconn,self.t0)
        
        #register exit function
        atexit.register(self.exit_handler,self.dbconn)

        self.Appliances.extend(appliances.makeAppliancesFromList(self.appliances))
        
        #make preference manager object from configuration file
        self.Preferences = human.PreferenceManager(**self.preferences)
        self.Preferences.printInfo()
        
        for app in self.Appliances:
            #add appliance to database
            self.dbnewappliance(app,self.dbconn,self.t0)
            
        self.dbsafe = True
            
        #Both smart appliances and distributed resources are considered Devices
        #it is useful to consider the two of these together sometimes
        self.Devices.extend(self.Resources)
        self.Devices.extend(self.Appliances)    
            
        #make devices addressible by name 
        self.DevDict = {}
        for dev in self.Devices:
            self.DevDict[dev.name] = dev    
            
        #initialize participation flags
        self.DR_participant = False
        self.FREG_participant = False
        self.gridConnected = False
        self.registered = False
                
        #initialize grid topology subgraph membership
        self.mygroup = None
        
        
        start = datetime.now()
        #this value doesn't matter
        end = start + timedelta(seconds = settings.ST_PLAN_INTERVAL)
        
        self.PlanningWindow = control.Window(self.name,self.winlength,1,end,settings.ST_PLAN_INTERVAL)
        self.CurrentPeriod = control.Period(0,start,end)
        self.NextPeriod = self.PlanningWindow.periods[0]
        self.CurrentPeriod.nextperiod = self.NextPeriod
        
        #group resources into bidgroups
        self.BidGroups = []
        for bg in self.Preferences.getBidGroups(self.NextPeriod):
            print("NEW BIDDING GROUP: {group}".format(group = bg))
            g = []
            for devname in bg:
                g.append(self.DevDict[devname])
            self.BidGroups.append(g)
            
        print self.BidGroups
        
        #core.schedule event object for the function call to begin next period
        self.advanceEvent = None
     
    def exit_handler(self,*targs,**kwargs):
        print("HOMEOWNER {me} exit handler: ".format(me = self.name))
        
        for arg in targs:
            print("{msg}".format(msg = arg))
        
        for key in kwargs:
            print("{key} : {msg}".format(key = key, msg = kwargs[key]))
            
        #disconnect all connected sources
        for res in self.Resources:
            res.disconnectSource()
        
        #close database connection
        self.dbconn.close() 
        
    @Core.receiver('onstart')
    def setup(self,sender,**kwargs):
        _log.info(self.config['message'])
        self._agent_id = self.config['agentid']
        
        #subscribe to default topics        
        self.vip.pubsub.subscribe('pubsub','energymarket', callback = self.followmarket)
#        self.vip.pubsub.subscribe('pubsub','demandresponse',callback = self.DRfeed)
        self.vip.pubsub.subscribe('pubsub','customerservice',callback = self.customerfeed)
        self.vip.pubsub.subscribe('pubsub','weatherservice',callback = self.weatherfeed)
#        self.vip.pubsub.subscribe("pubsub","FREG",callback = self.FREGfeed)
        
        for period in self.PlanningWindow.periods:
            self.requestForecast(period)
        
        sched = datetime.now() + timedelta(seconds = 1)
        #self.core.schedule(sched,self.firstplan)
        self.core.schedule(sched,self.makeNewPlan)
        
     
           
     
     
          
    def firstplan(self):
        #need more consideration for firstplan
        
        
        
        
        
        
        
        
        print('Homeowner {name} at {loc} beginning preparations for PERIOD 1'.format(name = self.name, loc = self.location))
        
        #find offer price
        before = time.time()
        self.NextPeriod.offerprice, self.NextPeriod.plan.optimalcontrol = self.determineOffer(True)
        #add plan to database
        self.dbnewplan(self.NextPeriod.plan.optimalcontrol,time.time()-before,self.dbconn,self.t0)
        
        if settings.DEBUGGING_LEVEL >= 2:
            print("HOMEOWNER {me} generated offer price: {price}".format(me = self.name,price = self.NextPeriod.offerprice))
            self.NextPeriod.plan.optimalcontrol.printInfo(0)
        self.NextPeriod.plan.planningcomplete = True
        
        self.prepareBidsFromPlan(self.NextPeriod)
                
        #now that we have the offer price we can respond with bids, but wait until the utility has solicited them
        if self.NextPeriod.supplybidmanager.recPowerSolicitation and self.NextPeriod.demandbidmanager.recDemandSolicitation:
            if settings.DEBUGGING_LEVEL >= 2:
                print("HOMEOWNER {me} ALREADY RECEIVED SOLICITATION, SUBMITTING BIDS NOW".format(me = self.name))
            self.submitBids(self.NextPeriod.demandbidmanager)
            self.submitBids(self.NextPeriod.supplybidmanager)
            
        else:
            if settings.DEBUGGING_LEVEL >= 2:
                print("HOMEOWNER {me} WAITING FOR SOLICITATION".format(me = self.name))
            
    @Core.periodic(settings.RESOURCE_MEASUREMENT_INTERVAL)
    def resourceMeasurement(self):
        for res in self.Resources:
            self.dbupdateresource(res,self.dbconn,self.t0)
        
    @Core.periodic(settings.SIMSTEP_INTERVAL)
    def simStep(self):
        #measure net power to load connection point
        totalavail = self.measureNetPower()
        unconstrained = 0
        
        #determine the nominal power consumption of all appliances in on state
        for app in self.Appliances:
            if app.on:
                unconstrained += app.nominalpower

        #if the unconstrained power demand is greater than the actual power
        #consumption, we have to determine how the available power is divided among appliances
        if unconstrained > totalavail:
            #if there is unconstrained demand (totalavail may be negative)
            if unconstrained > 0:
                #determine the ratio of satisfied unconstrained demand
                frac = totalavail/unconstrained
            else:
                frac = 0
                
                
            for app in self.Appliances:
                app.simulationStep(frac*app.nominalpower,settings.SIMSTEP_INTERVAL)
                #update database
                if self.dbsafe:
                    self.dbupdateappliance(app,frac*app.nominalpower,self.dbconn,self.t0)
        #if the unconstrained power demand is lower than the actual power consumption,
        #assume all demand is satisfied and any extra actual consumptio  cn            
        else:    
            for app in self.Appliances:   
                if app.on:
                    app.simulationStep(app.nominalpower,settings.SIMSTEP_INTERVAL)
                    #update database
                    if self.dbsafe:
                        self.dbupdateappliance(app,app.nominalpower,self.dbconn,self.t0)   
                else:
                    app.simulationStep(0,settings.SIMSTEP_INTERVAL)
                    #update database
                    if self.dbsafe:
                        self.dbupdateappliance(app,0,self.dbconn,self.t0)
 
    def priceForecast(self):
        for period in self.PlanningWindow.periods:
            if self.currentSpot:
                period.expectedenergycost = self.currentSpot





    '''callback for customerservice topic'''    
    def customerfeed(self, peer, sender, bus, topic, headers, message):
        mesdict = json.loads(message)
        messageTarget = mesdict.get("message_target",None)        
        if listparse.isRecipient(messageTarget,self.name, False):  
            messageSubject = mesdict.get("message_subject",None)
            messageType = mesdict.get("message_type",None)
            messageSender = mesdict.get("message_sender",None)
            
            if messageSubject == "customer_enrollment":
                if messageType == "new_customer_query":
                    self.utilityName = messageSender
                    rereg = mesdict.get("rereg",False)
                    if self.registered == False or rereg == True:
                        resdict = {}
                        resdict["message_subject"] = "customer_enrollment"
                        resdict["message_type"] = "new_customer_response"
                        resdict["message_target"] = messageSender
                        resdict["message_sender"] = self.name
                        resdict["info"] = [self.name, self.location, self.resources, "residential"]
                        
                        response = json.dumps(resdict)
                        self.vip.pubsub.publish(peer = "pubsub", topic = "customerservice", headers = {}, message = response)
                                                
                        if settings.DEBUGGING_LEVEL >= 1:
                            print("\nHOME {me} responding to enrollment request: {res}".format(me = self.name, res = response))
                    else:
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("\nHOME {me} ignoring enrollment request, already enrolled".format(me = self.name))
                elif messageType == "new_customer_confirm":
                    self.registered = True
            elif messageSubject == "group_announcement":
                groups = mesdict.get("group_membership",None)
                self.mygroup = mesdict.get("your_group",None)
                
                if settings.DEBUGGING_LEVEL >= 2:
                    print("HOMEOWNER {me} is a member of group {grp}".format(me = self.name, grp = self.mygroup))



    def followmarket(self, peer, sender, bus, topic, headers, message):
        mesdict = json.loads(message)
        
        messageSubject = mesdict.get('message_subject',None)
        messageSender = mesdict.get("message_sender",None)
        messageTarget = mesdict.get('message_target',None)
        
                
        if listparse.isRecipient(messageTarget,self.name):
            if settings.DEBUGGING_LEVEL >= 2:
                print("\nHOME {name} received a {top} message: {sub}".format(name = self.name, top = topic, sub = messageSubject))
                #print(message)
            #sent by a utility agent to elicit bids for generation    
            if messageSubject == 'bid_solicitation':
                service = mesdict.get("service",None)
                side = mesdict.get("side",None)
                periodNumber = mesdict.get("period_number",None)
                period = self.PlanningWindow.getPeriodByNumber(periodNumber)
                #replace counterparty with message sender
                mesdict["counterparty"] = messageSender
                
                if self.Devices:
                    if side == "demand":
                        period.demandbidmanager.procSolicitation(**mesdict)
                        
                        if settings.DEBUGGING_LEVEL >= 2:
                            print("HOME AGENT {me} received a demand bid solicitation".format(me = self.name))
                        
                        #if demand bids have already been prepared
                        if period.allplanscomplete():
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("HOME AGENT {me} done planning".format(me = self.name))
                            if period.demandbidmanager.readybids:
                                if settings.DEBUGGING_LEVEL >= 2:
                                    print("HOME AGENT {me} has bids ready for submission".format(me = self.name))
                                self.submitBids(period.demandbidmanager)
                            
                    elif side == "supply":
                        period.supplybidmanager.procSolicitation(**mesdict)

                        if period.allplanscomplete():
                            if period.supplybidmanager.readybids:
                                self.submitBids(period.supplybidmanager)
                                        
            #received when a homeowner's bid has been accepted    
            elif messageSubject == 'bid_acceptance':
                #if acceptable, update the plan
                side = mesdict.get("side",None)
                service = mesdict.get("service",None)
                amount = mesdict.get("amount",None)
                rate = mesdict.get("rate",None)
                periodNumber = mesdict.get("period_number",None)
                uid = mesdict.get("uid",None)
    
                
                period = self.PlanningWindow.getPeriodByNumber(periodNumber)
                
                #amount or rate may have been changed
                #service also may have been changed from power to regulation
                if side == "supply":
                    bid = period.supplybidmanager.findPending(uid)
                    
                    #stop processing if bid does not exist
                    if bid == None:
                        return
                    
                    period.supplybidmanager.bidAccepted(bid,**mesdict)
                    if bid.resourceName:
                        name = bid.resourceName
                        res = listparse.lookUpByName(name,self.Resources)
                        if service == "power":
                            period.disposition.components[name] = control.DeviceDisposition(name,amount,"power")
                        elif service == "reserve":
                            period.disposition.components[name] = control.DeviceDisposition(name,amount,"reserve",-.2)
                    
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("-->HOMEOWNER {me} ACK SUPPLY BID ACCEPTANCE".format(me = self.name))
                        bid.printInfo()
                        print(" TEMP DEBUG: resname: {rnam}".format(rnam = name))
                        
                elif side == "demand":
                    bid = period.demandbidmanager.findPending(uid)
                    
                    #stop processing if the bid doesn't exist
                    if bid == None:
                        return
                
                    period.demandbidmanager.bidAccepted(bid,**mesdict)
                    
                    if bid.resourceName:
                        name = bid.resourceName
                        period.disposition.components[name] = control.DeviceDisposition(name,amount,"charge")
                    else:
                        period.disposition.closeRelay = True
                        plan = bid.plan
                        if plan.optimalcontrol:
                            print("has opt control")
                            comps = plan.optimalcontrol.components
                            for app in self.Appliances:
                                if app.name in comps:
                                    if settings.DEBUGGING_LEVEL >= 2:
                                        print("HOMEOWNER {me} adding appliance disposition for {them} in {per}".format(me = self.name, them = app.name, per = period.periodNumber))
                                    period.disposition.components[app.name] = control.DeviceDisposition(app.name,comps[app.name],"consumption")
                                else:
                                    period.disposition.components[app.name] = control.DeviceDisposition(app.name,0,"consumption")
                        else:
                            pass
                    
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("-->HOMEOWNER {me} ACK DEMAND BID ACCEPTANCE for {id}".format(me = self.name, id = uid))
                        bid.printInfo()
                        
                if settings.DEBUGGING_LEVEL >= 2:
                    period.disposition.printInfo(0)
        
            elif messageSubject == "bid_rejection":
                side = mesdict.get("side",None)
                amount = mesdict.get("amount",None)
                rate = mesdict.get("rate",None)
                periodNumber = mesdict.get("period_number",None)
                uid = mesdict.get("uid",None)
                name = mesdict.get("resource_name",None)
                
                period = self.PlanningWindow.getPeriodByNumber(periodNumber)
                
                if side == "supply":
                    bid = period.supplybidmanager.findPending(uid)
                    period.supplybidmanager.bidRejected(bid)
                elif side == "demand":
                    bid = period.demandbidmanager.findPending(uid)
                    period.demandbidmanager.bidRejected(bid)
                
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("-->HOMEOWNER {me} ACK BID REJECTION FOR {id}".format(me = self.name, id = bid.uid))
                        bid.printInfo()
                        
            #subject used for handling general announcements            
            elif messageSubject == "announcement":
                messageType = mesdict.get("message_type",None)
                #announcement of next period start and stop times to ensure synchronization
                if messageType == "period_announcement":
                    pnum = mesdict.get("period_number",None)
                    
                    #look up period in planning window -- if not in planning window, ignore
                    period = self.PlanningWindow.getPeriodByNumber(pnum)
                    if period:
                        #make datetime object
                        startTime = mesdict.get("start_time",None)
                        endTime = mesdict.get("end_time",None)
                        startdtime = datetime.strptime(startTime,"%Y-%m-%dT%H:%M:%S.%f")
                        enddtime = datetime.strptime(endTime,"%Y-%m-%dT%H:%M:%S.%f")
                        if period.startTime == startdtime:
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("HOMEOWNER {me} already knew start time for PERIOD {per}".format(me = self.name, per = pnum))
                        else:
                            oldtime = period.startTime
                            period.startTime = startdtime
                            #since we are changing our start time, cancel any existing advancePeriod() calls
                            if self.advanceEvent:
                                self.advanceEvent.cancel()
                            #now create new call
                            self.advanceEvent = self.core.schedule(startdtime,self.advancePeriod)
                            
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("HOMEOWNER {me} revised start time for PERIOD {per} from {old} to {new}".format(me = self.name, per = pnum, old =  oldtime.isoformat(), new = startdtime.isoformat()))
                        
                        if period.endTime == enddtime:
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("HOMEOWNER {me} already knew end time for PERIOD {per}".format(me = self.name, per = pnum))
                        else:
                            #update end time
                            oldtime = period.endTime
                            period.endTime = enddtime
                            #now update all subsequent periods accordingly
                            self.PlanningWindow.rescheduleSubsequent(pnum+1,enddtime)
                            if settings.DEBUGGING_LEVEL >= 2:
                                print("HOMEOWNER {me} revised end time for PERIOD {per} from {old} to {new}".format(me = self.name, per = pnum, old = oldtime.isoformat(), new = enddtime.isoformat()))
                    
                elif messageSubject == "period_duration_announcement":
                    newduration = mesdict.get("duration",None)
                    self.PlanningWindow.increment = newduration    
                        
                        
            elif messageSubject == "rate_announcement":
                rate = mesdict.get("rate")
                pnum = mesdict.get("period_number")
                period = self.PlanningWindow.getPeriodByNumber(pnum)
                if period:
                    #print("period exists")
                    #update expected energy cost variable
                    period.expectedenergycost = rate
                    
                    if period == self.CurrentPeriod:
                        self.currentSpot = rate
                        self.priceForecast()
                    #if the rate announcement is for the next period
                    elif period == self.NextPeriod:
                        #print("period is next period")
                        #and there had either not been an announcement or the announced rate differs
                        period.firmrate = True
                
                if settings.DEBUGGING_LEVEL >= 2:
                    print("RECEIVED RATE NOTIFICATION FROM {them} FOR PERIOD {per}. NEW RATE IS {rate}".format(them = messageSender, per = pnum, rate = rate))


    def prepareBids(self,period):
        
        #submit bids based on plans
        for plan in period.plans:
            self.prepareBidFromPlan(plan)
                 
       
    def prepareBidFromPlan(self,plan):
        period = plan.period
        comps = plan.optimalcontrol.components
        rate = plan.offerprice
            
        
        for res in self.Resources:
            if comps.get(res.name,None):
                mesdict = {}
                pu = comps[res.name]
                amount = res.getPowerFromPU(pu)
                
                newbid = []
                #if this is a storage device it might be used as supply, reserve, or demand
                if res.issource:
                    #the device is supposed to be deployed as a source
                    if pu > 0:
                        newbid = control.SupplyBid(**{"counterparty": self.utilityName, "period_number": period.periodNumber, "side": "supply"})
                        period.supplybidmanager.initBid(newbid)
                        period.supplybidmanager.setBid(newbid,amount,rate,res.name,"power","reserve")
                        bidmanager = period.supplybidmanager
                    #the device is supposed to be charged
                    elif pu < 0:
                        newbid = control.DemandBid(**{"counterparty": self.utilityName, "period_number": period.periodNumber, "side": "demand"})
                        period.demandbidmanager.initBid(newbid)
                        period.demandbidmanager.setBid(newbid,abs(amount),rate,res.name)
                        bidmanager = period.demandbidmanager
                    if res.issink:
                        #the device is not being deployed and can be offered as reserve
                        if pu == 0:
                            #if the resource isn't too deeply discharged, submit reserve bid
                            if res.soc > .25:
                                newbid = control.SupplyBid(**{"counterparty": self.utilityName, "period_number": period.periodNumber, "side": "supply"})
                                period.supplybidmanager.initBid(newbid)
                                period.supplybidmanager.setBid(newbid,4,.5*rate,None,"reserve")
                                bidmanager = period.supplybidmanager
                if newbid:
                    if settings.DEBUGGING_LEVEL >= 2:
                        print("HOMEOWNER AGENT {me} READYING BID".format(me = self.name))
                        newbid.printInfo(0)
                        
                    mesdict["message_sender"] = self.name
                    mesdict["message_target"] = self.utilityName
                    mesdict["message_subject"] = "bid_response"
                    mesdict["period_number"] = period.periodNumber
                    
                    bidmanager.readyBid(newbid,**mesdict)
                    newbid.plan = plan
        #now take care of demand from smart appliances
        #since our actual loads are binary, any demand will require the load to be
        #switched on.  any extra load is assumed to come from appliances not represented
        #by an agent
        mesdict = {}
        anydemand = False
        comps = plan.optimalcontrol.components
        amount = 0
        for devkey in comps:
            dev = listparse.lookUpByName(devkey,self.Appliances)
            if comps[devkey] > 0:
                amount += dev.getPowerFromPU(comps[devkey])
                anydemand = True
        
        if anydemand:
            if settings.DEBUGGING_LEVEL >= 2:
                print("HOMEOWNER AGENT {me} HAS DEMAND FOR period {per}".format(me = self.name, per = period.periodNumber))
            
            newbid = control.DemandBid(**{"counterparty": self.utilityName, "period_number": period.periodNumber, "side": "demand"})
            period.demandbidmanager.initBid(newbid)
            #period.demandbidmanager.setBid(newbid,self.refload,rate)
            period.demandbidmanager.setBid(newbid,amount,rate)
            
            mesdict["message_sender"] = self.name
            mesdict["message_target"] = self.utilityName
            mesdict["message_subject"] = "bid_response"
            mesdict["period_number"] = period.periodNumber
            
            if settings.DEBUGGING_LEVEL >= 2:
                print("HOMEOWNER AGENT {me} READYING DEMAND BID".format(me = self.name))
                newbid.printInfo(0)
            
            period.demandbidmanager.readyBid(newbid,**mesdict)
            #associate the bid with a plan
            newbid.plan = plan
        else:
            if settings.DEBUGGING_LEVEL >= 2:
                print("HOMEOWNER AGENT {me} HAS NO DEMAND FOR period {per}".format(me =self.name, per = period.periodNumber))
            
    def submitBids(self,bidmanager):      
        while bidmanager.readybids:
            bid = bidmanager.readybids[0]
            
            #this takes care of the case when the utility name was not known
            #when the bids were created
            if not bid.counterparty:
                bid.counterparty = self.utilityName
                bid.routinginfo["message_target"] = self.utilityName
                
                
            if settings.DEBUGGING_LEVEL >= 2:
                print("SUBMITTING BID")
                bid.printInfo(0)
            self.vip.pubsub.publish("pubsub","energymarket",{},bidmanager.sendBid(bid))
            #bidmanager.printInfo()
    
    def homefeed(self,peer,sender,bus,topic,headers,message):
        mesdict = json.loads(message)
        messageTarget = mesdict.get('message_target',None)
        if listparse.isRecipient(messageTarget,self.name):
            messageSubject = mesdict.get('message_subject',None)
            messageSender = mesdict.get('message_sender',None)
            
                
    def weatherfeed(self,peer,sender,bus,topic,headers,message):
        mesdict = json.loads(message)
        messageSubject = mesdict['message_subject']
        messageTarget = mesdict['message_target']
        messageSender = mesdict['message_sender']
        messageType = mesdict['message_type']
        
        if listparse.isRecipient(messageTarget,self.name):    
            foredict = {}
            if messageSubject == "nowcast":
                if messageType == "nowcast_response":
                    responses = mesdict.get("responses",None)
                    if responses:
                        self.CurrentPeriod.addForecast(control.Forecast(responses,self.CurrentPeriod))
            elif messageSubject == "forecast":
                if messageType == "forecast_response":
                    periodnumber = mesdict.get("forecast_period")
                    responses = mesdict.get("responses",None)
                    if responses:
                        period = self.PlanningWindow.getPeriodByNumber(periodnumber)
                        if period:
                            period.addForecast(control.Forecast(responses,period))
                            print("HOMEOWNER {me} received forecast for period {per}: {rsp}".format(me = self.name, per = period.periodNumber, rsp = responses))
                        else:
                            if periodnumber == self.CurrentPeriod.periodNumber:
                                self.CurrentPeriod.addForecast(control.Forecast(responses,period))
                            else:
                                print("HOMEOWNER {me} doesn't know what to do with forecast for period {per}".format(me = self.name,per = periodnumber))
       
        
    #generate new bid for each planning group                    
    def makeNewPlan(self,debug = True):
        self.PlanningWindow.resetPlans(self.BidGroups,False)
        
        #associate cost functions with plans
        for period in self.PlanningWindow.periods:            
            for plan in period.plans:
                if debug:
                    print("HOMEOWNER {me} ASSOCIATING COSTFN WITH PLAN IN PERIOD {per}".format(me = self.name,per = period.periodNumber))
                
                plan.costfn = self.Preferences.getcfn(plan)
        
        #determine offer and plan for the devices in each bidgroup
        period = self.PlanningWindow.periods[0]
        for bidgroup in self.BidGroups:
            plan = period.getplan(bidgroup)            
            plan.offerprice, plan.optimalcontrol = self.determineOffer(bidgroup,True)
            
            
    
    #determine offer price by finding a price for which the cost function is 0          
    def determineOffer(self,bidgroup,debug = False):
        #how close to neutral value do we need to get?
        threshold = .005
        #largest step size we can take to bracket the bid rate
        maxstep = 2
        #initial lower bound for bid rate
        initprice = 0
        
        
        bound = initprice
        
        upper = initprice
        lower = initprice
        
        pstep = .1
        pstepinc = .2
        
        #saves best bid so far so we can fall back on this to avoid submitting null bids when we approach optimal bid from the wrong side
        savebid = None
        saveopt = None
        
        #if the bidgroup contains many devices, sacrifice precision for speed
        if len(bidgroup) >= 3:
            maxitr = 4
        else:
            maxitr = 6
            
        #turns debugging on or off for subroutines
        subdebug = True
        #subdebug = False
        
        start = time.time()
        rec = self.getOptimalForPrice(bound,bidgroup,subdebug)
        oneround = time.time() - start
        print("took {sec} seconds to find initial cost: {cos}".format(sec = oneround, cos = rec.pathcost))
        
        itr = 0
        if rec.pathcost > 0:
            while rec.pathcost > 0:
                upper = bound
                itr += 1
                bound -= pstep
                
                
                if pstep < maxstep:
                    pstep += pstepinc
                    
                if itr > maxitr:
                    print("HOMEOWNER {me}: couldn't bracket zero crossing".format(me = self.name))
                    return 0, rec
                
                rec = self.getOptimalForPrice(bound,bidgroup,subdebug)
                print("bracketing price - price: {pri}, costfn: {cos}".format(pri = bound, cos = rec.pathcost))
            lower = bound 
                
        elif rec.pathcost < 0:
            while rec.pathcost < 0:
                lower = bound
                itr += 1
                bound += pstep
                
                
                if pstep < maxstep:
                    pstep += pstepinc
                    
                if itr > maxitr:
                    print("HOMEOWNER {me}: couldn't bracket zero crossing".format(me = self.name))
                    return 0, rec
                
                rec = self.getOptimalForPrice(bound,bidgroup,subdebug)                
                print("bracketing price - price: {pri}, costfn: {cos}".format(pri = bound, cos = rec.pathcost))
            upper = bound
        else:
            #got it right the first time
            return bound, rec
        
        if bound == initprice:
            return
  
        print("bracketed price - upper: {upp}, lower: {low}".format(upp = upper, low = lower))
        
            
        itr = 0
        while abs(rec.pathcost) > threshold:
            
            mid = (upper + lower)*.5
            
            before = time.time()
            rec = self.getOptimalForPrice(mid,bidgroup,subdebug)
            after = time.time()
            print("new cost {cos} for price {mid}. iteration took {sec} seconds".format(cos = rec.pathcost, mid = mid, sec = after - before))
            
            if rec.pathcost > 0:
                upper = mid
            elif rec.pathcost < 0:
                lower = mid
            else:
                pass
            
            itr += 1
            
            #temporary debugging
            print("new range {low} - {upp}".format(low = lower, upp = upper))
            
            if abs(upper - lower) < .01:
                if settings.DEBUGGING_LEVEL >= 1:
                    print("HOMEOWNER {me} has narrowed the price window without reducing cost sufficiently. RANGE: {lower}-{upper} COST: {cost}".format(me = self.name,lower = lower, upper = upper, cost = rec.pathcost))
                return (upper + lower)*.5, rec
            
            if itr > maxitr:
                if settings.DEBUGGING_LEVEL >= 1:
                    elapsed = time.time() - start
                    print("HOMEOWNER {me} took too many iterations ({sec} seconds) to generate offer price. RANGE: {lower}-{upper} COST: {cost}".format(me = self.name, sec = elapsed, lower = lower, upper = upper, cost = rec.pathcost))
                    if rec.isnull():
                        if saveopt:
                            print("avoiding null bid by submitting saved bid. bid: {bid} for {act}".format(bid = savebid, act = saveopt.components))
                            return savebid, saveopt
                        else:
                            print("no saved bid to fall back on submit null bid")
                            return 0,rec
                    else:
                        return (upper + lower)*.5, rec
            
            #bids associated with null actions by saving acceptable non-null bids
            if mid > 0 and rec.pathcost <= 0:
                print("HOMEOWNER {me} not saving bid because it is null".format(me = self.name))
                if not rec.isnull():
                    saveopt = rec
                    savebid = mid
                    print("HOMEOWNER {me} saving acceptable bid: {bid} for action: {act}".format(me = self.name, bid = savebid, act = saveopt.components))
        
        price = (upper + lower)*.5
        elapsed = time.time() - start
        if settings.DEBUGGING_LEVEL >= 2:
            print("HOMEOWNER {me} determined offer price: {bid} (took {et} seconds)".format(me = self.name, bid = price, et = elapsed))
        
        if rec.isnull():
            if saveopt:
                print("avoiding null bid by submitting saved bid. bid: {bid} for {act}".format(bid = savebid, act = saveopt.components))
                return savebid, saveopt
            else:
                print("no saved bid to fall back on submit null bid")
                return 0,rec
        else:
            return price, rec

        
    def getOptimalForPrice(self,price,bidgroup,debug = False):
        #to do list:
        #the last period should not have a stategrid made, instead, just evaluate the costfn for the simulated terminal states in the penultimate period
        #the first period does not need a stategrid either, just evaluate the actual state
        #for the first state, consider using a finer discretization of the input space if applicable
        
        
        if debug:
            print("HOMEOWNER {me} starting new iteration".format(me = self.name))
            
        #window = control.Window(self.name,self.winlength,self.NextPeriod.periodNumber,self.NextPeriod.startTime,settings.ST_PLAN_INTERVAL)
        window = self.PlanningWindow
        
        #add current state to grid points
        snapstate = {}
        for dev in bidgroup:
            snapcomp = dev.addCurrentStateToGrid()
            if snapcomp is not None:
                snapstate[dev.name] = snapcomp
        
        if debug:
            print("HOMEOWNER {me} saving current state: {sta}".format(me =  self.name, sta = snapstate))
        
        selperiod = window.periods[-1]
        while selperiod:
            selperiod.expectedenergycost = price
            #begin sub
            if debug:
                print(">HOMEOWNER {me} now working on period {per}".format(me = self.name, per = selperiod.periodNumber))
                
            plan = selperiod.getplan(bidgroup)
            #remake grid points
            #plan.makeGrid(self.Preferences.eval)            
            plan.makeGrid(plan.costfn)
                        
            #if we failed to remake grid points, print error and return
            if not plan.stategrid.grid:
                print("Homeowner {me} encountered a missing state grid for period {per}".format(me = self.name, per = selperiod.periodNumber))
                return
            for state in plan.stategrid.grid:
                #if this is not the last period
                if selperiod.nextperiod:
                    if debug:
                        print(">WORKING ON A NEW STATE: {sta}".format(sta = state.components))
                    #make inputs for the state currently being examined
                    self.makeInputs(state,plan,debug)
                    if debug:
                        print(">EVALUATING {n} ACTIONS".format(n = len(plan.admissiblecontrols)))
                    
                    #find the best input for this state
                    currentbest = float('inf')
                    for input in plan.admissiblecontrols:
                        self.findInputCost(state,input,plan,settings.ST_PLAN_INTERVAL,debug)
                        if input.pathcost < currentbest:
                            if debug:
                                print(">NEW BEST OPTION! {newcost} < {oldcost}".format(newcost = input.pathcost, oldcost = currentbest))
                            currentbest = input.pathcost
                            #associate state with optimal input
                            state.setoptimalinput(input)
                        #else:
                        #    if debug:
                        #        print(">NO BETTER: {newcost} >= {oldcost}".format(newcost = input.pathcost, oldcost = currentbest))
                    
                    
                    if debug:
                        print(">HOMEOWNER {me}: optimal input for state {sta} is {inp}".format(me = self.name, sta = state.components, inp = state.optimalinput.components))
                else:
                    if debug:
                        print(">HOMEOWNER {me}: this is the final period in the window".format(me = self.name))
                        state.printInfo()
            
            selperiod = selperiod.previousperiod
            #end sub
            
        for dev in plan.devices:
            dev.revertStateGrid()
               
        #get beginning of path from current state
        plan = window.periods[0].getplan(bidgroup)
        curstate = plan.stategrid.match(snapstate)
        if curstate:
            recaction = curstate.optimalinput
        else:
            if debug:
                print("no state match found for {snap}".format(snap = snapstate))
            recaction = None
            
        if recaction:
            #return recaction.pathcost
            return recaction
        else:
            if debug:
                print("no recommended action")
            return 0
    
    def takeStateSnapshot(self):
        comps = {}
        for dev in self.Devices:
            state = dev.getState()
            if state:
                comps[dev.name] = state
        return comps
    
    def findInputCost(self,state,input,plan,duration,debug = False):
        period = plan.period
        
        if debug:
            print(">>HOMEOWNER {me}: finding cost for input {inp}".format(me = self.name, inp = input.components))
        #find next state if this input is applied
        comps = self.applySimulatedInput(state,input,duration,False)
        
        #if the next period is not the last, consider the path cost from that point forward
        if period.nextperiod.nextperiod:
            #cost of optimal path from next state forward
            #pathcost = period.nextperiod.plan.stategrid.interpolatepath(comps,False)
            pathcost = plan.nextplan.stategrid.interpolatepath(comps,False)
        else:
            #otherwise, only consider the statecost 
            pathcost = 0
        #add cost of being in next state for next period
        
        #evaluate the state cost function
        pathcost += self.Preferences.eval(period,comps)
        
        #cost of getting to next state with t
        totaltrans = 0
        for key in input.components:
            dev = listparse.lookUpByName(key, self.Devices)
            totaltrans += dev.inputCostFn(input.components[key],period.nextperiod,state,duration)
        input.pathcost = pathcost + totaltrans
        
        if debug:
            print(">>HOMEOWNER {me}: transition to state: {sta}".format(me = self.name, sta = comps))
            print(">>HOMEOWNER {me}: transition cost is {trans}, total path cost is {path}".format(me = self.name, trans = totaltrans, path = input.pathcost))
        
        return input.pathcost
    
            
    def applySimulatedInput(self,state,input,duration,debug = False):
        total = 0
        newstatecomps = {}
        
        for devname in state.components:
            devstate = state.components[devname]
            devinput = input.components[devname]
            newstate = listparse.lookUpByName(devname,self.Devices).applySimulatedInput(devstate,devinput,duration)
            newstatecomps[devname] = newstate
        
        if debug:
            print(">>>HOMEOWNER {me}: starting state is {start}, ending state is {end}".format(me = self.name, start = state.components, end = newstatecomps))
        
        return newstatecomps
    
        
    def makeInputs(self,state,plan,debug = False):
        
        inputdict = {}
        inputs = []
        
        period = plan.period
        
        for dev in plan.devices:
            if dev.actionpoints:
                if len(plan.devices) >= 2:
                    inputdict[dev.name] = dev.getActionpoints("lofi")
                elif len(plan.devices) == 1:
                    inputdict[dev.name] = dev.getActionpoints()
                else:
                    inputdict[dev.name] = dev.getActionpoints()
            
        devactions = combin.makeopdict(inputdict)
        
        #generate input components
        #grid connected inputs
        if period.pendingdrevents:
            for devact in devactions:
                newinput = optimization.InputSignal(devact,True,period.pendingdrevents[0])
                
                if self.admissibleInput(newinput,state,plan,False):
                    inputs.append(newinput)
        
        #no DR participation
        for devact in devactions:
            newinput = optimization.InputSignal(devact,True,None)
                
            if self.admissibleInput(newinput,state,plan,False):
                inputs.append(newinput)
            
        #non grid connected inputs
        #do this later... needs special consideration
        
        if debug:
            print("HOMEOWNER {me} made input list for period {per} with {num} points".format(me = self.name, per = period.periodNumber, num = len(inputs)))

        
        plan.setAdmissibleInputs(inputs)
        
        


    def admissibleInput(self,input,state,plan,debug = False):
        #sum power from all components
        totalsource = 0
        totalsink = 0
        maxavail = 0
        
        period = plan.period
        
        for compkey in input.components:
            device = listparse.lookUpByName(compkey,plan.devices)
            if device.issource:
                #we may be dealing with a source or storage element
                #the sign of the setpoint must indicate whether it is acting as a source or sink
                
                #is the disposition of the device consistent with its state?
                if device.statebehaviorcheck(state,input):
                    pass
                else:
                    #input not consistent with state
                    if debug:
                        print("inadmissible input: {input} doesn't make sense for {state}".format(input = input.components, state = state.components))
                    return False
                #keep track of contribution from source
                totalsource += device.getPowerFromPU(input.components[compkey])
            else:
                if device.issink:
                    #we're dealing with a device that is only a sink
                    #whatever the sign of its setpoint, it is consuming power
                    totalsink += device.getPowerFromPU(input.components[compkey])
            
            if device.issource:
                if device.isintermittent:
                    #get maximum available power for intermittent sources
                    maxavail += self.checkForecastAvailablePower(device,period)
                    if input.components[compkey] > maxavail:
                        #power contribution exceeds expected capability
                        if debug:
                            print("inadmissible input: {name} device contribution exceeds expected capability")
                        return False
                else:
                    maxavail += device.maxDischargePower
                
        totalnet = totalsource - totalsink
        
        minpower = 0
        maxpower = 0
        
        if not input.gridconnected:
            if totalnet != 0:
                #not connected to grid, all load must be locally served
                if debug:
                    print("Inadmissible input: source and load must balance when not grid connected")
                
        if input.drevent:
            dr = input.drevent
            if isinstance(dr,CurtailmentEvent):
                if input.gridconnected:
                    minpower = 0
                    maxpower = self.getDRPower(dr)
                else:
                    minpower = 0
                    maxpower = self.getLocallyAvailablePower()
            elif isinstance(dr,LoadUpEvent):
                if input.gridconnected:
                    minpower = self.getDRPower(dr)
                    maxpower = 999
                else:
                    #can't load up if we aren't loading at all
                    if debug:
                        print("inadmissible input: load up and disconnect")  # just for debugging
                    return False
            else:
                #if not participating in a DR event
                if input.gridconnected:
                    minpower = -float('inf')
                    maxpower = float('inf')
                else:
                    minpower = 0
                    
        
        return True

    def getDRpower(self,event):
        if event.spec == "reducebypercent":
            pass
    
        
        
            
    def requestForecast(self,period):
        mesdict = {}
        mesdict["message_sender"] = self.name
        mesdict["message_target"] = "Goddard"
        mesdict["message_subject"] = "forecast"
        mesdict["message_type"] = "forecast_request"
        mesdict["requested_data"] = ["temperature"]
        mesdict["forecast_period"] = period.periodNumber
        
        mes = json.dumps(mesdict)
        self.vip.pubsub.publish("pubsub","weatherservice",{},mes)


    def advancePeriod(self):
        self.CurrentPeriod = self.PlanningWindow.periods[0]
        self.PlanningWindow.shiftWindow()
        self.NextPeriod = self.PlanningWindow.periods[0]
        
        #request forecast
        for period in self.PlanningWindow.periods:
            self.requestForecast(period)
        
        if settings.DEBUGGING_LEVEL >= 1:
            print("\nHOMEOWNER AGENT {me} moving into new period:".format(me = self.name))
            self.CurrentPeriod.printInfo()
        
        #call enact plan
        self.enactPlan(self.CurrentPeriod)
                
        #run new price forecast
        self.priceForecast()
        
        #find offer price
        before = time.time()
        
        #self.NextPeriod.offerprice, self.NextPeriod.plan.optimalcontrol = self.determineOffer(True)
        self.makeNewPlan(True)
        
        #add plan to database
        if self.NextPeriod.plans:
            for plan in self.NextPeriod.plans:
                self.dbnewplan(plan.optimalcontrol,time.time()-before,self.dbconn,self.t0)
                plan.planningcomplete = True
                
#         if settings.DEBUGGING_LEVEL >= 2:
#             print("HOMEOWNER {me} generated offer price: {price}".format(me = self.name,price = self.NextPeriod.offerprice))
#             self.NextPeriod.plan.optimalcontrol.printInfo(0)
                    
        self.prepareBids(self.NextPeriod)
                
        #now that we have the offer price we can respond with bids, but wait until the utility has solicited them
        if self.NextPeriod.supplybidmanager.recPowerSolicitation and self.NextPeriod.demandbidmanager.recDemandSolicitation:
            if settings.DEBUGGING_LEVEL >= 2:
                print("HOMEOWNER {me} ALREADY RECEIVED SOLICITATION, SUBMITTING BIDS NOW".format(me = self.name))
            self.submitBids(self.NextPeriod.demandbidmanager)
            self.submitBids(self.NextPeriod.supplybidmanager)
            
        else:
            if settings.DEBUGGING_LEVEL >= 2:
                print("HOMEOWNER {me} WAITING FOR SOLICITATION".format(me = self.name))
            
        
        self.printInfo(0)
        
        
        
        
        #provisionally schedule next period pending any revisions from utility
        #if the next period's start time changes, this event must be cancelled
        self.advanceEvent = self.core.schedule(self.CurrentPeriod.endTime,self.advancePeriod)

    
    '''responsible for enacting the plan which has been defined for a planning period'''
    def enactPlan(self,period):
        comps = period.disposition.components
        
        #operate main relay
        if period.disposition.closeRelay:
            self.connectLoad()
            if settings.DEBUGGING_LEVEL >= 2:
                print("HOMEOWNER {me} connecting load in period {per}".format(me = self.name, per = period.periodNumber))
        else:
            self.disconnectLoad()
        
        #update resource dispositions
        for res in self.Resources:
            #is the resource in this period's disposition?
            if res.name in comps:
                devdisp = comps[res.name]
                if devdisp.mode == "power":
                    res.setDisposition(devdisp.value)
                elif devdisp.mode == "reserve":
                    if devdisp.param:
                        res.setDisposition(devdisp.value,devdisp.param)
                    else:
                        print("{dev} has no param even though it's a reserve. i wonder why.".format(dev = res.name))
                        res.setDisposition(devdisp.value,-.2)
                else:
                    pass
            #if not, we should make sure the device is disconnected
            else:
                res.setDisposition(0)
                
        #keep track of simulated device states
        for app in self.Appliances:
            #is the device in this period's disposition?
            if app.name in comps:
                appdisp = comps[app.name]
                if appdisp.value == 0:
                    app.on = False
                    print("turning {app} off".format(app = app.name))
                elif appdisp.value > 0:
                    app.on = True
                    print("turning {app} on".format(app = app.name))
            #if it isn't part of the disposition, turn it off
            else:
                print("{app} is not in this period's disposition. turning it off".format(app = app.name))
                app.on = False
        Cap = self.CapNumber()
        tagClient.writeTags(["TOTAL_CAP_DEMAND"], Cap, "load")
        print("capacitor number:")
        print(Cap)        
        #need to put into database also
        
    def disconnectLoad(self):
        #we can disconnect load at will
        tagClient.writeTags([self.relayTag],[False],"load")
        
        if settings.DEBUGGING_LEVEL >= 2:
            print("HOMEOWNER {me} DISCONNECTING FROM GRID".format(me = self.name))
        
        
    def connectLoad(self):
        #if we are not already connected, we need permission from the utility
        mesdict = {"message_subject" : "request_connection",
                   "message_sender" : self.name,
                   "message_target" : self.utilityName
                   }
        if settings.DEBUGGING_LEVEL >= 2:
            print("Homeowner {me} asking utility {them} for connection in PERIOD {per}".format(me = self.name, them = mesdict["message_target"], per = self.CurrentPeriod.periodNumber))
        
        mess = json.dumps(mesdict)
        self.vip.pubsub.publish(peer = "pubsub",topic = "customerservice",headers = {}, message = mess)
        
    def measureVoltage(self):
        #print (self.voltageTag)
        return tagClient.readTags([self.voltageTag],"load")
        
    def measureCurrent(self):
        #print(self.currentTag)
        return tagClient.readTags([self.currentTag],"load")
    
    def measurePower(self):
        #print(self.measureVoltage())
        #print(self.measureCurrent())
        return self.measureVoltage()*self.measureCurrent()
    
    def measurePF(self):
        return tagClient.readTags([self.powerfactorTag],"grid")
    
    
    def measureNetPower(self):
        net = self.measurePower()
        
        for res in self.Resources:
            #if resources are colocated, we have to account for their power contribution/consumption
            if res.location == self.location:
                if res.issource:
                    net += res.getOutputRegPower()
                if res.issink:
                    net -= res.getInputUnregPower()
        return net
    
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
    
    def dbnewappliance(self, newapp, dbconn, t0):
        command = 'INSERT INTO appliances VALUES("{time}",{et},"{name}","{type}","{owner}",{pow})'.format(time = datetime.utcnow().isoformat(), et = time.time()-t0, name = newapp.name, type = newapp.__class__.__name__, owner = newapp.owner, pow = newapp.nominalpower)
        self.dbwrite(command,dbconn)
        
        
    def dbnewresource(self, newres, dbconn, t0):
        command = 'INSERT INTO resources VALUES("{time}",{et},"{name}","{type}","{owner}","{loc}", {pow})'.format(time = datetime.utcnow().isoformat(), et = time.time()-t0, name = newres.name, type = newres.__class__.__name__,owner = newres.owner, loc = newres.location, pow = newres.maxDischargePower)
        self.dbwrite(command,dbconn)
        
        
    def dbupdateappliance(self, app, power, dbconn, t0):
        command = 'INSERT INTO appstate VALUES ("{time}",{et},{per},"{name}",{state},{pow})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = self.CurrentPeriod.periodNumber, name = app.name, state = app.getStateEng(), pow = power)
        self.dbwrite(command,dbconn)
        
    def dbupdateresource(self,res,dbconn,t0):
        ch = res.DischargeChannel
        meas = tagClient.readTags([ch.unregVtag, ch.unregItag, ch.regVtag, ch.regItag],"source")
        command = 'INSERT INTO resstate (logtime, et, period, name, connected, reference_voltage, setpoint, inputV, inputI, outputV, outputI) VALUES ("{time}",{et},{per},"{name}",{conn},{refv},{setp},{inv},{ini},{outv},{outi})'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = self.CurrentPeriod.periodNumber, name = res.name, conn = int(res.connected), refv = ch.refVoltage, setp = ch.setpoint, inv = meas[ch.unregVtag], ini = meas[ch.unregItag] , outv = meas[ch.regVtag], outi = meas[ch.regItag])
        self.dbwrite(command,dbconn)
    
    def dbnewplan(self, action, plantime, dbconn, t0):
        command = 'INSERT INTO plans VALUES ("{time}",{et},{per},{pt},"{planner}",{cost},"{action}")'.format(time = datetime.utcnow().isoformat(), et = time.time() - t0, per = self.NextPeriod.periodNumber, pt = plantime, planner = self.name, cost = action.pathcost, action = json.dumps(action.components).replace('"',' '))
        self.dbwrite(command,dbconn)
            
               
    def dbwrite(self,command,dbconn):
        try:
            cursor = dbconn.cursor()
            cursor.execute(command)
            dbconn.commit()
            cursor.close()
        except Exception as e:
            print(e)
            
    def printInfo(self,depth):
        print("\n________________________________________________________________")
        print("~~SUMMARY OF HOME STATE~~")
        print("HOME NAME: {name}".format(name = self.name))
        if 'self.CurrentPeriod' in globals():
            print("PERIOD: {per}".format(per = self.CurrentPeriod.periodNumber))
            print(">>>START: {start}  STOP: {end}".format(start = self.CurrentPeriod.startTime, end =  self.CurrentPeriod.endTime))
        print("HERE ARE MY CURRENT PLANS:")
        for plan in self.CurrentPeriod.plans:
            plan.printInfo(1)
        
        print("SMART APPLIANCES:")
        for app in self.Appliances:
            app.printInfo(1)
        print("LIST ALL OWNED RESOURCES ({n})".format(n = len(self.Resources)))
        for res in self.Resources:
            res.printInfo(1)
        
        print("__________________________________________________________________")
       
    
def main(argv = sys.argv):
    '''Main method called by the eggsecutable'''
    try:
        utils.vip_main(HomeAgent)
    except Exception as e:
        _log.exception('unhandled exception')
        
if __name__ == '__main__':
    sys.exit(main())


