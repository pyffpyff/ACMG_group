def findDisjointSubgraphs(matrix):
    dim = len(matrix)
    print("connectivity matrix dimension: {dim}".format(dim = dim))
    groups = []
    group = []
    expandlist = []
    unexamined = range(0,dim)
    sub = 0
    while len(unexamined) > 0:
        group = []
        expandlist.append(unexamined[0])
        while len(expandlist) > 0:
            row = expandlist[0]
            sub = 0
            for i in range(dim):
#                if matrix[row][i] == 0:
#                    sub += 0;
                if matrix[row][i] == 1 or matrix[row][i] == 0:# : and row != i
#                    sub += 1;
                    if i not in expandlist and i in unexamined:
                        expandlist.append(i)
                
            #print("to be expanded: {ex}".format(ex = expandlist))
            #print("unexamined: {un}".format(un = unexamined))
            unexamined.remove(row)
            expandlist.remove(row)
#            print("group: sub = {sub}".format(sub=sub))
#            if sub != 0:
            group.append(row)
#        print("groups: sub = {sub}".format(sub=sub))
#        if sub != 0:
        groups.append(group)
            
    return groups
    
