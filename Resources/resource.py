import math
import sys

from ACMGAgent.Resources.mathtools import interpolation
from ACMGAgent.Resources.demand import human

from ACMGAgent.CIP import tagClient
from volttron.platform.vip.agent import Core
from ACMGAgent.Resources import control

from datetime import datetime, timedelta

class Resource(object):
    def __init__(self,**res):
        self.owner = res["owner"]
        self.location = res["location"]
        self.capCost = res["capCost"]
        self.name = res["name"]
        
        self.tagCache = {}
        
        self.isintermittent = False
        self.issource = False
        self.issink = False
        
        self.gridpoints = []
        self.tempgridpoints = []
        self.actionpoints = []
        self.snapstate = []
        
    def getGridpoints(self):
        return self.gridpoints
    
    def getActionpoints(self):
        return self.actionpoints
        
    def addCurrentStateToGrid(self):
        #obtain current state
        currentstate = self.getState()
        #if the device has a state
        if currentstate:
            #and it isn't already in the list of grids
            if currentstate not in self.gridpoints and currentstate not in self.snapstate:
                #add the current point to the grid
                self.snapstate.append(currentstate)
                if currentstate not in self.tempgridpoints:
                    self.tempgridpoints.append(currentstate)
                #print("not already in state. added : {pts}".format(pts = self.gridpoints))
        return currentstate
                
    def revertStateGrid(self):
        for point in self.tempgridpoints:
            if point in self.snapstate:
                self.snapstate.remove(point)
            self.tempgridpoints.remove(point)
        
        
    def setOwner(self,newOwner):
        print("transferring ownership of {resource} from {owner} to {newowner}".format(resource = self, owner = self.owner, newowner = newOwner))
        self.owner = newOwner
        
    def printInfo(self, depth = 0):
        space = '    '
        print(space*depth + "**RESOURCE: {name} owned by {owner}\n        TYPE:{type}\n        LOCATION:{loc}".format(name = self.name, owner = self.owner, type = self.__class__.__name__, loc = self.location))

class Source(Resource):
    def __init__(self,**res):
        super(Source,self).__init__(**res)
        self.maxDischargePower = res["maxDischargePower"]
        self.dischargeChannel = res["dischargeChannel"]
        
        self.availDischargePower = 0
        
        self.connected = False
        
        self.DischargeChannel = Channel(self.dischargeChannel)
        
        #should only be nonzero if the resource is enrolled in frequency regulation
        self.FREG_power = 0
    
        self.isintermittent = False
        self.issource = True
        self.issink = False
    
    def statebehaviorcheck(self,state,input):
        return True
    
    def getPowerFromPU(self,pu):
        return pu*self.maxDischargePower
    
    def getPUFromPower(self,power):
        return power/self.maxDischargePower
        
    def getOutputUnregVoltage(self):
        voltage = self.DischargeChannel.getUnregV()
        return voltage
    
    def getOutputRegVoltage(self):
        voltage = self.DischargeChannel.getRegV()
        return voltage
        
    def getOutputUnregCurrent(self):
        current = self.DischargeChannel.getUnregI()
        return current
        
    def getOutputRegCurrent(self):
        current = self.DischargeChannel.getRegI()
        return current
    
    def getOutputUnregPower(self):
        current = self.DischargeChannel.getUnregI()
        voltage = self.DischargeChannel.getUnregV()
        return current*voltage
    
    def getOutputRegPower(self):
        current = self.DischargeChannel.getRegI()
        voltage = self.DischargeChannel.getRegV()
        return current*voltage
    
    #abstract method
    def getInputUnregPower(self):
        return 0
    
    
    def setDisposition(self,setpoint = None, offset = None):
        if setpoint != 0:
            if self.connected:
                if setpoint:
                    if offset:
                        self.DischargeChannel.changeReserve(setpoint,offset)
                    else:
                        self.DischargeChannel.changeSetpoint(setpoint)
                else:
                    #don't need to do anything, already connected
                    pass
            else:
                self.connectSource(setpoint,offset)
        else:
            self.disconnectSource()
                
    def connectSource(self,setpoint = None, offset= None):
        if setpoint:
            if offset:
                self.connected = self.DischargeChannel.connectWithSet(setpoint,offset)
            else:
                self.connected = self.DischargeChannel.connectWithSet(setpoint)
        else:
            self.connected = self.DischargeChannel.connect()
        
    def connectSourceSoft(self,mode,setpoint):
        self.connected = self.DischargeChannel.connectSoft(mode,setpoint)
    
    def disconnectSource(self):
        self.connected = self.DischargeChannel.disconnect()
        
    def disconnectSourceSoft(self):
        self.connected = self.DischargeChannel.disconnectSoft()
        
    
    def printInfo(self, depth = 0, verbosity = 0):
        spaces = '    '
        print(spaces*depth + "**RESOURCE: {name} owned by {owner}\n        TYPE:{type}\n        LOCATION:{loc}".format(name = self.name, owner = self.owner, type = self.__class__.__name__, loc = self.location))
        if verbosity == 1:
            print(spaces*depth + "CURRENT OPERATING INFO:")
            print(spaces*depth + "VUNREG: {vu}  IUNREG: {iu}".format(vu = self.getInputUnregVoltage(), iu = self.getInputUnregCurrent()))
            print(spaces*depth + "VREG: {vr}  IREG: {ir}".format(vr = self.getOutputRegVoltage(), ir = self.getOutputRegCurrent()))

class Storage(Source):
    
    def __init__(self,**res):
        super(Storage,self).__init__(**res)
        self.maxChargePower = res["maxChargePower"]
        self.capacity = float(res["capacity"])
        self.chargeChannel = res["chargeChannel"]
        
        self.isCharging = False
        self.isDischarging = False
        
        
        self.SOC = 0
        self.energy = 0
        
        self.ChargeChannel = Channel(self.chargeChannel)
        
        self.isintermittent = False
        self.issource = True
        self.issink = True
        
        self.replacementenergycost = None
        
        #self.associatedbehavior = None
        
#         #state based cost function specified in config
#         if res.get("costfn",None):
#             addCostFn(self,res)
        
    def getInputUnregVoltage(self):
        voltage = self.ChargeChannel.getUnregV()
        return voltage
    
    def getInputRegVoltage(self):
        voltage = self.ChargeChannel.getRegV()
        return voltage
        
    def getInputUnregCurrent(self):
        current = self.ChargeChannel.getUnregI()
        return current
        
    def getInputRegCurrent(self):
        current = self.ChargeChannel.getRegI()
        return current
    
    def getInputUnregPower(self):
        current = self.ChargeChannel.getUnregI()
        voltage = self.ChargeChannel.getUnregV()
        return current*voltage
    
    def getInputRegPower(self):
        current = self.ChargeChannel.getRegI()
        voltage = self.ChargeChannel.getRegV()
        return current*voltage
        
    def getState(self):
        return self.SOC
        
    def statebehaviorcheck(self,state,input):
        if state < 0.05:
            if input < 0:
                print("{name} inconsistency: discharging while empty".format(name = self.name))
                return False
        
    def getPowerFromPU(self,pu):
        if pu < 0:
            return pu*self.maxChargePower
        else:
            return pu*self.maxDischargePower
        
    def getPUFromPower(self,power):
        if power < 0:
            return power/self.maxChargePower
        else:
            return power/self.maxDischargePower
        
    def setDisposition(self,setpoint = None, offset = None):
        #indicates discharging
        if setpoint > 0:
            if self.isCharging:
                self.ChargeChannel.disconnect()
                self.isCharging = False
            if self.isDischarging:
                if setpoint:
                    if offset:
                        self.DischargeChannel.changeReserve(setpoint,offset)
                    else:
                        self.DischargeChannel.changeSetpoint(setpoint)
                else:
                    #don't need to do anything, already connected
                    pass
            else:
                self.connectSource(setpoint,offset)
                self.isDischarging = True
        #indicates charging
        elif setpoint < 0:
            if self.isDischarging:
                self.DischargeChannel.disconnect()
                self.isDischarging = False
            if self.isCharging:
                if setpoint:
                    if offset:
                        self.ChargeChannel.changeReserve(setpoint,offset)
                    else:
                        self.ChargeChannel.changeSetpoint(setpoint)
                else:
                    #don't need to do anything, already charging
                    pass
            else:
                self.connectSource(setpoint,offset)
                self.isCharging = True
        #indicates disconnection    
        else:
            if self.isCharging:
                self.ChargeChannel.disconnect()
                self.isCharging = False
            elif self.isDischarging:
                self.DischargeChannel.disconnect()
                self.isDischarging = False



class LeadAcidBattery(Storage):
    SOCtable = [(0, 11.8),(.25, 12.0),(.5, 12.2),(.75, 12.4),(1, 12.7)]
    def __init__(self,**res):
        super(LeadAcidBattery,self).__init__(**res)
        self.SOC = self.getSOCfromOCV()
        print("initial SOC for battery {me} is {soc}".format(me = self.name, soc = self.SOC))
        self.preferredSOC = .6
        
        self.cyclelife = 1000
        self.amortizationPeriod = 10
        
        self.FREG_power = .2*self.maxChargePower
        
        self.gridpoints = [0.0, 0.2, 0.4, 0.6, 0.8, 1]
        self.actionpoints = [-1, -0.25, 0, 0.25, 1]
        
        self.isintermittent = False
        self.issource = True
        self.issink = True
        
        self.chargeEfficiency = .7
        self.dischargeEfficiency = .8

    def inputCostFn(self,puaction,period,state,duration):
        if not self.replacementenergycost:
            self.replacementenergycost = .1
            
        power = self.getPowerFromPU(puaction)
        if power >= 0:
            cost = -power*duration*(period.expectedenergycost-(self.replacementenergycost/(self.chargeEfficiency*self.dischargeEfficiency)))
        else:
            cost = -power*duration*(period.expectedenergycost-self.replacementenergycost*self.chargeEfficiency*self.dischargeEfficiency)
        #high cost bids are unlikely to be accepted. estimate expected value of benefit
        #in the future, should probably use long run cost as std deviation for erf
        if power > 1:
            cost *= (1 - math.erf(period.expectedenergycost/self.replacementenergycost))
        #print("device: {name}, power: {pow}, cost: {cost}, duration: {dur}".format(name = self.name, pow = power, cost = cost, dur = duration))
        return cost
        
    def getSOCfromOCV(self):
        #get battery voltage
        voltage = self.DischargeChannel.getUnregV()
        soc = interpolation.lininterp(self.SOCtable,voltage)
        return soc
    
    def getActionpoints(self,mode = "hifi"):
        if mode == "hifi":
            return self.actionpoints
        if mode == "lofi":
            ap = [-1, 0, 1]
            return ap
    
    def getGridpoints(self,mode = "hifi"):
        if mode == "hifi":
            grid = self.gridpoints[:]
            grid.extend(self.snapstate)
            return grid
        elif mode == "lofi":
            grid = [0.0, 0.5, 1.0]
            grid.extend(self.snapstate)
            return grid
        elif mode == "dyn":
            dynamicgrid = []
            dynamicgrid.extend(self.snapstate)
            initstate = self.getState()            
            newstate = self.applySimulatedInput(initstate,1,30)
            if newstate <= 1 and newstate >= 0: 
                dynamicgrid.append(newstate)
            newstate = self.applySimulatedInput(newstate,1,60)
            if newstate <= 1 and newstate >= 0:
                dynamicgrid.append(newstate)
            newstate = self.applySimulatedInput(initstate,-1,30)
            if newstate <= 1 and newstate >= 0:
                dynamicgrid.append(newstate)
            newstate = self.applySimulatedInput(newstate,-1,60)
            if newstate <= 1 and newstate >= 0:
                dynamicgrid.append(newstate)
        
            print("generated gridpoints dynamically for {dev}: {grd}".format(dev = self.name, grd = dynamicgrid))
            return dynamicgrid
        
            
    def applySimulatedInput(self,state,input,duration):
        power = self.getPowerFromPU(input)
        soc = state
        
        delta = ((power/(0.8*12.0))*(duration/3600.0))/self.capacity
        soc = soc - delta
        
        #print("battery change: {delt} power: {pow}".format(delt = delta, pow = power))
        return soc
    
    #state:
    #input: InputSignal class
    def statebehaviorcheck(self,state,input):
        input = input.components[self.name]
        state = state.components[self.name]
        
        if state < 0.02 and input > 0:
            #print("battery is too depleted to discharge")
            return False
        elif state > .98 and input < 0:
            #print("battery is too full to charge")
            return False
        else:
            #print("state {sta} is consistent with input {inp}".format(sta = state, inp = input))
            return True

class ACresource(Source):
    def __init__(self,**res):
        super(ACresource,self).__init__(**res)
        self.fuelCost = res["fuel_cost"]
        self.amortizationPeriod = res["amortization_period"]
        
        self.actionpoints = [0, .1, .25, .5, 1]
        self.gridpoints = [1]
        
    def getState(self):
        return 1
    
#     def costFn(self,period,devstate):
#         return 0
    
    def inputCostFn(self,input,period,state,duration):
        cost = control.ratecalc(self.capCost,.05,self.amortizationPeriod,.2) + self.getPowerFromPU(input)*duration*self.fuelCost
        return cost
    
    def applySimulatedInput(self,state,input,duration):
        return 0









class Channel():
    def __init__(self,channelNumber):
        self.channelNumber = channelNumber
        
        self.connected = False
        
        #droop stuff
        self.noLoadVoltage = 24
        self.refVoltage = 23.6
        self.setpoint = 0
        
        
        #PLC tag names generated from channel number
        #tags for writing
        self.relayTag = "SOURCE_{d}_User".format(d = self.channelNumber)
        self.pSetpointTag = "SOURCE_{d}_psetpoint".format(d = self.channelNumber)
        self.battSelectTag = "SOURCE_{d}_BATTERY_CHARGE_SElECT".format(d = self.channelNumber)
        self.battReqChargeTag = "SOURCE_{d}_BatteryReqCharge".format(d = self.channelNumber)
        self.droopSelectTag = "SOURCE_{d}_DROOP_SELECT".format(d = self.channelNumber)
        self.noLoadVoltageTag = "SOURCE_{d}_noLoadVoltage".format(d = self.channelNumber)
        self.droopCoeffTag = "SOURCE_{d}_droopCoeff".format(d = self.channelNumber)
        
        #deprecated tags for writing
        self.vSetpointTag = "SOURCE_{d}_VoltageSetpoint".format(d = self.channelNumber)
        self.swingSelectTag = "SOURCCE_{d}_SWING_SOURCE_SELECT".format(d = self.channelNumber)
        self.powerSelectTag = "SOURCE_{d}_POWER_REG_SELECT".format(d = self.channelNumber)

        #tags for reading
        self.regVtag = "SOURCE_{d}_RegVoltage".format(d = self.channelNumber)
        self.unregVtag = "SOURCE_{d}_UnregVoltage".format(d = self.channelNumber)
        self.regItag =  "SOURCE_{d}_RegCurrent".format(d = self.channelNumber)
        self.unregItag = "SOURCE_{d}_UnregCurrent".format(d = self.channelNumber)
        
    def getRegV(self):
        tagName = self.regVtag     
        #call to CIP wrapper
        value = tagClient.readTags([tagName],"source")
        return value   
        
    def getUnregV(self):
        tagName = self.unregVtag
        value = tagClient.readTags([tagName],"source")
        return value
    
    def getRegI(self):
        tagName = self.regItag
        value = tagClient.readTags([tagName],"source")
        return value
    
    def getUnregI(self):
        tagName = self.unregItag
        value = tagClient.readTags([tagName],"source")
        return value
    
    '''low level method. only opens the relay'''
    def disconnect(self):
        #disconnect power from the source
        tagClient.writeTags([self.relayTag],[False],"grid")
        #read tag to confirm write success
        print(self.relayTag)
        return tagClient.readTags([self.relayTag],"grid")
    
    def disconnectsourceSoft(self):
        #change setpoint to zero
        tagClient.writeTags([self.pSetpointTag],[0],"source")
        #callback and keep calling back until the current is almost zero
        now = datetime.now()
        Core.schedule(now + timedelta(seconds = 1),self.waitForSettle)
    
    #when the current drops to zero, disconnect the source    
    def waitForSettle(self):
        current = tagClient.readTags([self.regItag],"source")
        if abs(current) < .01:
            self.connected = self.disconnect()
        else:
            now = datetime.now()
            Core.schedule(now + timedelta(seconds = 1),self.waitForSettle)
    
    '''low level method to be called by other methods. only closes the relay'''
    def connect(self):
        tagClient.writeTags([self.relayTag],[True],"grid")
        #read tag to confirm write success
        self.connected = tagClient.readTags([self.relayTag],"grid")
        #self.connected = True
        return self.connected
    
    def confirmrelaystate(self):
        conncheck = tagClient.readTags([self.relayTag],"grid")
        
        if conncheck == self.connected:
            return True
        else:
            print("relay state discrepancy found")
    
    '''calculates droop coefficient based on setpoint and writes to PLC before connecting
    the resource. includes an optional voltage offset argument to be used with reserves'''    
    def connectWithSet(self,setpoint,voffset = 0):
        self.setpoint = setpoint
        droopCoeff = self.setpoint/(self.noLoadVoltage - self.refVoltage)
        #set up parameters for droop control
        tags = [self.noLoadVoltageTag, self.droopCoeffTag, self.droopSelectTag]
        values = [self.noLoadVoltage + voffset, self.setpoint/(self.noLoadVoltage - self.refVoltage), True]
        tagClient.writeTags(tags,values,"source")
        #close relay and connect source
        self.connected = self.connect()
        return self.connected
    
        
        
        
        
    '''changes the droop coefficient by updating the power target at the reference voltage
     and writes it to the PLC. to be used on sources that are already connected'''
    def changeSetpoint(self,newPower):
        self.setpoint = newPower
        droopCoeff = self.setpoint/(self.noLoadVoltage - self.refVoltage)
        tagClient.writeTags([self.droopCoeffTag],[droopCoeff],"source")
    
    '''changes the droop coefficient by updating the power target at the reference voltage
    and writes it to the PLC. also takes a voltage offset argument. to be used with reserve
    sources that are already connected'''
    def changeReserve(self,newPower,voffset):
        self.setpoint = newPower
        droopCoeff = self.setpoint/(self.noLoadVoltage - self.refVoltage)
        tagClient.writeTags([self.droopCoeffTag, self.noLoadVoltageTag],[droopCoeff, self.noLoadVoltage + voffset],"source")
    
    '''creates a voltage offset to the V-P curve corresponding to the addition of a fixed
    amount of power, poffset, at every voltage.'''        
    def setPowerOffset(self,poffset):
        droopCoeff = self.setpoint/(self.noLoadVoltage - self.refVoltage)
        voffset = poffset/droopCoeff
        self.setVoltageOffset(voffset)
        
    '''adds a voltage offset corresponding to an increase in the power offset of deltapoffset'''    
    def addPowerOffset(self,deltapoffset):
        droopCoeff = self.setpoint/(self.noLoadVoltage - self.refVoltage)
        deltavoffset = deltapoffset/droopCoeff
        self.addVoltageOffset(deltavoffset)
    
    '''creates a voltage offset to V-P curve. can be used to create reserve sources
    that are only deployed when needed'''    
    def setVoltageOffset(self,voffset):
        tagClient.writeTags([self.noLoadVoltageTag],[self.noLoadVoltage + voffset],"source")
    
    '''adds to the voltage offset an amount deltavoffset. can be used to implement
    a secondary control loop to correct voltage'''
    def addVoltageOffset(self,deltavoffset):
        voffset = tagClient.readTags([self.noLoadVoltage],"source")
        voffset += deltavoffset
        tagClient.writeTags([self.noLoadVoltage],[voffset],"source")
        
    #deprecated functions below
    '''connects the channel converter and puts it in one of several operating modes.
    Behaviors in each of these modes are governed by the PLC ladder code'''
    def connectMode(self,mode,setpoint):
        ch = self.channelNumber
        
        if mode == "Vreg":
            tagClient.writeTags([self.vSetpointTag],[0],"source")
            tags = [self.battSelectTag ,
                    self.powerSelectTag,
                    self.swingSelectTag]
            tagClient.writeTags([tags],[False,False,True],"source")
            tagClient.writeTags([self.relayTag],[True],"source")
            tagClient.writeTags([self.vSetpointTag],[setpoint],"source")
        elif mode == "Preg":
            tagClient.writeTags([self.pSetpointTag],[0],"source")
            tags = [self.battSelectTag ,
                    self.powerSelectTag,
                    self.swingSelectTag]
            tagClient.writeTags([tags],[False,True,False],"source")
            tagClient.writeTags([self.relayTag],[True],"source")
            tagClient([self.pSetpointTag],[setpoint])
        elif mode == "BattCharge":
            tags = [self.pSetpointTag,
                   self.battSelectTag]
            tagClient.writeTags([tags],[0,True],"source")
            tag = "SOURCE_{d}_BatteryReqCharge".format(d = self.channelNumber)
            tagClient.writeTags([tag],[True],"source")
            tagClient.writeTags([self.pSetpointTag],[setpoint],"source")
        else:
            print("CHANNEL{ch} received a bad mode request: {mode}".format(ch = self.channelNumber,mode = mode))
        
        if tagClient.readTags([self.relayTag],"source"):
            self.connected = True
            return True
        else:
            self.connected = False
            return False
    '''connects in one of the usual modes, but if it's a power regulating source
    it ramps up gradually to avoid exceeding swing source headroom'''
    def connectSoft(self,mode,setpoint):
        ch = self.channelNumber
        
        if mode == "Vreg":
            tagClient.writeTags([self.vSetpointTag],[0],"source")
            tags = [self.battSelectTag ,
                    self.powerSelectTag,
                    self.swingSelectTag]
            tagClient.writeTags([tags],[False,False,True],"source")
            tagClient.writeTags([self.relayTag],[True],"source")
            tagClient.writeTags([self.vSetpointTag],[setpoint],"source")
        elif mode == "Preg":
            tagClient.writeTags([self.pSetpointTag],[0],"source")
            tags = [self.battSelectTag,
                    self.powerSelectTag,
                    self.swingSelectTag]
            tagClient.writeTags([tags],[False,True,False],"source")
            tagClient.writeTags([self.relayTag],[True],"source")
            self.ramp(setpoint)
        elif mode == "BattCharge":
            tags = [self.pSetpointTag,
                   self.battSelectTag]
            tagClient.writeTags([tags],[0,True],"source")
            tag = "SOURCE_{d}_BatteryReqCharge".format(d = self.channelNumber)
            tagClient.writeTags([tag],[True],"source")
            tagClient.writeTags([self.pSetpointTag],[setpoint],"source")
        else:
            print("CHANNEL{ch} received a bad mode request: {mode}".format(ch = self.channelNumber,mode = mode))
            
        if tagClient.readTags([self.relayTag],"source"):
            self.connected = True
            return True
        else:
            self.connected = False
            return False
        
    def disconnectSoft(self,maxStep = .5):
        #ramp down power setpoint and disconnect when finished
        self.ramp(0,maxStep,True)

        
    def ramp(self,setpoint,maxStep = .5,disconnectWhenFinished = False):
        tag = self.pSetpointTag
        currentSetpoint = tagClient.readTags([tag],"source")
        diff = setpoint - currentSetpoint
        if diff > maxStep:
            currentSetpoint += maxStep
        elif diff < -maxStep:
            currentSetpoint -= maxStep
        else:
            currentSetpoint += diff
        tagClient.writeTags([tag],[currentSetpoint],"source")
        
        if abs(diff) > .001:
            #schedule a callback, allowing some time for actuation
            sched = datetime.now() + timedelta(seconds = 1.5)
            Core.schedule(sched,self.ramp)
            print("{me} is scheduling another ramp call: {cs}".format(me = self.channelNumber, cs = currentSetpoint))
            if disconnectWhenFinished == True and setpoint == 0:
                print("ramp with disconnect completed, disconnecting {me}".format(me = self.channelNumber))
                self.disconnect()
        else:
            print("{me} is done ramping to {set}".format(me = self.name, set = setpoint))
        
    
def makeResource(strlist,classlist,debug = False):
    def addOne(item,classlist):
        if type(item) is dict:
            resType = item.get("type",None)
            if resType == "lead_acid_battery":
                res = LeadAcidBattery(**item)
            elif resType == "ACresource":
                res = ACresource(**item)
            else:
                pass
            classlist.append(res)
        
    if type(strlist) is list:
        if len(strlist) > 1:
            if debug:
                print("list contains multiple resources")
            for item in strlist:
                if debug:
                    print("working on new element")
                addOne(item,classlist)                
        if len(strlist) == 1:
            if debug:
                print("list contains one resource")
            addOne(strlist[0],classlist)
    elif type(strlist) is dict:
        if debug:
            print("no list, just a single dict")
        addOne(strlist,classlist)
    if debug:
        print("here's how the classlist looks now: {cl}".format(cl = classlist))









