import copy
#makes a list of all lists of indices for an outer product in a space with given dimensions
def makeindexop(dimensions):
    indlist = []
    indices = [0]*len(dimensions)
    position = 1
    while position <= len(dimensions):
        indlist.append(indices[:])
        #print(indices)
        indices[-1] += 1
        position = 1
        while indices[-position] >= dimensions[-position]:
            indices[-position] = 0
            position += 1
            if position > len(dimensions):
                return indlist
            indices[-position] += 1
    return indlist

def makeopfromindices(indices,lists):
    length = len(lists)
    ilist = range(length)
    product = []
    for ind in indices:
        comp = [0] * length
        for i in ilist:
            comp[i] = lists[i][ind[i]]
        product.append(comp[:])
    return product

            
#gets an element of the outer product of the given lists at the specified index
def getfromindices(ind,lists):
    length = len(lists)
    output = [0]*length
    for i in range(length):
        output[i] = lists[i][ind[i]]
    return output

#makes a list of all lists of outer product members for the given list of lists
def makeop(lists):
    outlist = []
    length = len(lists)
    indices = [0]*length
    position = 1
    dimensions = []
    for item in lists:
        dimensions.append(len(item))

    while position <= length:
        outmember = [0]*length
        for i in range(length):
            outmember[i] = lists[i][indices[i]]
        outlist.append(outmember[:])
        #print(indices)
        indices[-1] += 1
        position = 1
        while indices[-position] >= dimensions[-position]:
            indices[-position] = 0
            position += 1
            if position > length:
                return outlist
            indices[-position] += 1
    return outlist

def makeopdict(listdict):
    outlist = []
    length = len(listdict)
    indices = [0]*length
    position = 1
    dimensions = []
    keys = []

    for key in listdict:
        dimensions.append(len(listdict[key]))
        keys.append(key)

    #print(keys)
    #print(dimensions)
    while position <= length:
        outmember = {}
        for i in range(length):
            outmember[keys[i]] = listdict[keys[i]][indices[i]]
        outlist.append(outmember.copy())

        indices[-1] += 1
        position = 1
        while indices[-position] >= dimensions[-position]:
            indices[-position] = 0
            position += 1
            if position > length:
                return outlist
            indices[-position] += 1
    return outlist
