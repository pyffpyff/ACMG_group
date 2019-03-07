def findDisjointSubgraphs(matrix):
    dim = len(matrix)
    print("connectivity matrix dimension: {dim}".format(dim = dim))
    groups = []
    group = []
    expandlist = []
    unexamined = range(0,dim)
    while len(unexamined) > 0:
        group = []
        expandlist.append(unexamined[0])
        while len(expandlist) > 0:
            row = expandlist[0]
            for i in range(dim):
                if matrix[row][i] == 1 or matrix[row][i] == 0:#and row != i:
                    if i not in expandlist and i in unexamined:
                        expandlist.append(i)
            #print("to be expanded: {ex}".format(ex = expandlist))
            #print("unexamined: {un}".format(un = unexamined))
            unexamined.remove(row)
            expandlist.remove(row)
            group.append(row)
        groups.append(group)
            
    return groups
    
