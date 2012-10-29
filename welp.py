#!/usr/bin/python -tt
'''
-------------------------------------------------------------------------------
Name:        WELP - Web Error Log Processor
Purpose:     Scan error and access logs for known traces of scanners and then grab stats
Author:      Micah Hoffman (@WebBreacher)
-------------------------------------------------------------------------------
 TODO (Overall)

 8 - Look for other anomalies such as known bad (RAT) strings (/w00t-w00t...)
 9 - Do analysis on the IPs found - lookup? Country?
 10- Look at the HTTP response code for success or failure and only report 2xx
 11- Look at the HTTP response code and count # of each code each IP got (34 500s....)
 12- Mod the IP field to also allow for domain names
 13- Do reverse lookup of the domain names to get IPs and stats

'''

import os, sys, re, itertools, operator, signal
from datetime import datetime
from xml.dom import minidom
from welpcore import *


#=================================================
# Constants and Variables
#=================================================

# Pulled from ModSecurity modsecurity_35_scanners.data
USER_AGENT_STRINGS = [".nasl","absinthe","acunetix", "arachni","bilbo","black widow","blackwidow","brutus","bsqlbf","burp","cgichk","dirbuster","grabber","grendel-scan","havij","hydra","jaascois","metis","mozilla/4.0 (compatible)","mozilla/4.0 (compatible; msie 6.0; win32)","mozilla/5.0 sf//","n-stealth","nessus","netsparker","nikto","nmap nse","nsauditor","pangolin","paros","pmafind","python-httplib2","sql power injector","sqlmap","sqlninja","w3af","webinspect","webtrends security analyzer"]

HTTP_METHOD_LIST = ["head", "options", "track", "trace"]


attacker = [] #ip,ua,date_earliest,date_recent,date_all,cats,attacks,lines
php_ids_rules = {}
log = {}

#=================================================
# Functions & Classes
#=================================================
def signal_handler(signal, frame):
        print bcolors.RED + '\nYou pressed Ctrl+C! Exiting.' + bcolors.ENDC
        sys.exit()

def rematch(line):      # Determine log type and set name/regex
    # Apache 2.x Error Log
    match = re.match("^\[[A-Z][a-z]{2} ", line)
    if match:
        log['type']="Apache2 Error"
        # TODO - Make this strip off/ignore the referrer if it is there - regex not working
        # REGEX - 1=Date/Time of the activity, 2=IP, 3=URL Requested
        log['regex']="^\[([SMTWF].*)\] \[error\] \[client (\d.*)\] (.*), referer.*"
        return log

    # Apache 2.x Access Log
    match = re.match("^\d{1,3}\.\d{1,3}\.", line)
    if match:
        log['type']="Apache2 Access"
        # REGEX - 1=IP, 2=Date/Time of the activity, 3=HTTP Method, 4=URL Requested, 5=User Agent
        log['regex']='^(\d+\.\d+\.\d+\.\d+) .*\[(\d+.*) \-\d+\] "([A-Z]{1,11}) (\/.*) HTTP.*" \d{3} \d+ ".*" "([A-Za-z].+)"'
        return

    # If we have not returned already, there is no match. Exit
    print bcolors.RED + "\n[Error] " + bcolors.ENDC + "No idea what kinda log you just submitted. Right now we only work on Apache 2.x access and error logs."
    sys.exit()

def seen_ip_before(event): 
    # 0=remote_ip,1=user_agent,2=event_date,3=search_cat,4=search_string,5=line,6=line_counter]
    for actor in attacker:
        if event[0] in actor['ip']:
            print bcolors.YELLOW + "[Found] New activity for %s; Line# %d." % (event[0],event[6])
            if event[1].find('Nikto'): event[1] = 'Nikto' #TODO just grab using regex the Nikto and version
            actor['ua'].add(event[1])
            tt = datetime.strptime(event[2], "%d/%b/%Y:%H:%M:%S")
            actor['date_all'].add(tt)
            actor['cats'].add(event[3])
            if actor['date_earliest'] > tt : actor['date_earliest'] = tt
            if actor['date_recent'] < tt : actor['date_recent'] = tt
            actor['cats'].add(event[4])
            actor['line_num'].add(event[6])
            return

    # Add new if we haven't had a match
    print bcolors.PURPLE + "[Found] Making new record for %s." % event[0]
    if event[1].find('Nikto'): event[1] = 'Nikto' #TODO just grab using regex the Nikto and version
    attacker.append({'ip': event[0],\
                     'ua': set([event[1]]),\
                     'date_earliest':datetime.strptime(event[2], "%d/%b/%Y:%H:%M:%S"),\
                     'date_recent':datetime.strptime(event[2], "%d/%b/%Y:%H:%M:%S"),\
                     'date_all':set([datetime.strptime(event[2], "%d/%b/%Y:%H:%M:%S")]),\
                     'cats':set([event[3]]),\
                     'attacks':set([event[4]]),\
                     'line_num':set([event[6]])\
                     })
    

def findIt(line, line_counter, search_cat, search_strings):

    line_regex_split = re.search(log['regex'], line)

    # Some lines in the log we don't care about (notice, info...). So if we have no regex match discard those lines
    if line_regex_split == None: return

    # Break down the log_file line into components
    if log['type'] == "Apache2 Error":

        # Assign easy to understand variables
        remote_ip     = line_regex_split.group(2)
        event_date    = line_regex_split.group(1)
        error_thrown  = line_regex_split.group(3)
        user_agent    = 'Error Log. No U/A'

        # Set the spot in the log entry that we want to examine
        line = error_thrown

    elif log['type'] == "Apache2 Access":

        # Assign easy to understand variables
        remote_ip     = line_regex_split.group(1)
        event_date    = line_regex_split.group(2)
        http_method   = line_regex_split.group(3)
        url_requested = line_regex_split.group(4)
        user_agent    = line_regex_split.group(5)

        # Set the spot in the log entry that we want to examine
        if search_cat == 'HTTP Method':
            line = http_method          # Regex for HTTP Method is the first group
        elif search_cat == 'User Agent':
            line = user_agent           # Regex for the User Agent is second group

        # Look for search_strings
        for search_string in search_strings:
            if re.search(search_string, line, re.I):
                # Add content to the attacker
                event = [remote_ip,user_agent,event_date,search_cat,search_string,line,line_counter]
                seen_ip_before(event)

    # Look for PHP-IDS matches
    for id in php_ids_rules.keys():
        try:
            regex = re.compile(php_ids_rules[id])
        except:
            print bcolors.RED + "[Error] " + bcolors.ENDC + "Compiling PHP-IDS rule %s failed. Skipping it." % id
            continue

        if regex.search(line):
            # Add content to the attacker list of dictionaries
            event = [remote_ip,user_agent,event_date,'PHP-IDS Rule',id,line,line_counter]
            seen_ip_before(event)

def main():

    line_counter = 1          # Counts the lines in the file

    print bcolors.GREEN + "\n[Start] " + bcolors.CYAN + "Starting the WELP script. Hang on."

    # Check how many command line args were passed and provide HELP msg if not right
    if len(sys.argv) == 2:
        user_log_file=sys.argv[1]
    else:
        print bcolors.RED + "\n[Error] " + bcolors.ENDC + "You need to enter in the full logfile path and name such as: %s [logfilename]\n" % sys.argv[0]
        sys.exit()


    # TODO - Read in args for -t or --type and add those lists to the tests{}
    # For now, make a dictionary and lets do all tests
    tests = { 'User Agent': USER_AGENT_STRINGS, 'HTTP Method': HTTP_METHOD_LIST }

    # Open the log_file (or try to)
    try:
        log_file = open(user_log_file,'r').readlines()

    except (IOError) :
        print bcolors.RED + "\n[Error] " + bcolors.ENDC + "Can't read file the logfile you entered."
        sys.exit()

    # Open the PHP-IDS filter file - grab most recent from https://phpids.org/
    try:
        xmldoc = minidom.parse("default_filter.xml")
    except (IOError) :
        print bcolors.RED + "\n[Error] " + bcolors.ENDC + "Can't read file the PHP-IDS default_filter.xml. Please get the latest file from https://phpids.org/ and place the XML file in the same directory as this script.\n"
        sys.exit()

    # Cycle through all the PHP-IDS regexs and make a dictionary
    print bcolors.BLUE + "[info] " + bcolors.ENDC + "Opened the PHP-IDS filter file and parsing the rules. "
    for filt in xmldoc.getElementsByTagName('filter'):
        id_xml = filt.getElementsByTagName('id')[0].toxml()
        id_content = id_xml.replace('<id>','').replace('</id>','')
        rule_xml = filt.getElementsByTagName('rule')[0].toxml()
        rule_content = rule_xml.replace('<rule>','').replace('</rule>','')
        rule_content = rule_content.replace("<![CDATA[", "")
        rule_content = rule_content.replace("]]>", "")
        #TODO - Grab the rule name or what it does and output that for the "string" in the ouput

        try:
            regex = re.compile(rule_content)
        except:
            print bcolors.RED + "[Error] " + bcolors.ENDC + "Compiling PHP-IDS rule %s failed. Skipping it." % id_content
            continue

        php_ids_rules[id_content] = rule_content

    # Using line 1 - see what kind of log this is
    if line_counter == 1:
        print bcolors.BLUE + "[info] " + bcolors.ENDC + "Examining the log format"
        rematch(log_file[0])
        print bcolors.GREEN + "[info] " + bcolors.ENDC + "Log format found to be %s" % log['type']

    # Actually start to look for stuff
    print bcolors.GREEN + "[info] " + bcolors.ENDC + "Analyzing the file:", user_log_file
    print bcolors.GREEN + "[info] " + bcolors.ENDC+ "There are %d lines in this log file." % len(log_file) #TODO check this

    # Pull each line of the file then perform all analysis
    for line in log_file:
        #sys.stdout.write(bcolors.BLUE + "\r[info] " + bcolors.YELLOW + "Processing line # %d" %line_counter)
        #sys.stdout.flush()

        signal.signal(signal.SIGINT, signal_handler)    # Trap Ctrl-C
        
        # If the log traffic is from 127.0.0.1|localhost, ignore it
        if re.search('^((127.0.0.1)|localhost)', line): continue

        # Cycle through each of the tests the user specified
        for key in tests:
            findIt(line, line_counter, key, tests[key])

        line_counter += 1

    # Show the Results
    if len(attacker) == 0:
        print bcolors.GREEN + "[info] " + bcolors.ENDC + "No security events found."

    elif len(attacker) > 0:
        print bcolors.RED + "[FOUND] " + "Found the following IPs (and associated activity)" + bcolors.ENDC

        #attacker.sort(key=operator.itemgetter('string'))
        for event in attacker:
            print bcolors.YELLOW +  "%s" % event['ip']
            print bcolors.ENDC +    "\tUser-Agents: %s" % ",\n\t\t".join(event['ua'])
            print                   "\tEarliest Date Seen:   %s" % event['date_earliest']
            print                   "\tEarliest Recent Seen: %s" % event['date_recent']
            #print                   "\tAll Dates Seen:       %s" % ", ".join(event['date_all'])
            print                   "\tAll Categories Seen:  %s" % ", ".join(event['cats'])
            print                   "\tAll Attacks Seen:     %s" % ", ".join(event['attacks'])


#=================================================
# START
#=================================================

if __name__ == "__main__": main()

print bcolors.GREEN + "[Finished] " + bcolors.CYAN + "WELP script completed with %d events identified. Thanks for using it.\n" % len(attacker)