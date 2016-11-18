#!/usr/bin/env python
#
# $Id: mfgc_test.py 351337 2012-08-17 20:23:06Z leblancr $
# $Author: leblancr $
# $Date: 2012-08-17 13:23:06 -0700 (Fri, 17 Aug 2012) $
# Copyright here All Rights Reserved

"""mfgc_test.py: General wrapper for testing all DUT's using mfgc software.

   - Copies configuration and setting files to all duts in use.
   - Starts mfgc executables running on remote duts over rsh connection
   - Start test running and watch test status by sending mfgc remote commands over TCP/IP socket connection
   - mfgc_test.py -h or --help for usage

"""

__author__="bradleyd@broadcom.com (Brad Davis)"
__version__="$Revision: 351337 $"
__all__= []

# GLOBAL MODULES
import sys, subprocess, socket
import commands
import getpass
import ConfigParser
from time import strftime, sleep, time
from os import path, unlink, umask, makedirs
from tempfile import mkdtemp
from subprocess import Popen, PIPE
import optparse

# OUR MODULES
#MY_PATH = path.dirname( path.realpath( __file__ ) )
#sys.path.append( path.join( MY_PATH, "pkg" ) )

BASE_PATH = reduce (lambda l,r: l + path.sep + r, path.dirname( path.realpath( __file__ ) ).split( path.sep )[:-2] )
sys.path.append( path.join( BASE_PATH, "switchboard" ) )
REPDIR_PATH = reduce (lambda l,r: l + path.sep + r, path.dirname( path.realpath( __file__ ) ).split( path.sep )[:-2] )
REPDIR_PATH = path.join(REPDIR_PATH, "dvt_report")

print sys.path

pexpect = None

from brcm_dut_wlan import dut_mach
#import pyPath # /projects/hnd_tools/python
import logger   # From boardtest. Splits STDOUT to logfile as well.

def do_os_command(cmd, ignore_ret_code=False):
    ''' Executes a command on the host running this script '''
    print("LOCAL CMD->%s" % cmd, 2)
    (status, output)=commands.getstatusoutput(cmd)
    print("   RETURN CODE->%s" % status, 3)
    print("   RESPONSE->%s" % output, 2)
    if status != 0 and not ignore_ret_code:
        raise IOError("Return code = %d on local command \"%s\"" % (status, cmd))        
    return (status,output)   
    
class mfgc_host(dut_mach):
    ''' Extends the dut_mach class to have some MFGc specific functionality '''
    
    def __init__(self, name_or_addr, printfunc):
        ''' Inherits dut_mach (brcm_dut_wlan.py)
            I am a dut_mach object
 
        '''
        
        dut_mach.__init__(self, name_or_addr, printfunc) # Call parent's constructor

    def chdir(self, directory):
        if self.type=="windows":
            (out, err) = self.cmd("CD /D \"%s\"" % directory) # Use the /D switch to change current drive in addition to changing current directory for a drive.
        else:
            (out, err) = self.cmd("cd \"%s\"" % directory)
        return err
        
    def get_process_list(self):
        if self.type=="windows":
            (out, err) = self.cmd("tasklist /NH")
            outlines = out.splitlines()
            # Get first element of each line and strip the ".exe" off the end for Windows, to be platform agnostic, make lower case
            procs = [el.split()[0].split(".")[0].lower() for el in outlines if len(el)>1]
        else:
            (out, err) = self.cmd("ps ax")
            outlines = out.splitlines()
            procs = [el.split()[4] for el in outlines if len(el)>1]
        return procs
    
    def kill_process(self, pname, force=True, except_on_error=True, timeout=30):
        ''' Kills a process by name - BD - move to brcm_dut_wlan.py '''
        if self.type=="windows":
            if not pname.endswith(".exe"):
                pname += ".exe"
            if force:
                cmd = "taskkill /F /T /IM \"%s\"" % pname
            else:
                cmd = "taskkill /T /IM \"%s\"" % pname
            (out, err) = self.cmd(cmd, except_on_error, timeout)
        else:
            self.su_root() # Become root to kill process
            if force:
                cmd = "killall -s KILL %s" % pname
            else:
                cmd = "killall \"%s\"" % pname
            (out, err) = self.cmd(cmd, except_on_error, timeout)
            self.exit_root() 
                
        return (out, err)
    
class mfgc_dut(mfgc_host):
    """Perform dut specific functions. Inherits mfgc_host which inherits dut_mach (brcm_dut_wlan.py)
 
    - connect to mfgc process
    - start the test running
    - monitor the test running and report status
    
    """
    
    def __init__(self, options, dut, name_or_addr, printfunc, nonwindows_mach_addr = ""):
        '''Init the mfgc_duts.'''
        
        mfgc_host.__init__(self, name_or_addr, printfunc) # Call parent's constructor

        self.dut = dut # It's own dut number
#        self.addr = addr # It's IP address
        self.test_mode = "" # What mode you're testing in, Windows, Linux ...etc.
        self.options = options # Same options as mfgc_test
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
  
        self.s.recv(2048) # Receive the ">" prompt from the connect() command
        
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
        command = 'SETTEMPERATURE ' + str(self.options.temp) + '\n\r'
        self.s.send(command)
        self.s.recv(2048)

        # Start the actual test script running
        command = 'STARTTEST\n\r'
        self.s.send(command)
        self.s.recv(2048)

    def get_status(self):
        '''Send mfgc remote command GETSTATUS and return the results.'''

        command = 'GETSTATUS\n\r'
        self.s.send(command)
        data = self.s.recv(2048)
        self.s.recv(2048) # Need to get twice to get the second "\r\n>"
        
        return data

    def get_version(self):
        '''Send mfgc remote command GETVERSION and display the results.'''

        command = 'GETVERSION\n\r' # GUI and dll versions
        self.s.send(command)
        data = self.s.recv(2048)[:-2].split("\n\r") # Get the GUI and dll versions
        for item in data:
            self.myprint(item)
        data = self.s.recv(2048) # Get the last "\n\r>", throw it away
#        self.myprint(data)

class mfgc_ref(mfgc_host):
    pass
    
class mfgc_ctrl(mfgc_host):
    pass
    
class mfgc_test():
    '''Main class that does most of the MFGc test administration. Uses RSH to connect to windows machine.'''

    debug_level = 1
    prompts = None
    LOCALHOST = "localhost"
    STATION_INI_FILE = "station.ini"
                   
    # Things to look for in station.ini
    MFGC_STATION_FILE_VARIABLES = [
        "mfgc_dir",
        "mfgc_listener",
        "settings_d0",
        "settings_d1",
        "settings_d2",
        "settings_d3",
        "settings_d4",
        "settings_d5",
        "settings_d6",
        "settings_d7"
    ]
 
    def __init__(self, options):
        '''Init the test.
 
          - reading station config information
          - connecting to host machines
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
        self.nonwindows_machs = {}
        self.ref_machs={}
        self.ctrl_machs = {}

        self._read_station_ini_file() # Parse station.ini file in the dir for each station

        try:
            global pexpect
            if "/projects/hnd_tools/python" in sys.path:
                sys.path.insert(0, "/projects/hnd_tools/python/pexpect-2.4")
            print sys.path
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

    def kickoffreport(self):
        base_rep = self.options.base_rep
        if not base_rep:
            return
        # Kick off report generation....
        self.myprint("Kicking off default report in %s" % base_rep)
        # Works but messy on stdout
        # pid = Popen(["rsh", "xlinux", "cd '%s'; GenReport.py" % base_rep], stdin=None, stdout=None, stderr=None, cwd=base_rep)
        # The following works and doesn't pollute STDOUT and it logs to a log file. Nicer.
        pid = Popen(["rsh xlinux \"cd '%s'; %s/GenReport.py\" > %s/report_auto.log 2>&1" % (REPDIR_PATH, base_rep, base_rep)], shell=True, stdin=None, stdout=None, stderr=None, cwd=base_rep)
            
    def myprint(self, text, level=1, dbg=False):
        '''Prints out command if level >= the current debug level.'''

        text = strftime("%s-%%H%%M%%S" % strftime('%Y%m%d')) + " " + text

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
             
    def _makelogfilename(self, prefix, extension):
        '''Construct logfile name (no path) given a prefix and an extension.'''

        filename = '%s-%s-%s-%s.%s' % (prefix, self.options.station_name, self.options.comment, 
                                                self.starttimestr, extension )
        return filename
        
    def _prepare_file(self, src_file, dst_file, params):
        '''Read src_file to a variable, modify it, then write it out to a new file.
        
        Read settings file into dictionary as key, value pairs.
        
        Params is a list of tuples defining a key search strings and the value replacement text,
        such as:
           [ ( key1, text1 ), ( key2, text2 ) ... ]
        '''
        
#        self.myprint("Preparing file %s" % (src_file))
        
        fh = open(src_file, 'r')
        text = fh.read() 
        fh.close()
        
        # Read settings file into dictionary, split lines by "=" into key, value pairs.
        settings_file = dict([line.split("=", 1) for line in filter(None, text.split("\n"))]) # Filter out blank lines
            
        for search_str, replace_str in params:
            settings_file[search_str] = replace_str # Replace the current string with the desired one

        outfile = [] # List of key, value pairs joined by "=" to write out to new settings file
        for key, value in settings_file.items():
            outfile.append("=".join([key, value]))
            
        fh = open(dst_file, 'w')
        fh.write('\n'.join(sorted(outfile))) # write everything out to new settings file
        fh.close()
            
    def _parse_and_send_file(self, local_file_name, tempdir, params, destination):
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
        self._prepare_file(local_file_name, tempfile, params) # Reads file, modifies values, writes new file (same name) to tempdir

        # Actually copy the file to the destination
        self.myprint("Sending file   %s to %s" % (local_file_name, destination))
        cmd = "rcp '%s' '%s'" % (tempfile, destination)
#        self.myprint(cmd)
        self._do_os_command(cmd) 
        
        # Remove temp file and directory
        unlink(tempfile) # Comment out these three lines if you want to look at the file
        cmd = "rmdir '%s'" % tempdir
        self._do_os_command(cmd) 
     
    def _read_station_ini_file(self):
        ''' Loads from station config file.
            Rather than just load the entire file into attributes (easier)
            I choose to look for specific value so that any changes to the ini file can be reflected here
            and not all throughout the code
        '''
        
        # Determine the name of the local directory for this station
        local_station_dir = path.join('..','stations', self.options.station_name)
        if not path.exists(local_station_dir):
            errmsg = "Couldn't find station config directory for station '%s'" % (self.options.station_name)
            self.myprint(errmsg, 0)
            raise IOError(errmsg)
        self.myprint("Found local station directory '%s'" % local_station_dir, 3);

        self.options.local_station_dir = local_station_dir

        # Parse station.ini, located in the directory for each station 
        config = ConfigParser.SafeConfigParser()
        config.read(path.join(self.options.local_station_dir, self.STATION_INI_FILE))

        # Load all dut, ref and controller names or ip addresses from station.ini
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
            errmsg = "Couldn't find mfgc version section (%s) in station config file '%s'" % (mfgc_section, self.STATION_INI_FILE)
            self.myprint(errmsg, 0)
            raise IOError(errmsg)
            
        # Only looking in your mfgc_section
        variables_raw = {}
        for item in self.MFGC_STATION_FILE_VARIABLES:
            try:
                item_val = config.get(mfgc_section, item)
                variables_raw[item] = item_val
                msg = "Read %s: \"%s\"" % (item, item_val )
                print msg
                self.myprint(msg, 2)
            except ConfigParser.NoOptionError:
                msg = item + " not found in " + self.STATION_INI_FILE
                print msg
                self.myprint(msg, 2)

        # Add the variables read from the station.ini file to options
        for key, item in variables_raw.items():
            print key, item
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
            for mach in mach_list:
                mach_clean = True # Set to False if one of the processes is found running
                procs[mach_list[mach].host] = mach_list[mach].get_process_list() # Get list of processes running on that machine
                for process in in_use_processes:
                    self.myprint("Checking machine %s for process %s" % (mach_list[mach].host, process))
                    if process in procs[mach_list[mach].host]:
                        mach_clean = False # A process found running
                        if not self.options.killmfgc:
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
                       
    def setup_cal(self, dut, cal_file_path, destination):
        """Copy the calibration file for the dut to the controller."""

        # If calibration file exists copy it to controller
        if path.exists(cal_file_path):
            self.myprint("Found '%s'" % cal_file_path, 3);
            self.myprint("Sending file   %s to %s" % (cal_file_path, destination))
            cmd = "rcp '%s' '%s'" % (cal_file_path, destination)
#            self.myprint(cmd)
            self._do_os_command(cmd) 
        else:
            errmsg = "Couldn't find " + cal_file_path
            self.myprint(errmsg, 0)
            raise IOError(errmsg)

    def setup_connections(self):
        """Connects to every machine in an MFGc station using RSH for use later"""
        config = ConfigParser.SafeConfigParser() # To read station.ini
        config.read(path.join(self.options.local_station_dir, self.STATION_INI_FILE))
        
        # Create mfgc_dut objects, store in self.dut_machs{} then connect to them using rsh
        for dut in self.options.use_duts:
            try:
                int(dut)
            except:
                raise IOError("Unknown dut number '%s'" % dut)
            name_or_addr = self.options.dut_ips[int(dut)] # dut_ips{} from _read_station_ini_file()
            mach = mfgc_dut(self.options, dut, name_or_addr, self.myprint, "") # pass mfgc_test.options to mfgc_dut
            mach.connect() # Establish rsh connections, this is dut_mach's connect() method. An mfgc_dut is a dut_mach
            test_mode = mach.type # Machine OS type gets set in connect()
            self.myprint("Connected to dut machine %s, type %s" % (dut, mach.type))
            
            # If dut pc is non-windows get the ip address of the windows machine that controls it, use that instead for dut
            if mach.type is not "windows":
                test_mode = mach.type 
                self.nonwindows_machs[dut] = mach # Save the non-windows machines for killing and restarting mfgc_listener later
                name_or_addr = config.get("windows_mach", "dut%s" % dut) # Get the Windows machine to host this non-windows machine
                mach = mfgc_dut(self.options, dut, name_or_addr, self.myprint, mach.addr) # Make new Windows mfgc_dut instead of non-windows
                mach.connect() # Establish rsh connections, this is dut_mach's connect() method. An mfgc_dut is a dut_mach
                self.myprint("Connected to dut machine %s, type %s" % (dut, mach.type))
            else:
                mach.nonwindows_mach_addr = '' # If windows dut no other pc involved.

            mach.test_mode = test_mode
            self.dut_machs[dut] = mach # Add the dut machine to the dictionary, should all be windows
            
        for ref in "0":
            try:
                int(ref)
            except:
                raise IOError("Unknown ref number '%s'" % ref)
            
            if len(self.options.ref_names):
                name_or_addr = self.options.ref_names[int(ref)]
                mach = mfgc_ref(name_or_addr, self.myprint)
                mach.connect()
                self.myprint("Connected to ref machine %s, type %s" % (ref, mach.type))
                self.ref_machs[ref] = mach
         
        for ctrl in "0":
            try:
                int(ctrl)
            except:
                raise IOError("Unknown ctrl number '%s'" % ctrl)
            name_or_addr = self.options.ctrl_names[int(ctrl)]
            mach = mfgc_ctrl(name_or_addr, self.myprint)
            mach.connect()
            self.myprint("Connected to ctrl machine %s, type %s" % (ctrl, mach.type))
            self.ctrl_machs[ctrl] = mach
            
    def setup_log(self):
        ''' Starts a log file.
        
        Cannot be called until after we have talked with DUT so that we know what parameters about device,
        and can put the log file in the correct place.
        '''
        
        if self.options.output_dir is None:
            # Build full path here if user hasn't specified there own
            self.options.output_dir = "./%s/" % self.options.station_name

        umask(2)
        if not path.exists(self.options.output_dir):
            makedirs(self.options.output_dir)
        logname=path.join(self.options.output_dir, self._makelogfilename('MFGcTest', 'log'))
         
        # Save basic information into the log file
        self.myprint("")
        self.myprint("")
        logid=open('%s' %(logname), 'w')
        self.log=logger.teefile(logid)
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
        self.dut_machs[dut].cmd(cmd) # If it fails user may not be logged on.
        
        return settings_file_path

    def setup_settings(self, dut, cal_file_name, destination):
        ''' Modify settings file with dut specific info and send to controller.
        
        Backup any settings.txt if they exist in the mfgc binaries directory.
        Return settings_file_name to be used in dut registry.
        
        '''
        
        # Create instance-specific temporary directory to avoid race conditions with other instances
        tempdir = mkdtemp('', "config_%s_%s" % (self.ctrl_machs['0'], self.starttimestr))
        self.myprint("Using temp config directory: %s" % tempdir, 3) # for creating modified settings file
        
        # Matrix of OS/interface strings
        self.dut_hostname = {'windows_sdio': 'localhost',
                             'windows_hsic': self.options.interface_ip,
                             'linux_sdio': self.dut_machs[dut].nonwindows_mach_addr,
                             'linux_hsic': '/tmp/wlmips26',
                             }

        self.dut_location = {'windows_sdio': 'Local',
                             'windows_hsic': 'AccessPoint',
                             'linux_sdio': 'RemoteWL',
                             'linux_hsic': '/tmp/wlmips26',
                             }

        self.dut_ap_wl_command = {'windows_sdio': 'default',
                                  'windows_hsic': '/tmp/wlmips26',
                                  'linux_sdio': 'default',
                                  'linux_hsic': '/tmp/wlmips26',
                                  }
        
        self.dut_wireless_hostname = {'windows_sdio': '192.168.1.101',
                                      'windows_hsic': '192.168.3.2',
                                      'linux_sdio': '192.168.1.101',
                                      'linux_hsic': '/tmp/wlmips26',
                                      }

        # Replacement strings to put in the settings files for each DUT in use
        (head, tail) = path.split(self.options.script) 
        script_file = "\\\\" + self.ctrl_machs['0'].addr + "\\MFGc\\Scripts\\" + tail
        cal_file = "\\\\" + self.ctrl_machs['0'].addr + "\\MFGc\\Settings\\" + cal_file_name
        dvtc_data_dir = "\\\\" + self.ctrl_machs['0'].addr + "\\MFGc\\Log_files\\D" + dut
        log_filename = "\\\\" + self.ctrl_machs['0'].addr + "\\MFGc\\Local_log_files\\D" + dut + "\\logfile_%MAC_%SN_%DT_%TT_D" + dut + ".txt"
        result_filename = "\\\\" + self.ctrl_machs['0'].addr + "\\MFGc\\Local_log_files\\D" + dut + "\\resultdata_%MAC_%SN_%DT_%TT_D" + dut + ".csv"
        summary2_filename = "\\\\" + self.ctrl_machs['0'].addr + "\\MFGc\\Local_log_files\\D" + dut + "\\newsummary_%MAC_%SN_%DT_%TT_D" + dut + ".txt"
        summary_filename = "\\\\" + self.ctrl_machs['0'].addr + "\\MFGc\\Local_log_files\\D" + dut + "\\resultSummary_%MAC_%SN_%DT_%TT_D" + dut + ".csv"
        rc_enabled = self.options.mfgc_rc_enabled
        rc_port = self.options.mfgc_rc_port
        dut_ap_wl_command = self.dut_ap_wl_command[self.dut_machs[dut].test_mode + "_" + self.options.interface_type]
        dut_location = self.dut_location[self.dut_machs[dut].test_mode + "_" + self.options.interface_type]
        dut_hostname = self.dut_hostname[self.dut_machs[dut].test_mode + "_" + self.options.interface_type]
        dut_wireless_hostname = self.dut_wireless_hostname[self.dut_machs[dut].test_mode + "_" + self.options.interface_type]

        # String to use as key in dictionary, replacement string
        params = [  ('/mfgc_settings/general/script_file', script_file),
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
                    ('/mfgc_settings/dut/0/wireless_hostname', dut_wireless_hostname),
                 ]
        
        # Settings files for each dut are read from station.ini into options dictionary during mfgc_test._read_station_ini_file()
        opt_str = "settings_d" + dut # Key to get value for in options dictionary, value is settings file for that dut
        option_dict = vars(self.options) # Turn options value instance into a dictionary for searching

        # Search options dictionary for opt_str then get it's value (it's settings file)
        if opt_str in option_dict:
            settings_file_name = option_dict[opt_str]
        else:
            settings_file_name = "settings_" + self.options.station_name + "d" + dut + ".txt" # default settings filename
            self.myprint(opt_str + " entry not found in settings.ini, using default: " + settings_file_name)
                
        settings_file_path = "../../DVT_stations/" + self.options.station_name + "/" + settings_file_name # Network location of settings file
            
        # Modify and send the settings file to controller
        destination = self.ctrl_machs['0'].addr + ":" + self.options.windows_settings_dir
        self._parse_and_send_file(settings_file_path, tempdir, params, destination) # Modify the file then send it to the controller
            
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
        Copy user script to controller.
        Make backup directory on controller and backup (move) existing calibration and settings files.
        Modify settings file before sending it to the controller.
        Copy calibration and settings files for each DUT in use to mfgc settings directory on controller.
        Also modifies the registry on each DUT with the path to it's settings file on the controller.
           
        '''
        
        self.myprint("")
        
        # Create required directories for scripts and settings files
        cmd = "if not exist " + self.options.windows_script_dir + " md " + self.options.windows_script_dir 
        self.ctrl_machs['0'].cmd(cmd) 
        cmd = "if not exist " + self.options.windows_settings_dir + " md " + self.options.windows_settings_dir 
        self.ctrl_machs['0'].cmd(cmd) 

        # Copy user script to controller
        user_script = "../../dvtc_scripts/" + self.options.script
        if path.exists(user_script):
            self.myprint("Found '%s'" % user_script, 3);
            destination = self.ctrl_machs['0'].addr + ":" + self.options.windows_script_dir
            cmd = "rcp '%s' '%s'" % (user_script, destination)
            self._do_os_command(cmd) 
        else:
            errmsg = "Couldn't find " + user_script
            self.myprint(errmsg, 0)
            raise IOError(errmsg)
        
        # Make backup directory on controller and backup (move) existing calibration and settings files to backup directory.
        # Controller settings directory
        backup_dir_name = self.options.windows_settings_dir + "\BACKUP_%s" % self.starttimestr 
        cmd = "md " + backup_dir_name
        self.ctrl_machs['0'].cmd(cmd) 
        cmd = "if exist " + self.options.windows_settings_dir + "\*.csv move " + self.options.windows_settings_dir + "\*.csv " + backup_dir_name 
        self.ctrl_machs['0'].cmd(cmd) 
        cmd = "if exist " + self.options.windows_settings_dir + "\*.txt move " + self.options.windows_settings_dir + "\*.txt " + backup_dir_name 
        self.ctrl_machs['0'].cmd(cmd) 
        self.myprint("Deleting (actually archiving) old setup", 3)
        
        # Copy calibration and settings files for each DUT in use to mfgc settings directory on controller
        destination = self.ctrl_machs['0'].addr + ":" + self.options.windows_settings_dir # Where to copy files on controller
        connector = ""
        
        # Go thru each dut setting up it's cal file, settings file and registry path to settings file
        for dut in self.dut_machs:
            cal_file_name = "cal_table_" + self.options.station_name + "d" + dut + connector + ".csv"  
            cal_file_path = "../../DVT_stations/" + self.options.station_name + "/" + cal_file_name
            self.setup_cal(dut, cal_file_path, destination) # Just copy it over
            self.setup_registry(dut, self.setup_settings(dut, cal_file_name, destination)) 
            
            # Create log directories
            cmd = "if not exist " + self.options.windows_log_files_dir + "\D" + dut + " md " + self.options.windows_log_files_dir + "\D" + dut
            self.ctrl_machs['0'].cmd(cmd) 
            cmd = "if not exist " + self.options.windows_local_log_files_dir + "\D" + dut + " md " + self.options.windows_local_log_files_dir + "\D" + dut
            self.ctrl_machs['0'].cmd(cmd) 
            
    def start_mfgc(self):
        """Start the mfgc executables on the test machines."""
        self.myprint("")
        for (refnum, ref) in self.ref_machs.iteritems():
            self.myprint("Starting mfcremote on " + ref.addr)
            ref.chdir(self.options.mfgc_dir)
            ref.cmd("start mfcremote.exe")

        for (ctrlnum, ctrl) in self.ctrl_machs.iteritems():
            self.myprint("Starting mfcremote on " + ctrl.addr)
            ctrl.chdir(self.options.mfgc_dir)
            ctrl.cmd("start mfcremote.exe")
            
        for dutnum in self.nonwindows_machs.iterkeys(): 
#            cmd = self.options.mfgc_listener + " -v"
#            error_lvl_integer = 0
#            try:
#                response, error_lvl_integer = self.nonwindows_machs[dutnum].cmd(cmd)
#            except IOError, err:
#                error_lvl_integer = str(err).split()[3]
#                print error_lvl_integer
#                print IOError
#                print err
#                if int(error_lvl_integer) != 255:
#                    raise IOError(err)
#            print response
#            sys.exit()
            
            self.myprint("Starting mfc_listener on " + self.nonwindows_machs[dutnum].addr)
            cmd = "nohup " + self.options.mfgc_listener + " > mfgc_listener.log &"
            self.nonwindows_machs[dutnum].cmd(cmd)
            
        for dutnum in self.dut_machs.iterkeys(): # dut_machs{} created in mfgc_host.setup_connections()
            self.myprint("Starting mfcmfgc on " + self.dut_machs[dutnum].addr)
            self.dut_machs[dutnum].chdir(self.options.mfgc_dir)

            # Sometimes the dut can't read the settings file when it's time to start mfgc causing mfgc to fail. 
            error_lvl = 1
            max_tries = 10
            try_count = 0
            while error_lvl:
                response = ""
                try:
                    try_count += 1
                    response, error_lvl = self.dut_machs[dutnum].cmd("dir \\\\192.168.2.30\MFGc\Settings\*.txt")
                except IOError, err:
                    print str(err)
                    if try_count > max_tries:
                        print "Max tries exceeded."
                        raise IOError(err)
                    self.myprint(response)
                    sleep(1)
                
            self.dut_machs[dutnum].start_time = time() # Used for duts stuck in Initialize state too long
            self.dut_machs[dutnum].cmd("start mfcmfgc.exe")
            
    def watch_mfgc(self):
        ''' Send mfgc remote commands to monitor and report test status in real time.
        
        When dut is ready start the test.
        Get status for all duts until no more "Test In Progress".
        Delay for a polling period between GETSTATUS commands.
        If any dut is in "Iinitializing" too long kill that mfgc process.
        Display all duts status on screen overwriting previous status.

        '''
 
        self.myprint("")

        init_timeout = 300 # seconds
        self.myprint("Test started - " + self.options.comment)
        self.myprint("Script: " + self.options.script)
        self.myprint("Dut Status: ")
        
        dut_status = {} # Collect status for all duts in this dictionary
        sorted_status = [] 
        test_running = True
        
        for dut in self.options.use_duts:
            dut_status[dut] = "" # Initialize the dictionary
        
        # While test is running get status of all duts then print status of all duts on screen.
        while(test_running):
            sys.stdout.write('\x1b[A' * len(sorted_status)) # \x1b[A positions the cursor up one line (times number of duts)
            # Get status for each dut in use
            for dut in self.options.use_duts:
                if "Killed" not in dut_status[dut]:
                    data = self.dut_machs[dut].get_status()
                    if data == "Ready":
                        self.dut_machs[dut].start_test() 
                
                    if "Pass" or "Fail" in data:
                        data = data.replace("\n\r", " ") # If data is "Pass\n\r<test time>" (or Fail) replace "\n\r" with a space

                    dut_status[dut] = data # Save status for the current dut
                
            # Analyze status for each dut in use, check if all duts are finished
            test_running = False
            for dut, value in dut_status.items():    
                if ("Test" in value) or ("Initializing" in value) or ("Ready" in value):
                    test_running = True # Still running, not finished

                # Check if dut has been initializing too long, if so kill it
                if "Initializing" in value:
                    current_time = time()
                    init_time = current_time - self.dut_machs[dut].start_time
                    if init_time > init_timeout:
                        process = "mfcmfgc.exe"
                        self.dut_machs[dut].kill_process(process) # If stuck in "Initializing" too long kill mfgc process
                        dut_status[dut] = "Killed" # Assign it killed status and ignore          

            sleep(self.options.polling) # delay between checks

            # Print status of all duts in use
            sorted_status = [(dut, self.dut_machs[dut].test_mode.ljust(7), dut_status[dut]) for dut in sorted(dut_status.keys())] # Sort status message by dut
            for item in sorted_status:
                self.myprint(str(item).ljust(40)) # Just enough padding to overwrite previous "Test In Progress" message
            
            sys.stdout.flush()
            
        print   
            
                        
def _main():
    '''Parse command line, copy files to machines, run mfgc on the specified DUTS, monitor test status.'''
    # Parse command line
    try:
        # -h or --help to print usage.
        usage_msg = "usage: %prog --useduts=1,2,4-7 --script=emb.py --connector=hiroze --temp=25 --comment=\"First Try\" \
--interface=hsic::192.168.1.1 --killmfgc=1 --mfgc_ver=2.7.35 --station=athena --verbosity=1 --polling=5"

        parser = optparse.OptionParser(usage_msg, version=__version__) # use --version on the command line.
        
        # dest is the variable that will hold the argument value of the option. "type" defaults to string.
        parser.add_option("--useduts", dest="use_duts", help="DUTs to run mfgc on.", metavar="USEDUTS")
        parser.add_option("--script", dest="script", help="script file to run on each DUT", metavar="SCRIPT")
        parser.add_option("--connector", dest="connector", help="connector type", metavar="CONNECTOR")
        parser.add_option("--temp", type="int", dest="temp", help="chamber temperature", metavar="TEMP")
        parser.add_option("--comment", dest="comment", help="add a user comment", metavar="COMMENT")
        parser.add_option("--interface", dest="interface", default="hsic::192.168.1.1", help="Interface type", metavar="INTERFACE")
        parser.add_option("--verbosity", type="int", default=1, dest="verbosity", help="level of dbug information", metavar="VERBOSITY")
        parser.add_option("--killmfgc", type="int", default=0, dest="killmfgc", help="kill any mfgc process already running")
        parser.add_option("--mfgc_ver", dest="mfgc_ver", help="version of mfgc to use for running test", metavar="MFGC_VER")
        parser.add_option("--station", dest="station_name", help="station name, used to find station.ini", metavar="STATION")
        parser.add_option("--polling", type="int", default=2, dest="polling", help="how often to send GETSTATUS to mfgc", metavar="POLLING")

        # options, an object containing values for all of your options.
        # args, the list of positional arguments leftover after parsing options.
        (options, args) = parser.parse_args()
    except optparse.OptionError, err:
        print str(err)
        usage_msg()
        sys.exit(1)

    if (len(sys.argv) == 1): # If no command line options
        print usage_msg.replace("%prog", sys.argv[0]) # Replace "%prog" with program name
        sys.exit()

    # Expand DUT ranges for options.use_duts
    templist = options.use_duts.split(",") # split original command line argument
    dutlist = [] # Will hold all DUT numbers after ranges are expanded
        
    # Build new list of DUTs to use with ranges expanded
    for item in templist:
        if "-" not in item:      # Not a range
            dutlist.append(item) # Nothing to do with individual numbers just append them to the list
        # Expand ranges and append the numbers to the list
        if "-" in item:          # Python range notation
            rangenums = item.split("-") # Split range by dash to get beginning and ending numbers of the range
            if rangenums[0] > rangenums[1]:
                rangenums[0],rangenums[1] = rangenums[1],rangenums[0] # If first number bigger swap them
            for x in range(int(rangenums[0]), int(rangenums[1])+1): # Cast to ints because they're really strings
                dutlist.append(str(x)) 
                    
    # Remove any accidental duplicate DUT numbers if any, sort ascending
    options.use_duts = sorted(set(dutlist)) # Set removes duplicates, sorted returns sorted list
    
    # Handle spaces in user comments because mfgc can't, convert spaces to underscores
    options.comment = options.comment.replace(" ", "_")
       
    # Hard coded options needed elsewhere in the script
    options.output_dir = None # For log
    options.windows_script_dir = "\"\documents and settings\hwlab\desktop\MFGc\scripts\"" # Where to copy files to
    options.windows_settings_dir = "\"\documents and settings\hwlab\desktop\MFGc\settings\"" 
    options.windows_log_files_dir = "\"\documents and settings\hwlab\desktop\MFGc\log_files\"" 
    options.windows_local_log_files_dir = "\"\documents and settings\hwlab\desktop\MFGc\local_log_files\"" 
    options.mfgc_rc_enabled = "true"
    options.mfgc_rc_port = "7130"
    
    if "::" in options.interface:
        options.interface_type = options.interface.split("::")[0]
        options.interface_ip = options.interface.split("::")[1]
    else:
        options.interface_type = options.interface
        options.interface_ip = None

    # Here's where we do it all
    mfgc = mfgc_test(options) # Create the one and only mfgc_test object
    mfgc.setup_log() # Setup the main log file
    mfgc.setup_connections() # mfgc_dut objects created, connect to the pc's involved (rsh), get dut machine type
    mfgc.check_machines() # See if any mfgc processes running 
    mfgc.setup_test() # Setup and copy config files over
#    sys.exit() # Good debug spot to check settings files on duts before mfgc runs and modifies them
    mfgc.start_mfgc() # Start the mfgc exectables on test machines by rsh
    mfgc.setup_sockets() # Connect to mfgc process using TCP/IP sockets to send remote mfgc commands
    mfgc.watch_mfgc() # Monitor mfgc test status, start test when dut is ready
    
if __name__=='__main__':
    _main()
