#!/bin/bash

if [ $# -lt 1 ] ; then
    echo "Use: ld_drv.sh <dut number>"
    echo "no dut specificed, exiting..."
    exit 0
fi

IP='192.168.1.'$1
BLD='/projects/hnd/swbuild/build_linux/'


#TRX='PHOENIX2_REL_6_10_56_189/linux-mfgtest-dongle-usb/2012.8.20.1/release/bcm/firmware/4334b0-ram/usb-ag-mfgtest-nodis-oob-seqcmds-srsusp-srfast-sr.bin.trx'
#NVR='PHOENIX2_REL_6_10_56_189/linux-mfgtest-dongle-usb/2012.8.20.1/src/shared/nvram/bcm94334CentMurataMSE.txt'

TRX='PHOENIX2_REL_6_10_56_183/linux-mfgtest-dongle-usb/2012.8.5.0/release/bcm/firmware/4334b0-ram/usb-ag-mfgtest-nodis-oob-seqcmds-srsusp-srfast-sr.bin.trx'
NVR='PHOENIX2_REL_6_10_56_183/linux-mfgtest-dongle-usb/2012.8.5.0/src/shared/nvram/bcm94334CentMurataMSE.txt'


#TRX='PHOENIX2_REL_6_10_56_185/linux-mfgtest-dongle-usb/2012.8.8.0/release/bcm/firmware/4334b0-ram/usb-ag-mfgtest-nodis-oob-seqcmds-srsusp-srfast-sr.bin.trx'
#NVR='PHOENIX2_REL_6_10_56_185/linux-mfgtest-dongle-usb/2012.8.8.0/src/shared/nvram/bcm94334CentMurataMSE.txt'

wl --socket $IP sh rmmod /lib/modules/dhd

wl --socket $IP hsic_download $BLD$TRX $BLD$NVR

wl --socket $IP sh bcmdl /etc/jffs2/dwnldfile.bin
wl --socket $IP sh insmod /lib/modules/dhd.ko
wl --socket $IP sh ifconfig eth1 192.168.3.2 up
wl --socket $IP sh wl up
wl --socket $IP sh wl ver

#/projects/hnd/swbuild/build_linux/PHOENIX2_REL_6_10_56_93/linux-mfgtest-dongle-usb/2012.5.16.0/release/bcm/firmware/4334b1-roml/
