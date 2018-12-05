from volttron.platform.vip.agent import core
import random

class Fault(object):
    def __init__(self,state = "suspected"):
        self.state = state
        self.owners = []
        self.uid = random.getrandbits(32)
        
    def remALLEXCEPT(self,keep):
        if keep in self.owners:
            for owner in self.owners:
                if owner is not keep:
                    self.owners.remove(owner)
        else:
            pass 
    
#fault has been cleared, restore nodes and unlink fault object
    def cleared(self):
        for owner in self.owners:
            if owner in self.isolatednodes or owner in self.faultednodes:
                if owner.__class__.__name__ == "Node":
                    self.restorenode(owner)
            if self in owner.faults:
                owner.faults.remove(self)
                
            self.owners.remove(owner)
        
class GroundFault(object):
    def __init__(self,state,zone):
        super(GroundFault,self).__init__(state)
        self.reclose = True
        self.isolatednodes = []
        self.faultednodes = []
        self.reclosecounter = 0
        self.reclosemax = 2
        
        
    def isolatenode(self,node):
        if node in self.owners:
            node.isolatenode()
            self.isolatednodes.append(node)
    
    def restorenode(self,node):
        if node in self.owners:
            node.restore()
            self.isolatednodes.remove(node)
            
            self.faultednodes.remove(node)
            
    def reclosenode(self,node):
        self.reclosecounter += 1
        self.restorenode(node)
        if self.reclosecounter == self.reclosemax:
            self.reclose = False
    
    #initiate procedure to clear a persistent fault    
    def clearfault(self):
        self.reclosecounter = 0
        #send message to SG PLC
        
    def printInfo(self,depth = 0):
        tab = "    "
        print(tab*depth + "FAULT: {state}".format(state = self.state))
        print(tab*depth + "-RECLOSES LEFT: {amt} of {max}".format(amt = self.reclosemax-self.reclosecounter, max = self.reclosemax))
        print(tab*depth + "-AFFECTED NODES and ZONES")
        for owner in self.owners:
            print(tab*depth + "--" + owner.name)
            if owner in self.isolatednodes:
                print("... CURRENTLY ISOLATED ...")
            if owner in self.faultednodes:
                print("!!! FAULT LOCATED HERE!!!")     