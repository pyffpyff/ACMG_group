from __future__ import absolute_import
from datetime import datetime, timedelta
import logging
import sysconfig
import json
import sys

from volttron.platform.vip.agent import Agent, Core, PubSub, compat
from volttron.platform.agent import utils
from volttron.platform.messaging import headers as headers_mod

from ACMGAgent.Resources.misc import listparse

from . import settings
from zmq.backend.cython.constants import RATE
utils.setup_logging()
_log = logging.getLogger(__name__)

class TransmissionAgent(Agent):
    
    def __init__(self,config_path, **kwargs):
        super(TransmissionAgent,self).__init__(**kwargs)
        self.config = utils.load_config(config_path)
        self._agent_id = self.config["agentid"]
        #read from config structure
        self.name = self.config["name"]
        
        self.FREG_SIGNAL = .1
        self.FREG_ENROLLEES = []
        
    @Core.receiver("onstart")
    def setup(self,sender,**kwargs):
        _log.info(self.config["message"])
        self._agent_id = self.config["agentid"]
        
        print("Transmission Agent {me} starting up".format(me = self.name))
        
        self.vip.pubsub.subscribe("pubsub","FREG",callback = self.enrollmentfeed)
    
    def enrollmentfeed(self, peer, sender, bus, topic, headers, message):
        mesdict = json.loads(message)
        messageTarget = mesdict.get("message_target",None)
        messageSubject = mesdict.get("message_subject",None)
        messageSender = mesdict.get("message_sender",None)
        if listparse.isRecipient(messageTarget,self.name,False):
            if messageSubject == "FREG_enrollment":
                messageType = mesdict.get("message_type",None)
                if messageType == "acceptance":
                    if messageSender not in self.FREG_ENROLLEES:
                        self.FREG_ENROLLEES.append(messageSender)
                        resdict = {"message_subject" : "FREG_enrollment",
                                   "message_target" : messageSender,
                                   "message_sender" : self.name,
                                   "message_type" : "enrollment_ACK"
                                   }
                        if settings.DEBUGGING_LEVEL >= 1:
                            print("TRANSMISSION AGENT {me} HAS ENROLLED A NEW ASSET {them}".format(me = self.name, them = messageSender))
                            print("NOW THERE ARE {n} FREG ASSETS".format(n = len(self.FREG_ENROLLEES)))
                        
                            
    
    @Core.periodic(settings.FREG_UPDATE_INTERVAL)
    def FREG_update(self):
        if self.FREG_ENROLLEES:
            timestamp = datetime.now()
            mesdict = {"message_subject" : "FREG_signal",
                       "message_target" : self.FREG_ENROLLEES,
                       "message_sender" : self.name,
                       "FREG_signal" : self.FREG_SIGNAL,
                       "timestamp" : timestamp.isoformat()
                       }
            
            if settings.DEBUGGING_LEVEL >= 1:
                print("TRANSMISSION AGENT {me}: FREG signal is {f}".format(f = self.FREG_SIGNAL))
            message = json.dumps(mesdict)
            self.vip.pubsub.publish("pubsub","FREG",{},message)
            
    @Core.periodic(settings.SOLICITATION_INTERVAL)
    def enrollmentSolicitation(self):
        mesdict = {"message_subject" : "FREG_enrollment",
                   "message_target" : "broadcast",
                   "message_sender" : self.name,
                   "message_type" : "solicitation"                   
                   }
        message = json.dumps(mesdict)
        self.vip.pubsub.publish("pubsub","FREG",{},message)
    
    '''modelling the transmission grid isn't something we want to do, so just use 
    a stochastic process to come up with the FREG signal, assuming it's set by 
    dynamics outside the scope of our model'''    
    def determineFREG(self):
        pass

def main(argv = sys.argv):
    '''main method called by the eggsecutable'''
    try:
        utils.vip_main(TransmissionAgent)
    except Exception as e:
        _log.exception("unhandled exception")
        
if __name__ == "__main__":
    sys.exit(main())        
