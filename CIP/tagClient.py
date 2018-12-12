import socket
import sys
import subprocess
from datetime import datetime
    

'''writes multiple tags to a tag server. tag names and values must be provided as lists
even if only a single tag value pair is being written'''
def writeTags(names,values,plc):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tserver_addr = ('localhost',12897)
   # print("tag client attempting to connect and write to {host}:{port}".format(host = tserver_addr[0], port = tserver_addr[1]))
    
    try:
        sock.connect(tserver_addr)
    #    print("sock.connect is running")
        message = "write {plc}".format(plc = plc)
        for index,name in enumerate(names):
            message = message + " {name}:{value}".format(name = name, value = str(values[index]))
                
        message = message + "\n"
    #    print("already install message")
    #    print(message)
        sock.sendall(message)
        
        data = sock.recv(1024)
        #print("\nWRITE @ {dt} \nMESSAGE: {mes}  REC: {dat}".format(dt = datetime.isoformat(datetime.now()), mes = message, dat = data))
        
        #flog = open("~/volttron/taglog","a+")
    #    print("WRITE @ {dt} \nMESSAGE: {mes}REC: {dat}".format(dt = datetime.isoformat(datetime.now()), mes = message, dat = data))
    except Exception as e:
        print("tag client experiencing problem")
        print (e)
    finally:
        #print("closing tag client socket")
        sock.close()

    
'''reads multiple tags from a tag server. tag names must be provided as a list even
if there is only a single tag being read'''
def readTags(names, plc):
    outdict = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tserver_addr = ('localhost',12897)
  #  print("tag client attempting to connect to  and read from {host}:{port}".format(host = tserver_addr[0], port = tserver_addr[1]))
    
    try:
   #     print("start try")
   #     print(names)
        sock.connect(tserver_addr)
   #     print("sock.connect is running")
        message = "read {plc}".format(plc = plc)
        for index,name in enumerate(names):
            message = message + " " + name
        message = message + "\n"
   #     print("already install message")
        sock.sendall(message)
   #     print("already send message")
        data = sock.recv(1024)
   #     print("data receive")
        #print("\nREAD @ {dt} \nMESSAGE: {mes}  REC: {dat}".format(dt = datetime.isoformat(datetime.now()), mes = message, dat = data))
            
    except Exception as e:
        print("tag client experiencing problem")
        print(e)
    finally:
        #print("closing tag client socket")
        sock.close()
        if "flog" in globals():
            flog.close()
    
    #print("tag client received: {info}".format(info = data))
    pairs = data.split(",")
    for pair in pairs:
        name,value = pair.split(":")
        #print(" name = {n}\n value = {v}".format(n = name, v = value))
        try:
            value = float(value)
            #print("float: {v}".format(v = value))
        except Exception:
            #string isn't a number so it should be a boolean
            #make string lowercase
            value.lower()
            #print("val to lower: {v}".format(v = value))
            if value.find("true") >= 0:
                #print("val is true")
                value = True
            elif value.find("false") >= 0:
                #print("val is false")
                value = False
            else:
                print("can't process properly")
                                    
            
        outdict[name] = value
        
    #return an atom if we can
    if len(outdict) == 1:
        return outdict[names[0]]
    else:
        return outdict