def isRecipient(list,name,debug = False):
    if type(list) is list:
        for item in list:
            if item == name:
                if debug == True:
                    print("DEBUG: found a match for name: {n} in list: {l}".format(n = name, l = list))
                return True
        if debug == True:
            print("DEBUG: no matches found for name: {n} in list {l}".format(n = name, l = list))
        return False
    elif type(list) is str or type(list) is unicode:
        if list == "broadcast" or list == "all":
            if debug == True:
                print("DEBUG: broadcast message")
            return True
        if list == name:
            if debug == True:
                print("DEBUG: string is name")
            return True
        else:
            if debug == True:
                print("DEBUG: string: {s} is not name: {n}".format(s = list, n = name))
            return False
    else:
        if debug == True:
            print("DEBUG: neither a list nor a string but a {t}".format(t = type(list)))
            return False
        
'''helper function to get the name of a resource or customer from a list of
class objects'''                        
def lookUpByName(name,list):
    for entity in list:
        if entity.name == name:
            return entity
    return None