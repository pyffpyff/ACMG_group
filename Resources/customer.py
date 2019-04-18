from datetime import datetime, timedelta

from ACMGAgent.CIP import tagClient
from __builtin__ import False
import math

class CustomerProfile(object):
    def __init__(self,name,location,resources,priorityscore,**kwargs):
        self.name = name
        self.location = location
        self.resources = resources
        self.Resources = []
        self.tagCache = {}
        #permission to connect to grid
        self.permission = False
        
        self.priorityscore = priorityscore
        
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
        
        self.customerAccount = Account(self.name,0.0)
        self.DRenrollee = False
        
      
    def addResource(self,res):
        res.owner = self
        self.Resources.append(res)
        
        
    #change maximum power draw for a customer
    def updateService(self,amount):
        self.maxDraw = amount
            
    def disconnectCustomer(self):
        tagClient.writeTags([self.relayTag],[False],"load")
        
    def connectCustomer(self):
        tagClient.writeTags([self.relayTag],[True],"load")
        print("connect customer")
        
    def measureVoltage(self):
        tag = self.voltageTag
        tagval = tagClient.readTags([tag],"load")
        self.tagCache[tag] = (tagval, datetime.now())
        return tagval
    
    def measureCurrent(self):
        tag = self.currentTag
        tagval = tagClient.readTags([tag],"load")
        self.tagCache[tag] = (tagval, datetime.now())
        return tagval
    
    def measurePower(self):
        print("currentTag in customer profile:")
        print(self.currentTag)
        currentvals = tagClient.readTags([self.currentTag],"load")
        print("voltageTag in customer profile:")
        print(self.voltageTag)
        
        voltagevals = tagClient.readTags([self.voltageTag],"load")
        print("measure power")
        power = currentvals * voltagevals
        print("measure finished")
 #       self.tagCache[self.powerTag] = (power, datetime.now())
        return power
            
    def measurePF(self):
        tag = self.powerfactorTag
        tagval = tagClient.readTags([tag],"load")
        self.tagCache[tag] = (tagval, datetime.now())
        return tagval    
    
   
    
    '''calls measureCurrent only if cached value isn't fresh'''    
    def getCurrent(self,threshold = 5.1):
        tag = self.currentTag
        val, time = self.tagCache.get(tag,(None, None))
        if val is not None and time is not None:
            diff = datetime.now() - time
            et = diff.total_seconds()    
                    
            if et < threshold:
                return val        
        return measureCurrent()
    
    '''calls measureVoltage only if cached value isn't fresh'''
    def getVoltage(self,threshold = 5.1):
        tag = self.voltageTag
        val, time = self.tagCache.get(tag,(None, None))
        if val is not None and time is not None:
            diff = datetime.now() - time
            et = diff.total_seconds()
            
            if et < threshold:
                return val 
        return measureVoltage()
    
   
    def getPF(self, threshold = 5.1):
        tag = self.powerfactorTag
        val, time = self.tagCache.get(tag, (None, None))
        if val is not None and time is not None:
            diff = datetime.now() - time
            et = diff.total_seconds()    
                    
            if et < threshold:
                return val 
        return measurePF()
    
        
    
    def printInfo(self, depth = 0):
        spaces = '    '
        print(spaces*depth + "CUSTOMER: {name} is a {type}".format(name = self.name, type = self.__class__.__name__))
        print(spaces*depth + "LOCATION: {loc}".format(loc = self.location))
        print(spaces*depth + "RESOURCES:")
        for res in self.Resources:
            res.printInfo(depth + 1)
            
class ResidentialCustomerProfile(CustomerProfile):
    def __init__(self,name,location,resources,priorityscore,**kwargs):
        super(ResidentialCustomerProfile,self).__init__(name,location,resources,priorityscore,**kwargs)
        self.maxDraw = 20
        self.rateAdjustment = 1
        
        
class CommercialCustomerProfile(CustomerProfile):
    def __init__(self,name,location,resources,priorityscore,**kwargs):
        super(CommercialCustomerProfile,self).__init__(name,location,resources,priorityscore,**kwargs)
        self.maxDraw = 10
        self.rateAdjustment = 1
        
class IndustrialCustomerProfile(CustomerProfile):
    def __init__(self,name,location,resources,priorityscore,**kwargs):
        super(CommercialCustomerProfile,self).__init__(name,location,resources,priorityscore,**kwargs)
        self.maxDraw = 10
        self.rateAdjustment = 1
        
class Account(object):
    def __init__(self,holder,initialBalance = 0):
        self.holder = holder
        self.accountBalance = initialBalance
        
    def adjustBalance(self,amount):
        self.accountBalance += amount
        
        
class ResourceProfile(object):
    def __init__(self,**res):
        self.owner = res["owner"]
        self.capCost = res["capCost"]
        self.location = res["location"]
        self.name = res["name"]
        
        self.dischargeChannelNumber = res["dischargeChannel"]
        
        self.state = None
        self.setpoint = None
        
        self.dischargeVoltageTag = "SOURCE_{d}_RegVoltage".format(d = self.dischargeChannelNumber)
        self.dischargeCurrentTag =  "SOURCE_{d}_RegCurrent".format(d = self.dischargeChannelNumber)
        
        
        
    def getDischargeCurrent(self):
        return tagClient.readTags([self.dischargeCurrentTag],"grid")
    
    def getDischargeVoltage(self):
        return tagClient.readTags([self.dischargeVoltageTag],"grid")
        
    def getDischargePower(self):
        power = self.getDischargeCurrent()*self.getDischargeVoltage()
        return power
            
    def setOwner(self,newOwner):
        self.owner = newOwner
        
    def printInfo(self, depth = 0):
        spaces = '    '
        print(spaces*depth + "RESOURCE: {name} is a {type}".format(name = self.name, type = self.__class__.__name__))
        print(spaces*depth + "LOCATION: {loc}".format(loc = self.location))

class SourceProfile(ResourceProfile):
    def __init__(self,**res):
        super(SourceProfile,self).__init__(**res)
        self.maxDischargePower = res["maxDischargePower"]
       
    def getChargePower(self):
        return 0
    
class StorageProfile(SourceProfile):
    def __init__(self,**res):
        super(StorageProfile,self).__init__(**res)
        self.maxChargePower = res["maxChargePower"]
        self.capacity = res["capacity"]
        
        self.chargeChannelNumber = res["chargeChannel"]
        
        self.chargeVoltageTag = "SOURCE_{d}_UnregVoltage".format(d = self.chargeChannelNumber)
        self.chargeCurrentTag = "SOURCE_{d}_UnregCurrent".format(d = self.chargeChannelNumber)
        

    def getChargeCurrent(self):
        return tagClient.readTags([self.chargeCurrentTag],"grid")
    
    def getChargeVoltage(self):
        return tagClient.readTags([self.chargeVoltageTag],"grid")
        
    def getChargePower(self):
        return self.getChargeCurrent() * self.getChargeVoltage()

        
class LeadAcidBatteryProfile(StorageProfile):
    def __init__(self,**res):
        super(LeadAcidBatteryProfile,self).__init__(**res)
        
class ACresourceProfile(SourceProfile):
    def __init__(self,**res):
        super(ACresourceProfile,self).__init__(**res)
        #generator is called "ACresource"
        
        
        
        
        