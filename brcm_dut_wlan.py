#!/usr/bin/env python

from __future__ import print_function

import pyPath
import os
from os import path
import sys
import re
import socket
#import pexpect
import string
import ConfigParser
import time
import board_records
import commands
import csv
import tempfile
from copy import copy

MY_PATH = reduce (lambda l,r: l + path.sep + r, path.dirname( path.realpath( __file__ ) ).split( path.sep ) )
EMBDVT_PATH = reduce (lambda l,r: l + path.sep + r, path.dirname( path.realpath( __file__ ) ).split( path.sep )[:-1] )
BRD_PATH = path.join( EMBDVT_PATH, "boards" )

sys.path.append( BRD_PATH )
import board_cfg

sys.path.insert(0, "/projects/hnd_tools/python/pexpect-2.4")
pexpect = __import__('pexpect')
print("  brcm_dut_wlan using pexpect version %s, %s" % (pexpect.__version__, pexpect.__revision__))
#print("      from: %s" % (pexpect.__file__), 2)

class MacNotSet(Exception):
    pass
class NoMfgInfo(Exception):
    pass
class DirNotFound(Exception):
    pass
class FileNotFound(Exception):
    pass
class CISTupleNotFound(Exception):
    pass
class CISStartNotKnown(Exception):
    pass

otp_ids = {
    "19": ["macaddr", "Mac Address"],
    "02": ["boardrev", "Board Rev"],
    "1b": ["boardid", "Board ID"],
    "33": ["HNBU_BRMIN", "Boot Loader Min Rev"],
    "4d": ["HNBU_USBREGS", "For Programming the USB Dev Control"],
    "83": ["vendor", "Vendor Defined. Used internal to BRCM for ciswrite verification"],
}
    
# Although this is duplicate of board_records I want to keep a seperate list here for future additions.
#   The one in board_records.py must match the board_records dB so it is a little less flexible.
PAVARS = [ 'pa0b0', 'pa0b1', 'pa0b2', 'pa1lob0', 'pa1lob1', 'pa1lob2', 'pa1b0', 'pa1b1', 'pa1b2', 'pa1hib0', 'pa1hib1', 'pa1hib2' ]   
    
def do_os_command(cmd, ignore_ret_code=False, logf=None):
    ''' Executes a command on the host running this script '''
    if not logf:
        def logf(msg, dbglvl=2):
            if dbglvl <= 2:
                print(msg)
    logf("LOCAL CMD->%s" % cmd, 2)
    (status, output)=commands.getstatusoutput(cmd)
    logf("   RETURN CODE->%s" % status, 3)
    logf("   RESPONSE->%s" % output, 3)
    if status != 0 and not ignore_ret_code:
        raise IOError("Return code = %d on local command \"%s\"" % (status, cmd))        

class CisTuples:
    ''' An interator class for storing/scanning the cis tuples '''
    def __init__(self, cis, start=0):
        self.cis = cis[:]
        self.i = start

    def __iter__(self):
        return self

    def next(self):
        while self.i < len(self.cis):
            if self.cis[self.i]=="00":
                self.i+=1
                continue
            if self.cis[self.i]=="ff":
                self.i+=1
                continue
            if self.cis[self.i]!="80" and self.cis[self.i]!="20" and self.cis[self.i]!="15":
                msg = "Unexpected start of tuple character '%s' at address %d" % (self.cis[self.i], self.i)
                print(msg)
                raise IOError(msg)
            tag=self.cis[self.i]
            length = self.cis[self.i+1]
            lengthi = int(length, base=16)
            id = self.cis[self.i+2]   # aka subtag
            val = self.cis[self.i+3:self.i+lengthi+2]
            self.i+=lengthi+2
            name = "unknown"
            if tag=="80":
                if id in otp_ids:
                    name = otp_ids[id][0]
            return (tag, id, name, val, self.i)
        raise StopIteration
            
def tuple_helper(tag, id):
    ''' Converts the tag/id to two character hexidecimal strings
         will lookup with id in the otp_ids dictionary so it can be the id number, or the tuple name '''
    if type(tag)==type(0):
        tag = "%02x" % tag
    if type(id)==type(0):
        # If in passed in then convert to hex string for matching to otp_ids
        id = "%02x" % id
    for el in otp_ids:
        val=otp_ids[el][0]
        if val==id:
            ids = el
            break
    if not ids:
        if len(id)==2:
            junk = int(id, base=16)
            # If we get here there was no exception so this is an 2 character hex integer
            ids = id
        elif len(id)==4 and id.startswith("0x"):
            junk = int(id[2:], base=16)
            # If we get here there was no exception so this is an 2 character hex integer
            ids = id[2:]
    if not ids:
        raise IOError("I don't know how to interpret tuple id %s" % id)
    name="unknown"
    if tag=="80":
        if ids in otp_ids:
            name = otp_ids[ids][0]
    return (tag, ids, name)

def print_tuples(cis, logf=None, err_lvl=2):
    ''' Prints out the cis in human readable form '''
    print("Showing cis:")
    for (tag, id, name, val, i) in CisTuples(cis):
        msg = "  Tag=0x%s, subtag=0x%s (%s), val=%s" % (tag, id, name, " ".join(val))
        if logf is None:
            print(msg)
        else:
            logf(msg, err_lvl)

def get_tuple(cis, id, search_tag="80"):
    ''' finds a tuple by id and returns it's value from the cis (list of integers) '''
    
    # First See if user used the string
    (search_tag, search_ids, search_name) = tuple_helper(search_tag, id)
    #print("Searching for tag=0x%s, subtag=0x%s (%s)" % (search_tag, search_ids, search_name))
    
    for (tag, id, name, val, i) in CisTuples(cis):
        #print("  Read tag=0x%s, subtag=0x%s (%s), val=%s" % (tag, id, name, " ".join(val)))
        if tag==search_tag and id==search_ids:
            #print("  Match")
            return (tag, id, val)
    raise CISTupleNotFound("Cound not find CIS tuple tag=0x%s, subtag=0x%s (%s)" % (search_tag, search_ids, search_name))

def update_tuple(cis, search_tag, id, new_val):
    ''' finds a tuple by id and update it's value '''
    
    for el in new_val:
        if len(el) != 2:
            raise IOError("Length of each item in new_val must be 2 hexidecimal characters, not %d" % len(el))
            
    # First See if user used the string
    (search_tag, search_ids, search_name) = tuple_helper(search_tag, id)
    #print("Updating tag=0x%s, subtag=0x%s (%s) to val %s" % (search_tag, search_ids, search_name, new_val))
    
    for (tag, id, name, val, i) in CisTuples(cis):
        #print("  Read tag=0x%s, subtag=0x%s (%s), val=%s" % (tag, id, name, " ".join(val)))
        if tag==search_tag and id==search_ids:
            #print("    Match - updating")
            if len(new_val) != len(val):
                raise IOError("Error updating tuple. New length (%d) != tuple length (%d)" % (len(new_val), len(val)))
            cis[i-len(val):i]=new_val
            found = True
    if not found:
        raise CISTupleNotFound("Cound not find CIS tuple for update. tag=0x%s, subtag=0x%s (%s)" % (search_tag, search_ids, search_name))

def append_tuple(cis, search_tag, id, new_val):
    ''' Appends tuple to cis, even if it already exists '''
    for el in new_val:
        if len(el) != 2:
            raise IOError("Length of each item in new_val must be 2 hexidecimal characters, not %d" % len(el))
            
    # First See if user used the string
    (search_tag, search_ids, search_name) = tuple_helper(search_tag, id)
    
    for (tag, id, name, val, i) in CisTuples(cis):
        print("FIXME - need to find end of used cis space and then add this new tuple")
        if tag==search_tag and id==search_ids:
            #print("    Match - updating")
            if len(new_val) != len(val):
                raise IOError("Error updating tuple. New length (%d) != tuple length (%d)" % (len(new_val), len(val)))
            cis[i-len(val):i]=new_val
            found = True
    if not found:
        raise CISTupleNotFound("Cound not find CIS tuple for update. tag=0x%s, subtag=0x%s (%s)" % (search_tag, search_ids, search_name))
        
def parse_cis_or_otp_string(somestring, chip=None, interface=None):
    if "byte" in somestring:
        cis = parse_cis_string(cis_full)
        return cis
    else:
        (otp_params, cis) = parse_otp_string(somestring, chip, interface)
        return cis
        
def parse_cis_string(cis_full):
    ''' Parses a cis string (from cisdump or otpdump) and returns a list of the tuple values '''
    cis_full = cis_full.lower()
    lines = cis_full.splitlines()
    cis = []   
    for line in lines:
        if not line.startswith("byte"):
            continue
        (addr, seperator, restofline) = line.partition(':') 
        nibble_strings = restofline.split()
        nibbles = [el[2:] for el in nibble_strings]
        cis.extend(nibbles)
    return cis

def parse_otp_string(full_otp, chip=None, interface=None):
    if not chip:
        chip="0x4334"
        print("WARNING: Assuming chip=%s for otp parsing" % chip)
    if not interface:
        interface="hsic"
        print("WARNING: Assuming interface=%s for otp parsing" % interface)
    full_otp = full_otp.lower()
    lines = full_otp.splitlines()
    found = False
    otp = []   # We will build the actual otp contents in here as a list of hex nibbles
    otp_params={}
    i = 0
    for line in lines:
        if len(line)==0:
            continue
        (addr, seperator, restofline) = line.partition(':') 
        if addr == "0x0000":
            # Hack to get the old mfg info
            mfginfo = restofline
            found = True
        if addr != "0x%04x" % i:
            print("Unexpected OTP address %s, on line %s" % (addr, line))
            raise IOError("Unexpected OTP address %s, on line %s" % (addr, line))
        bytes = restofline.split()
        for x in bytes:
            y = re.search("0x([0-9a-f][0-9a-f])([0-9a-f][0-9a-f])", x, re.I)
            if not y:
                msg = "Unexpected byte string %s, on line %s" % (x, line)
                print(msg)
                raise IOError(msg)
            otp.append(y.group(2).lower())
            otp.append(y.group(1).lower())
            i += 2
            
    # I now have full OTP information
    # Parse the damn thing so we are not susceptible to driver parsing bugs 
    try:
        
        otp_params["mfginfo"]="0x" + "".join(otp[:0x8])
        print("mfginfo:%s" % otp_params["mfginfo"])
        otp_params["mfgfull"]=otp[:0x40]
        otp_params["hwswboundry"]=otp[56:58]
        print("HW/SW Boundry:%s" % otp_params["hwswboundry"])
        otp_params["hwprog"]=(int(otp[63], 16) & 0x8) != 0
        otp_params["swprog"]=(int(otp[63], 16) & 0x4) != 0
        otp_params["chipidprog"]= (int(otp[63], 16) & 0x2) != 0
        otp_params["fuseprog"]= (int(otp[63], 16) & 0x1) != 0
        print("hwprog=%s, swprog=%s, chipidprog=%s, fuseprog=%s" % (otp_params["hwprog"], otp_params["swprog"], otp_params["chipidprog"], otp_params["fuseprog"]))
        if chip == "0x4330" or chip == "0xa962" or chip == "0x4336":
            i=0x30
        elif chip == "0x4334" or chip == "0xa94e" or chip == "0x4324" or chip == "0x4335" or chip == "0x4339" or chip == "0x4345":
            i=0x40
        else:
            msg = "Unknown chip for determining the start of cis information. OTP cannot be parsed"
            print(msg)
            raise IOError(msg)
        if int(otp[i], 16) != 0:
            # If the first byte of the cisregion is non-zero then assume ciswrite has already been done
            otp_params["hascis"] = True
            #print("Has been written with ciswrite")
        else:
            otp_params["hascis"] = False
            #print("Has not been written with ciswrite")
            
        if interface.startswith("hsic"):
            otp_params["SDIOHeader"]=None
            otp_params["SDIOExtraHeader"]=None
        elif interface.startswith("sdio"):
            otp_params["SDIOHeader"]=otp[i:i+12]
            i+=12
            if chip == "0x4330" or chip == "0xa962" or chip == "0x4336" or chip == "0x4329":
                length=4
            elif chip == "0x4334" or chip == "0xa94e" or chip == "0x4324" or chip == "0x4339" or chip == "0x4335":
                length=0
            else:
                msg = "Unknown chip '%s' for finding 'Extra SDIO Header' length" % chip
                print(msg)
                raise IOError(msg)
            otp_params["SDIOExtraHeader"]=otp[i:i+length]
            i+=length
        else:
            raise IOError("Uknown interface for parsing otp header")
        cis = otp[i:]
        
        return (otp_params, cis)  
        
    except:
        raise
        print("WARNING: I could not parse the OTP", 1)
        print("         Either the format is wrong, or", 1)
        print("         I don't know how to parse otp for this chip/chiprev/interface type", 1)

def key_defined(thedict, keyname):
    ''' Checks if the key is undefined, None, or empty on the dict
    ''' 
    if keyname not in thedict:
        return False
    elif thedict[keyname] is None:
        return False
    elif len(str(thedict[keyname])) < 1:
        return False
    return True
       
def getkey_value(thedict, keyname, default):
    ''' returns the value for a key from dict if it exists, else returns the default value
    '''
    if key_defined(thedict, keyname):
        return thedict[keyname]
    else:
        return default
    
class drv_files:

    INI_FILE_KEYS = [
                     'basedir'    ,    # This basedir. Default is build_location. 
                     'interface'  ,    # Interface information (of the form TYPE::ADDRESS). only the type is examined to seperate sdio/hsic/usb
                     #-------------- FIRMWARE 
                     'build'      ,    # This build folder (ex. build_windows) - can typically be left blank for auto-determination
                     'branch'     ,    # The branch name (ex. PHOENIX2) for the build
                     'tag'        ,    # Enough of the tag to uniquely identify the directory
                     'brand'      ,    # The driver brand (ex mfgtest[-_]dongle[-_]usb=default if blank) - usb will automatically be converted to SDIO. 
                     'date'       ,    # can typically be left blank and newest date folder will be used
                     'client'     ,    # The client folder (ex bcm, android, olympic, Nokia) - default if blank is "bcm"
                     'chip'       ,    # Allows the firmware folder to be filtered by chip name. Default=blank.
                     'firmware',      # Firmware file to use  (if host os is windows then this can be blank). Required for SDIO
                     #------------- NVRAM
                     'nvdir'      ,    # The folder for the nvram files. Default if blank = "nvram"
                     'nvram'      ,    # The nvram file name. Required.
                     #------------- SYS/INF FILE (Windows)
                     'sysfile'    ,    # For windows host os - the sysfile name (ex. /ag/bcmsddhd.sys). Default if blank is bcmsddhd.sys
                     'inffile'    ,    # For windows host os - the inf file name. Default if blank is the same as sys file 
                     'binfile'    ,    # For windows, if defined, the firmware will be copied to this name in system32 folder
                     #------------- APPS
                     'apps_build' ,    # For overrideing the build folder for the apps
                     'apps_brand' ,    # For overriding the brand folder for the apps
                     'apps_client',    # For overriding the client folder for the apps
                     'apps_dir'    ,    # The directory name to look for apps. Default if blank = "apps"
                     'wl'         ,    # The wl tool to use (should be platform independed regular expression). ex wl.exe, wl_olympic.exe
                     'wlm'        ,    # The wlm library to use. ex wlm.dll 
                     'wlu'        ,    # The wlu library to use. ex brcm_wlu.dll, olympic_wlu.dll
                     'nvserial'   ,    # The nvserial tool to use. ex nvserial.exe
                     #------------- DHD
                     'dhd_build',
                     'dhd_branch',     # When DHD comes off another branch. Default is branch
                     'dhd_tag',        # The tag for dhd. Default is tag
                     'dhd_brand',
                     'dhd_date',
                     'dhd_client',
                     'dhd_apps_dir',   # The directory name for the dhd executable
                     'dhd',            # The actual name (or full path) of the dhd app
                     'dhdko_dir',      # The directory name to look for the dhd kernel object
                     'dhdko'           # The name of the kernel object (dhd.ko)
                     ]
    
    build_location = '/projects/hnd/swbuild/'
    
    def __init__(self, printfunc=None):
        #--- init variables/structures for class -------------------------------
        self.run_files = {}
        self.dut_os = ''   # For some interfaces (ex. HSIC) the host os is different than the dut
        self.host_os = ''
        self.myprint=printfunc
    
    def set_cfg(self, cfg_obj):
        ''' Sets the configuration information from a dictionary 
            Use this when the config details come from another source (ex. bate system configuration) '''
        self.config = {}   # Clear existing config.
        
        if "board" not in cfg_obj or "boardcfg" not in cfg_obj:
            raise IOError("brcm_dut_wlan requires that both board and boardcfg defined")
        self.load_cfg(cfg_obj["board"], cfg_obj["boardcfg"]) # Load defaults from board cfg file
        # Overwrite defaults with user specified values....
        for key in self.INI_FILE_KEYS:
            if key in cfg_obj:
                self.config[key]=cfg_obj[key]
        
    def load_cfg(self, board, drv_cfg):
        ''' Loads the driver config from a board configuration (ini) file '''
        
        # board - as input, this corresponds to the name of the ini file to load the driver config file.
        # drv_cfg - the driver config, which must corresponds to a section in the ini file
        
        brd_cfg = board_cfg.board_cfg(board)
        section = drv_cfg

        #--- parse config file -------------------------------------------------
        self.drv_cfg = brd_cfg.getConfigParser()
        
        self.config = {}
        self.myprint('searching for driver for board_cfg "%s"' % section, 3)
        if self.drv_cfg.has_section(section):
            self.myprint('Driver section found: %s in file: %s' % (section, brd_cfg.getCfgFileName() ))
                
            for key in self.INI_FILE_KEYS:
                full_key = "drv_" + key
                try:
                    # use default key if no value is given
                    if not self.drv_cfg.has_option(section, full_key) or \
                            self.drv_cfg.get(section, full_key) == '': 

                        if self.drv_cfg.has_option("defaults", full_key):
                            val = self.drv_cfg.get("defaults", full_key)
                            self.config[key] = val
                            self.myprint("   loaded from defaults %s=%s" % (key, val))
                        else:
                            self.config[key] = ''
                    else:
                        val = self.drv_cfg.get(section, full_key)
                        self.config[key] = val
                        self.myprint("   loaded %s=%s" % (key, val))
                except: 
                    self.myprint('\nError key value ....')
                    
        else:
            self.myprint('\nError, can not find board type section %s for board %s in file: %s' % (section, board, brd_cfg.getCfgFileName() ))
            sys.exit(1)

    def set_dut_os(self, value):
        self.dut_os = value
        
    def set_host_os(self, value, uname=None):
        self.host_os = value
        self.host_uname = uname
        if not self.dut_os:
            # IF dut_os has not been specified, assume it is the same as host. User can overide with set_dut_os
            self.dut_os = value
        
    def find_dir_error(self, desc, base, searchstr):
        self.myprint("***** Driver Find Directory Error *************")
        self.myprint("* Finding %s directory" % desc)
        self.myprint("* In Directory: %s" % base)
        self.myprint("* Looking For: %s" % searchstr)
        self.myprint("***********************************************")
        raise DirNotFound("Error locating directory %s. Expected %s in %s" % (desc, searchstr, base))
        
    def find_file_error(self, desc, base, searchstr):
        self.myprint("***** Driver Find File Error *****************")
        self.myprint("* Finding %s file" % desc)
        self.myprint("* Starting from: %s" % base)
        self.myprint("* Recursive search for: %s" % searchstr)
        self.myprint("***********************************************")
        raise FileNotFound("Error locating file %s. Searching for %s from %s" % (desc, searchstr, base))
    
    def _cfgkey_defined(self, keyname):
        ''' Checks if the key is undefined, None, or empty
        ''' 
        return key_defined(self.config, keyname)

    def find_files(self):
        #-----------------------------------------------------------------------
        # find the directories to build a path up to and including the date dir.
        #-----------------------------------------------------------------------
        self.myprint('Try to find driver files...', 2)
        if self._cfgkey_defined("basedir"):
            basedir = self.config["basedir"]
            self.myprint("Starting from user specified basedir %s" % basedir, 3)
        else:
            basedir = self.build_location
            self.myprint("Starting from default basedir %s" % basedir, 3)

        ## First for the firmware itself        
        path = basedir

        # Firmware Build
        if not self._cfgkey_defined("build"):
            self.config['build'] = "build_" + self.dut_os
            self.myprint('Looking for autogenerated build folder name %s' % self.config['build'], 3)
        else:
            self.myprint('Looking for user specified build folder name %s' % self.config['build'], 3)
        path = self.find_dir(path, self.config['build'], self.desc_sort_sel, "build")
            
        # Firmware Branch+tag
        if len(self.config['branch']) < 5:
            raise IOError("Please specify a reasonable branch name ('%s' is not valid)" % self.config["branch"])
        if self.config['branch'] == "NIGHTLY" or self.config['branch'] == "TRUNK":
            sstr = self.config['branch'] + "$"
        else:
            sstr = self.config['branch'] + '.*' + self.config["tag"].replace(".", "_") # + "$"
        self.myprint("Looking for branch+tag folder name: %s" % sstr, 3)
        path = self.find_dir(path, sstr, self.tag_sort_sel, "branch+tag")
        
        # Firmware Brand
        interface = self.config['interface'].lower()
        if not self._cfgkey_defined('brand'):
            sstr = "mfgtest"
            if interface.startswith("sdio"):
                sstr += "[-_]dongle.*sdio"
            elif interface.startswith("hsic") or interface.startswith("usb"):
                sstr += "[-_]dongle.*usb"
            elif interface.startswith("pcie"):
                sstr += "[-_]wl"
            else:
                raise RuntimeError("Unknown interface %s for figuring out brand" % interface)
            self.myprint('Looking for autogenerated brand string %s' % sstr, 3)
        else:
            sstr = self.config['brand']
            self.myprint('Looking for user specified brand string %s' % sstr, 3)
        path = self.find_dir(path, sstr, None, "brand directory")
        
        # Firmware Date
        if self._cfgkey_defined('date'):
            self.myprint("Looking for user specified date directory %s" % self.config["date"], 3)
            path = self.find_dir(path, self.config['date'], self.desc_sort_sel, "Date")
        else:
            self.myprint("Looking for most recent date directory", 3)
            path = self.find_dir(path, "", self.desc_sort_sel, "Date")
        
        # SYS/INF File (for windows)
        if not self._cfgkey_defined('client'):
            self.config['client']='bcm'
        if self.dut_os == 'windows':
            
            #--- .sys and .inf -------------------------------------------------
            files_found = []
            if not self._cfgkey_defined('sysfile'):
                if interface.startswith("sdio"):
                    self.config['sysfile']='bcmsddhd.sys'
                elif interface.startswith("hsic") or interface.startswith("usb"):
                    self.config['sysfile']='bcmsddhd.sys'
                elif interface.startswith("pcie"):
                    self.config['sysfile']='bcmwl5.sys'
                else:
                    raise RuntimeError("Unknown interface %s for figuring out sys file name" % interface)
                self.myprint('Looking for autogenerated sysfile name %s' % self.config['sysfile'], 3)
            elif self.config['sysfile'].startswith("/"):
                if not os.path.isfile(sysfile):
                    raise IOError('Error, sys file not found at specified location %s' % self.config['sysfile'])
                else:
                    self.myprint('Using user specified sysfile %s' % self.config['sysfile'], 3)
                files_found = [ self.config['sysfile'] ]
            else:
                self.myprint('Looking for user specified sysfile name %s' % self.config['sysfile'], 3)
            if len(files_found) < 1:
                self.myprint('client: %s' % self.config['client'], 3)
                self.myprint('chip: %s' % self.config['chip'], 3)
                self.myprint('sysfile: %s' % self.config['sysfile'], 3)
                re_sys = re.compile(self.config['client']   +'.*'+
                                    self.config['chip']     +'.*'+
                                    self.config['sysfile'], re.I)
                self.myprint('Looking for sys files...(re = %s)' % re_sys.pattern, 3)
                os.path.walk(path, self.path_match, [re_sys, files_found, ['src'] ])

            self.run_files['sys'] = files_found
            no_of_files = len(files_found)
            if no_of_files == 0:
                # Try again without included the chip (ex. Z:\projects\hnd\swbuild\build_window\PHOENIX2_REL_6_10_56_14\win_mfgtest_dongle_sdio\2012.1.20.4\release\BcmDHD\Bcm_Sdio_DriverOnly\bcmsddhd.sys)
                re_sys2 = re.compile(self.config['client']   +'.*'+
                                self.config['sysfile'], re.I)
                self.myprint('Looking for sys files again...(re = %s)' % re_sys2.pattern, 3)
                files_found2 = []
                os.path.walk(path, self.path_match, [re_sys2, files_found2, ['src'] ])
                for file in files_found2:
                    if not re.match(".*4[0-9][0-9][0-9].*", file):
                        files_found.append(file)
                self.run_files['sys'] = files_found
                no_of_files = len(files_found)
            
            if no_of_files != 1:
                self.find_file_error("sys", path, re_sys.pattern)
            
            if not self._cfgkey_defined('inffile'):
                (inffilebase, junk) = os.path.splitext(self.run_files['sys'][0])
                inffile = inffilebase + ".inf"
            elif self.config['inifile'].startswith("/"):
                inffile = self.config['inifile']
            else:
                inffile = os.path.join( os.path.dirname(self.run_files['sys'][0]), self.config['inffile'])
            
            if not os.path.isfile(inffile):
                raise IOError('Error, inf file not found at expected location %s' % inffile)
            self.run_files['inf'] = [inffile]
             
        # Firmware Itself (typical not windows)
        if self.dut_os != "windows" or self._cfgkey_defined('binfile'):
            # On windows, we only look for the firmware (bin file) if the 'binfile' is defined.
            #   else we always search for it
                
            files_found = []
            if self.config['firmware'].startswith("/"):
                firmware = self.config['firmware']
                if not os.path.isfile(firmware):
                    raise IOError('Error, firmware not found at specified location %s' % firmware)
                else:
                    self.myprint('Using user specified firmware %s' % firmware, 3)
                files_found = [ firmware ]
            else:
                re_firmware = re.compile(self.config['client']   +'.*'+
                                self.config['chip']     +'.*'+
                                self.config['firmware'], re.I)
                self.myprint('Looking for firmware (bin*) files...(%s)' % re_firmware.pattern, 3)
                ignore_list = ["src"]
                os.path.walk(path, self.path_match, [re_firmware, files_found, ignore_list])
                no_of_files = len(files_found)
                if no_of_files != 1:
                    self.find_file_error("bin", path, re_firmware.pattern)

            self.run_files['firmware'] = files_found
            
        # NVRAM
        if not self._cfgkey_defined('nvdir'):
            self.config['nvdir'] = 'nvram'
        files_found = []
        if not self._cfgkey_defined('nvram'):
            if interface=="pcie":
                self.run_files['nvram'] = []
            else:
                raise IOError("nvram file must be defined")
        elif self.config['nvram'].startswith("/"):
            nvram = self.config['nvram']
            if not os.path.isfile(nvram):
                raise IOError('Error, nvram not found at specified location %s' % nvram)
            else:
                self.myprint('Using user specified nvram %s' % nvram, 2)
            files_found = [ nvram ]
            self.run_files['nvram'] = files_found
        elif self.config['nvdir'].startswith("/"):
            re_nvram = re.compile(self.config['nvram'])
            self.myprint('Looking for nvram...(%s)' % re_nvram.pattern, 3)
            ignore_list = ["tools", ".svn"]
            os.path.walk(self.config['nvdir'], self.path_match, [re_nvram, files_found, ignore_list])
            self.run_files['nvram'] = files_found
            no_of_files = len(files_found)
            if no_of_files == 0 or no_of_files > 1:
                self.find_file_error("nvram", self.config['nvdir'], re_nvram.pattern)
        else:
            re_nvram = re.compile(self.config['nvdir']+'.*'+
                                  self.config['nvram'])
            self.myprint('Looking for nvram...(%s)' % re_nvram.pattern, 3)
            ignore_list = ["tools", ".svn"]
            os.path.walk(path, self.path_match, [re_nvram, files_found, ignore_list])
            self.run_files['nvram'] = files_found
            no_of_files = len(files_found)
            if no_of_files == 0 or no_of_files > 1:
                self.find_file_error("nvram", path, re_nvram.pattern)
          
        # Apps
        if not self._cfgkey_defined('apps_client'):
            self.config['apps_client'] = self.config['client']
        if not self._cfgkey_defined('apps_dir'):
            self.config['apps_dir'] = 'apps'

        if interface.startswith("hsic"):
            # If on HSIC we need to get apps based on the host_os
            path = basedir

            # Apps Build
            if not self.config['apps_build']:
                if self.host_os == "linux": 
                    self.config['apps_build'] = "build_" + self.host_os
                elif self.host_os == "windows":
                    self.config['apps_build'] = "build_" + self.host_os
                else:
                    raise IOError("Unknown host_os %s for finding apps_build folder" % self.host_os)
                self.myprint("Looking for autogenerate apps_build folder name %s" % self.config['apps_build'], 3)
            else:
                self.myprint("Looking for user specified apps_build folder name %s" % self.config['apps_build'], 3)
            path = self.find_dir(path, self.config['apps_build'], self.desc_sort_sel, "apps build")

            # Apps Branch/tag
            if self.config['branch'] == "NIGHTLY" or self.config['branch'] == "TRUNK":
                sstr = self.config['branch'] + "$"
            else:
                sstr = self.config['branch'] + '.*' + self.config["tag"].replace(".", "_") # + "$"
            self.myprint("Looking for apps branch+tag folder name %s" % (path), 3)
            path = self.find_dir(path, sstr, self.desc_sort_sel, "apps branch+tag")

            # Apps Brand
            if not self._cfgkey_defined('apps_brand'):
                if self.host_os == "linux":
                    apps_brand = "linux_mfgtest_dongle_sdio"
                elif self.host_os == "windows":
                    apps_brand = "win_mfgtest_dongle_sdio"
                else:
                    raise IOError("Unknown host_os %s for finding apps_brand folder" % self.host_os)
                self.myprint("Looking for autogenerate brand string %s" % apps_brand, 3)
            else:
                apps_brand = self.config['apps_brand']
                self.myprint("Looking for user specified apps_brand string %s" % apps_brand, 3)
            path = self.find_dir(path, apps_brand, None, "apps brand")
            
            # Apps Date
            if self._cfgkey_defined('date'):
                self.myprint("Looking for user specified apps date string %s" % self.config['date'], 3)
                path = self.find_dir(path, self.config['date'], self.desc_sort_sel, "apps date")
            else:
                self.myprint("Looking for most recent apps date directory", 3)
                path = self.find_dir(path, '', self.desc_sort_sel, "apps date")
        else:
            # host is the same as dut so just continue searching with same base path
            self.config["apps_build"] = self.config["build"] # save apps_build dir for later..
            
        # Apps 
        files_found = []
        if self.config['apps_dir'].startswith("/"):
            appsdir = self.config['apps_dir']
            if not os.path.isdir(appsdir):
                raise IOError('Error, apps_dir not found at specified location %s' % appsdir)
            self.myprint('Using user specified apps_dir %s' % appsdir, 3)
            re_apps_base = ""
        else:
            appsdir = path            
            self.myprint('apps_client: %s' % self.config['apps_client'], 3)
            self.myprint('apps_dir: %s' % self.config['apps_dir'], 3)
            re_apps_base = self.config['apps_client']+'.*'+self.config['apps_dir']
            
        apps_re = {       # dictionary of tuples (regular expression, required)
            "wl": ("(wl.exe|wl|wl_[a-z]+.exe)$", True),
            "wl_server_socket": ("wl_server_socket", self.host_os!="windows"),
            "wlu": (".*wlu.dll", self.host_os=="windows"),
            "wlm": ("wlm.dll", self.host_os=="windows"),
            "nvserial": ("nvserial", False),
            }
            
        self.run_files['apps'] = []
        for app in apps_re:
            (appre, required) = apps_re[app]
            if self._cfgkey_defined(app):
                appre = self.config[app]
            if appre.startswith("/"):
                # App is a full path
                srcname = self.config[app]
                if not os.path.isfile(srcname):
                    raise IOError('Error, %s tool not found at specified location %s' % (app, srcname))
                else:
                    self.myprint('Using user specified %s app %s' % (app, srcname), 2)
            else:
                re_apps = re.compile(re_apps_base+".*"+appre, re.I)
                self.myprint('Looking for app %s ...(re = %s)' % (app, re_apps.pattern), 3)
                files_found=[]
                os.path.walk(appsdir, self.path_match, [re_apps, files_found, ["src"]])
                no_of_files = len(files_found)
                if no_of_files < 1:
                    if required:
                        self.find_file_error("%s app" % app, appsdir, re_apps.pattern)
                    else:
                        self.myprint('App %s not found but not required. Continuing' % app, 2)
                        continue
                if no_of_files > 1:
                    self.myprint('Too many %s apps found (%d), check driver load configuration' % (app, no_of_files))
                    self.find_file_error("%s app" % app, appsdir, re_apps.pattern)
                srcname = files_found[0]
                
            # If we get here there is one app found that needs to be added. May need to rename though...    
            trgname = os.path.basename(srcname)
            if trgname.endswith("_wlu.dll"):
                trgname = "brcm_wlu.dll"
            elif re.match("wl_.*\.exe", trgname):
                trgname = "wl.exe"
            self.run_files['apps'].append( (srcname, trgname) )

        # DHD
        if interface.startswith("hsic"):
            pass
            # Do nothing since dhd is built into the hsic platform
        else:
            # ex
            # dhd app -> /projects/hnd_swbuild/build_linux/TRUNK/linux-internal-dongle/2013.5.19.2/release/bcm/apps/
            # dhd.ko  -> /projects/hnd_swbuild/build_linux/TRUNK/linux-internal-dongle/2013.5.19.2/release/bcm/host/dhd-cdc-sdstd-debug-2.6.38.6-26.rc1.fc15.i686.PAE/

            path = basedir
            
            # DHD Build
            if not self._cfgkey_defined('dhd_build'):
                self.config['dhd_build'] = self.config['apps_build']
                self.myprint("Looking for dhd_build name %s" % self.config['dhd_build'])
            else:
                self.myprint("Looking for user specified dhd_build name %s" % self.config['dhd_build'])
            path = self.find_dir(path, self.config['dhd_build'], self.desc_sort_sel, "dhd build")

            # DHD Branch+Tag
            if not self._cfgkey_defined('dhd_branch'):
                self.config['dhd_branch']=self.config["branch"]
                if not self._cfgkey_defined('dhd_tag'):
                    self.config['dhd_tag']=self.config["tag"]
            if self.config['dhd_branch'] == "NIGHTLY" or self.config['dhd_branch'] == "TRUNK":
                sstr = self.config['dhd_branch'] + "$"
            else:
                sstr = self.config['dhd_branch'] + '.*' + self.config["dhd_tag"].replace(".", "_") # + "$"
            self.myprint("Looking for dhd branch+tag folder name %s" % sstr, 3)
            path = self.find_dir(path, sstr,self.desc_sort_sel, "dhd branch+tag")

            # DHD Brand (ex. linux-mfgtest-dongle-sdio or linux-internal-dongle)
            if not self._cfgkey_defined('dhd_brand'):
                if self.host_os == "linux":
                    if not interface.startswith("USB"):
                        dhd_brand = "linux-internal-dongle$"
                    else:
                        dhd_brand = "linux-internal-dongle-usb"
                elif self.host_os == "windows":
                    if not interface.startswith("USB"):
                        dhd_brand = "win_internal_dongle$"
                    else:
                        dhd_brand = "win_internal_dongle_usb$"
                else:
                    raise IOError("Unknown host_os %s for finding dhd_brand folder" % self.host_os)
            else:
                dhd_brand = self.config['dhd_brand']
                self.myprint("Using user specified dhd_brand %s" % dhd_brand)
            path = self.find_dir(path, dhd_brand, None, "dhd brand")
            
            # DHD Date 
            if self._cfgkey_defined('dhd_date'):
                self.myprint("Looking for user specified dhd_date directory %s" % self.config["dhd_date"], 3)
                path = self.find_dir(path, self.config['dhd_date'], self.desc_sort_sel, "dhd date")
            else:
                self.myprint("Looking for most recent dhd date directory", 3)
                path = self.find_dir(path, '', self.desc_sort_sel, "dhd date")
            
            # DHD Apps
            if not self._cfgkey_defined('dhd_client'):
                self.config['dhd_client'] = self.config['apps_client']
            if self.config["dhd"].startswith("/"):
                # dhd app is a full path
                srcname = self.config["dhd"]
                if not os.path.isfile(srcname):
                    raise IOError('Error, dhd tool not found at specified location %s' % (srcname))
                else:
                    self.myprint('Using user specified dhd app %s' % (srcname), 3)
            else:
                if not self._cfgkey_defined('dhd_apps_dir'):
                    self.config['dhd_apps_dir'] = self.config['apps_dir']
                if self.config['dhd_apps_dir'].startswith("/"):
                    appsdir = self.config['dhd_apps_dir']
                    if not os.path.isdir(appsdir):
                        raise IOError('Error, dhd_apps_dir not found at specified location %s' % appsdir)
                    self.myprint('Using user specified dhd_apps_dir %s' % appsdir, 3)
                    re_apps_base = ""
                else:
                    appsdir = path            
                    self.myprint('dhd_client: %s' % self.config['dhd_client'], 3)
                    self.myprint('dhd_apps_dir: %s' % self.config['dhd_apps_dir'], 3)
                    re_apps_base = self.config['dhd_client']+'.*'+self.config['dhd_apps_dir']
                    
                if not self._cfgkey_defined('dhd'):
                    self.config['dhd'] = 'dhd$'
                re_dhd = re.compile(re_apps_base+".*"+self.config['dhd'], re.I)
                self.myprint('Looking for dhd application ...(re = %s)' % (re_dhd.pattern), 3)
                files_found=[]
                os.path.walk(appsdir, self.path_match, [re_dhd, files_found, ["src"]])
                no_of_files = len(files_found)
                if no_of_files < 1:
                    if self.host_os == "linux":
                        # On linux, dhd is required
                        self.find_file_error("dhd app", appsdir, re_dhd.pattern)
                    else:
                        self.myprint('dhd application not found but not required. Continuing', 3)
                elif no_of_files > 1:
                    self.myprint('Too many dhd apps found (%d), check driver load configuration' % (no_of_files))
                    self.find_file_error("dhd app", appsdir, re_dhd.pattern)
                srcname = files_found[0]
            trgname = os.path.basename(srcname)
            self.run_files['apps'].append( (srcname, trgname) )
    
            # DHD kernel object
            if self.config["dhdko"].startswith("/"):
                # dhd ko is a full path
                srcname = self.config["dhdko"]
                if not os.path.isfile(srcname):
                    raise IOError('Error, kernel object not found at specified location %s' % (srcname))
                else:
                    self.myprint('Using user specified kernel object %s' % (srcname), 3)
            else:
                if not self._cfgkey_defined('dhdko_dir'):
                    self.config['dhdko_dir'] = "dhd-cdc-sdstd-"
                if self.config['dhdko_dir'].startswith("/"):
                    kodir = self.config['dhdko_dir']
                    if not os.path.isdir(kodir):
                        raise IOError('Error, dhdko_dir not found at user specified location %s' % kodir)
                    self.myprint('Using user specified dhdko_dir %s' % kodir, 3)
                    re_ko_base = ""
                else:
                    kodir = path            
                    self.myprint('dhd_client: %s' % self.config['dhd_client'], 3)
                    self.myprint('dhdko_dir: %s' % self.config['dhdko_dir'], 3)
                    re_ko_base = self.config['dhd_client']+'.*'+self.config['dhdko_dir']+self.host_uname
                    
                if not self._cfgkey_defined('dhdko'):
                    self.config['dhdko'] = 'dhd\.ko'
                re_dhd = re.compile(re_ko_base+".*"+self.config['dhdko'], re.I)
                self.myprint('Looking for dhd.ko kernel object...(re = %s)' % (re_dhd.pattern), 3)
                files_found=[]
                os.path.walk(kodir, self.path_match, [re_dhd, files_found, ["src"]])
                no_of_files = len(files_found)
                if no_of_files < 1:
                    if self.host_os == "linux":
                        # On linux, kernel object is required
                        self.find_file_error("dhd.ko kernel object", kodir, re_dhd.pattern)
                    else:
                        self.myprint('dhd.ko kernel object not found but not required. Continuing', 3)
                elif no_of_files > 1:
                    self.myprint('Too many dhd.ko kernel objects found (%d), check driver load configuration' % (no_of_files))
                    self.find_file_error("dhd.ko kernel object", kodir, re_dhd.pattern)
                srcname = files_found[0]
            trgname = os.path.basename(srcname)
            self.run_files['apps'].append( (srcname, trgname) )
    
    def path_match(self, args, directory, files):
        ''' 
        args[0] is the pattern to match
        args[1] is the list to append all files found
        args[2] is a list of dir names to not search
        '''
        if len(args) > 2:
            ignore_list = args[2]
        else:
            ignore_list = []
            
        for fil in files:
            dir_and_file = directory+'/'+fil
            if fil in ignore_list:
                #print "Ignoring directory  %s" % dir_and_file
                files.remove(fil)
                continue
            if args[0].search(dir_and_file) and os.path.isfile(dir_and_file):
                args[1].append(dir_and_file)
                self.myprint('  File found:' + dir_and_file )
                continue
            #print 'File not matched:', dir_and_file


    def tag_sort_sel(self, dir_list):
        dir_sorted = sorted(dir_list)
        dir_sorted = sorted(dir_sorted, self.tag_comp)
        dir_sorted.reverse()
        return dir_sorted[0]

    def desc_sort_sel(self, dir_list):
        dir_sorted = sorted(dir_list, reverse=True)
        return dir_sorted[0]

    def find_dir(self, path, val, sorter, desc=None):
        ''' Searches for a directory named "val" in "path". Non-recursive.

            path - start path
            val -  regular expression for what we are searching for
            sorter - a function to sort the results so we can pick the latest
            desc - human description of what we are searching for
        '''
        
        try:
            cur_dirs = os.listdir(path)
            
            # Filter to match our re
            dirs2 = [td for td in cur_dirs if re.search(val, td)]
            # Filter to ensure valid directory
            dirs = [td for td in dirs2 if os.path.isdir(os.path.join(path, td))]
            
            if not dirs:
                self.find_dir_error(desc, path, val)

            if len(dirs) > 1:
                if sorter:
                    dir1 = sorter(dirs)
                    for el in dirs:
                        if dir1==el:
                            self.myprint('  USING->%s' % el)
                        else:
                            self.myprint('         %s' % el)
                else:
                    self.myprint("ERROR: More than one directory found %s" % dirs)
                    self.find_dir_error(desc, path, val)
            else:
                dir1 = dirs[0]
                
            new_path = os.path.join(path, dir1)
            
        except Exception, Err:
            self.find_dir_error(desc, path, val)
        else:
            self.myprint('  Found dir: ' + dir1, 3)
            return new_path

    def proc_user_conf(self, tag, dut_mach_type = None, uname=None, interface = None, brand = None, date = None):
        ''' allows updating the internal config info based on user optoins - ex command line '''
        self.host_os = dut_mach_type
        self.host_uname = uname
        self.dut_os = dut_mach_type
        
        interface = interface.lower()
        self.config["interface"] = interface
        
        #--- tag ---------------------------------------------------------------
        if tag:
            #--- if user specified it, then find exact branch/tag string
            self.config['tag'] = tag

        #--- date --------------------------------------------------------------
        if date:
            self.config['date'] = date

        #--- interface ---------------------------------------------------------
        if interface.startswith('hsic'):
            self.dut_os = 'linux'
        elif interface.startswith('sdio'):
            pass
        elif interface.startswith('pcie'):
            pass
        else:
            raise IOError('Error, not a valid interface: ' + interface)

        #--- brand -------------------------------------------------------------
        if brand:
            self.config['brand'] = brand
        else:
            if not self.config['brand']:
                self.config['brand'] = "mfgtest[-_]dongle[-_]usb"
            #--- is this to complicated??
            if interface == 'sdio':
                self.config['brand'] = self.config['brand'].replace('usb','sdio')
 
    def tag_comp(self, a, b):
        aa = [int(n) for n in a.split('_') if str.isdigit(n)]
        bb = [int(n) for n in b.split('_') if str.isdigit(n)]

        if len(aa) > len(bb):
            aa = aa[:-1]
        if len(aa) < len(bb):
            bb = bb[:-1]

        asum = sum(aa)
        bsum = sum(bb)

        if asum > bsum:
            return 1
        if asum < bsum:
            return -1
        else:
            return 0
   
class dut_mach:
    ''' Class to represent the dut pc (sometimes connects directly to dut, sometimes uses socket server) '''
    addr = None    # Ip address of DUT machine
    type = None    # type of machine (ex windows, linux)
    dut_host = None  # Pexpect session to the host
    host = None    # The name or of the host (for connecting to. Must be IP or DNS resolvable to IP address)
    hostname = None # The actual name of the host (according to itself - not DNS. From "hostname")
    WINDOWS_DRIVER_DIR=r"C:\WINDOWS\system32\drivers\\"  
    WINDOWS_SYS_DIR=r"C:\WINDOWS\system32\\"  
    WINDOWS_TEMP_DIR=None # Fill at instantiation time so can be dynamic time
    UNIX_TEMP_DIR=None
    tempdir = None    # Will be filled in once the machine type (winodws or linus) is known.
    hastemp = False   # Set to True after temp dir is created
    
    def __init__(self, host, printfunc=None):
        self.runtime = time.strftime('%Y%m%d-%H%M%S')
        self.WINDOWS_TEMP_DIR=r"C:\TEMP\\"+self.runtime+"\\"
        #self.UNIX_TEMP_DIR=r"/tmp/"+self.runtime+"/"
        self.UNIX_TEMP_DIR=r"/tmp/bate/"   # On linux we need to be able to find all the driver files for every enable so dir name must be constant
    
        self.addr = ''
        self.host=host
        self.printfunc = printfunc
        
        hostname = socket.gethostname().split('.')[0]
        self.myprint('My Hostname: %s' % hostname, 4)
        self.myprint('   dut host: %s' % host, 4)
        #if not host:
        #    sb_cfg = open('switchboard.cfg','r')
        #    for line in sb_cfg:
        #        line = line.strip()
        #        if not re.match('^#', line):
        #            cfg_fields = line.split(',')
        #            if re.match(hostname,cfg_fields[0]):
        #                self.addr = socket.gethostbyname(cfg_fields[2])
        #                self.host = cfg_fields[2]
        #    sb_cfg.close()
        #else:
        self.addr = socket.gethostbyname(host)

        if not self.addr:
            self.myprint('Error, no DUT machine found')
            sys.exit(1)
            
        self.myprint('HOST address: %s' % self.addr, 3)
        
    def cleanup(self):
        ## Cleans up the temp directory.
        if self.hastemp:
            try:
                if self.type == 'linux':
                    self.cmd("cd %s" % self.tempdir)
                    self.cmd("rm *")
                    self.cmd("cd ../")
                    self.cmd("rmdir %s" % self.tempdir)
                elif self.type == 'windows':
                    self.cmd("del /Q \"%s\\*.*\"" % self.tempdir)
                    self.cmd("cd \"%s\\..\"" % self.tempdir)
                    self.cmd("rmdir \"%s\"" % self.tempdir)
            except:
                pass
            finally:
                self.hastemp=False
                
        if self.dut_host:
            self.dut_host.sendline("exit")
        else:
            self.myprint("No pexpect host", 4)
        
    def __del__(self):
        ## Remove the contents of the temp dir and the dir itself
        self.cleanup()
    
    def myprint(self, msg, dbglvl=1):
        if self.host:
            hdr = self.host
        else:
            hdr = ""
            
        msgf = ""
        first = True
        for line in msg.splitlines():
            if first:
                msgf += "%s: %s" % (hdr, line)
                space = len(hdr)
                first=False
            else:
                msgf += "\n" + " "*space + "  %s" % ( line)
           
        if self.printfunc:
            self.printfunc(msgf, dbglvl)
        else:
            print(msgf)
    
    def mkdir_wbackup(self, dir, needsroot=False):
        ''' Creates a directory while backing up any that already exist 
        
            needsroot = set to true if the directory needs to be created by root 
        '''
        (str, rv) = self.cmd("cd %s" % dir, except_on_error=False, needsroot=False)
        if rv==0:
            # Directory already exists so archive...
            self.cmd("cd ..")
            bkdir = dir.rstrip("/").rstrip("\\") + "_bak" + self.runtime
            
            if self.type == "windows":
                self.cmd("move %s %s" % (dir, bkdir), needsroot=True)
            else:
                self.cmd("mv %s %s" % (dir, bkdir), needsroot=True) 
        self.cmd("mkdir %s" % dir, needsroot=needsroot)
        self.cmd("cd %s" % dir)

    def mktemp(self):
        # Creates the temp directory and cd to it.
        if not self.hastemp:
            self.mkdir_wbackup(self.tempdir, needsroot=False)
            self.hastemp = True

    def connect(self, timeout=30):
        machtype_re = {'linux'   : 'linux'     ,
                       'windows' : '.*\$OSTYPE',
                       'freebsd' : 'freebsd'   ,
                       'darwin'  : 'darwin'
                       }
                       
        self.myprint("CMD=%s" % ('rsh ' + self.addr + ' echo $OSTYPE'), 3)
        result = pexpect.run('rsh ' + self.addr + ' echo $OSTYPE', timeout)
        self.myprint("  result=%s" % result, 3)

        for machtype,rex in machtype_re.items():
            if re.match(rex, result):
                self.myprint('HOST mach: %s' %machtype, 3)
                self.type = machtype
        if not self.type:
            raise IOError("Could not identify machine at '%s' type from string '%s'" % (self.addr, result))
        
        if self.type == 'windows':
            self.uname = "windows"
            shell_cmd = 'rsh %s cmd.exe' % self.addr
            self.myprint(shell_cmd, 3)
            self.dut_host = pexpect.spawn(shell_cmd, [], timeout)

            prompt = "SWITCHBOARDPROMPT"    # > gets added to prompt list 
            self.prompts = [ prompt + ">" ]  # Allow exceptions to be thrown, pexpect.TIMEOUT, pexpect.EOF]
            self.dut_host.sendline("set prompt=%s$G" % prompt)
            self.dut_host.expect( self.prompts )
            self.tempdir = self.WINDOWS_TEMP_DIR
            import ntpath
            self.join = ntpath.join
        else:
            #get hostname and use it in the prompt for pexpect
            #workaround for not be able to set the prompt and pexpect detect it
            hostname = commands.getoutput("rsh " + self.addr + " hostname").split('.')[0]

            shell_cmd = 'rsh %s' % self.addr
            self.myprint(shell_cmd, 3)
            self.dut_host = pexpect.spawn(shell_cmd, [], timeout)
            time.sleep(2)

            self.prompts = [hostname, pexpect.EOF, pexpect.TIMEOUT]
            self.dut_host.expect(self.prompts)
            prompt = "SWITCHBOARDPROMPT>"
            self.prompts = [prompt]     #, pexpect.EOF, pexpect.TIMEOUT]
            self.dut_host.sendline("set prompt=\"%s\"" % prompt)
            self.dut_host.expect(self.prompts)
            self.dut_host.expect(self.prompts)
            self.tempdir = self.UNIX_TEMP_DIR
            import posixpath
            self.join = posixpath.join
            
        self.isroot = False
        
        (response, errorlvl) = self.cmd("hostname -s", except_on_error=False)
        if errorlvl==0:
            self.hostname = response.strip()
        else:
            self.hostname = self.host
            
        (response, errorlvl) = self.cmd("uname -r", except_on_error=False)
        if errorlvl==0:
            self.uname = response.strip()
        else:
            self.uname = ""

        return result.strip()
        
    def exit_root(self):
        if not self.isroot:
            # We are not root
            return
        
        self.myprint("Exiting root")
        if self.type == 'windows':
            pass
        else:
            self.dut_host.sendline("exit")
            self.isroot = False
            self.prompts = self.dfltprompts
            self.dut_host.expect(self.prompts)
            self.cmd("whoami")
        
    def su_root(self):
        if self.isroot:
            # Already root
            return
        
        self.myprint("Becoming root")        
        if self.type == 'windows':
            pass
        else:
            self.myprint("sending su", 4)
            self.dut_host.sendline("su")
            match = self.dut_host.expect( "Password:")

            self.myprint("   BEFORE: %s" % self.dut_host.before, 4)
            self.myprint("   AFTER: %s" % self.dut_host.after, 4)
            self.myprint("   MATCH: %s" % self.dut_host.match.group(), 4)
            
            self.myprint("sending passwd", 4)
            self.dut_host.sendline("hrun*10")

            root_default_prompts = ['#']
            self.myprint("waiting on first prompt: %s" % root_default_prompts, 4)
            match = self.dut_host.expect( root_default_prompts)
            
            # Get shell first
            self.dut_host.sendline("echo $SHELL")
            
            match = self.dut_host.expect( root_default_prompts )
            if "bash" in self.dut_host.before:
                self.shell="BASH"
            elif "csh" in self.dut_host.before:
                self.shell="CSH"
            else:
                raise IOError("Unknown shell %s" % self.dut_host.before)

            prompt = "ROOTSWITCHBOARDPROMPT>"   # Compatible with existing self.prompts
            self.myprint("setting prompt to something more useful (%s)" % prompt, 4)
            self.dfltprompts = self.prompts
            self.prompts = [prompt]
            
            if self.shell=="BASH":
                self.dut_host.sendline("export PS1=\"%s\"" % prompt)   # BASH NOTATION FOR ROOT.
            elif self.shell=="CSH":
                self.dut_host.sendline("set prompt=\"%s\"" % prompt)
            else:
                raise IOError("Unknown shell for setting root prompt")

            self.dut_host.expect(self.prompts)
            self.dut_host.expect(self.prompts)

            self.cmd("whoami")
            
        self.isroot = True
        
    def cmd(self, cmd, except_on_error=True, timeout=30, needsroot=False):
        '''
        Sends commands to host machine and reads error code.
        Returns a tuple of (response_string, error_lvl_integer)
        '''
        if needsroot:
            self.su_root()
        else:
            self.exit_root()
            
        self.myprint("REMOTE CMD->%s" % cmd, 2)
        response = self.cmd_lowlevel(cmd, timeout)

        if self.type == 'windows':
            cmd2 = "echo %ERRORLEVEL%"
        elif self.type == 'linux':
            cmd2 = "echo $?"
        else:
            raise ValueError("Unknown machine type")

        self.myprint("CMD->%s" % cmd2, 4)
        error_lvl_str = self.cmd_lowlevel(cmd2, timeout).splitlines()[0]
        try: 
            error_lvl_integer = int(error_lvl_str)
        except ValueError:
            print("DEBUG string = -%s-" % error_lvl_str)
            error_lvl_integer = 99
                     
        self.myprint("  RTRN VAL->%d" % error_lvl_integer, 3)
        first = True
        msgf = ""
        for line in response.splitlines():
            if first:   
                msgf =  "  RESPONSE->" + line + "\n"
                first = False
            else:
                msgf += "            " + line + "\n"
        self.myprint(msgf, 2)
        
        if (except_on_error and (error_lvl_integer != 0)):
            raise IOError("Return code = %d running command \"%s\"" % (error_lvl_integer, cmd))
            
        return (response, error_lvl_integer)		

    def cmd_lowlevel(self, cmd, timeout):
        # Sends command to the host machine
        # cmd - the command to send
        self.dut_host.sendline(cmd)

        match = self.dut_host.expect( self.prompts, timeout=timeout)

        self.myprint("   BEFORE: %s" % self.dut_host.before, 4)
        self.myprint("   AFTER: %s" % self.dut_host.after, 4)

        # before seems to include and echo of the command and then the response
        tempvalue=self.dut_host.before.split('\r\n')  # This echos command and then response

        response=""
        response_index = 1
        if self.type == 'windows':
            # windows machines echo an extra line
            response_index = 2

        if len(tempvalue) >= response_index:
            response="\n".join(tempvalue[response_index:])

        self.myprint("   match=%d, len(prompts)=%d" % (match, len(self.prompts)), 4)		        
        if self.dut_host.match != pexpect.TIMEOUT:
            self.myprint("   PROMPT MATCH->%s" % self.dut_host.match.group(), 4)

        return response
        
    def sendfileto(self, src, dstdir=None, dstfile=None):
        # Sends file to host.
        #   if dstdir=None then sends to temp dir
        #   if dstfile=None then keeps file name of src
        if not dstdir:
            self.mktemp()   # Ensure the temp directory exists
            dstdir = self.tempdir
        if dstfile:
            dst = self.join(dstdir, dstfile)  # target full path (dir + name)
            dstpath = dst                     # full path at destination
        else:
            dst = dstdir                      # target directory
            dstfile = path.basename(src)          # target filanem
            dstpath = self.join(dstdir, dstfile)  # full path at destination
            
        # First delete any process that matches the file name...
        self.kill_process(dstfile, force=True, except_on_error=False)
        
        self.myprint("   %s to %s:%s" % (src, self.addr, dst))
        cmd = "rcp %s '%s:\"%s\"'" % (src, self.addr, dst)
        do_os_command(cmd, self.myprint)
        return dstpath

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

    def get_pids(self, searchstr, except_on_error=True, timeout=30):
        ''' retruns a list of PID's for all processes that match the searchstr'''
        pids = {} # pid:searchstr       
        if self.type == "windows":
            raise IOError("NOT IMPLEMENTED")
        else:
            cmd = "ps auxw" 
            (out, err) = self.cmd(cmd, except_on_error, timeout)

        for line in out.splitlines():
            if searchstr in line:
                pids[line.split()[1]] = line.split()[11]
                
        return pids

    def kill_process_by_pid(self, pid, force=True, except_on_error=True, timeout=30):
        if self.type=="windows":
            if force:
                cmd = "taskkill /F /T /PID \"%s\"" % pid
            else:
                cmd = "taskkill /T /PID \"%s\"" % pid
            (out, err) = self.cmd(cmd, except_on_error, timeout)
        else:
            if force:
                cmd = "kill -s KILL %s" % pid
            else:
                cmd = "kill \"%s\"" % pid
            (out, err) = self.cmd(cmd, except_on_error, timeout, True)
                
        return (out, err)

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
            if force:
                cmd = "killall -s KILL '%s'" % pname
            else:
                cmd = "killall '%s'" % pname
            (out, err) = self.cmd(cmd, except_on_error, timeout, True)
                
        return (out, err)
        
class _hsic_dut_wlan:
    def __init__(self, host_mach, address, files, printfunc):
        self.host_mach = host_mach
        self.dut_ip = address    
        self.myprint = printfunc
        self.files = files
    def wl_cmd(self, wl_cmd, except_on_error=True, timeout=30):
        cmd_full = "wl --socket %s %s" % (self.dut_ip, wl_cmd)
        return self.host_mach.cmd(cmd_full, except_on_error, timeout, True) 
    def enable(self, maxtries, failok, drv_options):
        verstr = self.enable_hsic(drv_options)
        return verstr
    def disable(self):
        self.disable_hsic()
    def install(self,drv_options):
        self.copy_hsic_files()
        self.install_hsic_files()
        verstr = self.enable_hsic(drv_options)
        return verstr
    def copy_hsic_files(self):
        self.filenames = ''

        source = self.files['firmware']+self.files['nvram']
        for src in source:
            self.host_mach.sendfileto(src)
            self.filenames = self.filenames + ' ' + src.split('/')[-1] 
        for (src, trgname) in self.files['apps']:
            if self.host_mach.type=="windows":
                # If windows, put in system32 folder for easy access
                self.host_mach.sendfileto(src, dstdir=self.host_mach.WINDOWS_SYS_DIR, dstfile=trgname)
            else:
                # Else, just put in the temp folder
                self.host_mach.sendfileto(src, dstfile=trgname)

    def install_hsic_files(self):

        self.myprint('ping to check if device is up @ %s' % (self.dut_ip))
        done = False
        tries = 1
        while not done:
            if self.host_mach.type == 'linux':
                (response, error_code) = self.host_mach.cmd('ping -c 3 ' + self.dut_ip, False)
            if self.host_mach.type == 'windows':
                (response, error_code) = self.host_mach.cmd('ping -n 3 ' + self.dut_ip, False)

            if error_code == 0:
                break

            if tries < 3: tries = tries + 1
            else: 
                raise DLException('no connection to router board')

            self.myprint('sleeping for 5 seconds')
            time.sleep(5)

        self.myprint('Installing files to router address: %s' % (self.dut_ip))

        wl_commands = ['sh rm /etc/jffs2/*',
                       'hsic_download ' + self.filenames,
                       ]
                       
        self.host_mach.cmd("cd %s" % self.host_mach.tempdir)

        for wl_cmd in wl_commands:
            self.wl_cmd(wl_cmd, False)
            #time.sleep(1)  # BD - was 1, set to 2 to see if the reset was more reliable.
            
        return

    def enable_hsic(self,drv_options):
        
        if self.host_mach.type=="windows":
            grep_cmd = "find"
        else:
            grep_cmd = "grep"
        
        bridging = getkey_value(drv_options, "bridging", False)
        
        if not (bridging):
            wl_commands = [
                           ('sh cat /proc/bus/usb/devices | %s "endor"' % grep_cmd, 0),
                           ('sh rmmod dhd', 3),
                           #('sh bcmdl -t', 1),
                           ('sh dongle_wlregon.sh', 0),
                           ('sh bcmdl /etc/jffs2/dwnldfile.bin', 0),
                           #('sh bcmdl -t', 1),
                           ('sh insmod /lib/modules/dhd.ko', 0),
                           ('sh ifconfig eth1 up', 1),
                           #('sh ifconfig eth1 192.168.1.101', 0),
                           #('up', 0),
                           ]
        else:
            if(self.host_mach.type=="windows"):
                command = 'ipconfig /all'
                ipconfig_all = self.host_mach.cmd(command)
                ipconfig_output = str(ipconfig_all[0])           
                d = {} #dictionary used to store all the data
                adapter = None
                for line in ipconfig_output.splitlines():
                    line = line.strip() 
                    if not line: #igonore the empty lines
                        continue

                    if line.startswith("Ethernet adapter"):
                        z = re.split(":" , line)
                        adapter = z[0]
                        d[adapter] = {}
                    else:
                        if(adapter != None):
                            if( line.find(':') != -1 ): 
                                no_of_values=0 #make the number of values in each field 0
                                values = { } # empty dictionaly to store the vaues of each field. values can be one or more
                                toks = re.split(":",line)
                                key = toks[0].replace(".", " ").strip()
                                values[no_of_values] = toks[1].strip()
                                d[adapter][key] = values
                            else:                             # This is a line continuation and has values in more than one line                                                   
                                no_of_values += 1             # DNS Servers . . . . . . . . . . . : 10.17.21.20
                                values[no_of_values] = line   #                                      10.17.18.20              
                                d[adapter][key] = values

            # we have all the information stored in the dictionary.Find the one we care about
            for adapter in d:
                if 'IP Address' in d[adapter].keys() or  'IPv4 Address' in d[adapter].keys() : # sometimes there are adapters without IP addres
                    if 'IP Address' in d[adapter].keys():
                        ip_string = 'IP Address' #win XP
                    elif 'IPv4 Address' in d[adapter].keys():
                        ip_string = 'IPv4 Address' #win 7
                    else:
                        raise IOError("Something wrong . Ipconfig \all does not have 'IP Address'(XP) or 'IPv4 Address (WIN7) ")
                    if (d[adapter][ip_string][0].startswith("192.168.1")) :                    
                        mac = d[adapter]['Physical Address'][0]
                        mac = re.sub("-", ":" , mac)
                        self.myprint('For bridging the MAC address found = %s' %(mac ))
                        wireless_ip=re.search('^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' , d[adapter][ip_string][0])
                        wireless_ip = wireless_ip.group(0)
                        self.myprint('For bridging the wireless ip is = %s' %(wireless_ip))
                        break
                else:
                    self.myprint('Passing the Ethernet adapter %s as this one does not have a IP adddress' %(adapter))
                    
            wl_commands = [
                           ('sh cat /proc/bus/usb/devices | %s "endor"' % grep_cmd, 0),
                           ('sh rmmod dhd', 3),
                           ('sh dongle_wlregon.sh', 0),
                           ('sh bcmdl /etc/jffs2/dwnldfile.bin', 1),
                           ('sh insmod /lib/modules/dhd.ko', 1),
                           ('sh dhd proptx 0', 0),
                           ('sh ifconfig eth1 up', 1),
                           ('sh wl wsec 0', 0),
                           ('sh wl wpa_auth 0', 0),
                           ('sh brctl addif br0 eth1', 1),
                           ('promisc 0', 1),
                           ('sh brctl stp br0 off', 1),
                           ('sh sysctl -w net.core.raw_sock_hack=1', 1),
                           ('sh ifconfig eth1 hw ether %s ' % mac , 1),
                           ('sh ifconfig eth1 down', 5),
                           ('sh ifconfig eth1 up', 1), 
                           ('sh cd /tmp; wget ftp://%s//wlmips26' %(wireless_ip), 0),
                           ('sh cd /tmp; chmod +x wlmips26', 0),
                           ('ver', 0),
                           #('sh dhd hsicautosleep 1', 0),
                           ]
            
        self.myprint('Enabling driver on HSIC router board')
        
        # Must assume matching wl.exe is on the path
        for (wl_cmd, sleep_time) in wl_commands:
            self.wl_cmd(wl_cmd, False)
            time.sleep(sleep_time)  
            
        (verstr, code) = self.wl_cmd("ver")
        return verstr

    def disable_hsic(self):

        wl_commands = [('sh wl down', 0),
                       ('sh rmmod dhd', 3),
                       ]

        self.myprint('Disabling driver on HSIC board')
        
        # Have to trust that an appropriate wl command is on the system path
        
        for (wl_cmd, sleep_time) in wl_commands:
            self.wl_cmd(wl_cmd, False)
            time.sleep(sleep_time)  
    
class _sdio_dut_wlan:
    driver_dir = "/usr/local/bin/wl_driver/"
        
    def __init__(self, host_mach, files, printfunc):
        self.host_mach = host_mach
        self.myprint = printfunc
        self.files = files
    def wl_cmd(self, wl_cmd, except_on_error=True, timeout=30):
        cmd_full = "/usr/local/bin/wl %s" % (wl_cmd)
        return self.host_mach.cmd(cmd_full, except_on_error, timeout, True) 
    def enable(self, maxtries, failok, drv_options): 
        verstr = self.enable_sdio(drv_options)
        return verstr
    def disable(self):
        self.disable_sdio()
    def install(self,drv_options):
        self.copy_sdio_files()
        verstr = self.enable_sdio(drv_options)
        return verstr
        
    def copy_sdio_files(self):
        #self.host_mach.exit_root()
        
        self.host_mach.mkdir_wbackup(self.driver_dir, needsroot=True)
        self.host_mach.cmd("chmod a+rwx %s" % self.driver_dir, needsroot=True)
        self.host_mach.chdir(self.driver_dir)
        self.host_mach.sendfileto(self.files['firmware'][0], dstfile=self.host_mach.join(self.driver_dir, "rtecdc.bin"))
        self.host_mach.sendfileto(self.files['nvram'][0], dstfile=self.host_mach.join(self.driver_dir, "nvram.txt"))
        for (src, trgname) in self.files['apps']:
            self.host_mach.sendfileto(src, dstfile=self.host_mach.join(self.driver_dir, trgname))
        # Create system links so default apps are picked up
        self.host_mach.cmd("rm -f /usr/local/bin/wl", needsroot=True)
        self.host_mach.cmd("ln -s %swl /usr/local/bin/wl" % (self.driver_dir), needsroot=True)
        self.host_mach.cmd("rm -f /usr/local/bin/dhd", needsroot=True)
        self.host_mach.cmd("ln -s %sdhd /usr/local/bin/dhd" % (self.driver_dir), needsroot=True)
        self.host_mach.cmd("rm -f /usr/local/bin/wl_server_socket", needsroot=True)
        #Moved to enable_sdio self.host_mach.cmd("ln -s %swl_server_socket /usr/local/bin/wl_server_socket" % (self.driver_dir), needsroot=True)
        #self.host_mach.cmd()
        
    def enable_sdio(self, drv_options):
        ''' When enabling SDIO dut on non-windows we assume we are in the directoy with the approprate files
             the install() routine will ensure this '''
             
        sd_divisor = getkey_value(drv_options, "sd_divisor", 1)
        #ifconfig -a | sed -n 's/^\([^ ]\+\).*/\1/p' | grep eth
        rmmod_cmd = [('rmmod dhd', 3) ]
        insmod_cmd =[ ('insmod dhd.ko sd_divisor=%d' % int(sd_divisor), 0) ]

        wl_commands =  [
                       ('up', 0),
                       ('mpc 0', 0),
                       ]

        self.myprint('Enabling driver on SDIO host')
        
        self.host_mach.chdir(self.driver_dir)
        self.host_mach.mktemp()   # Create and go to
        
        eth_list_before=self.run_DHDcmd_return_ethlist( rmmod_cmd)
        eth_list_after=self.run_DHDcmd_return_ethlist( insmod_cmd)
        eth_new = [aa for aa in eth_list_after if aa not in eth_list_before]
        if len(eth_new) == 1:
            eth_adapter = eth_new[0]
        else:
            raise IOError( "No new Adapter was found after insmod was done . So check . Ethernet Adapters before = %s  Ethernet Adapters before = %s " % (eth_list_before , eth_list_after))
        
        pre_commands = [
               #('rmmod dhd', 3),
               #('insmod dhd.ko sd_divisor=%d' % int(sd_divisor), 0),
               ('/usr/local/bin/dhd -i %s download rtecdc.bin nvram.txt' % (eth_adapter), 0),
               ('ifconfig eth1 192.168.1.101 up', 0),
               ('nohup %swl_server_socket >> %swl_server_socket.log 2>&1 &' % (self.driver_dir, self.driver_dir), 0),
               ]
                       
        
        for (cmd, sleep_time) in pre_commands:
            self.host_mach.cmd(cmd, False, 30, True)
            time.sleep(sleep_time)
        
        for (wl_cmd, sleep_time) in wl_commands:
            self.wl_cmd(wl_cmd, False)
            time.sleep(sleep_time)  
            
        (verstr, code) = self.wl_cmd("ver")
        return verstr

    def disable_sdio(self):
    
        wl_commands = [('down', 0),
                        ] 
        os_commands = [('rmmod dhd', 0),
                       ('killall wl_server_socket', 0),
                       ('killall %swl_server_socket' % self.driver_dir, 0),
                       ]

        self.myprint('Disabling driver on HSIC board')
        
        for (wl_cmd, sleep_time) in wl_commands:
            self.wl_cmd(wl_cmd, False)
            time.sleep(sleep_time) 
            
        for (cmd, sleep_time) in os_commands:
            self.host_mach.cmd(cmd, False, 30, True)
            time.sleep(sleep_time)
            
    def run_DHDcmd_return_ethlist(self,dhd_cmds):
        
        #here we remove dhd  and get the ethernet adapters and then insmod and get the internet adapters again. If there is a new one then return otherwise throw error.
        for (cmd , sleep_time)  in dhd_cmds :
            self.host_mach.cmd(cmd, False, 30, True)
            time.sleep(sleep_time)
        # now get the ethernet apater list ifconfig -a | sed -n 's/^\([^ ]\+\).*/\1/p' | grep eth
        command = " ifconfig -a | cut -c 1-8 | sort | uniq -u | grep eth "
        op=self.host_mach.cmd(command)[0].strip()
        eth_list = [ item.strip() for item in string.split(op , "\n") ]
        return eth_list
        
class _win_dut_wlan:
    DEFAULTSLEEPTIME=5
    def __init__(self, host_mach, address, files, printfunc):
        self.host_mach = host_mach
        self.pcistr = address
        self.myprint = printfunc
        self.files = files
    def wl_cmd(self, wl_cmd, except_on_error=True, timeout=30):
        cmd_full = "wl %s" % (wl_cmd)
        return self.host_mach.cmd(cmd_full, except_on_error, timeout, True) 
    def _fname(self, path):
        mtch = re.match(".*[\\\\/](.*)", path)
        if mtch:
            return mtch.group(1)
        else:
            return path
    def install(self,drv_options): 
        self.myprint("Devcon installing new driver", 1)
        # Note - for this to work I found that the rsh server on windows must NOT be set to run as hidden
        #       - may also require to use the 'run all tests as user' option, not sure
        
        self.disable()    # Just to be safe
        self.delete_win_files()
        self.copy_win_files()
        
        self.host_mach.cmd("cd \"%s\"" % self.host_mach.WINDOWS_DRIVER_DIR)
        ##time.sleep(5)
        try:
            self.host_mach.cmd("devcon update \"%s\" \"%s\"" % (self._fname(self.files['inf'][0]), self.pcistr ), timeout=120 )
        except pexpect.TIMEOUT, Err:
            raise IOError('\nError, devcon update timeout. Please make sure that driver signing' \
                ' check is ignored\n')
        verstr = self.enable(3, False, drv_options)   
        return verstr

    def enable(self, maxtries, failok, drv_options): 
        self.myprint("Enabling driver on host %s, PCI String %s" % (self.host_mach.addr, self.pcistr), 1)

        done = False
        tries = 0
        sleeptime = self.DEFAULTSLEEPTIME 
        while not done:            
            tries += 1
            self.host_mach.cmd("devcon enable \"%s\"" % self.pcistr, False, timeout=120)
            (response, error_code) = self.wl_cmd("ver", False) #Expect error if driver load fails
            if error_code <> 0:
                self.myprint("waiting %d seconds" % sleeptime, 2)
                time.sleep(sleeptime)
                (response, error_code) = self.wl_cmd("ver", False) #Expect error if driver load fails
            if error_code == 0:
                self.myprint("Driver succesfully enabled on try %d" % tries, 2)
                done = True
            elif tries < maxtries: 
                self.myprint("Driver enable failed on try %d. Will try again." % tries, 0)
                sleeptime = self.DEFAULTSLEEPTIME*5   # a little longer each time
                self.myprint("Disabling driver", 2)
                self.host_mach.cmd("devcon disable \"%s\"" % self.pcistr, timeout=120) 
                self.myprint("Waiting %d seconds" % sleeptime, 3) 
                time.sleep(sleeptime)        
            else:
                self.myprint("Driver enable failed on try %d. Aborting" % tries, 0)
                if failok:
                    return ("", "", "", "")
                else:
                    raise IOError("DUT Driver on host machine (%s) could not be enabled" % self.host_mach)
        return response

    def copy_win_files(self):
        ''' Copies the necessary windows file to the host_machine '''
        self.myprint("Copying driver files", 1)

        #--- applications
        dst = self.host_mach.WINDOWS_SYS_DIR
        for (src, trgname) in self.files['apps']:
            self.host_mach.sendfileto(src, dstdir=dst, dstfile=trgname)

        #--- sys file
        if self.interface=="sdio":
            dst = self.host_mach.WINDOWS_DRIVER_DIR+'bcmsddhd.sys'
        else:
            dst = self.host_mach.WINDOWS_DRIVER_DIR
        for src in self.files['sys']:
            self.host_mach.sendfileto(src, dstdir=dst)

        #-- inf file
        dst = self.host_mach.WINDOWS_DRIVER_DIR
        for src in self.files['inf']:
            self.host_mach.sendfileto(src, dstdir=dst)

        #--- nvram file
        dst = self.host_mach.WINDOWS_DRIVER_DIR+'nvram.txt'
        for src in self.files['nvram']:
            self.host_mach.sendfileto(src, dstdir=dst)
                
    def disable(self):
        '''
        This unloads the windows driver for the DUT using the microsoft devcon 
        utility. The driver_string is used to match the appropriate device and 
        needs to be specific enough not to disable anything else (like the 
        Ethernet card for example)
        '''
        self.myprint("Disabling driver on %s" % self.host_mach.addr)

        self.wl_cmd("down", False)
        time.sleep(2)

        self.host_mach.cmd("devcon disable \"%s\"" % self.pcistr, False, timeout=120)
        self.myprint("  waiting %d seconds" % (self.DEFAULTSLEEPTIME), 2)
        time.sleep(self.DEFAULTSLEEPTIME)
        self.myprint("Driver disabled", 1)
        
    def delete_win_files(self):
        files_todelete = [ self.host_mach.WINDOWS_DRIVER_DIR + "*.bin",
                           self.host_mach.WINDOWS_DRIVER_DIR + "*.bin.trx",
                           self.host_mach.WINDOWS_DRIVER_DIR + "nvram*.txt",
                           self.host_mach.WINDOWS_DRIVER_DIR + "*.nvm",
                           self.host_mach.WINDOWS_DRIVER_DIR + "bcm*.sys",
                           self.host_mach.WINDOWS_DRIVER_DIR + "bcm*.inf",
                           self.host_mach.WINDOWS_SYS_DIR + "wl*.exe",
                           self.host_mach.WINDOWS_SYS_DIR + "brcm*.dll",
                           self.host_mach.WINDOWS_SYS_DIR + "dhd*.exe",
                           self.host_mach.WINDOWS_SYS_DIR + "nvserial*.exe",
                           self.host_mach.WINDOWS_SYS_DIR + "wlm*.dll"]

        for fil in files_todelete:
            self.host_mach.cmd("del \"%s\"" % fil, except_on_error=False)

class _winSDIO_dut_wlan(_win_dut_wlan):
    interface="sdio"
    
class _winPCIe_dut_wlan(_win_dut_wlan):
    interface="pcie"
  
class brcm_dut_wlan:
    
    def __init__(self, dut_mach, interface_spec, files):
        self.dut_mach  = dut_mach
        self.addr  = dut_mach.addr
        self.files = files
        interface = interface_spec.lower()
        self.interface = interface
        toks = interface.lower().split("::")
        if interface.startswith('hsic'):
            if len(toks) != 2:
                self.myprint("\nError: For HSIC interface you must specify the IP Address using the format hsic::<IP Address>")
                self.myprint("         For example \"hsic::192.168.1.1\"")
                raise IOError("Invalid HSIC interface specification")
            self.dut = _hsic_dut_wlan(dut_mach, toks[1], self.files, self.myprint)
        elif interface.startswith('sdio') and dut_mach.type == 'windows':
            if len(toks) == 1:
                self.myprint("\nNote: Using default windows PCI Identifier string for BRCM sdio controller: PCI\VEN_14E4&DEV_43F2")
                self.myprint("         To override use \"sdio::<PCI_Identifier>\"")
                self.myprint("         For example to use Arasan use \"sdio::PCI\VEN_1095&DEV_0670\"")
                pci_string = "PCI\VEN_14E4&DEV_43F2"
            elif len(toks) == 2:
                pci_string = toks[1] 
            else:
                self.myprint("\nError: For SDIO interface you must specify PCI Identifier string using the format sdio::<PCI_Identifier>")
                self.myprint("         For example to use Arasan use \"sdio::PCI\VEN_1095&DEV_0670\"")
                raise IOError("Invalid SDIO interface specification")
            self.dut = _winSDIO_dut_wlan(dut_mach, pci_string, self.files, self.myprint)
        elif (interface.startswith('pcie') and dut_mach.type == 'windows') or (interface.startswith('pci') and dut_mach.type == 'windows'):
            if len(toks) == 1:
                self.myprint("\nNote: Using default windows PCI Identifier string for BRCM: PCI\VEN_14E4&DEV_43A0")
                self.myprint("         To override use \"pcie::<PCI_Identifier>\"")
                pci_string = "PCI\VEN_14E4&DEV_43A0"
            elif len(toks) == 2:
                pci_string = toks[1] 
            else:
                self.myprint("\nError: For PCIE interface you must specify PCI Identifier string using the format pcie::<PCI_Identifier>")
                self.myprint("         For example to use default \"pcie::PCI\VEN_14E4&DEV_43A0\"")
                raise IOError("Invalid PCIE interface specification")
            self.dut = _winPCIe_dut_wlan(dut_mach, pci_string, self.files, self.myprint)
        elif interface.startswith('sdio') or interface.startswith('pcie'):
            # Other SDIO platforms - linux etc.
            self.dut = _sdio_dut_wlan(dut_mach, self.files, self.myprint)
        else:
            raise IOError("Unknown interface specification: \"%s\" on machine type \"%s\"" % (interface_spec, dut_mach.type))
            
        self.brd_db = board_records.board_records_db(self.myprint)

    def myprint(self, msg, dbglvl=1):
        msgf = ""
        first = True
        for line in msg.splitlines():
            if first:
                msgf += "%s: %s" % (self.interface, line)
                space = len(self.interface)
                first=False
            else:
                msgf += "\n" + " "*space + "  %s" % (line)
        self.dut_mach.myprint(msgf, dbglvl)
            
    def disable(self):
        self.dut.disable()   # Call interface specific disable function

    def fix_nvram(self, nvram_orig, sn, drv_options):
        ''' corrects the mac address in the nvram file to match the boardid and SN 
            returns the path to the new (temperary) nvram file 
            
            removes the deadman_to if it exists
            '''
        
        appendnvram=getkey_value(drv_options, "appendnvram", None)
        removedeadmanto=getkey_value(drv_options, "removedeadmanto", None)
        nvram_name = path.basename(nvram_orig)
        td = tempfile.mkdtemp()
        nvram_new = path.join(td, nvram_name)
        self.myprint("Creating temp nvram with corrected values at %s" % nvram_new)
        fo = open(nvram_new, 'w')
        try:
            fi = open(nvram_orig, 'r')
            
            try:
                # read the nvram into memory (they are pretty small)
                nv = fi.read() # Read file in as one long string
            finally:
                fi.close()
            
            # get the nvram boardid
            y = re.search("boardtype=(0x[0-9a-f]+)", nv, re.I)
            if y:
                boardid=y.group(1)
            else:
                raise IOError("Could not find boardtype in nvram file %s" % nvram_orig)
            
            # get the nvram macaddr
            y = re.search("macaddr=([0-9:a-f]+)", nv, re.I)
            if y:
                old_mac=y.group(1)
            else:
                raise IOError("Could not find boardtype in nvram file %s" % nvram_orig)
            
            # Compute the correct macaddr
            new_mac = self.gen_mac_id(old_mac, boardid, sn)
            self.myprint("   Replacing macaddr %s -> %s" % (old_mac, new_mac))
            
            # write the new nvram file
            nv_new = nv.replace(old_mac, new_mac)
            
            # Turn off the deadman timer by removing that line
            self.myprint("   Disabling the deadman timer by removing deadman_to=n")
            
            # Figure out which type of line endings we have
            if "\0" in nv_new:
                le="\0"
            if "\r\n" in nv_new:
                le="\r\n"
            elif "\r" in nv_new:
                le="\r"
            elif "\n" in nv_new:
                le="\n"
            else:
                raise IOError("Unknown line ending in nvram")
                
            nv_new_list = nv_new.split(le) # Split by line ending




            nv_new_list = [item for item in nv_new_list if "deadman_to=" not in item] # Keep all lines without "deadman_to="
            
            print("Number of lines in new nvram file %s" % len(nv_new_list))
            if len(nv_new_list) <= 1:
                raise IOError("nvram file format incorrect %s" % nv_new_list)
            
            if appendnvram is not None:
                self.myprint("   Appending \"%s\" to nvram" % appendnvram)
                nv_new_list.insert(0, appendnvram) # Insert at top of list (index 0)
            
            # Join the list items back into one long string to write to file, use proper separator
            fo.write(le.join(nv_new_list)) # Join list back into one lone string to write to file
        finally:
            fo.close()
            
        return nvram_new # A new file we opened at the beginning of the function
                
    def install(self, SN=None, drv_options={}):
    
        removedeadmanto = getkey_value(drv_options, "removedeadmanto", False)
            
        if SN:
            if removedeadmanto:
                # Modify nvram files here before installing
                self.files['nvram'] = [ self.fix_nvram(nvram_in, SN) for nvram_in in self.files['nvram'] ]
            try:
                response = self.dut.install(drv_options)    # Call interface specific install function
                (self.driver_ver, self.driver_date, self.driver_variant, self.driver_majorver, self.driver_minorver) = self.parse_wl_ver_response(response)
            finally:
                for file in self.files['nvram']:
                    try:
                        os.unlink(file)   # Delete the temporary nvram file
                    except:
                        pass
        else:
            response = self.dut.install(drv_options)
            (self.driver_ver, self.driver_date, self.driver_variant, self.driver_majorver, self.driver_minorver) = self.parse_wl_ver_response(response)

    def enable(self, maxtries=3, failok=False , drv_options={}):
        response = self.dut.enable(maxtries, failok , drv_options)  # Call the interface specific enable
        (self.driver_ver, self.driver_date, self.driver_variant, self.driver_majorver, self.driver_minorver) = self.parse_wl_ver_response(response)
        return (self.driver_ver, self.driver_date, self.driver_variant)
        
    def wl_cmd(self, wl_cmd, except_on_error=True, timeout=30):
        return self.dut.wl_cmd(wl_cmd, except_on_error, timeout)
        
    def brdrev_to_hex(self, boardrev):
        ''' Converts a human board rev string into hex 
            accepts formats like:
               X101
               P101
               ES5.2
        '''
        str=None
            
        y = re.match("([xp])([1-9])([0-9][0-9])", boardrev, re.I)
        if y:
            if y.group(1).lower()=="x":
                str="0x0"
            elif y.group(1).lower()=="p":
                str="0x1"
            else:
                raise IOError("Unrecognized boardrev string %s" % boardrev)
            str+="%d%02d" % (int(y.group(2)), int(y.group(3)))   
            
        y = re.match("es([1-9])\.([0-9][0-9]?)", boardrev, re.I)
        if y:
            # This is a customer numbering format
            str="0x1%d%02d" % (int(y.group(1)), int(y.group(2)))

        y = re.match("es([1-9])$", boardrev, re.I)
        if y:
            # This is a customer numbering format
            str="0x1%d00" % (int(y.group(1)))

        y = re.match("p([1-9])", boardrev, re.I)
        if y:
            # This is a custom Mclaren numbering scheme...
            str="0x11%02d" % (int(y.group(1)))
        y = re.match("([1-9])\.([0-9])", boardrev, re.I)
        if y:
            # THis is a simple format ex. 2.1
            str="0x1%d%02d" % (int(y.group(1)), int(y.group(2)))

        if not str:
            raise IOError("Don't know how to map human board rev string (%s) to hex" % boardrev)
            
        self.myprint("Converted human board rev string %s to hex string %s" % (boardrev, str))
        return str
        
    def wl_cmdws(self, cmd):
        ''' Runs a wl command but has the option to switch into simulation mode '''
        if (0):
            # Simulate the command
            self.myprint("   SIM: %s" % cmd)
        else:
            self.dut.wl_cmd(cmd)
        
    def read_cisconfig(self):
        ''' Reads the cisconfig file to determine the full path to the appropriate bare cisfile for this dut '''
        cisdir = "cisfiles"
        ciscfgfile = "cisconfig.csv"
        filename = os.path.join(cisdir, ciscfgfile)
        self.myprint("Loading cis config from file %s" % filename, 1)
        f = open(filename, 'rb')
        y = csv.DictReader(f)
        ciss = {}
        for row in y:
            chip = row["chip"].lower()
            chiprev = row["chiprev"].lower()
            interface = row["interface"].lower()
            if chip not in ciss:
                ciss[chip]={}
            if chiprev not in ciss[chip]:
                ciss[chip][chiprev]={}
            ciss[chip][chiprev][interface]=row["cisfile"]

        chip = self.chip.lower()
        if chip not in ciss:
            raise IOError("Could not find any cisfiles for chip %s in file %s" % (self.chip, filename))
        
        chiprev = self.chiprev.lower()
        if chiprev not in ciss[chip]:
            if "*" not in ciss[chip]:
                raise IOError("Could not find any cisfile for chip %s, chiprev %s in file %s" % (self.chip, self.chiprev. filename))
            else:
                chiprev="*"
            
        if self.interface.startswith("hsic"):
            interface = "hsic"
        elif self.interface.startswith("sdio"):
            interface = "sdio"
        else:
            raise IOError("Unknown interface %s for cisfile extraction" % self.interface)
        if interface not in ciss[chip][chiprev]:
            if "*" not in ciss[interface]:
                raise IOError("Could not find any cisfile for chip %s, chiprev %s, interface %s" % (self.chip, self.chiprev, interface, filename))

        fullpath = os.path.join(os.getcwd(), cisdir, ciss[chip][chiprev][interface])
        return (fullpath)
         
    def program_board_min(self, board):
        ''' Programs the minimum number of parameters to uniquely identify a board (macaddr + boardtype) plus the boardrev.
            Tihs is used by DVT & SVT for board tracking and data tracking 
            
            This mac address MUST already be mangled to include the board type (~macmid) and SN (~maclo)
            
        '''
        macaddr = self.macaddr    # use the current device mac address
        boardrev = board.boardrev # get the boardrev from the board database
        boardid = board.boardid   # Get the boardid from the board database
        
        # Input validity checking
        board_records.check_mac(macaddr)
        
        boardrevhex = self.brdrev_to_hex(boardrev)
        
        y = re.match("0x0?[0-9a-f][0-9a-f][0-9a-f]", boardid, re.I)
        if not y:
            raise IOError("boardid format (%s) not recognized")
        
        # At this point we are happy with our values and will attempt to program...
        if not self.otp_hascis:
            # Doesn't look like otp has ever been programmed so we need to write cisfile
            self.myprint("OTP has never been written before...looking up correct cisfile")
            localcisfile = self.read_cisconfig()
            cisfilesize = os.path.getsize(localcisfile)
            # TODO - check that file size matches the cisfile size
            self.myprint("  Using cisfile of size %d from %s" % (cisfilesize, localcisfile))
            cisfilename = os.path.basename(localcisfile)
            self.dut_mach.sendfileto(localcisfile)  # Sends the file to the temp dir with same name
            self.wl_cmdws("ciswrite %s" % cisfilename)
        if not hasattr(self, "otp_macaddr"):
            self.myprint("OTP writing macaddr=%s" % macaddr)
            self.wl_cmdws("wrvar macaddr=%s" % macaddr)
        if not hasattr(self, "otp_boardrev") :
            self.myprint("OTP writing boardrev=%s" % boardrevhex)
            self.wl_cmdws("wrvar boardrev=%s" % boardrevhex)
        elif (int(boardrevhex, 0) != int(self.otp_boardrev, 0)):
            self.myprint("OTP updating boardrev=%s (was %s)" % (boardrevhex, self.otp_boardrev))
            self.wl_cmdws("wrvar boardrev=%s" % boardrevhex)
        
        if not hasattr(self, "otp_boardid"):
            self.myprint("OTP writing boardid=%s" % boardid)
            self.wl_cmdws("wrvar boardtype=%s" % boardid)
        self.myprint("Done OTP programming. Must enable/disable driver...")
        
    def update_board_rev(self, boardrev):
        boardrevhex = self.brdrev_to_hex(boardrev)
        self.myprint("OTP updating boardrev=%s" % boardrevhex)
        self.wl_cmdws("wrvar boardrev=%s" % boardrevhex)
        
    def gen_mac_id(self, dflt_macaddr, boardid, sn):
        ''' Computes the 'correct' macaddr by taking the current mac address
             and updating the macmid with boardid, and the maclow with sn '''
             
        (oid, macmid_inuse, inuse_sn, inuse_sni) = board_records.check_mac(dflt_macaddr)
        
        # Get the boardid parts ready for mac address
        bd_parts = re.search("0x[012]?([0-9a-f][0-9a-f])([0-9a-f])", boardid.lower())
        if not bd_parts:
            raise IOError("Unrecognized boardid format %s" % self.boardid)
            
        # Get the sn parts ready for mac address
        sn_parts = re.search("([0-9a-f])([0-9a-f][0-9a-f])", "%03x" % int(sn, 0))
        if not sn_parts:
            raise IOError("Unrecognized sn format %s" % sn)
            
        gen_mac_id = "%s:%s:%s%s:%s" % (oid, bd_parts.group(1), bd_parts.group(2), sn_parts.group(1), sn_parts.group(2))
        return gen_mac_id
        
    def _get_macaddr(self, sn, quick=False):
        ''' Gets the DUT mac address. Ideally from OTP or else by driver+SN
                ex. HSIC
                    wl --socket 192.168.1.1 cur_etheraddr
                    cur_etheraddr 00:00:00:C0:FF:EE
                    
                ex. SDIO
                C:\Documents and Settings\hwlab>wl ver 5.90 RC125.120
                wl0: Dec 21 2011 11:46:21 version 5.90.125.120 (WLTEST)

                C:\Documents and Settings\hwlab>wl cur_etheraddr
                cur_etheraddr 00:00:00:C0:FF:EE
        '''
        self.macaddr=None
        # The the mac address in use
        (ether_response, error_lvl_integer) = self.dut.wl_cmd("cur_etheraddr")
        y = re.search("cur_etheraddr[\s\t]+([0-9a-fA-F:]+)", ether_response)
        if not y:
            msg = "Could not determine device mac address"
            self.myprint(msg, 0)
            raise IOError(msg)
        mac_in_use = y.group(1).lower()
        
        try:
            otp_mac = self.otp_macaddr
        except AttributeError:
            otp_mac = None
            
        if otp_mac:
            board_records.check_mac(otp_mac)
            if (otp_mac != mac_in_use):
                self.myprint(otp_mac)
                self.myprint(mac_in_use)
                self.myprint("ERROR: Driver is not taking the mac address from the OTP")
                self.myprint("        PLEASE FIX! Either driver is broken (File PR!) or OTP is not programmed correctly")
                self.myprint("")
                if not quick:
                    time.sleep(10)
         
            (oid_otp, macmid, nvram_sn, nvram_sni) = board_records.check_mac(otp_mac)

        if sn:
            gen_mac_id = self.gen_mac_id(mac_in_use, self.boardid, sn)
            board_records.check_mac(gen_mac_id)  # Probably not necessary, but why not?
        else:
            # Cannot generate our own macid if the SN is not known
            gen_mac_id = None
         
        if otp_mac and otp_mac == mac_in_use:
            self.myprint("MAC address in use is from OTP (%s). Using for board identification" % mac_in_use)
        elif otp_mac:
            self.myprint("Using OTP MAC address for board identification (%s), not the mac address in use" % otp_mac)
            mac_in_use = otp_mac
        else:
            self.myprint("MAC address in use is not from OTP",)
            if not gen_mac_id:
                self.myprint(", and unable to generate unique mac address - try specifying the SN")
                raise IOError("Could not reliably determine boards mac address. Try specifying the SN")
            else:
                # User provided SN and self generate mac address should be adequate.
                self.myprint(", using self generated mac address: %s" % gen_mac_id)
            mac_in_use=gen_mac_id
        
        if gen_mac_id and (mac_in_use != gen_mac_id):
            self.myprint(("CAUTION: generated mac address (%s) doesn't match MAC address (%s) being used" % (gen_mac_id, mac_in_use)))
            
        self.macaddr = mac_in_use
     
    def _get_revinfo(self):
        ''' Gets the mac address based unique id based upon the boardtype_macaddr 
            Adding the board type is necessary since mobility nvrams
            use a common macmid for all boards so mac addresses will collide '''
            
        (revinfo, error_lvl_integer) = self.dut.wl_cmd("revinfo")
        y = re.search("boardid[= ]([0-9a-fA-Fx]+)", revinfo)
        if not y:
            raise IOError("Could not find boardid in revinfo")
        self.boardid = y.group(1).lower()
        
        y = re.search("chipnum[= ]([0-9a-fA-Fx]+)", revinfo)
        if not y:
            raise IOError("Could not find chipnum in revinfo")
        self.chip = y.group(1).lower()
        
        y = re.search("chiprev[= ]([0-9a-fA-Fx]+)", revinfo)
        if not y:
            raise IOError("Could not find chiprev in revinfo")
        self.chiprev = y.group(1).lower()
        
    def _get_otpinfo(self):
        ''' Gets the ATE programmed unique id (manufacturing info) '''
        
        
        try:
            chip = self.chip
        except AttributeError:
            self._get_revinfo()
            chip = self.chip
        
        # Get OTP and extract unique id
        (full_otp, error_lvl_integer) = self.dut.wl_cmd("otpdump")

        (otp_params, otp) = parse_otp_string(full_otp, self.chip, self.interface)
        for param in otp_params:
            # Copy the dictionary values to attributes on ourselves prefixed with _otp
            print("Saving %s=%s" % ("otp_" + param, otp_params[param]))
            setattr(self, "otp_" + param, otp_params[param])
            
        self.myprint("HW/SW Boundry:%s" % self.otp_hwswboundry)
        self.myprint("hwprog=%s, swprog=%s, chipidprog=%s, fuseprog=%s" % (self.otp_hwprog, self.otp_swprog, self.otp_chipidprog, self.otp_fuseprog))
        if self.otp_hascis:
            self.myprint("Has been written with ciswrite")
        else:
            self.myprint("Has not been written with ciswrite")
                
        self.myprint("SDIOHeader:%s" % self.otp_SDIOHeader)
        self.myprint("SDIOExtraHeader:%s" % self.otp_SDIOExtraHeader)
        
        for (tag, id, name, val, i) in CisTuples(otp):
            self.myprint("  Tuple tag=%s, subtag=%s, len=%d, val=%s" % (tag, id, len(val), "".join(val)), 3)
            ## Handle some specefic names.
            if name=="macaddr":
                self.otp_macaddr=":".join(val)
                self.myprint("Found OTP Mac Address = %s" % self.otp_macaddr, 2)
            elif name=="boardrev":
                val.reverse()
                self.otp_boardrev = "0x"+"".join(val)
                self.myprint("Found OTP boardrev = %s" % self.otp_boardrev, 2)
            elif name=="boardid":
                val.reverse()
                self.otp_boardid = "0x"+"".join(val)
                self.myprint("Found OTP boardid = %s" % self.otp_boardid, 2)
            elif name=="vendor":
                self.otp_vendor = val
        self.myprint("got_otp complete", 0)
        
    def read_cis(self):
        ''' Get cisdump. Store in self and return to caller 
            returns a list of two digit hexidecimal strings '''
        (cis_full, error_lvl_integer) = self.dut.wl_cmd("cisdump")    
        #self.myprint("cis=%s" % cis)
        self.cis = parse_cis_string(cis_full)
        return copy(self.cis)
        
    def parse_wl_ver_response(self, ver_response):
        '''
        Parses the drivers response to the ver command
        
        Returns a tuple of (version, timestamp, variant)
            where   version = string describing the full version number (ex. 4.218.175.2)
                    timestamp = time object containting the driver build time
                    variant = string describing the variant of the driver (ex. "BRCM MFGTEST")
        '''
        lines = ver_response.splitlines()
            
        found = False
        for line in lines:
            if re.search("^wl0:", line) is None:
                continue
            found = True
            break

        if not found:
            errmsg = "Couldn't determine driver version"
            self.myprint(errmsg, 0)
            raise IOError(errmsg)
        
        (junk, seperator, restofline) = line.partition(':')
        verstr_start = re.search("version", restofline)
        if verstr_start is None:
            errmsg = "Couldn't find the word version in version string (%s)" % restofline
            self.myprint(errmsg, 0)
            raise IOError(errmsg)
        date_time_str = restofline[0:verstr_start.start()].strip()
        self.myprint("Driver Date String: %s" % date_time_str, 3)
        driver_timestamp = time.strptime(date_time_str, "%b %d %Y %H:%M:%S")
        driver_timestamp_str = time.strftime("%b %d %Y %H:%M:%S", driver_timestamp)
        self.myprint("Driver Date: %s" % driver_timestamp_str, 2)
        
        tail = restofline[verstr_start.end():].strip()
        (verstr, seperator, restofline) = tail.partition(' ')
        if re.match("^[0-9]+\.[0-9]+", verstr) is None:
            errmsg = "Version string (%s) doesn't match expected format" % verstr
            self.myprint(errmsg, 0)
            raise IOError(errmsg)
        majorver_match = re.match("^([0-9]+)\.([0-9]+)", verstr)
        majorver = int(majorver_match.group(1))
        minorver = int(majorver_match.group(2))
        variantstr = restofline.strip()
        variantstr = variantstr.strip().replace(" ", "_")
        if variantstr.find("TOT") >= 0 or variantstr.find("TOB") >= 0:
            self.myprint("Caution: appending date to driver version to distinguish TOT driver build", 1)
            verstr = verstr + "." + time.strftime("%y%m%d%H%M%S", driver_timestamp)
        self.myprint("Driver Version: %s" % verstr, 3)
        self.myprint("Driver major version = %d" % majorver)
        self.myprint("Driver minor version = %d" % minorver)
        self.myprint("Driver Variant: %s" % variantstr, 3)
         
        return (verstr, driver_timestamp, variantstr, majorver, minorver)
        
    def save_board_pavars(self, board):
        ''' Save the pavars currently attached to the "board" object for the current board '''

        self.brd_db.save_board_pavars(self.otp_mfginfo, self.boardid, self.macaddr, board)
    
    def identify_board(self, sn=None, add=False, update=False, quick=False):
        ''' Identifies the current board
            and optionally can add it if it is not
            If sn is provided, will verify that it matches the board record database
            If quick then delays for error messages will be skipped
        '''
        tries = 0
        retry = True
        while retry:
            tries += 1
            retry = False
                
            ## Allows for otp programmer where we need to re-identify the board afterward
            (response, junk) = self.wl_cmd("ver")
            (self.driver_ver, self.driver_date, self.driver_variant, self.driver_majorver, self.driver_minorver) = self.parse_wl_ver_response(response)
            
            board = board_records.boardinfo    # Really just an empty object to attach attributes to from the board database

            # Get the drivers revinfo
            self._get_revinfo()   # Gets information from the driver goes into self.* variables

            # Get the otpinfo
            try:
                self._get_otpinfo()   # Loads the self.otp_* variables
            except IOError:
                self.otp_mfginfo=None
                pass

            # Get the mac address
            self._get_macaddr(sn, quick) # Loads the ""correct"" mac address (munged) into self.macaddr 
                                  #  this is not necessarily the response from "cur_etheraddr"
                 
            # Get the board information from the board database
            found_in_dB = False
            try:
                self.brd_db.get_board_record(self.otp_mfginfo, self.boardid, self.macaddr, board)
                found_in_dB = True
            except board_records.UnknownBoard:
                if add:
                    self.brd_db.add_board(self.otp_mfginfo, self.chip, self.chiprev, self.boardid, self.macaddr, sn)
                    self.brd_db.get_board_record(self.otp_mfginfo, self.boardid, self.macaddr, board)
                else:
                    # Since not in database we must assume the driver is correct, or take defaults
                    board.chip = self.chip
                    board.chiprev = self.chiprev
                    board.boardid = self.boardid
                    board.serialnum = sn
                    board.chiprevname=None
                    board.boardtype=None
                    board.boardsubtype=None
                    board.boardrev=None
                    board.devcfgfilecomments=None
                    pass
            
            # A LOT OF SANITY CHECKS...
            Warnings = False
            
            if found_in_dB:
                ## Check SN if provided against board database
                if sn:
                    if int(board.serialnum, 0) != int(sn, 0):
                        raise board_records.UnknownBoard("Serial Number Mismatch (expected=%s, database=%s)" % (sn, board.serialnum))
                    else:
                        self.myprint("Board uniqueid found in database and serial number matches", 1)
                else:
                    pass
                    #self.myprint("Board mac address found in database", 1)
           
                ## Check the driver against the board database
                if int(self.chip, 0) != int(board.chip, 0):
                    self.myprint("WARNING: chip (%s) doesn't match database (%s)" % (self.chip, board.chip))
                    Warnings=True
                if int(self.chiprev, 0) != int(board.chiprev, 0):
                    self.myprint("WARNING: chiprev (%s) doesn't match database (%s)" % (self.chiprev, board.chiprev))
                    Warnings=True
                if int(self.boardid, 0) != int(board.boardid, 0):
                    self.myprint("WARNING: boardid (%s) doesn't match database (%s)" % (self.boardid, board.boardid))
                    Warnings=True
            
            ## Check THE DVT OTP values against the driver
            try:
                if int(self.boardid, 0) != int(self.otp_boardid, 0):
                    self.myprint("WARNING: Board ID in use (%s) does not match OTP Board ID (%s)" % (self.boardid, self.otp_boardid))
                    Warnings=True
            except AttributeError:
                pass
            
            NeedsOTPRevUpdate = False
            boardrevhex = None
            try:
                boardrevhex = self.brdrev_to_hex(board.boardrev)
                if int(boardrevhex, 0) != int(self.otp_boardrev, 0):
                    self.myprint("WARNING: Board Rev in use (%s) does not match OTP Board Rev (%s)" % (boardrevhex, self.otp_boardrev))
                    # This is not marked as a warning because it is expected when board rev changes
                    NeedsOTPRevUpdate = True
            except AttributeError:
                pass
            except TypeError:
                pass
                
            try:
                if self.macaddr != self.otp_macaddr:
                    self.myprint("WARNING: MAC Address in use (%s) does not match OTP MAC Address (%s)" % (self.macaddr, self.otp_macaddr))
                    Warnings=True                
            except AttributeError:
                pass
                        
            self.myprint("sn%s: chip=%s, chiprev=%s=%s" % (board.serialnum, board.chip, board.chiprev, board.chiprevname), 1)
            self.myprint("        brdtype=%s=%s, subtype=%s, rev=%s=%s, comment=%s" % 
                (board.boardid, board.boardtype, board.boardsubtype, board.boardrev, boardrevhex, 
                board.devcfgfilecomments), 1)
                
            self.myprint("", 2)
            self.myprint("Checking OTP information...", 2)
            hasAll=True
            if not hasattr(self, "otp_hascis"):
                self.myprint("  Don't know if OTP has been written with ciswrite", 1)
            elif not self.otp_hascis:
                self.myprint("  OTP has not been written with ciswrite", 1)
                hasAll=False
            try:
                self.otp_macaddr
            except AttributeError:
                hasAll=False
                self.myprint("  OTP is missing macaddr", 1)
            try:
                self.otp_boardrev
            except AttributeError:
                hasAll=False
                self.myprint("  OTP is missing boardrev", 1)
            try:
                self.otp_boardid
            except AttributeError:
                hasAll=False
                self.myprint("  OTP is missing boardid", 1)
            
            if not hasAll:
                self.myprint("OTP does not contain all data expected for DVT/SVT/Coex test", 1)
                if tries >= 2:
                    raise IOError("OTP programming did not seem to take. Please debug.")
                
                if not add:
                    self.myprint("   This is non-interactive run so I will not program OTP. Please run in interactive mode to have OTP programmed", 1)
                elif Warnings:
                    self.myprint("   There were board identification related warnings so I will not program OTP. Please correct and rerun so OTP can be programmed", 1)
                else:
                    final_choice = raw_input("Do you want to OTP program this board (y/n)? ").strip().lower()
                    if final_choice in [ "y", "yes", "ok", "sure", "si", "oui" ]:
                        self.program_board_min(board)
                        self.disable()
                        self.enable()
                        retry = True  # Force the board to be re-identified.
            elif NeedsOTPRevUpdate:
                self.myprint("OTP has all data required, but board rev needs to be updated", 1)
                if add:
                    final_choice = raw_input("Do you want to update OTP board rev (y/n)? ").strip().lower()
                    if final_choice in [ "y", "yes", "ok", "sure", "si", "oui" ]:
                        self.update_board_rev(board.boardrev)
                        self.disable()
                        self.enable()
                        retry = True  # Force the board to be re-identified.
                else:
                    self.myprint("   This is non-interactive run so I will not update OTP boardrev. Please run in interactive mode to have OTP updated", 1)
            else:
                self.myprint("   OTP is good to go", 2)
                self.myprint("", 2)
                    
        return (board, found_in_dB)
 
class DLException(Exception):
    def __init__(self, sstr):
        self.sstr = sstr
    def __str__(self):
        return repr('DLException: ' + self.sstr)

def stdaloneprint(mstr, dbglevel=2):
    global lcldbglevel   
    if dbglevel <= lcldbglevel:
        print(mstr)

def main():
    import sys, readline
    import optparseWrap as optparse

    parser = optparse.OptionParser()
    parser.usage = '''%prog [options]

    Install driver for SDIO or HSIC 
    '''

    #--- required --------------------------------------------------------------
    parser.add_option('', '--board', dest='board',
                      default=None, 
                      help='The type of device to load driver for - must correspond to a file in boards directory')
    parser.add_option('', '--drv_cfg', dest='drv_cfg',
                      default=None, 
                      help='The specific driver configuration - must correspond to a section in the boards config file')                      
    parser.add_option('', '--interface', dest='interface',
                      default=None, 
                      help='Type of DUT device and address to load driver for, HSIC::<IP> or SDIO(::<PCI_Identifier>)')
    
    #--- optional or needed if not specified in the config file ----------------
    group = optparse.OptionGroup(parser, 'Optional', 'Optional for overriding the values specfified in the configuration file or needed when not specified in the file')
    group.add_option('', '--tag', dest='tag',
                      default='', 
                      help='User specified driver tag, to override default or use if no tag is specified in the config file')
    group.add_option('', '--brand', dest='brand',
                      default='',
                      help='User specified brand, to override default or use if no brand is specified in the config file')
    parser.add_option_group(group)

    #--- optional --------------------------------------------------------------
    group = optparse.OptionGroup(parser, 'Optional','These are optional')
    group.add_option('','--date', dest='date',
                     default='',
                     help='Specify date of driver')
    group.add_option('', '--cfgdir', dest='cfg_dir',
                      default='./drv_cfgs', 
                      help='Directory path to config file if other than default (./drv_cfgs)')
    group.add_option('', '--test', dest='test',
                      default=False, action='store_true',
                      help='Just find the driver files, do not install')
    group.add_option('', '--SN', dest='sn',
                      default=None, action="store", 
                      help='Expected device serial number for verify')
    group.add_option('-H', '', dest='host',
                      default=None, action="store", 
                      help='Host PC for the DUT (DUT PC)')                  
    group.add_option('', '--verify', dest='verify',
                      default=False, action="store_true",
                      help='verify board is known by board database and add if not')
    group.add_option('', '--identify', dest='identify',
                      default=False, action="store_true",
                      help='identify the current board by querying for its uniqueid and matching to database')
    group.add_option('', '--enable', dest='enable',
                        default=False, action="store_true",
                        help='enable windows SDIO driver')
    group.add_option('', '--disable', dest='disable',
                        default=False, action="store_true",
                        help='disable driver')
    group.add_option('', '--install', dest='install',
                        default=False, action="store_true",
                        help='install a driver')
    group.add_option('', '--tries', dest='tries',
                        default=3, action="store", type="int",
                        help='number of tries (when enabling driver)')
    group.add_option('', '--enablefailok', dest='enablefailok',
                        default=None, action="store_true",
                        help="Driver load failure is ok - don't throw exception if driver doesn't load correctly")    
    group.add_option('', '--noprompt', dest='noprompt',
                        default=False, action="store_true",
                        help="Disable all user prompts to guarantee unattended operation")
    group.add_option('', '--removedeadmanto', dest='removedeadmanto',
                        default="False", type="string",
                        help="remove the deadmanto value from the nvram file (boolean)")
    group.add_option('', '--appendnvram', dest='appendnvram',
                        default=None, type="string",
                        help="Append this line to the nvram file")
    group.add_option('', '--bridging', dest='bridging',
                    default=False, action="store_true",
                    help="Enables bridging while laoding the driver")
    group.add_option('', '--parsecisfile', dest='parsecisfile',
                    default=None, action="store", type="string",
                    help="Parse cis file into human readable form")
    group.add_option('', '--sd_divisor', dest='sd_divisor',
                    default=1, action="store", type="int",
                    help="For SDIO devices, the sd_divisor to use (Currently Linux Only)")
    parser.add_option_group(group)
    
    parser.add_option('-v', '', dest='verbosity',
                      default=2, action="count",
                      help='Increase output verbosity')
    
    (options, args) = parser.parse_args()

    global lcldbglevel
    lcldbglevel = options.verbosity
    # Some commands that don't actually need a dut...
    donesomething = True
    if options.parsecisfile:
        print("Parsing CIS information form %s" % options.parsecisfile)
        cis_full = open(options.parsecisfile, 'r').read()
        print("cis_full=%s" % cis_full)
        cis = parse_cis_or_otp_string(cis_full, chip=None, interface=options.interface)
        print("cis=%s" % cis)
        print_tuples(cis)
        
    else:
        donesomething = False
    
    if not options.verify \
        and not options.enable \
        and not options.disable \
        and not options.identify \
        and not options.install:
        # If we haven't don't anything yet, and now dut action requested this is a usage error
        
        if not donesomething:
            stdaloneprint("")
            parser.print_help()
            stdaloneprint("")
            sys.exit(1)
        else:
            sys.exit(0)
    
    # Stored as a string, convert to boolean now
    if options.removedeadmanto.lower() in ["t", "1", "true", "yes", "y", "si"]:
        options.removedeadmanto = True
    elif options.removedeadmanto.lower() in ["f", "0", "false", "no", "n"]:
        options.removedeadmanto = False
    else:
        print("--removedeadmanto requires a boolean value; 0, 1, true, false, yes, no, etc.")
        usage_error = True

    usage_error = False
    if options.test:
        pass
    if not options.interface:
        options.interface="sdio"
        stdaloneprint("CAUTION: Assuming SDIO since no interface specified (should auto-lookup station default)")
    elif options.interface=="hsic":
        options.interface="hsic::192.168.1.1"
        stdaloneprint("CAUTION: Assuming HSIC router board ip address=192.168.1.1")
    if not options.host:
        options.host="localhost"
        stdaloneprint("CAUTION: Assuming localhost since no host specified (this is usually wrong - should be auto-looking up the dut)")
    if options.verify and not options.sn:
        stdaloneprint("Require serial number (--SN) in order to verify board")
        usage_error = True
    if options.identify and options.sn:
        stdaloneprint("WARNING: Trying to identify a board when specifying a SN can report erronouse results if the mac address")
        stdaloneprint("         is not programmed into otp (because the user provided SN is used to generate the mac address and thus")
        stdaloneprint("         form the board identification string)")
        time.sleep(2)
    if not (options.install or options.enable or options.disable or options.verify or options.identify):
        stdaloneprint("Must request an action (install, enable, disable, verify, or identify")
        usage_error = True
    if options.install and not (options.board and options.drv_cfg):
        print("You must specify a board type and drv_cfg to install a driver")
        usage_error = True
        
    if usage_error:
        stdaloneprint("")
        parser.print_help()
        stdaloneprint("")
        sys.exit(1)

    #---------------------------------------------------------------------------
    #--- find driver files and install them ------------------------------------

    dm = dut_mach(options.host, stdaloneprint)
    dm.connect()
    fdp = None
    if options.install:
        fdp = drv_files(stdaloneprint)
        fdp.load_cfg(options.board, options.drv_cfg)
        fdp.proc_user_conf(tag = options.tag,
                           dut_mach_type = dm.type,
                           uname = dm.uname,
                           interface = options.interface.lower(),
                           brand = options.brand,
                           date = options.date)
        fdp.find_files()

    if options.test:
        sys.exit(0)

    if fdp:
        idf = brcm_dut_wlan(dm, options.interface, fdp.run_files)
    else:
        idf = brcm_dut_wlan(dm, options.interface, None)
    
    # Copy the drv_load_options to their own dictionary.
    drv_options = {}
    for parameter in ["bridging", "sd_divisor", "appendnvram", "removedeadmanto"]:
        drv_options[parameter] = getattr(options, parameter)
        
    if options.parsecisfile:
        cis_full = open(self.options.parsecisfile, 'r').read()
        parse_cis_string(cis_full)
    if options.disable:
        idf.disable()
    if options.install:
        idf.install(options.sn, drv_options)
    if options.enable:
        idf.enable(options.tries, options.enablefailok , drv_options )
    if options.identify:
        try:
            idf.identify_board(options.sn, False)
        except board_records.UnknownBoard:
            sys.stderr.write("Board not found in board databse - could not be identified\n")
            sys.exit(-2)
    if options.verify:
        try:
            idf.identify_board(options.sn, not options.noprompt)
        except board_records.UnknownBoard:
            sys.stderr.write("Board not found in board databse - could not be verified\n")
            sys.exit(-3)

if __name__ == '__main__':
    main()
    
