class PreferenceManager(object):
    def __init__(self,**spec):
        print spec
        self.behaviorsets = []
        self.selector = makeSelectionRule(**spec["selection_rule"])
        print self.selector
        for behaviorset in spec["behavior_sets"]:
            print behaviorset
            self.behaviorsets.append(BehaviorSet(behaviorset))            
        
    def getBidGroups(self,period):
        bidgroups = []
        
        cfnindex = self.selector.eval(period.periodNumber)
        print(self.behaviorsets)
        for behavior in self.behaviorsets[cfnindex].behaviors:
            bidgroups.append(behavior.devicenames)
        
        print bidgroups
        return bidgroups
    
    def getcfn(self,plan):
        cfnindex = self.selector.eval(plan.period.periodNumber)
        
        behaviorset = self.behaviorsets[cfnindex]
        
        for behavior in behaviorset.behaviors:
            if plan.devices[0].name in behavior.devicenames:
                return behavior.eval
        
    def eval(self,period,comps):
        #use the period number to determine which set of cost functions to use
        cfnindex = self.selector.eval(period.periodNumber)
    
        return self.behaviorsets[cfnindex].eval(period.periodNumber,comps)
    
    def printInfo(self,depth = 1):
        tab = "    "
        print(tab*depth + "-=PREFERENCE MANAGER=-")
        self.selector.printInfo(depth+1)
        for bset in self.behaviorsets:
            bset.printInfo(depth+1)
        print(tab*depth + "-=END PREFERENCE MANAGER=-")

class SelectionRule(object):
    def __init__(self,**spec):
        print spec
        
        
    def printInfo(self,depth=1):
        tab = "    "
        print(tab*depth + " SELECTION RULE: ")
        
    def eval(self,periodNumber):
        return 0
        
class BehaviorSet(object):
    def __init__(self,spec):
        self.behaviors = []
        for behavior in spec:
            self.behaviors.append(EnergyBehavior(**behavior))
    
    def getbehaviorbygroupname(self,groupname):
        for behavior in self.behaviors:
            if behavior.groupname == groupname:
                return behavior 
    
    def getbehavior(self,comps):
        for behavior in self.behaviors:
            if comps.keys()[0] in behavior.devicenames:
                return behavior
        
    def eval(self,period,comps):
        behavior = self.getbehavior(comps)
        return behavior.eval(period,comps)
                
    def printInfo(self,depth = 1):
        for behavior in self.behaviors:
            behavior.printInfo(depth)
            
        
class EnergyBehavior(object):
    def __init__(self, **spec):
        self.name = spec["name"]
        self.devicenames = spec["devicenames"]
        self.costfn = makeCostFn(**spec["costfn"])
                
    def printInfo(self,depth):
        tab = "    "
        print(tab*depth + "ENERGY BEHAVIOR: {name}".format(name = self.name))
        self.costfn.printInfo(depth + 1)
        
    #replaces existing costfn object with new costfn object
    def setcostfn(self, fn):
        self.costfn = fn
        
    def eval(self,period,comps):        
        if len(comps) == 1:
            return self.costfn.eval(comps.values()[0])
            
class QuadraticCostFn(object):
    def __init__(self,**params):
        self.a = params["a"]
        self.b = params["b"]
        self.c = params["c"]
        
        self.name = "quad"
    
    def eval(self,x):
    #@    print("x:{x}".format(x=x))
        return self.b + self.a*(x-self.c)**2
    
    def printInfo(self,depth):
        tab = "    "
        print(tab*depth + "COST FUNCTION = {b} + {a}*(x-{c})**2".format(a = self.a,b = self.b,c = self.c))
    
class QuadraticWCapCostFn(QuadraticCostFn):
    def __init__(self,**params):
        super(QuadraticWCapCostFn,self).__init__(**params)
        self.cap = params["cap"]
        
        self.name = "quadcap"
        
    def eval(self,x):
        retval = super(QuadraticWCapCostFn,self).eval(x)
        if retval > self.cap:
            return self.cap
        return retval
    
class QuadraticOneSideCostFn(QuadraticCostFn):
    def __init__(self,**params):
        super(QuadraticOneSideCostFn,self).__init__(**params)
        self.side = params["side"]
        
        self.name = "quadmono"
        
    def eval(self,x):
        if side == "left":
            if x < self.b:
                return super(QuadraticOneSideCostFn,self).eval(x)
            else:
                return self.c
        elif side == "right":
            if x > self.b:
                return super(QuadraticOneSideCostFn,self).eval(x)
            else:
                return self.c
            
class QuadraticOneSideWCapCostFn(QuadraticOneSideCostFn):
    def __init__(self,**params):
        super(QuadraticOneSideWCapCostFn,self).__init__(**params)
        self.cap = params["cap"]
        
        self.name = "quadmonocap"
        
    def eval(self,x):
        retval = super(QuadraticOneSideWCapCostFn,self).eval(x)
        if retval > self.cap:
            return self.cap
        return retval
    
class ConstantCostFn(object):
    def __init__(self,**params):
        self.c = params["c"]
        
        self.name = "const"
    
    def eval(self,x):
        return self.c
    
class PiecewiseConstant(object):
    def __init__(self,**params):
        self.values = params["values"]
        self.bounds = params["bounds"]
        self.bounds.sort()
        
        self.name = "piecewise"
        
    def eval(self,x):
        for index,bound in enumerate(self.bounds):
            if x <= bound:
                return self.values[index]
        return self.values[-1]
    
    def printInfo(self,depth):
        tab = "    "
        print(tab*depth + "PIECEWISE CONSTANT with {n} intervals".format(n = len(self.values)))
    
    
class Interpolated(object):
    def __init__(self,**params):
        self.states = params["states"]
        self.values = params["values"]
        
        self.name = "interpolate"
        
    def eval(self,z):
        rindex = None
        for index,state in enumerate(self.states):
            if z <= state:
                rindex = index
                break
        if not rindex:
            return self.values[-1]
        if rindex == 0:
            return self.values[0]
        
        x1 = self.states[rindex-1]
        y1 = self.values[rindex-1]
        x2 = self.states[rindex]
        y2 = self.values[rindex]
        
        out = (((y2-y1)/(x2-x1))*(z - x1)) + y1
        return out

       
class RepeatingSets(SelectionRule):
    def __init__(self,**spec):
        super(RepeatingSets,self).__init__(**spec)
        self.periods = spec["periods"]
        #self.behaviors = spec["behaviors"]
        
        #determine number of periods in pattern
        self.patternlength = 0
        for set in self.periods:
            self.patternlength += len(set)
            
        
    def eval(self,periodNumber):
        periodNumber = periodNumber % self.patternlength
        #determine the index of the rule to be used
        for i,set in enumerate(self.periods):
            if periodNumber in set:
                return i
        
    def printInfo(self,depth = 1):
        super(RepeatingSets,self).printInfo(depth)
        tab = "    "
        print(tab*depth + "PARAMETERS:")
        print(tab*(depth+1) + "PERIOD SETS: {per}".format(per = self.periods))        

class Fixed(SelectionRule):
    def __init__(self,**spec):
        super(Fixed,self).__init__(**spec)
    
    def printInfo(self,depth = 1):
        super(Fixed,self).printInfo(depth)
        tab = "    "
        
        print(tab*depth + "FIXED")
        
    def eval(self,periodNumber):
        return 0
          
def makeSelectionRule(**spec):
    print "inside makeselectionrule"
    print spec
    
    type = spec["type"]
    params = spec["params"]
    if type == "repeating_sets":
        newrule = RepeatingSets(**params)
    elif type == "fixed":
        newrule = Fixed(**params)
    else:
        print("HOMEOWNER {me} encountered unknown costfn selection rule: {type}".format(me = self.name, type = type))  
    return newrule
    
#def makeCostFn(appobj,appdict):
def makeCostFn(**cfndict):
    #fn = appdict["costfn"]
    #paramdict = appdict["cfnparams"]
    #type = appdict["type"]
    print "inside makecostfn"
    print cfndict
    
    fn = cfndict["type"]
    paramdict = cfndict["params"]
    
    if fn == "quad":
        newfn = QuadraticCostFn(**paramdict)
    elif fn == "quadcap":
        newfn = QuadraticWCapCostFn(**paramdict)
    elif fn == "quadmono":
        newfn = QuadraticOneSideCostFn(**paramdict)
    elif fn == "quadmonocap":
        newfn = QuadraticOneSideWCapCostFn(**paramdict)
    elif fn == "const":
        newfn = ConstantCostFn(**paramdict)
    elif fn == "piecewise":
        newfn = PiecewiseConstant(**paramdict)
    elif fn == "interpolate":
        newfn = Interpolated(**paramdict)
    else:
        print("HOMEOWNER {me} encountered unknown cost function".format(me = self.name))
        
    return newfn

    #behavior = human.EnergyBehavior(type,appobj,newfn)
    #appobj.associatedbehavior =  behavior
