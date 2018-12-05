def lininterp(points,evalat):
    if evalat <= points[0][1]:
        return points[0][0]
    elif evalat >= points[-1][1]:
        return points[-1][0]

    for index, point in enumerate(points):
        if evalat < points[index + 1][1]:
            if abs(points[index + 1][1]-point[1]) < .00001:
                ans = point[0]
            else:
                ans = point[0] + ((points[index + 1][0]-point[0])*(evalat-point[1])/(points[index + 1][1]-point[1]))
            return ans
