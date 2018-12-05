
from volttron.platform.vip.agent import RPC
from ACMGAgent.Resources.demand import human

class DeviceComplex(object):
    def __init__(self,**devs):
        self.name = dev["name"]

class NestedTemps(DeviceComplex):
    def __init__(self,**devs):
        super(NestedTemps,self).__init__(**devs)
        self.inner =  devs["inner"]
        self.outer = devs["outer"]
        
        
    def getState(self):
        return [self.inner.getState, self.outer.getState]
    
    
    
    
class Device(object):
    def __init__(self, **dev):
        self.name = dev["name"]
        self.owner = dev["owner"]
        self.nominalpower = dev["nominalpower"]
        
        self.isintermittent = False
        self.issource = False
        self.issink = True
        
        self.associatedbehavior = None
        
        self.snapstate = []
        self.gridpoints = []
        self.actionpoints = []
        self.tempgridpoints = []
        
    def stateEngToPU(self,eng):
        return eng/self.statebase
    
    def statePUToEng(self,pu):
        return pu*self.statebase
        
    def addCurrentStateToGrid(self):
        #obtain current state
        currentstate = self.getState()
        #print("DEVICE {me} adding state {cur} to grid".format(me = self.name, cur = currentstate))
        #if the device has a state
        if currentstate:
            #and it isn't already in the list of grids
            #print("has a state")
            if currentstate not in self.gridpoints and currentstate not in self.snapstate:
                #record the added state
                self.snapstate.append(currentstate)
                if currentstate not in self.tempgridpoints:
                    self.tempgridpoints.append(currentstate)
                #print("not already in state. added : {pts}".format(pts = self.gridpoints))
        return currentstate
                
    def revertStateGrid(self):
        for point in self.tempgridpoints:
            if point in self.snapstate:
                #print("removing point {pt} from grid".format(pt = point))
                self.snapstate.remove(point)
            self.tempgridpoints.remove(point)
        
    def getState(self):
        return None
    
    def getStateEng(self):
        return None
    
    def printInfo(self,depth):
        tab = "    "
        print(tab*depth + "DEVICE NAME: {name}".format(name = self.name))
        
    def costFn(self,period,devstate):
        devstate = self.statePUToEng(devstate)
        cost = self.associatedbehavior.costFn(period,devstate)
        #print("device: {name}, state: {sta}, cost: {cos}".format(name = self.name, sta = devstate, cos = cost))
        return cost
    
    
class HeatingElement(Device):
    def __init__(self,**dev):
        super(HeatingElement,self).__init__(**dev)
        self.shc = dev["specificheatcapacity"]
        self.mass = dev["mass"]        
        self.thermR = dev["thermalresistance"]
        
        self.tamb = 25
        self.statebase = 50.0
        
        self.gridpoints = [0.5, 0.6, 0.7, 0.8, 0.9]
        self.actionpoints = [0,1]
        
        self.on = False
        self.temperature = dev["inittemp"]
        
    def getState(self):
        return self.stateEngToPU(self.temperature)
    
    def getStateEng(self):
        return self.temperature
    
    def getPowerFromPU(self,pu):
        return pu*self.nominalpower
    
    def getPUFromPower(self,power):
        return power/self.nominalpower
        
    def getActionpoints(self,mode = "hifi"):
        return self.actionpoints
        
    def getGridpoints(self,mode = "hifi"):
        if mode == "hifi":
            grid = self.gridpoints[:]
            grid.extend(self.snapstate)
            return grid
        elif mode == "lofi":
            grid = [ 0.5, 0.75, 0.9]
            grid.extend(self.snapstate)
            return grid
        elif mode == "superhi":
            grid = [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9]
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
            newstate = self.applySimulatedInput(initstate,0,30)
            if newstate <= 1 and newstate >= 0:
                dynamicgrid.append(newstate)
            newstate = self.applySimulatedInput(newstate,0,60)
            if newstate <= 1 and newstate >= 0:
                dynamicgrid.append(newstate)
        
            print("generated gridpoints dynamically for {dev}: {grd}".format(dev = self.name, grd = dynamicgrid))
            return dynamicgrid
        
    #Euler's method, use only for relatively short time periods
    def applySimulatedInput(self,state,input,duration,pin = "default"):
        state = self.statePUToEng(state)
        if pin == "default":
            pin = self.nominalpower
            
        et = 0
        defstep = 5
        if input != 0:
            input = 1
        while et < duration:
            if (duration - et) >= defstep:
                step = defstep
            else:
                step = (duration -et)
            et += step
            state = (((pin*input)-((state - self.tamb)/self.thermR))/(self.mass*self.shc))*step + state
            #print("another step: after {et} seconds out of {dur} newstate is {ns}".format(et = et, dur = duration, ns = state))
            
        return self.stateEngToPU(state)
        
    def simulationStep(self,pin,duration):
        if self.on:
            if pin > 0.0005:
                input = 1
            else:
                input = 0
        else:
            input = 0
            
        state = self.stateEngToPU(self.temperature)
        state = self.applySimulatedInput(state,input,duration,pin)
        self.temperature = self.statePUToEng(state)
        self.printInfo()
        return self.temperature
    
    def inputCostFn(self,puaction,period,state,duration):
        power = self.getPowerFromPU(puaction)
        #print("name: {name}, cost: {cost}, power: {pow}, duration: {dur}".format(name = self.name, cost = period.expectedenergycost, pow = power, dur = duration))
        return power*duration*period.expectedenergycost    
        
    def printInfo(self,depth = 0):
        tab = "    "
        print(tab*depth + "^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^")
        print(tab*depth + "DEVICE SUMMARY FOR HEATER: {heat}".format(heat = self.name))
        print(tab*depth + "OWNER: {own}".format(own = self.owner))
        print(tab*depth + "STATE: {temp}".format(temp = self.temperature ))
        print(tab*depth + "INPUT: {inp}".format(inp = self.on))
        if self.associatedbehavior:
            print(tab*depth + "BEHAVIOR:")
            self.associatedbehavior.printInfo(depth + 1)
        print(tab*depth + "^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^")
    
class HeatPump(Device):
    def __init__(self,**dev):
        super(HeatPump,self).__init__(**dev)
        self.vol = dev["volume"]
        self.heatcap = self.vol*1.2*1007  #volume * approx. density * specific heat capacity
        self.thermR = dev["thermalresistance"]
        self.carnotrelativeefficiency = dev["relativeefficiency"]
        
        self.tamb = 25
        self.tbase = 40
        
        self.gridpoints = [0.375, 0.5, 0.625, 0.75, 0.875, 1]
        self.actionpoints = [0,1]
        
        self.on = False
        self.temperature = dev["inittemp"]
        
    def getState(self):
        return self.stateEngToPU(self.temperature)
    
    def getStateEng(self):
        return self.temperature
    
    def getPowerFromPU(self,pu):
        return pu*self.nominalpower
    
    def getPUFromPower(self,power):
        return power/self.nominalpower
        
    def getActionpoints(self,mode = "lofi"):
        return self.actionpoints
    
    def getGridpoints(self,mode = "hifi"):
        if mode == "hifi":
            grid = self.gridpoints[:]
            grid.extend(self.snapstate)
            return grid
        elif mode == "lofi":
            grid = [ 0.5, 0.75, 0.9]
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
            newstate = self.applySimulatedInput(initstate,0,30)
            if newstate <= 1 and newstate >= 0:
                dynamicgrid.append(newstate)
            newstate = self.applySimulatedInput(newstate,0,60)
            if newstate <= 1 and newstate >= 0:
                dynamicgrid.append(newstate)
        
            print("generated gridpoints dynamically for {dev}: {grd}".format(dev = self.name, grd = dynamicgrid))
            return dynamicgrid    
        
    #Euler's method, use only for relatively short time periods
    def applySimulatedInput(self,state,input,duration,pin = "default"):
        state = self.statePUToEng(state)
        if pin == "default":
            pin = self.nominalpower
        et = 0
        defstep = 5
        if input != 0:
            input = 1
        while et < duration:
            if (duration - et) >= defstep:
                step = defstep
            else:
                step = (duration - et)
            et += step
            #estimate working fluid temperatures
            tc = state - 6 + 273
            th = self.tamb + 4 + 273
            #print("tc: {cold}, th: {hot}, ratio: {rat}".format(cold = tc, hot = th, rat = (th/tc)))
            #efficiency = self.carnotrelativeefficiency*(1-((tc + 273)/(th + 273)))
            efficiency = self.carnotrelativeefficiency/((float(th)/float(tc))-1.0)
            peff = pin*efficiency
            state = ((-peff*input-((state - self.tamb)/self.thermR))/(self.heatcap))*step + state            
            #print("another step: after {et} seconds out of {dur} newstate is {ns}".format(et = et, dur = duration, ns = state))
        return self.stateEngToPU(state)
        
    def simulationStep(self,pin,duration):
        if self.on:
            if pin > 0.0005:
                input = 1
            else:
                input = 0
        else:
            input = 0
        
        state = self.stateEngToPU(self.temperature)
        state = self.applySimulatedInput(state,input,duration,pin)
        self.temperature = self.statePUToEng(state)
        self.printInfo(0)
        return self.temperature
    
    def inputCostFn(self,puaction,period,state,duration):
        power = self.getPowerFromPU(puaction)
        #print("name: {name}, cost: {cost}, power: {pow}, duration: {dur}".format(name = self.name, cost = period.expectedenergycost, pow = power, dur = duration))
        return power*duration*period.expectedenergycost    
        
    def printInfo(self,depth):
        tab = "    "
        print(tab*depth + "^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^")
        print(tab*depth + "DEVICE SUMMARY FOR HEATER: {heat}".format(heat = self.name))
        print(tab*depth + "OWNER: {own}".format(own = self.owner))
        print(tab*depth + "STATE: {temp}".format(temp = self.temperature ))
        print(tab*depth + "INPUT: {inp}".format(inp = self.on))
        if self.associatedbehavior:
            print(tab*depth + "BEHAVIOR:")
            self.associatedbehavior.printInfo(depth + 1)
        print(tab*depth + "^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^")
        
class Refrigerator(HeatPump):
    def __init__(self,**dev):
        super(Refrigerator,self).__init__(**dev)
        self.gridpoints = [0.0, 0.2, 0.4, 0.6, 0.8]
        self.statebase = 10.0
        
    def getActionpoints(self,mode = "hifi"):
        return self.actionpoints    
    
    def getGridpoints(self,mode = "hifi"):
        if mode == "hifi":
            grid = self.gridpoints[:]
            grid.extend(self.snapstate)
            return grid
        elif mode == "lofi":
            grid = [ 0.0, 0.4, 0.8]
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
            newstate = self.applySimulatedInput(initstate,0,30)
            if newstate <= 1 and newstate >= 0:
                dynamicgrid.append(newstate)
            newstate = self.applySimulatedInput(newstate,0,60)
            if newstate <= 1 and newstate >= 0:
                dynamicgrid.append(newstate)
        
            print("generated gridpoints dynamically for {dev}: {grd}".format(dev = self.name, grd = dynamicgrid))
            return dynamicgrid
        
            
    def printInfo(self,depth):
        tab = "    "
        print(tab*depth + "^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^")
        print(tab*depth + "DEVICE SUMMARY FOR REFRIGERATOR: {me}".format(me = self.name))
        print(tab*depth + "OWNER: {own}".format(own = self.owner))
        print(tab*depth + "STATE: {temp}".format(temp = self.temperature ))
        print(tab*depth + "INPUT: {inp}".format(inp = self.on))
        if self.associatedbehavior:
            print(tab*depth + "BEHAVIOR:")
            self.associatedbehavior.printInfo(depth + 1)
        print(tab*depth + "^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^")
        
        
class NoDynamics(Device):
    def __init__(self,**dev):
        super(NoDynamics,self).__init__(**dev)
        self.gridpoints = [0, 1]
        self.actionpoints = [0, 1]
        
        self.on = False
        self.statebase = 1
    
    def getState(self):
        if self.on:
            return 1
        else:
            return 0
        
    def getStateEng(self):
        return self.getState()
        
    def getActionpoints(self,mode = "hifi"):
        return self.actionpoints
    
    def getGridpoints(self,mode = "hifi"):
        return self.gridpoints
    
    #the state becomes whatever the input tells it to be
    def applySimulatedInput(self,state,input,duration,pin = "default"):
        return input
    
    def simulationStep(self,pin,duration):        
        #print("power in: {pow}".format(pow = pin))
        self.printInfo(0)
        return self.on
    
    def inputCostFn(self,puaction,period,state,duration):
        power = self.getPowerFromPU(puaction)
        #print("name: {name}, cost: {cost}, power: {pow}, duration: {dur}".format(name = self.name, cost = period.expectedenergycost, pow = power, dur = duration))
        return power*duration*period.expectedenergycost 
    
    def getPowerFromPU(self,pu):
        return pu*self.nominalpower
    
    def getPUFromPower(self,power):
        return power/self.nominalpower
    
        
class Light(NoDynamics):
    def __init__(self,**dev):
        super(Light,self).__init__(**dev)        
        
    def printInfo(self,depth = 0):
        tab = "    "
        print(tab*depth + "^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^")
        print(tab*depth + "DEVICE SUMMARY FOR LIGHT: {me}".format(me = self.name))
        print(tab*depth + "OWNER: {own}".format(own = self.owner))
        print(tab*depth + "ON: {state}".format(state = self.on))
        if self.associatedbehavior:
            print(tab*depth + "BEHAVIOR:")
            self.associatedbehavior.printInfo(depth + 1)
        print(tab*depth + "^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^")
 
def makeResource(strlist,classlist,debug = False):
    def addOne(item,classlist):
        if type(item) is dict:
            resType = item.get("type",None)
            if resType == "solar":
                res = SolarPanel(**item)
            elif resType == "lead_acid_battery":
                res = LeadAcidBattery(**item)
            elif resType == "generator":
                res = Generator(**item)
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
        
        
def makeAppliancesFromList(applist, debug = True):   
    #initialize output list
    outlist = []
    
    #if the input is a list, iterate over the list and make all appliances
    if type(applist) is list:
        #for each device dictionary, create a corresponding object and append to list
        for app in applist:
            outlist.append(makeAppliance(app,True))
    #if the input is a single dictionary, just make one Appliance
    elif type(applist) is dict:
        outlist = makeAppliance(applist,True)
    else:
        return None
    
    return outlist
            
            
def makeAppliance(appdict,debug = False):
    apptype = appdict.get("type",None)
    if apptype == "heater":
        newapp = HeatingElement(**appdict)
    elif apptype == "refrigerator":
        newapp = Refrigerator(**appdict)
    elif apptype == "light":
        newapp = Light(**appdict)
    elif apptype == "airconditioner":
        newapp == AirConditioner(**appdict)
    elif apptype == "fridgeroomcomplex":
        newapp = FridgeRoomComplex(**appdict)
    else:
        return None
    return newapp
        
    
