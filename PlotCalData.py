#!/usr/bin/env python
import numpy as np
import csv
import os
import glob
import sys
import urllib
import cProfile
import pstats

from matplotlib.font_manager import FontProperties
from matplotlib.figure import Figure
from matplotlib.patches import Polygon
from matplotlib import cm as cm
from matplotlib.backends.backend_agg import FigureCanvasAgg

BASE_PATH = reduce (lambda l,r: l + os.path.sep + r, os.path.dirname( os.path.realpath( __file__ ) ).split( os.path.sep )[:-1] )
GENREP_PATH = os.path.join(BASE_PATH, "dvt_report")
sys.path.append( GENREP_PATH )

from GenReport import *

CalCols = {
    "DUT Type":-1,
    "DUT Number":-1,
    "Path Type":-1,
    "Path Number":-1,
    "Frequency":-1,
    "Offset (dB)":-1,
}

class AutoVivification(dict):
    """Implementation of perl's autovivification feature."""
    def __getitem__(self, item):
        try:
            return dict.__getitem__(self, item)
        except KeyError:
            value = self[item] = type(self)()
            return value

class MFGcCalDataReport(GenReport): 

    def __init__(self, dir, dbglvl=3):
        GenReport.__init__(self, dir, dbglvl)
        self.CalData = None
        self.rptdir = dir
        self.mainfilename = "report.htm"
        self.title = "MFGc Cal Report"
        self.imgs = []
            
    def loaddata(self, filenames):
        ''' Loads the data from a list of MFGc Cal Files '''
        CalData = AutoVivification()
        for file in filenames:
            if file.startswith("http"):
                f = urllib.urlopen(file)
            else:
                f = open(file)
            CalReader = csv.reader(f, delimiter=',', quotechar='"')
            tracedata = []
            xvals = []
            yvals = []
            legend = None
            src = os.path.basename(file)
            # REV5 - Wed Jun 20 14:08:35 2012,,,,,,
            # DUT Type, DUT Number, Path Type, Path Number, Frequency, OBT RF Amp, Offset (dB)
            
            header = True # Until we find columns we are in header info land...
            for line in CalReader:
                lineno = CalReader.line_num
                line = [el.replace("#", "").strip() for el in line]
                #print "Parsing line %d %s" % (lineno, line)
                if header:
                    if line[0].lower()=="dut type":
                        # Found the column headers
                        for el in CalCols:
                            try:
                                i = line.index(el)   # Will raise ValueError if not found
                                CalCols[el] = i
                            except ValueError:
                                if el=="DUT Number":
                                    pass
                                else:
                                    print("Column %s missing from header line %d in Cal file %s" % (el, lineno, file))
                                    raise
                        header = False
                    continue
                # If we get here we should be in data
                duttype = line[CalCols["DUT Type"]]
                if CalCols["DUT Number"] >= 0:
                    dutnum = line[CalCols["DUT Number"]]
                else:
                    dutnum = 0
                pathtype = line[CalCols["Path Type"]]
                pathnum = line[CalCols["Path Number"]]
                freq = line[CalCols["Frequency"]]
                offset = line[CalCols["Offset (dB)"]]
                band = "2g" if int(freq) < 3000 else "5g"
                
                if not "freq" in CalData[band][duttype][dutnum][pathtype][pathnum][src]:
                    CalData[band][duttype][dutnum][pathtype][pathnum][src]["freq"] = []
                    CalData[band][duttype][dutnum][pathtype][pathnum][src]["offset"] = []

                #print "Saving cal data %s %s %s %s %s %s = %s, %s" % (band, duttype, dutnum, pathtype, pathnum, src, freq, offset)
                CalData[band][duttype][dutnum][pathtype][pathnum][src]["freq"].append(freq)
                CalData[band][duttype][dutnum][pathtype][pathnum][src]["offset"].append(offset)
            f.close()
            
        self.CalData = CalData
        self.filenames = filenames
   
    def plot_all(self, page):
        ''' Plots all the MFGc Cal data on a single HTML page '''
        CalData = self.CalData
        for band in CalData:
            page.addHeading1("Band %s" % band)
            for duttype in CalData[band]:
                page.addHeading2("Dut Type %s" % duttype)
                for dutnum in CalData[band][duttype]:
                    page.addHeading3("Dut Number %s" % dutnum)
                    for pathtype in CalData[band][duttype][dutnum]:
                        tracedata = []
                        title = ", ".join([band, duttype, "Dut Number %s" % dutnum, pathtype])
                        print "title=%s" % title
                        for pathnum in CalData[band][duttype][dutnum][pathtype]:
                            for src in CalData[band][duttype][dutnum][pathtype][pathnum]:
                                xvals = CalData[band][duttype][dutnum][pathtype][pathnum][src]["freq"]
                                yvals = CalData[band][duttype][dutnum][pathtype][pathnum][src]["offset"]
                                legend = ", ".join([src, "Path %s" % pathnum])
                                tracedata.append( {
                                    "xvals":np.array(xvals),
                                    "yvals":np.array([float(el) for el in yvals]),
                                    "limnums":[],
                                    "legend":legend,
                                } )
                        # Do plot here
                        xname = "Frequency (MHz)"
                        yname = "Offset (dB)"
                        limdata = []
                        self._addGraph(page, "", title, xname, yname, tracedata, limdata, True, "NA", None, None)
    
    def gen_report(self):
        self.myprint("\nGenerating Report")
        f = open(os.path.join(self.rptdir, self.mainfilename), 'w')  # open early so we can abort on error

        mainpage = Page(self.title)
        try:
            subtitle = self.filenames
            mainpage.addBRCMTitle(self.title, subtitle, time.strftime("%a, %b %d, %Y  %H:%M:%S"))
            
            self.plot_all(mainpage)
            
        finally:
            f.write(mainpage.tostring() )
            self.myprint("Report location: http://www.sj.broadcom.com%s" % os.path.abspath(os.path.join(self.rptdir, self.mainfilename)))


def main():
    if (len(sys.argv) > 1):
        InputFiles = sys.argv[1:]
    else:
        print "Please enter some cal file name to plot on command line"
        sys.exit(-1)

    (base, ext) = os.path.splitext(InputFiles[-1])
    if not ext:
        # Last filaname does NOT have extension - it is report directory name
        ReportDir = InputFiles.pop()
    else:
        # Last filename DOES have extenstion - make up the report dir name
        (base, ext) = os.path.splitext(InputFiles[0])
        print "Base = %s" % base
        ReportDir = base
               
    y = MFGcCalDataReport(ReportDir, 2)
    y.loaddata(InputFiles)
    y.gen_report()

if __name__ == '__main__':
    main()
    #cProfile.run("main()", "reportprof")
    #p = pstats.Stats('reportprof')
    #print("")
    #print("Top 10 Functions by Cumulative Time")
    #p.sort_stats('cumulative').print_stats(10)
    #print("")
    #print("Top 10 Functions by Time in function")
    #p.sort_stats('time').print_stats(10)
    

 




    
