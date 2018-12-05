from ACMGAgent.Resources import groups, optimization
from ACMGAgent.Resources.misc import faults, listparse
from ACMGAgent.Resources.mathtools import combin

from datetime import datetime, timedelta

import random
import json
from twisted.application.service import Service
from setuptools.command.build_ext import if_dl

class Window(object):
    def __init__(self,planner,length,startingPeriod,startTime,increment):
        self.windowlength = length
        self.periods = []
        self.nextstarttime = startTime
        self.increment = increment
        self.planner = planner
        
        self.nextperiodnumber = startingPeriod
        for i in range(self.windowlength):
            self.appendPeriod()
    
    #remove the expired period and add a new one to the end of the list
    def shiftWindow(self):
        #get rid of oldest period
        self.periods.pop(0)
        #remove link to newly removed period
        self.periods[0].previousperiod = None
        
        self.appendPeriod()
        
    #remove the plans associated with all periods in the window
    def clearPlans(self):
        for period in self.periods:
            period.plans = []
            
    
    def resetPlans(self,devicesets,debug = False):
        for period in self.periods:
            period.plans = []
            for deviceset in devicesets:
                period.plans.append(Plan(period,deviceset))
        
        for period in self.periods:   
            if period.nextperiod:
                for plan1 in period.plans:
                    for plan2 in period.nextperiod.plans:
                        if plan1.devices == plan2.devices:
                            plan1.nextplan = plan2
                            
        if debug:
            print("!!! PLANNING WINDOW PLANS RESET !!!")
            self.printInfo(1)
                    
        
    #create a new Period instance and append it to the list of periods in the window
    def appendPeriod(self):
        endtime = self.nextstarttime + timedelta(seconds = self.increment)
        newperiod = Period(self.nextperiodnumber,self.nextstarttime,endtime)
        newperiod.planner = self.planner
        #default assumption is that price won't change from previous period
        if self.periods:
            newperiod.setExpectedCost(self.periods[-1].expectedenergycost)
            #link new period to last one currently in list
            newperiod.previousperiod = self.periods[-1]
            #link last period to new period
            self.periods[-1].nextperiod = newperiod
        self.periods.append(newperiod)
        self.nextperiodnumber += 1
        self.nextstarttime = endtime
        
    
    def rescheduleWithNewInterval(self,periodnumber,newstarttime,newinterval):
        self.increment = newinterval
        self.rescheduleSubsequent(periodnumber,newstarttime)
            
    def rescheduleSubsequent(self,periodnumber,newstarttime):
        for period in self.periods:
            if period.periodNumber >= periodnumber:
                period.startTime = newstarttime
                endtime = newstarttime + timedelta(seconds = self.increment)
                period.endTime = endtime
                newstarttime = endtime
                self.nextstarttime = newstarttime
                
    def getPeriodByNumber(self,number):
        for period in self.periods:
            if period.periodNumber == number:
                return period
        print("can't find period number {num}".format(num = number))
        return None
                
    def printInfo(self,depth):
        tab = "    "
        print(tab*depth + "PLANNING WINDOW for periods {start} to {end}".format(start = self.periods[0].periodNumber, end = self.periods[-1].periodNumber))
        print(tab*depth + ">>PERIODS:")
        for period in self.periods:
            print(tab*depth + ">PERIOD:")
            period.printInfo(depth + 1)
        
class Period(object):
    def __init__(self,periodNumber,startTime,endTime,planner = None):
        self.periodNumber = periodNumber
        self.startTime = startTime
        self.endTime = endTime
        #self.planner = planner
        
        self.pendingdrevents = []
        self.accepteddrevents = []
        self.forecast = None
        
        self.expectedenergycost = 0
        self.offerprice = None
        
        #initialize the plan for this period
        self.plans = []
        
        #initialize bid manager object for this period
        self.supplybidmanager = BidManager(self)
        self.demandbidmanager = BidManager(self)
        
        #initialize resource disposition for this period
        self.disposition = Disposition(self)
        
        #links to previous and subsequent periods
        self.previousperiod = None
        self.nextperiod = None
        
        #has an official rate been announced for this period?
        self.firmrate = False
        
    def makeplan(self,bidgroup):
        plan = self.getplan(bidgroup)
        plan = Plan(self,bidgroup)
        self.plans.append(plan)
         
        return plan
     
    def getplan(self,bidgroup):
        for plan in self.plans:
            if plan.devices[0] in bidgroup:
                return plan
        return None
        
    def allplanscomplete(self):
        for plan in self.plans:
            if not plan.planningcomplete:
                return False
        return True
        
    def setExpectedCost(self,cost):
        self.expectedenergycost = cost
        
    def newDRevent(self,event):
        self.pendingdrevents.append(event)
        
    def acceptDRevent(self,event):
        self.accepteddrevents.append(event)
        self.pendingdrevents.remove(event)
    
    def addForecast(self,forecast):
        self.forecast = forecast
        
    def printInfo(self, depth = 0):
        tab = "    "
        print(tab*depth + "SUMMARY OF PERIOD {num}".format(num = self.periodNumber))
        print(tab*depth + "START: {start}".format(start = self.startTime.isoformat()))
        print(tab*depth + "END: {end}".format(end = self.endTime))
        print(tab*depth + "PLAN INFORMATION:")
        for plan in self.plans:
            plan.printInfo(depth + 1)
        print(tab*depth + "BID INFORMATION:")
        self.supplybidmanager.printInfo(depth + 1)
        self.demandbidmanager.printInfo(depth + 1)
        
    
class Plan(object):
    def __init__(self,period,devices):
        self.period = period
        #self.planner = planner
        
        self.devices = devices
        self.costfn = None
        self.offerprice = None
            
        #list of all points in the device statespace to be evaluated
        self.stategrid = None
        #list of all controls admissible for a point,
        self.admissiblecontrols = None
        #list of inputs that have been deemed unsatisfactory for this plan
        self.disqualifiedcontrols = None
        #current estimate of optimal control
        self.optimalcontrol = None
        
        self.planningcomplete = False
        
        self.nextplan = None
        
    def nextplan(self):
        return self.period.nextperiod.getplan(self.bidgroup)
        
    def makeGrid(self,costfunc):
        inputdict = {}
        for dev in self.devices:
            if dev.gridpoints:
                if len(self.devices) >= 3:
                    inputdict[dev.name] = dev.getGridpoints("lofi")
                else:
                    inputdict[dev.name] = dev.getGridpoints()
                    
        devstates = combin.makeopdict(inputdict)
        
        self.stategrid = optimization.StateGrid(self.period,devstates,costfunc)
        
        return self.stategrid
        
    def setAdmissibleInputs(self,inputs):
        temp = []
        if self.admissiblecontrols:
            temp = self.admissiblecontrols[:]
        self.admissiblecontrols = inputs
        return temp
    
    def printInfo(self, depth = 0):
        tab = "    "
        print(tab*depth + "PLAN for {per}".format(per = self.period.periodNumber))
        print(tab*depth + "INCLUDES THE FOLLOWING DEVICES:")
        for dev in self.devices:
            print(tab*(depth+1) + "{name}".format(name = dev.name))

        print(tab*depth + "OPTIMAL CONTROL:")
        if self.optimalcontrol:
            self.optimalcontrol.printInfo(depth + 1)
        if self.offerprice:
            print(tab*depth + "FOR RATE: {rate}".format(rate = self.offerprice))
        if self.costfn:
            print(tab*depth + "COST FUNCTION: {cfn}".format(cfn = self.costfn))

class Disposition(object):
    def __init__(self,period):
        self.period = period
        self.components = {}
        
        self.closeRelay = False
        
        
    def printInfo(self,depth = 1):
        tab = "    "
        print(depth*tab + "ASSET DISPOSITION FOR PERIOD {per}".format(per = self.period.periodNumber))
        for compkey in self.components:
            self.components[compkey].printInfo(depth + 1)
            
            
class DeviceDisposition(object):
    def __init__(self,name,value,mode,param = None):
        self.name = name
        self.value = value
        self.mode = mode
        
        self.param = param
        
    def printInfo(self,depth = 0):
        tab = "    "
        print(depth*tab + "DISPOSITION FOR DEVICE {dev}: {val} as {mod}".format(dev = self.name, val = self.value, mod = self.mode))
        
class BidManager(object):
    def __init__(self,period):
        self.period = period
        
        self.recDemandSolicitation = False
        self.recReserveSolicitation = False
        self.recPowerSolicitation = False
        
        #preliminary bids that are created in response to solicitations
        self.initializedbids = []
        
        #bids that have been defined and ready to be sent
        self.readybids = []
        
        #bids that have been submitted to a utility
        self.pendingbids = []
        
        #bids that have been accepted by a utility
        self.acceptedbids = []
        
        #bids that have been rejected by a utility
        self.rejectedbids = []
        
    def initBid(self,newbid):
        self.initializedbids.append(newbid)
        
    def setBid(self,bid,amount,rate,name = None,service = None, auxilliaryService = None):
        bid.rate = rate
        bid.amount = amount
        if service:
            bid.service = service
        if name:
            bid.resourceName = name
        if auxilliaryService:
            bid.auxilliaryService = auxilliaryService
        
        return bid.makedict()
    
    def readyBid(self,bid,**mesdict):
        bid.routinginfo = mesdict
        
        self.moveInitToReady(bid)
    
    def sendBid(self,bid):
        outdict = bid.makedict()
        for key in bid.routinginfo:
            outdict[key] = bid.routinginfo[key]
        bid.bidstring = json.dumps(outdict)    
        
        self.moveReadyToPending(bid)
        #print(bid.bidstring)
        return bid.bidstring
        
    def findBid(self,uid,list):
        #print(list)
        for bid in list:
            #print(bid.uid)
            if bid.uid == uid:
                return bid
        print("couldn't match id: {id}".format(id = uid))
        return None
    
    def procSolicitation(self,**soldict):
        if soldict.get("message_subject",None) == "bid_solicitation":
            if soldict.get("side",None) == "supply":
                if soldict.get("service",None) == "power":
                    self.recPowerSolicitation = True
                elif soldict.get("service",None) == "reserve":
                    self.recReserveSolicitation = True
                else:
                    print("error processing solicitation: unrecognized service name")
            elif soldict.get("side",None) == "demand":
                self.recDemandSolicitation = True
            else:
                print("error processing solicitation: bad solicitation message")
        elif soldict.get("message_subject",None) == "bid_solicitation_cancellation":
            if soldict.get("side",None) == "supply":
                if soldict.get("service",None) == "power":
                    self.recPowerSolicitation = False
                elif soldict.get("service",None) == "reserve":
                    self.recReserveSolicitation = False
                else:
                    print("error processing solicitation cancellation: unrecognized service name")
            elif soldict.get("side",None) == "demand":
                self.recDemandSolicitation = False
            else:
                print("error processing solicitation cancellation: bad solicitation message")
        else:
            print("error processing solicitation cancellation: not a solicitation")
        
    def findAccepted(self,uid):
        return self.findBid(uid,self.acceptedbids)
    
    def findReady(self,uid):
        return self.findBid(uid,self.readybids)
    
    def findPending(self,uid):
        return self.findBid(uid,self.pendingbids)
    
        
    def getTotalAccepted(self):
        total = 0
        for bid in self.acceptedbids:
            total += bid.amount
        return total
     
    
    def updateBid(self,bid,**biddict):
        if biddict.get("rate",None):
            bid.rate = biddict["rate"]
            
        if biddict.get("amount",None):
            bid.amount = biddict["rate"]
    
    def bidAccepted(self,bid,**biddict):
        #may need to revise amount and rate
        bid.rate = biddict["rate"]
        bid.amount = biddict["amount"]
        mode = biddict.get("service",None)
        self.movePendingToAccepted(bid)
        
#         if bid.resourceName:
#             self.period.disposition.components[bid.resourceName] = DeviceDisposition(bid.resourceName,bid.amount,mode)
#         else:
#             if bid.side == "demand":
#                 self.period.disposition.closeRelay = True
        
    def bidRejected(self,bid):
        self.movePendingToRejected(bid)
        
    def move(self,bid,fromlist,tolist):
        if bid in fromlist:
            if bid not in tolist:
                tolist.append(bid)
                fromlist.remove(bid)
                return 0
            else:
                return -1
        else:
            return -2
        
    def moveInitToReady(self,bid):
        return self.move(bid,self.initializedbids,self.readybids)
    
    def moveReadyToPending(self,bid):
        return self.move(bid,self.readybids,self.pendingbids)
    
    def movePendingToAccepted(self,bid):
        bid.accepted = True
        return self.move(bid,self.pendingbids,self.acceptedbids)
    
    def movePendingToRejected(self,bid):
        bid.accepted = False
        return self.move(bid,self.pendingbids,self.rejectedbids)
        
    def printInfo(self,depth = 0):
        tab = "    "
        print("BID MANAGER for period {per}".format(per = self.period.periodNumber))
        print("INITIALIZED BIDS:")
        for bid in self.initializedbids:
            bid.printInfo(depth + 1)
        print("READY BIDS:")
        for bid in self.readybids:
            bid.printInfo(depth +1)
        print("PENDING BIDS:")
        for bid in self.pendingbids:
            bid.printInfo(depth + 1)
        print("ACCEPTED BIDS:")
        for bid in self.acceptedbids:
            bid.printInfo(depth + 1)
        print("REJECTED BIDS:")
        for bid in self.rejectedbids:
            bid.printInfo(depth + 1)
            
#financial stuff
class BidBase(object):
    def __init__(self,**biddict):
        self.amount = biddict.get("amount",None)
        self.rate = biddict.get("rate",None)
        self.counterparty = biddict["counterparty"]
        self.periodNumber = biddict["period_number"]
        self.side = biddict["side"]
                
        self.accepted = False
        self.modified = False
        
        self.bidstring = None
        self.routinginfo = None
        
        #optionally used to link to the plan on which the bid is based
        self.plan = None
        
        #generate an id randomly if one is not specified
        if biddict.get("uid",None):
            self.uid = biddict["uid"]
        else:
            self.uid = random.getrandbits(32)
        
    
    def makedict(self):
        outdict = {}
        outdict["amount"] = self.amount
        outdict["rate"] = self.rate
        outdict["counterparty"] = self.counterparty
        outdict["period_number"] = self.periodNumber
        outdict["uid"] = self.uid
        
        return outdict

class SupplyBid(BidBase):
    def __init__(self,**biddict):
        super(SupplyBid,self).__init__(**biddict)
        self.service = biddict.get("service",None)
        self.auxilliaryService = biddict.get("auxilliary_service",None)
        self.resourceName = biddict.get("resource_name",None)
        
    def makedict(self):
        outdict = super(SupplyBid,self).makedict()
        if self.service:
            outdict["service"] = self.service
        if self.resourceName:
            outdict["resource_name"] = self.resourceName
        if self.auxilliaryService:
            outdict["auxilliary_service"] = self.auxilliaryService
            
        outdict["side"] = "supply"
        
        return outdict
        
    def printInfo(self, depth = 0, verbosity = 1):
        spaces = '  '
        print(spaces*depth + "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%")
        print(spaces*depth + "SUPPLY BID INFORMATION for BID {id}".format(id = self.uid))
        print(spaces*depth + "SERVICE: {service} FROM: {res}".format(service = self.service, res = self.resourceName))
        if self.auxilliaryService:
            print(spaces*depth + "AUXILLIARY SERVICE: {aux}".format(aux = self.auxilliaryService))
        print(spaces*depth + "AMOUNT: {amt} AT: {rate} Credits/Joule".format(amt = self.amount, rate = self.rate))
        print(spaces*depth + "FOR PERIOD: {per}".format(per = self.periodNumber))
        print(spaces*depth + "COUNTERPARTY: {ctr}".format(ctr = self.counterparty))
        print(spaces*depth + "STATUS:\n   ACCEPTED: {acc}    MODIFIED: {mod}".format(acc = self.accepted, mod = self.modified))
        print(spaces*depth + "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%")    
        
class DemandBid(BidBase):
    def __init__(self,**biddict):
        super(DemandBid,self).__init__(**biddict)
        #more stuff here later?
        self.resourceName = biddict.get("resource_name",None)
        
    def makedict(self):
        outdict = super(DemandBid,self).makedict()
        #probably more stuff here later...
        outdict["side"] = "demand"
        
        return outdict
        
    def printInfo(self, depth = 0):
        spaces = "    "
        print(spaces*depth + "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%")
        print(spaces*depth + "DEMAND BID INFORMATION for BID {id}".format(id = self.uid))
        if self.resourceName:
            print(spaces*depth + "FROM: {res}".format(res = self.resourceName))
        print(spaces*depth + "AMOUNT: {amt} AT: {rate} Credits/Joule".format(amt = self.amount, rate = self.rate))
        print(spaces*depth + "FOR PERIOD: {per}".format(per = self.periodNumber))
        print(spaces*depth + "COUNTERPARTY: {ctr}".format(ctr = self.counterparty))
        print(spaces*depth + "STATUS:\n   ACCEPTED: {acc}    MODIFIED: {mod}".format(acc = self.accepted, mod = self.modified))
        print(spaces*depth + "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%")    

class Forecast(object):
    def __init__(self,data,period):
        self.data = data
        self.creationperiod = period    

#determine daily rate based on capital cost and rate of return        
def dailyratecalc(capitalCost,discountRate,term):
    yearlyrate = ((discountRate*(1+discountRate)**(term - 1))*capitalCost)/(((1+discountRate)**term)-1)
    dailyrate = yearlyrate/365   #close enough
    return dailyrate

def ratecalc(capitalCost,discountRate,term,capacityFactor):
    dailyrate = dailyratecalc(capitalCost, discountRate, term)
    rate = dailyrate/capacityFactor
    return rate
    
def acceptbidasis(bid):
    bid.accepted = True
    bid.modified = False
        
def acceptbidmod(bid,modamount):
    bid.accepted = True
    bid.modified = True
    bid.amount = bid.amount - modamount
        
def rejectbid(bid):
    bid.accepted = False
    bid.modified = False
    





