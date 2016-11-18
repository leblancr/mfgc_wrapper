#!/usr/bin/env python
#
# $Id: mfgc_wrapper.py 429771 2013-10-16 00:40:47Z leblancr $
# $Author: leblancr $
# $Date: 2013-10-15 17:40:47 -0700 (Tue, 15 Oct 2013) $
# Copyright here All Rights Reserved

"""mfgc_wrapper.py: General wrapper for testing all DUT's using mfgc software.

   - Copies configuration and setting files to all duts in use.
   - Starts mfgc executables running on remote duts over rsh connection
   - Start test running and watch test status by sending mfgc remote commands over TCP/IP socket connection
   - mfgc_wrapper.py -h or --help for usage

"""

__author__="bradleyd@broadcom.com (Brad Davis)"
__version__="$Revision: 429771 $"
__all__= []

# GLOBAL MODULES
import sys, subprocess, socket
import getpass
import ConfigParser
from time import strftime, sleep, time
from os import path, unlink, umask, makedirs
from tempfile import mkdtemp
from subprocess import Popen, PIPE
import optparse
import re
import os

# OUR MODULES
#MY_PATH = path.dirname( path.realpath( __file__ ) )
#sys.path.append( path.join( MY_PATH, "pkg" ) )
#import pdb; pdb.set_trace()

BASE_PATH = reduce (lambda l,r: l + path.sep + r, path.dirname( path.realpath( __file__ ) ).split( path.sep )[:-1] )
MY_PATH = reduce (lambda l,r: l + path.sep + r, path.dirname( path.realpath( __file__ ) ).split( path.sep ) )
WLAN_PATH = reduce (lambda l,r: l + path.sep + r, path.dirname( path.realpath( __file__ ) ).split( path.sep )[:-2] )
SCRIPTS_PATH = "/".join([WLAN_PATH, "dvtc_scripts"])
STATIONS_PATH = "/".join([WLAN_PATH, "DVT_stations"])
sys.path.append( path.join( BASE_PATH, "bate" ) )
REPDIR_PATH = reduce (lambda l,r: l + path.sep + r, path.dirname( path.realpath( __file__ ) ).split( path.sep )[:-2] )
REPDIR_PATH = path.join(REPDIR_PATH, "dvt_report")

print sys.path

pexpect = None

from brcm_dut_wlan import dut_mach, brcm_dut_wlan
#import pyPath # /projects/hnd_tools/python
import mlogger   # Splits STDOUT to logfile as well.
import board_cfg
class mfgc_host(dut_mach):
    ''' Extends the dut_mach class to have some MFGc specific functionality '''
    
    def __init__(self, name_or_addr, printfunc):
        ''' Inherits dut_mach (brcm_dut_wlan.py)
            I am a dut_mach object
 
        '''
        
        dut_mach.__init__(self, name_or_addr, printfunc) # Call parent's constructor

class mfgc_dut(mfgc_host):
    """Perform dut specific functions. Inherits mfgc_host which inherits dut_mach (brcm_dut_wlan.py)
 
    - connect to mfgc process
    - start the test running
    - monitor the test running and report status
    
    """
    
    def __init__(self, options, dut, name_or_addr, printfunc, nonwindows_mach_addr = ""):
        '''Init the mfgc_duts.'''
        
        mfgc_host.__init__(self, name_or_addr, printfunc) # Call parent's constructor

        self.boardtype = "" # It's dut board type
        self.dut = dut # It's own dut number
        self.test_mode = "" # What mode you're testing in, Windows, Linux ...etc.
        self.options = options # Same options as mfgc_wrapper
        self.start_time = 0 # The time the test started
        self.nonwindows_mach_addr = nonwindows_mach_addr
            
    def backup_settings(self, backup_dir):
        """Backup settings.txt from mfgc binaries directory to backup directory."""
        
        # If a settings.txt file exists in the mfgc binaries directory back it up
        cmd = "if exist " + "\"" + self.options.mfgc_dir[2:] + "settings.txt\" md " \
                            "\"" + self.options.mfgc_dir[2:-1] + backup_dir + "\" && move " \
                            "\"" + self.options.mfgc_dir[2:] + "settings.txt\" " \
                            "\"" + self.options.mfgc_dir[2:-1] + backup_dir + "\""
        self.cmd(cmd) 

    def connect_process(self):
        ''' Connect to mfcmfgc app port 7130 on each dut using TCP/IP sockets.
        
        mfgc remote commands will be sent over this connection.
        Like the rsh connections in dut_mach.connect() we don't close the connections.
        Connections stay open until the end of the script.
        
        '''

        HOST = self.addr # DUT ip address passed in when dut_mach object created
        PORT = int(self.options.mfgc_rc_port) # mfgc port for remote control
        MAXIMUM_NUMBER_OF_ATTEMPTS = 5 # Number of times to try if connect fails    
        wait_time = 10 # Number of seconds to wait before trying to connect again
        
        # Connect to dut using a TCP/IP socket
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # Make a socket object
        sleep(2) # Needed before connecting or could get connect error
        
        self.myprint("Connecting to " + HOST + ":" + str(PORT))

        for attempt in range(2, MAXIMUM_NUMBER_OF_ATTEMPTS + 1):
            try:
                self.s.connect((HOST, PORT)) # Connect to dut using socket
            except Exception, e: 
                errmsg = "Error connecting to %s:%d. Exception type is %s" % (HOST, PORT, `e`)
                self.myprint(errmsg)
                if "Connection refused" in e:
                    self.myprint("MFGc needs to be \"Ready\" before we can connect, attempt " + str(attempt) + "/" \
                                 + str(MAXIMUM_NUMBER_OF_ATTEMPTS) + " retry again in " + str(wait_time) + " seconds...")
                    sleep(wait_time) # wait then try to connect again
                else:
                    raise IOError(errmsg) # re-raise otherwise
            else: # we tried, and we had no failure, so
                break
        else: # we never broke out of the for loop
            raise RuntimeError("maximum number of unsuccessful connect attempts reached")
  
        data = self.s.recv(2048) # Receive the ">" prompt from the connect() command
        
    def start_test(self):
        ''' Send mfgc remote commands to start the test
        
        Set the user supplied comment.
        Set the chamber temperature.
        Start the test running.
          
        '''

        # Set user supplied comment
        command = 'SETSERIALNUMBER ' + self.options.comment + '\n\r'
        self.s.send(command)
        self.s.recv(2048)
        
        # Set chamber temperature
        if self.options.temp:
            command = 'SETTEMPERATURE ' + str(self.options.temp) + '\n\r'
            self.s.send(command)
            self.s.recv(2048)

        # Start the actual test script running
        command = 'STARTTEST\n\r'
        self.s.send(command)
        self.s.recv(2048)

    def get_status(self, timeout = 10):
        '''Send mfgc remote command GETSTATUS and return the results.'''

        #make socket non blocking
        self.s.setblocking(0)
        try:
            total_data=''
            data='';
             
            command = 'GETSTATUS\n\r'
            self.s.send(command)

            #beginning time for timeout
            begin_time = time()
           
            while 1:
                # if you got some data, then break after timeout
                if total_data and (time() - begin_time) > timeout:
                    self.myprint("got data then recv command timed out")
                    break
                 
                # if you got no data at all, wait a little longer, twice the timeout
                elif (time() - begin_time) > (timeout * 2):
                    self.myprint("recv command timed out waiting for data", 5)
                    break
                 
                # recv something
                try:
                    data = self.s.recv(2048)
                    if data:
                        total_data += data
                        if '\n\r> ' in total_data:
                            break 
                        # reset beginning time for measurement if previous recv was successful
                        begin_time = time()
                    else:
                        # sleep for sometime to indicate a gap
                        sleep(0.1)
                except:
                    pass
        finally:
            # set socket back to blocking
            self.s.setblocking(1)
    
        return total_data.strip()
        
    def get_version(self):
        '''Send mfgc remote command GETVERSION and display the results.'''

        command = 'GETVERSION\n\r' # GUI and dll versions
        self.s.send(command)
        data = self.s.recv(2048)[:-2].split("\n\r") # Get the GUI and dll versions
        for item in data:
            self.myprint(item, 1)
        data = self.s.recv(2048) # Get the last "\n\r>", throw it away

class mfgc_ref(mfgc_host):
    pass
    
class mfgc_ctrl(mfgc_host):
    pass
    
class mfgc_wrapper():
    '''Main class that does most of the MFGc test administration. Uses RSH to connect to windows machine.'''

    debug_level = 1
    prompts = None
    LOCALHOST = "localhost"
    STATION_INI_FILE = "station.ini"
                   
    # Things to look for in station.ini
    MFGC_STATION_FILE_VARIABLES = [
        "mfgc_dir",
        "mfgc_listener",
#        "windows_apps_dir",
        "linux_apps_dir",
        "cal_table_d0",
        "cal_table_d1",
        "cal_table_d2",
        "cal_table_d3",
        "cal_table_d4",
        "cal_table_d5",
        "cal_table_d6",
        "cal_table_d7",
        "settings_d0",
        "settings_d1",
        "settings_d2",
        "settings_d3",
        "settings_d4",
        "settings_d5",
        "settings_d6",
        "settings_d7",
    ]
 
    ADAPTER_OFFSETS = [
        "txadapteroffset_2g",
        "rxadapteroffset_2g",
        "txadapteroffset_5g",
        "rxadapteroffset_5g",
    ]
    
    def __init__(self, options, dut_sn):
        '''Init the test.
 
          - reading station config information
        '''

        version = __version__
        self.version = version.strip('$ ').split(":")[1].strip()  # Normalize version string
        self.options = options
        # debuglevel = 0 critical/errors only, 1 = messages, 2 = info, 3 = debug, 4... more debug
        self.debug_level = self.options.verbosity
        self.myprint("Debug level: %d" % self.debug_level, 3)
        self.startdatestr = strftime('%Y%m%d')
        # Save start time to ensure all timestamps use the exact same time           
        self.starttimestr = strftime("%s-%%H%%M%%S" % self.startdatestr )    
        
        # These dictionaries are populated in setup_connections()           
        self.dut_machs = {} # Windows duts and/or Windows machines that control non-windows duts
        self.dut_sn = dut_sn # dictionary of duts and their board serial numbers
        self.nonwindows_machs = {} # Duts that use non-windows OSes like Linux, FreeBSD, Mac...
        self.ref_machs={}
        self.ctrl_machs = {}

        self._read_station_ini_file() # Parse station.ini file in the dir for each station
        
        try:
            global pexpect
            self.myprint("sys.path: %s" % sys.path, 4)
            if "/projects/hnd_tools/python" in sys.path:
                self.myprint("/projects/hnd_tools/python is in sys.path", 4)
                if "/projects/hnd_tools/python/pexpect-2.4" not in sys.path:
                    self.myprint("/projects/hnd_tools/python/pexpect-2.4 is not in sys.path, inserting", 4)
                    sys.path.insert(0, "/projects/hnd_tools/python/pexpect-2.4")
                else:
                    self.myprint("/projects/hnd_tools/python/pexpect-2.4 is in sys.path", 4)
                    
            self.myprint("sys.path: %s" % sys.path, 4)

            pexpect = __import__('pexpect')
            self.myprint("Using pexpect version %s, %s" % (pexpect.__version__, pexpect.__revision__), 2)
        except ImportError:
            self.myprint("ERROR: Cannot find (import) pexpect python module which is required when not running directly on litepoint windows machine", 0)
            raise               
        
    def _do_os_command(self, cmd, ignore_ret_code=False, shell=False, ignoreoutput=False):
        '''Submit Operating System level (shell) command.'''
        
        self.myprint("   local cmd=%s" % cmd, 2)
        if ignoreoutput:
            p = Popen(cmd, shell=shell)
        else:
            p = Popen(cmd, stdout=PIPE, stderr=subprocess.STDOUT, shell=True)
        output, junk = p.communicate()   # STDERR redirected to STDOUT above
        p.poll()
        status = p.returncode
        self.myprint("   RETURN CODE->%d" % status, 2)
        self.myprint("   RESPONSE->%s" % output, 2)
        if status != 0 and not ignore_ret_code:
            raise IOError("Return code = %d on local command \"%s\"" %
                       (status, cmd)) 
        return (output, status)            
            
    def _makelogfilename(self, prefix, extension):
        '''Construct logfile name (no path) given a prefix and an extension.'''

        filename = '%s-%s-%s-%s.%s' % (prefix, self.options.station_name, self.options.comment, 
                                                self.starttimestr, extension )
        return filename
        
    def _parse_and_send_script(self, dut, user_src_script, tempdir, params, destination, sort_file=False):
        '''Parse a file replacing parameters with their values and then send the file 
          to the mfgc controller.
          Inputs:   usr_src_script - The original source usere script file
                    usr_dut_script - The original script file modified with dut specific info
                    tempdir - A local temporary directory that can be used as intermediate location
                    params - A dictinary of tuples of (search, replace) values.
                    destination - where on the remote machine the file will go
        '''

        # Modify and send the user script file to controller
        (head, tail) = path.split(user_src_script) # split path and filename
        temp = tail.rpartition(".") # Split string at last occurrence of sep, return a 3-tuple, the part before separator, separator, and part after separator
        user_dut_script = temp[0] + "d" + dut + temp[1] + temp[2] # insert "d" plus dut number between filename and dot

        tempfile = path.join(tempdir, user_dut_script) # temp directory path plus user script name with dut number

        # Modify the temp file with new settings then copy the new file to the destination
        self._prepare_file(user_src_script, tempfile, params, sort_file) # Reads file, modifies values, writes new file (same name) to tempdir
        cmd = "rcp '%s' '%s'" % (tempfile, destination)
        self.myprint(cmd)
        self._do_os_command(cmd) 

        # Remove temp file and directory
        unlink(tempfile) # Comment out these three lines if you want to look at the file
        cmd = "rmdir '%s'" % tempdir
        self._do_os_command(cmd) 
     
    def _parse_and_send_key_value_file(self, local_file_name, tempdir, params, destination, sort_file=False):
        '''Parse a file replacing parameters with their values and then send the file 
          to the mfgc controller.
          Inputs:   local_file_name - The original/source file
                    tempdir - A local temporary directory that can be used as intermediate location
                    params - A list of tuples of (search, replace) values.
                    destination - where on the remotre machine the settings file will go
        '''

        (head, tail) = path.split(local_file_name)
        tempfile = path.join(tempdir, tail)

        # Modify the temp file with new settings
        if not path.exists(local_file_name):
            raise IOError("File doesn't exist: %s" % local_file_name)
        self._prepare_key_value_file(local_file_name, tempfile, params, sort_file) # Reads file, modifies values, writes new file (same name) to tempdir

        # Actually copy the file to the destination
        self.myprint("Sending file   %s to %s" % (local_file_name, destination))
        cmd = "rcp '%s' '%s'" % (tempfile, destination)
        self._do_os_command(cmd) 
        
        # Remove temp file and directory
        unlink(tempfile) # Comment out these three lines if you want to look at the file
        cmd = "rmdir '%s'" % tempdir
        self._do_os_command(cmd) 

    def _prepare_file(self, src_file, dst_file, params, sort_file=False):
        '''Read src_file to a variable, modify it, then write it out to a new file.
        
        Read src_file file into dictionary as key, value pairs.
        dst_file is the tempfile being made in the tempdir
        Params is a dictionary of tuples defining a key search string and the value replacement text,
        such as:
           [ ( key1, text1 ), ( key2, text2 ) ... ]
        sorted flag is for sorting the output file, like the settings file
        '''
        
        self.myprint("Preparing file %s" % (dst_file))
        
        # Read file into a list split by newlines.
        fh = open(src_file, 'r')
        text = fh.read().split("\n") 
        fh.close()
        
        # Go thru each line looking for one of the search strings, then replace the value for that line.
        pattern = re.compile("^([a-zA-Z][a-zA-Z0-9_-]+)\s*=(.*)") # Look for key = value lines
        for i, line in enumerate(text):
            result = pattern.match(line) # Look for key = value lines, key=result.group(1), value=result.group(2) 
            if result:
                for key in params.keys():
                    if result.group(1).lower() == key: # Keys are in lower case
                        text[i] = " = ".join([result.group(1), params[key]]) + "\r" # Replace the item in the list with the new value

        fh = open(dst_file, 'w') # write everything out to new file
        
        if sort_file:
            fh.write('\n'.join(sorted(text))) 
        else:
            fh.write('\n'.join(text)) 
        fh.close()

    def _prepare_key_value_file(self, src_file, dst_file, params, sort_file=False):
        '''Read src_file to a variable, convert to dictionary, modify it, then write it out to a new file.
        
        Read settings file into dictionary as key, value pairs. Replace settings values with values from params.
        
        Params is a dictionary of tuples defining a key search string and the value replacement text,
        such as:
           [ ( key1, text1 ), ( key2, text2 ) ... ]
        '''
        
        self.myprint("Preparing file %s" % (src_file))
        
        fh = open(src_file, 'r')
        text = fh.read() 
        fh.close()
        
        # Read settings file into dictionary, split lines by "=" into key, value pairs.
        try:
            dict_file = dict([line.split("=", 1) for line in filter(None, text.split("\n"))]) # Filter out blank lines then split key/value pairs by "="
        except ValueError as e:
            print e
            sys.exit(1)
            
        for key in params.keys():
            dict_file[key] = params[key] # Replace the current string with the desired one from params

        outfile = [] # List of key, value pairs joined by "=" to write out to new settings file
        for key, value in dict_file.items():
            outfile.append("=".join([key, value]))
            
        fh = open(dst_file, 'w')
        if sort_file:
            fh.write('\n'.join(sorted(outfile))) 
        else:
            fh.write('\n'.join(outfile)) 
        fh.close()
            
    def _read_station_ini_file(self):
        ''' Loads from station config file <station>.ini.'''
        
        # Determine the name of the directory for this station
        station_dir = path.join(BASE_PATH, 'stations', self.options.station_name)

        if not path.exists(station_dir):
            errmsg = "Couldn't find station config directory for station '%s'" % (self.options.station_name)
            self.myprint(errmsg, 0)
            raise IOError(errmsg)
        self.myprint("Found local station directory '%s'" % station_dir, 3);

        self.options.station_dir = station_dir

        # Parse station.ini, located in the directory for each station 
        config = ConfigParser.SafeConfigParser()
        config.read(path.join(self.options.station_dir, self.STATION_INI_FILE))

        # Load all dut, ref and controller names or ip addresses from station.ini
        self.options.mfgc_listener = "" # Default is nothing if doesn't exist in station.ini
        self.options.dut_ips = {} # To hold dut ips read from station.ini, dut number: dut_ip
        for i in range(16):
            try:
                dut_nameip = config.get("hosts", "dut%d" % i)
            except:
                continue
            self.options.dut_ips[i] = dut_nameip
            self.myprint("Loaded Dut %d = %s" % (i, dut_nameip))
            
        self.options.ref_names = {}
        for i in range(16):
            try:
                ref_nameip = config.get("hosts", "ref%d" % i)
            except:
                continue
            self.options.ref_names[i] = ref_nameip
            self.myprint("Loaded Ref %d = %s" % (i, ref_nameip))
        
        self.options.ctrl_names = {}
        for i in range(16):
            try:
                ctrl_nameip = config.get("hosts", "ctrl%d" % i)
            except:
                continue
            self.options.ctrl_names[i] = ctrl_nameip
            self.myprint("Loaded Ctrl %d = %s" % (i, ctrl_nameip))
            
        mfgc_section = "mfgc_%s" % self.options.mfgc_ver # mfgc_ver is command line option
        if not config.has_section(mfgc_section):
            errmsg = "Couldn't find mfgc version section (%s) in station.ini config file '%s'" % (mfgc_section, self.STATION_INI_FILE)
            self.myprint(errmsg, 0)
            self.myprint("Versions found:")
            for item in config.sections():
#                print item
                y = re.search("(mfgc_[0-9]+.[0-9]+.[0-9]+)", item)
                if y:
                    self.myprint(y.group(0))
                
            raise IOError(errmsg)
            
        # Only looking in mfgc_n_n_n section
        variables_raw = {}
        for item in self.MFGC_STATION_FILE_VARIABLES:
            try:
                item_val = config.get(mfgc_section, item)
                variables_raw[item] = item_val
                msg = "Read %s: \"%s\"" % (item, item_val )
                self.myprint(msg, 2)
            except ConfigParser.NoOptionError:
                msg = item + " not found in " + self.STATION_INI_FILE
                self.myprint(msg, 2)

        # Add the variables read from the station.ini file to options
        for key, item in variables_raw.items():
            self.myprint(key, item)
            setattr(self.options, key, item)
            
        return True

    def check_machines(self):
        ''' Checks all machines to see if MFGc is already running and if so aborts. 
        Else kills any stray processes that might cause trouble.
        
        '''
        
        # Processes to look for, extensions are stripped of in get_process_list()
        in_use_processes = [
            self.options.mfgc_listener,                
            "wl_server_socket",
            "./mfgc_listener",
            "mfcremote",
            "mfcmfgc",
            "mfgc",
        ]

        # If these processes are found we assume station is in use and abort this script or kill the process (command line option)
        procs = {}
        self.myprint("")
        # Go thru each list of machines looking for running processes
        for mach_list in [self.dut_machs, self.ref_machs, self.ctrl_machs, self.nonwindows_machs]:
            if (mach_list == self.ctrl_machs or mach_list == self.ref_machs) and not self.options.firstdut:
                self.myprint("Dut %s Ignoring processes running on Controllers and Refs" % self.options.use_duts)
                continue # Only for use with BATE, don't kill mfgc if you're not the first dut. First dut will start mfgc, don't kill it after he starts it.
            for mach in mach_list:
                mach_clean = True # Set to False if one of the processes is found running
                procs[mach_list[mach].host] = mach_list[mach].get_process_list() # Get list of processes running on that machine
                for process in in_use_processes:
                    self.myprint("Checking machine %s for process %s" % (mach_list[mach].host, process))
                    if process in procs[mach_list[mach].host]:
                        mach_clean = False # A process found running
                        if not self.options.killmfgc: # if self.options.killmfgc is 0
                            msg="Machine %s appears to be in use (process %s found). Aborting" % (mach_list[mach].host, process)
                            self.myprint(msg, 0)
                            raise IOError(msg)
                        else:
                            if process[:2] == "./": # Local processes that were started manually
                                process = process[2:]
                            self.myprint("Killing process %s on %s" % (process, mach_list[mach].host))
                            mach_list[mach].kill_process(process)

                if mach_clean:
                    self.myprint("   machine %s clean" % mach_list[mach].host)
            
    def find_board_config_file(self, dut):
        ''' Query the board with brcm_dut_wlan.identify_board(), use temp brcm_dut_wlan() objects.
            Assign boardtype value to dut_mach.boardtype attribute
        
        '''
        
        self.myprint("")
        
        # Make temp brcm_dut_wlan() objects to get idenfify_board()
        temp_obj = brcm_dut_wlan(self.dut_machs[dut], self.options.interface, "") # Pass in dut_mach object

        # Verify user entered serial number is correct for that dut in board database.
        try:
            board, found_in_dB = temp_obj.identify_board(self.dut_sn[dut])
        except IOError:
            self.myprint("Unable to identify board for dut%s (%s)" % (dut, self.dut_machs[dut].addr))
            sys.exit(1)
        
#        for attr, value in board.__dict__.iteritems():
#            print attr, value

        if hasattr(board, "boardtype"):
            self.myprint("DUT boardtype: %s" % board.boardtype)
        else:
            self.myprint("DUT boardtype not found")
            sys.exit(1)
        
        self.dut_machs[dut].boardtype = board.boardtype
                        
    def get_nvram_offsets(self,dut):
        ''' get the Nvram offset from nvram_dump and adapter offsets from  the board.ini file'''
        self.myprint("")
        object_temp = brcm_dut_wlan(self.dut_machs[dut], self.options.interface, "")
        
        #get offset from nvram
        try:
            (nvram, resp) = object_temp.wl_cmd("nvram_dump", False)
            y = re.search("offtgpwr[\s\t]*=[\s\t]*([0-9]*)", nvram)
            if y:
                self.myprint("Found offtgpwr=%s" % y.group(1))
                self.nvram_offtgpwr=y.group(1)
            else:
                self.nvram_offtgpwr="0"
        except pexpect.TIMEOUT:
            self.myprint("Dut%s pexpect timeout trying to dump nvram" % dut)
            sys.exit(1)
            
    def get_adapter_offsets(self,dut):
        #get the offset from board.ini 
        self.myprint("", 2)
        brdtype = self.dut_machs[dut].boardtype
        brdcfgo = board_cfg.board_cfg(brdtype)  # Raw board config object
        config = brdcfgo.getConfigParser() 
                        
        self.myprint("Reading device information from %s" % brdcfgo.getCfgFileName(), 2)
        adapter_offset_raw={ }
        for item in self.ADAPTER_OFFSETS:
            try:
                item_val = config.get("defaults", item)
                adapter_offset_raw[item] = item_val
                self.myprint("Read %s: \"%s\"" % (item, item_val ), 2)
            except (ConfigParser.NoOptionError, ConfigParser.NoSectionError):
                pass
        bands = ['2g' , '5g']
        offset_str = [ 'txadapteroffset_' , 'rxadapteroffset_' ]
        self.adapter_correction={}
        for band in bands:
            tx_str = offset_str[0] + band
            rx_str = offset_str[1] + band 
            if ( tx_str in adapter_offset_raw.keys() )and (rx_str in adapter_offset_raw.keys() ):
                if  adapter_offset_raw[tx_str] != adapter_offset_raw[rx_str]:
                    raise IOError("IN MFGC there is no TX/RX path offset demarcation, they need to be same . But are different txadapteroffset_%s=%s rxadapteroffset_%s=%s"  %(band,adapter_offset_raw[tx_str],band ,adapter_offset_raw[rx_str] ))
                self.adapter_correction[band] =  float(adapter_offset_raw[tx_str])

    def modify_and_send_script(self, dut, user_src_script, destination):
        ''' Modify user script with board specific info from <boardtype>.ini file and dut ip address then send to controller. 
            Scripts are dut specific because of the dut ip address, therefore scripts will be uniqely named; <user_script>dut.*
        
            Returns the dut specific script name.
        
        '''
        
        self.myprint("Modifying user script: " + user_src_script)
        
        board_files = os.listdir(BASE_PATH + "/boards/") # List of all files on the directory
        
        # Make everything lowercase for searching for file then keep origianl filename found with case preserved
        for item in board_files:
            if (item.lower()) == (self.dut_machs[dut].boardtype.lower() + ".ini"):
                board_config_file = BASE_PATH + "/boards/" + item # Assign the original file, case preserved as found
                break
        
        file_list = [user_src_script, board_config_file]
                
        # Find the user script and <boardtype>.ini file.
        for item in file_list:
            if path.exists(item):
                self.myprint("Found '%s'" % item, 3)
            else:
                errmsg = "Couldn't find " + item
                self.myprint(errmsg, 0)
                raise IOError(errmsg)
        
        # Create instance-specific temporary directory to avoid race conditions with other instances
        tempdir = mkdtemp('', "config_%s_%s" % (self.ctrl_machs['0'], self.starttimestr))
        self.myprint("Using temp config directory: %s" % tempdir, 3) # for creating modified settings file
        
        # Parse <boardtype>.ini for values to change in script 
        config = ConfigParser.SafeConfigParser()
        config.read(board_config_file) # Keys stored in lowercase
        
#        self.myprint(str(config.sections()))
        
#        for section_name in config.sections():
#            print 'Section:', section_name
#            print '  Options:', config.options(section_name)
#            for name, value in config.items(section_name):
#                print '  %s = %s' % (name, value)
                
#        sys.exit(1)

        mfgc_config_params = {}
        
        # Read the key=value pairs from the [MFGc] section in the boardfile if the section exists
        if config.has_section("MFGc"):
            print "Found [MFGc] section in %s" % board_config_file
            mfgc_config_params = dict(config.items("MFGc")) # Get the items from that section into a dictionary (the params)
        
        # Set more param values before modifying the script 
        # If no board config file specified, use command line option for comments
        mfgc_config_params["comments"] = "\"" + self.options.comment + "\""
        mfgc_config_params["interface"] = "\"" + self.options.interface + "\"" # command line overrides .ini file.
        mfgc_config_params["username"] = "\"" + self.options.username + "\"" 
        mfgc_config_params["dutip"] = "\"" + self.dut_machs[dut].addr + "\""
         
#        # If dut is non-windows, dut ip for script is ip of the windows machine (vdut)
#        if self.dut_machs[dut].nonwindows_mach_addr is "":
#            mfgc_config_params["dutip"] = "\"" + self.dut_machs[dut].addr + "\""
#        else:
#            mfgc_config_params["dutip"] = "\"" + self.nonwindows_machs[dut].addr + "\""

        self._parse_and_send_script(dut, user_src_script, tempdir, mfgc_config_params, destination) 
        
#        return user_dut_script

    def myprint(self, text, level=1, dbg=False):
        '''Prints out command if level >= the current debug level.'''
        if level <= self.debug_level:
            if dbg:
                print >> sys.stderr, "MFGc: %s" % text
            else:
                try:
                    self.log.writeln(str(text))
                except (AttributeError):
                    if len(text) > 0:
                        print "MFGc: %s" % text
                    else:
                        print ""
                        
    def setup_cal(self, dut, cal_file_path, destination ,  nvram_offset , adapter_offsets ):
        '''Prepare the calibration file for the dut to the controller '''
        
        # If calibration file exists copy it to controller
        try:
            calfile = open(cal_file_path , 'r')
        except:
            errmsg = "Couldn't find " + cal_file_path
            self.myprint(errmsg, 0)
            raise IOError(errmsg)
        self.myprint("Found '%s'" % cal_file_path, 3);
        
        tempdir = mkdtemp('', "calfile_%s_%s" % (self.ctrl_machs['0'], self.starttimestr))
        self.myprint("Using temp config directory: %s" % tempdir, 3) 
        (head, tail) = path.split(cal_file_path)
        tempfile = path.join(tempdir, tail)
        
        temp_cal_file = open(tempfile ,'w')
        first = True
        Rev = None
        header = [ ]
        for line in calfile.readlines():
            #print "Scanning line: %s" % line
            if re.match('^[!#]', line):
                header.append(line)
                if Rev is None:
                    y = re.search("REV([0-9]+)", line)
                    if y:
                        Rev = int(y.group(1))
                        print "  Rev %d file" % Rev
            else:
                if first:
                    first = False
                    for header_item in header:
                        temp_cal_file.write("%s" % header_item)
                    
                tokens = [p.strip() for p in line.strip().split(",") ]
                #dont care much about all the parameters just need to add the correction to all . Just need to cehck for freq though
                
                if Rev == 5:
                    OffsetCol=6
                    FreqCol=4
                elif Rev == 2:
                    OffsetCol=4
                    FreqCol=3
                else:
                    raise IOError("Unsupported cal file version (%d)" % Rev)
                    
                Freq = int(tokens[FreqCol])
                OffsetOrig = float(tokens[OffsetCol])
                if Freq < 4000:
                    correction = adapter_offsets['2g'] + float(nvram_offset)
                elif  Freq > 4800 and Freq < 6000:
                    correction = adapter_offsets['5g'] + float(nvram_offset)
                else: 
                    raise IOError("Frequency in the cal file is wrong. Check it Freq=%s  line=%s " %(Freq,line) )
                
                Offset=OffsetOrig + correction
                tokens[OffsetCol] = str(Offset)
                new_line = ",".join(tokens) 
                temp_cal_file.write("%s\n" % new_line)
                self.myprint("%s" % new_line )
        self.myprint("destination=%s" %(destination))
        temp_cal_file.close()
        self.myprint("destination=%s" %(destination))
        cmd = "rcp '%s' '%s'" % (tempfile, destination)
        self.myprint(cmd)
        self._do_os_command(cmd)

        #Remove temp file and directory
        unlink(tempfile) # Comment out these three lines if you want to look at the file
        cmd = "rmdir '%s'" % tempdir
        self._do_os_command(cmd)

        
    def setup_connections(self):
        """Connects to every machine in an MFGc station using RSH for use later"""
        config = ConfigParser.SafeConfigParser() # To read station.ini
        config.read(path.join(self.options.station_dir, self.STATION_INI_FILE))
        
        # Create mfgc_dut objects, store in self.dut_machs{} then connect to them using rsh
        for dut in self.options.use_duts:
            try:
                int(dut)
            except:
                raise IOError("Unknown dut number '%s'" % dut)
            name_or_addr = self.options.dut_ips[int(dut)] # dut_ips{} from _read_station_ini_file()
            mach = mfgc_dut(self.options, dut, name_or_addr, self.myprint, "") # pass mfgc_wrapper.options to mfgc_dut
            mach.connect() # Establish rsh connections, this is dut_mach's connect() method. An mfgc_dut is a dut_mach
            test_mode = mach.type # Machine OS type gets set in connect()
            self.myprint("Connected to dut  machine %s, %s, type %s" % (dut, name_or_addr, mach.type))
            
            # If dut pc is non-windows get the ip address of the windows machine (vdut) that controls it from the [windows_mach] section instead
            if mach.type is not "windows":
                test_mode = mach.type 
                self.nonwindows_machs[dut] = mach # Save the non-windows machines for killing and restarting mfgc_listener later
                name_or_addr = config.get("windows_mach", "vdut%s" % dut) # Get the Windows machine to host this non-windows machine
                mach = mfgc_dut(self.options, dut, name_or_addr, self.myprint, mach.addr) # Make new Windows mfgc_dut instead of non-windows
                mach.connect() # Establish rsh connections, this is dut_mach's connect() method. An mfgc_dut is a dut_mach
                self.myprint("Connected to vdut machine %s, %s, type %s" % (dut, name_or_addr, mach.type))
            else:
                mach.nonwindows_mach_addr = '' # If windows dut no other pc is involved.

            mach.test_mode = test_mode.strip()
            self.dut_machs[dut] = mach # Add the dut machine to the dictionary, should all be windows
            
            # Set boardtype for each dut, if "unknown" that means it was not specified on the command line.
            if self.options.boardtype != "unknown":
                # If boardtype specified on command line all duts are same dut type and use same <boardtype>.ini
                self.dut_machs[dut].boardtype = self.options.boardtype 
                self.myprint("Dut %s boardtype: %s (command line)" % (dut, self.dut_machs[dut].boardtype))
            else:
                # No board config file specified on command line so auto-determine for each dut (duts may be different board types)
                self.myprint("No board config file specified on command line so auto-determine")
                self.find_board_config_file(dut) # Uses brcm_dut_wlan.identify_board(), sets dut_mach[dut].boardtype
                
                if self.dut_machs[dut].boardtype == "unknown":
                    raise IOError("Unknown boardtype for dut%s" % dut)
                else:
                    self.myprint("Dut %s boardtype: %s (auto-determined)" % (dut, self.dut_machs[dut].boardtype))
                        
        for ref in "0":
            try:
                int(ref)
            except:
                raise IOError("Unknown ref number '%s'" % ref)
            
            if len(self.options.ref_names):
                name_or_addr = self.options.ref_names[int(ref)]
                mach = mfgc_ref(name_or_addr, self.myprint)
                mach.connect()
                self.myprint("Connected to ref machine %s, %s, type %s" % (ref, name_or_addr, mach.type))
                self.ref_machs[ref] = mach
         
        for ctrl in "0":
            try:
                int(ctrl)
            except:
                raise IOError("Unknown ctrl number '%s'" % ctrl)
            name_or_addr = self.options.ctrl_names[int(ctrl)]
#            print "name_or_addr " + str(name_or_addr)
            mach = mfgc_ctrl(name_or_addr, self.myprint)
            mach.connect()
            self.myprint("Connected to ctrl machine %s, %s, type %s" % (ctrl, name_or_addr, mach.type))
            self.ctrl_machs[ctrl] = mach
            
    def setup_log(self):
        ''' Starts a log file.
        
        Cannot be called until after we have talked with DUT so that we know what parameters about device,
        and can put the log file in the correct place.
        '''
        
        if self.options.output_dir is None:
            # Build full path here if user hasn't specified there own
            self.options.output_dir = MY_PATH + "/logs/%s/" % self.options.station_name
            
        umask(2) # Permissions
        
        if not path.exists(self.options.output_dir):
            makedirs(self.options.output_dir)
        logname=path.join(self.options.output_dir, self._makelogfilename('MFGcTest', 'log'))
         
        # Save basic information into the log file
        self.myprint("")
        self.myprint("")
        logid=open('%s' %(logname), 'w')
        self.log=mlogger.teefile(logid)
        self.myprint("# MFGc Wrapper Script")
        self.myprint("")
        self.myprint("Start Date-Time: %s" %(self.starttimestr))
        self.myprint("Mfgc Station: %s" % self.options.station_name)
        self.myprint("Comment: %s" % self.options.comment)
        self.myprint("User Name: %s" % getpass.getuser())
        self.myprint("Test Program Version: %s" % self.version)
        self.myprint("")
        self.myprint("Log File: %s" % logname)
        self.myprint("Command Line: " + ' '.join(sys.argv))
        self.myprint("")
        self.myprint("")
       
    def setup_registry(self, dut, settings_file):
        ''' Modify the dut's registry path to it's mfgc settings file on the controller.'''
        controllerip = "\\\\" + self.ctrl_machs['0'].addr    

        # Get SID of current user (hwlab) this is where we set mfgc settings file path in registry
        cmd = "Reg.exe QUERY \"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\""
        result, return_value = self.dut_machs[dut].cmd(cmd)
        if len(result) == 1:
            self.myprint("No user profiles found in registry.")
            sys.exit(1)
            
        profile_list = result.splitlines() # Get Profile list of logged on users
        if profile_list[0] == None:
            self.myprint("No user profiles found in registry.")
            sys.exit(1)
            
        sid = ""
        
        # Query each profile looking for "hwlab" (current user) then get it's SID, REG ADD uses the SID
        for line in profile_list:
            if "HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\\" in line:
                cmd = "Reg.exe QUERY \"" + line + "\"" # Query each profile looking for "hwlab"
                result, return_value = self.dut_machs[dut].cmd(cmd)
                if "hwlab" in result: 
                    for line in result.splitlines():
                        if "HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\\" in line:
                            sid = line.split("HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\\")[1]
#                            print "sid " + sid
                            break
        if sid == "":
            self.myprint("hwlab SID not found for dut" + dut + " " + self.dut_machs[dut].addr)
            sys.exit(1)
        
        # Add the registry entry for the mfgc settings file path
        settings_file_path = controllerip + "\MFGc\Settings\\" + settings_file
        cmd = "REG ADD HKEY_USERS\%s\Software\Broadcom\mfgc\MFGC /v Settings_File /t REG_SZ /d " % sid + settings_file_path + " /f"
        self.myprint("Dut" + dut + " " + cmd, 3)
        self.myprint("Adding Dut" + dut + " registry entry for MFGc settings file: " + settings_file_path)
        try:
            result = self.dut_machs[dut].cmd(cmd) # If it fails user may not be logged on.
        except:
            raise IOError("Check hwlab is logged into controller")
        
        return settings_file_path

    def setup_script(self, dut, destination):
        ''' Modify user script with board specific info from <boardtype>.ini file and dut ip address then send to controller. 
            Assume user script always gets modified. If board.ini not specified on command line auto-determine.
            Scripts are dut specific because of the dut ip address, therefore scripts will be uniqely named; <user_script>dut.*
        
            Returns the dut specific script name.
        
        '''

        user_src_script = "/".join([SCRIPTS_PATH, self.options.script]) # Source, generic user script, will be modified with dut specific info.
        
        self.modify_and_send_script(dut, user_src_script, destination) # modify scripts with params in [MFGc] section in <boardtype>.ini 
        
    def setup_settings(self, dut, cal_file_name, destination):
        ''' Modify settings file with dut specific info and send to controller.
        
        Backup any settings.txt if they exist in the mfgc binaries directory.
        Takes cal_file_name, returns settings_file_name.
        Return settings_file_name to be used in dut registry.
        
        '''
        
        # Create instance-specific temporary directory to avoid race conditions with other instances
        tempdir = mkdtemp('', "config_%s_%s" % (self.ctrl_machs['0'], self.starttimestr))
        self.myprint("Using temp config directory: %s" % tempdir, 3) # for creating modified settings file
        
        # Replacement strings to put in the settings files for each DUT in use
        (head, tail) = path.split(self.options.script) 
        temp = tail.rpartition(".")
        script_file = "\\\\" + self.ctrl_machs['0'].addr + "\\MFGc\\Scripts\\" + temp[0] + "d" + dut + temp[1] + temp[2]
        cal_file = "\\\\" + self.ctrl_machs['0'].addr + "\\MFGc\\Settings\\" + cal_file_name
        
        # Get current users home directory and make log_files directory in it.
        cmd = "echo %UserProfile%"
        result, return_value = self.dut_machs[dut].cmd(cmd)

        if not result.startswith("C:\\Documents and Settings\\hwlab"):
            self.myprint("Error %s issuing %s" % (return_value, cmd))
            sys.exit(1)
            
        log_file_dir = "\"" + result[:-4] + "\\Desktop\\MFGc\\log_files" + "\"" # Get the newline off the end of result (\n\r)
         
        # Create log directories
        cmd = "if not exist " + log_file_dir + " md " + log_file_dir
        result, return_value = self.dut_machs[dut].cmd(cmd)
        if return_value:
            self.myprint("Error %s issuing %s" % (return_value, cmd))
            sys.exit(1)
            
        # Set log file paths
        log_file_dir = log_file_dir[1:-1] # Remove outer quotes before appending more characters
        dvtc_data_dir = log_file_dir
        log_filename = log_file_dir + "\\logfile_%MAC_%SN_%DT_%TT_D" + dut + ".txt"
        result_filename = log_file_dir + "\\resultdata_%MAC_%SN_%DT_%TT_D" + dut + ".csv"
        summary2_filename = log_file_dir + "\\newsummary_%MAC_%SN_%DT_%TT_D" + dut + ".txt"
        summary_filename = log_file_dir + "\\resultSummary_%MAC_%SN_%DT_%TT_D" + dut + ".csv"
        rc_enabled = self.options.mfgc_rc_enabled
        rc_port = self.options.mfgc_rc_port
        
        # Matrix of OS/interface strings
        self.dut_hostname = {'windows_sdio': 'localhost',
                             'windows_hsic': self.options.interface_ip,
                             'linux_sdio': self.dut_machs[dut].nonwindows_mach_addr,
                             'linux_hsic': self.options.interface_ip,
                             'windows_epcie': self.options.interface_ip,
                             }

        self.dut_location = {'windows_sdio': 'Local',
                             'windows_hsic': 'AccessPoint',
                             'linux_sdio': 'RemoteWL',
                             'linux_hsic': 'AccessPoint',
                             'windows_epcie': 'AccessPoint',                             
                             }

        self.dut_ap_wl_command = {'windows_sdio': 'default',
                                  'windows_hsic': '/tmp/wlmips26',
                                  'linux_sdio': 'default',
                                  'linux_hsic': '/tmp/wlmips26',
                                  'windows_epcie': 'default',
                                  }
        
        self.dut_wireless_hostname = {'windows_sdio': '192.168.1.101',
                                      'windows_hsic': '192.168.3.2',
                                      'linux_sdio': '192.168.1.101',
                                      'linux_hsic': '192.168.3.2',
                                      'windows_epcie': '192.168.3.2',
                                      }

        dut_ap_wl_command = self.dut_ap_wl_command[self.dut_machs[dut].test_mode + "_" + self.options.interface_type]
        dut_location = self.dut_location[self.dut_machs[dut].test_mode + "_" + self.options.interface_type]
        dut_hostname = self.dut_hostname[self.dut_machs[dut].test_mode + "_" + self.options.interface_type]
        dut_wireless_hostname = self.dut_wireless_hostname[self.dut_machs[dut].test_mode + "_" + self.options.interface_type]

        # Dictionary of search strings to use as key in dictionary and replacement strings
        params = dict([('/mfgc_settings/general/script_file', script_file),
                       ('/mfgc_settings/general/calibration_file ', cal_file),
                       ('/mfgc_settings/general/dvtc_data_directory', dvtc_data_dir), 
                       ('/mfgc_settings/general/log_filename', log_filename),    
                       ('/mfgc_settings/general/results_filename', result_filename),
                       ('/mfgc_settings/general/summary2_filename', summary2_filename),
                       ('/mfgc_settings/general/summary_filename', summary_filename),
                       ('/mfgc_settings/general/remote_control_enabled', rc_enabled),
                       ('/mfgc_settings/general/remote_control_port', rc_port),
                       ('/mfgc_settings/dut/0/ap_wl_command', dut_ap_wl_command),
                       ('/mfgc_settings/dut/0/hostname', dut_hostname),
                       ('/mfgc_settings/dut/0/location', dut_location),
#                       ('/mfgc_settings/dut/0/wireless_hostname', dut_wireless_hostname),
                      ])
        
        # Settings files for each dut are read from station.ini into options dictionary during mfgc_wrapper._read_station_ini_file()
        opt_str = "settings_d" + dut # Key to get value for in options dictionary, value is settings file for that dut
        option_dict = vars(self.options) # Turn options value instance into a dictionary for searching
        
        # Search options dictionary for opt_str then get it's value (it's settings file)
        if opt_str in option_dict:
            settings_file_name = option_dict[opt_str]
        else:
            settings_file_name = "settings_" + self.options.station_name + "d" + dut + ".txt" # default settings filename
            self.myprint(opt_str + " entry not found in settings.ini, using default: " + settings_file_name)
                
        settings_file_path = "/".join([STATIONS_PATH, self.options.station_name, settings_file_name]) # Network location of settings file
            
        # Modify and send the settings file to controller
        destination = self.ctrl_machs['0'].addr + ":" + self.options.windows_settings_dir
        self._parse_and_send_key_value_file(settings_file_path, tempdir, params, destination, True) # Modify the file then send it to the controller
            
        # Backup any settings.txt if they exist in the mfgc binaries directory            
        backup_dir_name = "\BACKUP_%s" % self.starttimestr 
        
        for dut in self.options.use_duts:
            self.dut_machs[dut].backup_settings(backup_dir_name) # self.dut_machs{} = dictionary of mfgc_dut objects

        return settings_file_name # Settings filename for this particular dut, used in setup_registry()
    
    def setup_sockets(self):
        """Connects to every DUT using TCP/IP sockets to send mfgc remote commands."""

        self.myprint("")
        
        for dut in self.options.use_duts:
            self.dut_machs[dut].connect_process() # self.dut_machs{} = dictionary of mfgc_dut objects
            self.myprint("Connected to mfgc process on dut machine %s, type %s, port %s" % (dut, self.dut_machs[dut].type, self.options.mfgc_rc_port))
            self.dut_machs[dut].get_version() # Get the MFGc version running on each dut
           
    def setup_test(self):
        '''Set up mfgc test by copying across the user script, config files and settings files to controller.
        
        Create required directories for logs, scripts and settings files on controller.
        Make backup directory on controller and backup (move) existing calibration and settings files.
        Modify user script and settings file before sending it to the controller.
        Copy calibration, script and settings files for each DUT in use to mfgc settings directory on controller.
        Also modifies the registry on each DUT with the path to it's settings file on the controller.
           
        '''
        
        self.myprint("")
        
        # Create required directories for scripts and settings files
        # Make backup directory on controller and backup (move) existing calibration and settings files to backup directory.
        if self.options.firstdut:
            self.myprint("Deleting (actually archiving) old setup", 3)
            backup_dir_name = self.options.windows_settings_dir + "\BACKUP_%s" % self.starttimestr 
            cmd_list = ["if not exist " + self.options.windows_script_dir + " md " + self.options.windows_script_dir,
                        "if not exist " + self.options.windows_settings_dir + " md " + self.options.windows_settings_dir,
                        "md " + backup_dir_name,
                        "if exist " + self.options.windows_settings_dir + "\*.csv move " + self.options.windows_settings_dir + "\*.csv " + backup_dir_name,
                        "if exist " + self.options.windows_settings_dir + "\*.txt move " + self.options.windows_settings_dir + "\*.txt " + backup_dir_name
                        ]
            
            for cmd in cmd_list:
                result, return_value = self.ctrl_machs['0'].cmd(cmd) 
                if return_value:
                    self.myprint("Error %s issuing %s" % (return_value, cmd))
                    sys.exit(1)
             
        connector = ""
        script_destination = self.ctrl_machs['0'].addr + ":" + self.options.windows_script_dir # Where to copy files on controller
        settings_destination = self.ctrl_machs['0'].addr + ":" + self.options.windows_settings_dir # Where to copy files on controller
        
        # Go thru each dut setting up it's cal file, settings file and registry path to settings file
        for dut in self.dut_machs:
            # Get calibration file first, it goes in setttings file.
            # Calibration files for each dut are read from station.ini into options dictionary during mfgc_wrapper._read_station_ini_file()
            opt_str = "cal_table_d" + dut # Key to get value for in options dictionary, value is calibration file for that dut
            option_dict = vars(self.options) # Turn options value instance into a dictionary for searching
            
            # Search options dictionary for opt_str then get it's value (it's settings file)
            if opt_str in option_dict:
                cal_file_name = option_dict[opt_str]
            else:
#                cal_file_name = "cal_table_" + self.options.station_name + "d" + dut + ".csv" # default settings filename
                if connector:
                    cal_file_name = "cal_table_%sd%s_%s.csv" % (self.options.station_name, dut, connector) # default calibration filename
                else:
                    cal_file_name = "cal_table_%sd%s.csv" % (self.options.station_name, dut) # default calibration filename
                    
                self.myprint(opt_str + " entry not found in station.ini, using default: " + cal_file_name)
                    
            cal_file_path = "/".join([STATIONS_PATH, self.options.station_name, cal_file_name]) # Network location of settings file

            # Modify user script with board file variables, copy cal file, point registry at settings file
            self.setup_script(dut, script_destination) # Modify with boardtype specific info
            self.get_nvram_offsets(dut) # get the offsets
            self.get_adapter_offsets(dut)
            self.myprint("^^^^^^pbilling=%s" % self.adapter_correction)
            self.setup_cal(dut, cal_file_path, settings_destination , self.nvram_offtgpwr , self.adapter_correction ) # Just copy it over
            self.setup_registry(dut, self.setup_settings(dut, cal_file_name, settings_destination)) # Settings file path for mfgc

    def start_mfgc(self):
        """Start the mfgc executables on the test machines."""
        self.myprint("")
        
        # Only the first dut will start mfgc on the controller and ref.
#        self.myprint(len(self.dut_machs))
        if len(self.dut_machs) > 0: 
            if self.options.firstdut: # Only if you're the first dut start the listener on the controller and ref else you get popup error that it's already running.
                for (ctrlnum, ctrl) in self.ctrl_machs.iteritems():
                    self.myprint("Starting mfcremote on controller: " + ctrl.addr)
                    ctrl.chdir(self.options.mfgc_dir)
                    ctrl.cmd("start mfcremote.exe")

                sleep(5) # Give some time for mfcremote.exe to start on controller
        
                for (refnum, ref) in self.ref_machs.iteritems():
                    self.myprint("Starting mfcremote on ref: " + ref.addr)
                    ref.chdir(self.options.mfgc_dir)
                    ref.cmd("start mfcremote.exe")
                    
                self.myprint("***** SETUP COMPLETE *****") # Signal to other threads it's ok to start test (BATE mode only)
                            
        # Start mfgc listener on non-windows machines here
        for dut in self.nonwindows_machs.iterkeys(): 
            self.myprint("Starting mfc_listener on " + self.nonwindows_machs[dut].addr)
            
            # Determine dut shell
            cmd = "echo $SHELL"
            response, error_lvl_integer = self.nonwindows_machs[dut].cmd(cmd)
            self.myprint("response " + response)

            if "/bin/bash" in response:
                cmd = "nohup " + self.options.mfgc_listener + " 2>&1 &"
            elif "/bin/tcsh" in response or "/bin/csh" in response:
                cmd = "nohup " + self.options.mfgc_listener + " > & /dev/null &"
            else:
                self.myprint(("Unknown shell for start_mfgc on %s: '%s'" % (self.nonwindows_machs[dut].addr, response)))
                sys.exit(1)
                
            error_lvl_integer = 0

            try:
                response, error_lvl_integer = self.nonwindows_machs[dut].cmd(cmd)
#                print error_lvl_integer
#                print response
            except IOError, err:
                error_lvl_integer = str(err).split()[3]
                self.myprint(error_lvl_integer)
                self.myprint(err)
                raise IOError(err)
            
            # Check if mfgc_listener is running
            procs = self.nonwindows_machs[dut].get_process_list() # Get list of processes running on that machine
            self.myprint("Checking machine %s for %s" % (self.nonwindows_machs[dut].addr, self.options.mfgc_listener))
            if self.options.mfgc_listener not in procs:
                self.myprint("%s not running. Check if %s is installed." % (self.options.mfgc_listener, self.options.mfgc_listener))
                sys.exit(1)
        
        sleep(5)
        # If station only has one linux dut and one controller, start mfcmfgc on controller not dut.
        for dut in self.dut_machs.iterkeys(): # dut_machs{} created in mfgc_host.setup_connections()
            # Sometimes the dut can't read the settings file when it's time to start mfgc causing mfgc to fail. 
            # Make sure we can at least do a dir command first before trying to start mfgc.
            error_lvl = 1
            max_tries = 10
            try_count = 0
            cmd = "dir \\\\%s\MFGc\Settings\*.txt" % self.ctrl_machs['0'].addr
            self.myprint(cmd)
            while error_lvl:
                response = ""
                try:
                    try_count += 1
                    response, error_lvl = self.dut_machs[dut].cmd(cmd)
                except IOError, err:
                    self.myprint(str(err))
                    if try_count > max_tries:
                        self.myprint("Max tries exceeded.")
                        raise IOError(err)
                    self.myprint(response)
                    sleep(1)
                
            # If controller and dut are not same machine start mfgc on dut, else on controller
            if self.ctrl_machs['0'].addr != self.dut_machs[dut].addr:
                self.myprint("Starting mfcmfgc on " + self.dut_machs[dut].addr)
                self.dut_machs[dut].chdir(self.options.mfgc_dir)
                self.dut_machs[dut].start_time = time() # Used for duts stuck in Initialize state too long
                self.myprint("Start time: " + str(self.dut_machs[dut].start_time))
                self.dut_machs[dut].cmd("start mfcmfgc.exe")
            else:
                self.myprint("Starting mfcmfgc on " + self.ctrl_machs['0'].addr)
                self.ctrl_machs['0'].chdir(self.options.mfgc_dir)
                self.ctrl_machs['0'].start_time = time() # Used for duts stuck in Initialize state too long
                self.myprint("Start time: " + str(self.ctrl_machs['0'].start_time))
                self.ctrl_machs['0'].cmd("start mfcmfgc.exe")
                            
    def watch_mfgc(self):
        ''' Send mfgc remote commands to monitor and report test status in real time.
        
        When dut is ready start the test.
        Get status for all duts until no more "Test In Progress".
        Delay for a polling period between GETSTATUS commands.
        If any dut is in "Iinitializing" too long kill that mfgc process.
        Display all duts status on screen overwriting previous status.

        '''
 
        init_timeout = 300 # seconds
        dut_status = {} # Collect status for all duts in this dictionary
        previous_dut_status = {}
        sorted_status = [] 
        test_running = True
        
        for dut in self.options.use_duts:
            dut_status[dut] = "" # Initialize the dictionaries
            previous_dut_status[dut] = "" 
        
        self.myprint("")
        self.myprint("Test started - " + self.options.comment)
        self.myprint("Script: " + self.options.script)
        self.myprint("Dut Status: ")
        
        # While test is running get status of all duts then print status of all duts on screen.
        while(test_running):
            # Get status for each dut in use
            for dut in self.options.use_duts:
                if "Killed" not in dut_status[dut]:
                    data = self.dut_machs[dut].get_status()
                    if "Ready" in data:
                        sleep(1)
                        self.dut_machs[dut].start_test() 
                
                    if "Pass" or "Fail" in data:
                        data = data.replace("\n\r", " ").split(" >")[0] # If data is "Pass\n\r<test time>" (or Fail) replace "\n\r" with a space

                    dut_status[dut] = data # Save status for the current dut
                
            # Analyze status for each dut in use, check if all duts are finished
            test_running = False
            for dut, value in dut_status.items():    
                if ("Test" in value) or ("Initializing" in value) or ("Ready" in value):
                    test_running = True # Still running, not finished

                # Check if dut has been initializing too long, if so kill it
                if "Initializing" in value:
                    current_time = time()
                    if self.ctrl_machs['0'].addr != self.dut_machs[dut].addr:
                        init_time = current_time - self.dut_machs[dut].start_time # mfgc ruunning on dut
                    else:
                        init_time = current_time - self.ctrl_machs['0'].start_time # mfgc ruunning on controller
                    
                    if init_time > init_timeout:
                        self.myprint("current, start : " + str(current_time)  + " " + str(self.dut_machs[dut].start_time))
                        self.myprint("Killing mfgc: " + str(init_time)  + " " + str(init_timeout))
                        
                        process = "mfcmfgc.exe"
                        self.dut_machs[dut].kill_process(process) # If stuck in "Initializing" too long kill mfgc process
                        dut_status[dut] = "Killed" # Assign it killed status and ignore          

            sleep(self.options.polling) # delay between checks

            # Print status of all duts in use. sorted_status is a dictionary of tuples
            sorted_status = [(dut, self.dut_machs[dut].test_mode, dut_status[dut]) for dut in sorted(dut_status.keys())] # Sort status message by dut
            for item in sorted_status:
                dut, os, status = item # Assign status tuple to three variables
                if status != previous_dut_status[dut]: # Update screen only when dut status is different
                    previous_dut_status[dut] = status # Save each duts current status
                    self.myprint(str(item).strip()) # Make tuple into string
            try:
                sys.stdout.flush()
            except IOError, err:
                self.myprint(str(err))
            
        self.myprint("")   

# -------------------------------------------- End of functions ----------------------------------------------------            
                        
def _main():
    '''Parse command line, copy files to machines, run mfgc on the specified DUTS, monitor test status.'''
    # Parse command line
    try:
        # -h or --help to print usage.
        usage_msg = "usage: %prog --useduts=0,123 --useduts=1,456 --script=emb.py --connector=hiroze --temp=25 --comment=\"First Try\" \
--interface=hsic::192.168.1.1 --killmfgc=1 --mfgc_ver=2_7_52 --station=athena"

        parser = optparse.OptionParser(usage_msg, version=__version__) # use --version on the command line.
        
        # dest is the variable that will hold the argument value of the option. "type" defaults to string.
        parser.add_option("--boardtype", default="unknown", dest="boardtype", help="Used for script settings", metavar="BOARDTYPE")
        parser.add_option("--comment", dest="comment", default="", help="add a user comment", metavar="COMMENT")
        parser.add_option("--connector", dest="connector", help="connector type", metavar="CONNECTOR")
        parser.add_option("--interface", dest="interface", default="hsic::192.168.1.1", help="Interface type", metavar="INTERFACE")
        parser.add_option("--firstdut", type="int", default=1, dest="firstdut", help="Perform startup tasks such as resetting controller/ref, creating backups etc. (for use with BATE)")
        parser.add_option("--killmfgc", type="int", default=0, dest="killmfgc", help="kill any mfgc process already running")
        parser.add_option("--mfgc_ver", dest="mfgc_ver", help="version of mfgc to use for running test", metavar="MFGC_VER")
        parser.add_option("--polling", type="int", default=2, dest="polling", help="how often to send GETSTATUS to mfgc", metavar="POLLING")
        parser.add_option("--script", dest="script", help="script file to run on each DUT", metavar="SCRIPT")
        parser.add_option("--station", dest="station_name", help="station name, used to find station.ini", metavar="STATION")
        parser.add_option("--temp", dest="temp", help="chamber temperature", metavar="TEMP")
        parser.add_option("--useduts", dest="use_duts", action='append', help="DUTs to run mfgc on.", metavar="USEDUTS")
        parser.add_option("--verbosity", type="int", default=9, dest="verbosity", help="level of dbug information", metavar="VERBOSITY")

        # options, an object containing values for all of your options.
        # args, the list of positional arguments leftover after parsing options.
        (options, args) = parser.parse_args()
    except optparse.OptionError, err:
        print str(err)
        usage_msg()
        sys.exit(1)

    if (len(sys.argv) == 1): # If no command line options
        print usage_msg.replace("%prog", sys.argv[0]) # Replace "%prog" with program name
        sys.exit(1)

    dut_sn={} # To hold duts and their board serial numbers
    
    for item in options.use_duts:
        try:
            temp_dut, temp_sn = item.split(":") # split 'dut:sn' into serparate variables
        except ValueError:
            print "Option --useduts requires dut:SN ex. --useduts 0:123"
            sys.exit(1)
        dut_sn[temp_dut]=temp_sn # Populate dictionary of duts and their board serial numbers
        
    options.use_duts = [item.split(":")[0] for item in options.use_duts] # Set use_duts to just the dut numbers only without the serial number
    options.username = getpass.getuser() # User running this script
    options.comment.replace(" ", "_") # Convert spaces to underscores for MFGc
       
    if options.temp == "nom":
        options.temp = None
    
    if "::" in options.interface:
        options.interface_type = options.interface.split("::")[0]
        options.interface_ip = options.interface.split("::")[1]
    else:
        options.interface_type = options.interface
        options.interface_ip = None
        
    # Hard coded options needed elsewhere in the script
    options.output_dir = None # For log
    options.windows_script_dir = "\"\documents and settings\hwlab\desktop\MFGc\scripts\"" # Where to copy files to
    options.windows_settings_dir = "\"\documents and settings\hwlab\desktop\MFGc\settings\"" 
    options.mfgc_rc_enabled = "true"
    options.mfgc_rc_port = "7130"
    
    # Here's where we do it all
    mfgc = mfgc_wrapper(options, dut_sn) # Create the one and only mfgc_wrapper object
    mfgc.setup_log() # Setup the main log file
    mfgc.setup_connections() # mfgc_dut objects created, connect to the pc's involved (rsh), get dut machine type
    mfgc.check_machines() # See if any mfgc processes running 
#    mfgc.find_board_config_file() # 
    mfgc.setup_test() # Setup and copy calibration, settings files and user script over
    mfgc.start_mfgc() # Start the mfgc exectables on test machines by rsh
    sleep(5)  # Give the MFGc gui some time to get started
    mfgc.setup_sockets() # Connect to mfgc process using TCP/IP sockets to send remote mfgc commands
    mfgc.watch_mfgc() # Monitor mfgc test status, start test when dut is ready
    
if __name__=='__main__':
    _main()
