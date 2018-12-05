'''this module contains functions for retrieving tag values from
PLC data tables. Rockwell's Unified CIP stack is written in Java and
compiled to an .elf using GCJ. These are wrappers that invoke a 
system call to run the .elf in a subprocess'''

import subprocess

def getTagValue(name):
    retval = subprocess.check_output(["readTagMock", name])
    #check output here
    index = retval.find(name)
    if index >= 0:
        start = retval.find(':',index +1)
        end = retval.find('\n',index +1)
        excerpt = retval[start + 1 : end]
        cleanExcerpt = excerpt.replace(" ","")
        if cleanExcerpt == 'true':
            cleanExcerpt = True
        elif cleanExcerpt == 'false':
            cleanExcerpt = False
        else:
            try:
                cleanExcerpt = float(cleanExcerpt)
            except ValueError:
                print("got a bad value back from wrapper function")
    else:
        cleanExcerpt = None
    return cleanExcerpt

def getTagValues(names):
    command = names[:]
    retdict = {}
    command.insert(0, 'readTagsMock')
    retval = subprocess.check_output(command)
    #process output here
    for name in names:
        index = retval.find(name)
        if index >= 0:
            start = retval.find(':',index +1)
            end = retval.find('\n',index +1)
            excerpt = retval[start + 1:end]
            cleanExcerpt = excerpt.replace(" ","")
            #parse tag value and convert to appropriate type
            if cleanExcerpt == 'true':
                cleanExcerpt = True
            elif cleanExcerpt == 'false':
                cleanExcerpt = False
            else:
                try:
                    cleanExcerpt = float(cleanExcerpt)
                except ValueError:
                    print("got a bad value back from wrapper function")
            retdict[name] = cleanExcerpt
        else:
            retdict[name] = None
    return retdict

def setTagValue(name,value):
    retval = subprocess.check_output(['writeTagMock', name, value])

def setTagValues(names,values):
    command = ['writeTagsMock'] + names + ["delim"] + values
    retval = subprocess.check_output(command)

def connectServer():
    pass