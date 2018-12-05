###GLOBAL SETTINGS FOR UTILITY AGENT
##TIMING STUFF
#debugging verbosity level for utility agent in seconds
DEBUGGING_LEVEL = 2

#currently unused
LT_PLAN_INTERVAL = 120

#short term planning interval in seconds
ST_PLAN_INTERVAL = 45
#interval between bid solicitation and auction
BID_SUBMISSION_INTERVAL = 30

#time between relay state evaluations in seconds
SWITCH_FAULT_INTERVAL = 50

#time between fault detection routine runs in seconds
FAULT_DETECTION_INTERVAL = 5

#time between DR enrollment solicitation messages in seconds
DR_SOLICITATION_INTERVAL = 30

#time between customer solicitation messages in seconds
CUSTOMER_SOLICITATION_INTERVAL = 20

#time between account credit/debit routine runs in seconds
ACCOUNTING_INTERVAL = 5

#currently unused
RESERVE_DISPATCH_INTERVAL = 5

#interval in seconds between resource current and voltage measurements
RESOURCE_MEASUREMENT_INTERVAL = 10

#interval between announcements of next planning period begin/end times in seconds
ANNOUNCE_PERIOD_INTERVAL = 10

#interval between bus voltage correction function runs in seconds
SECONDARY_VOLTAGE_INTERVAL = 5

#interval between infrastructure current measurements for database
INF_CURRENT_MEASUREMENT_INTERVAL = 5

##OTHER STUFF
#upper and lower limits for acceptable voltage band
VOLTAGE_BAND_LOWER = 23.6
VOLTAGE_BAND_UPPER = 24.0

#emergency voltage threshold
VOLTAGE_LOW_EMERGENCY_THRESHOLD = 22.6