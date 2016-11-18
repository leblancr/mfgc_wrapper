#!/bin/sh

PATH=$PATH:/sbin

#export KERNEL_DRIVER_DIR=/projects/hnd_swbuild/build_linux/PHOENIX2_REL_6_10_56_26/linux-mfgtest-dongle-usb/2012.2.2.0/
export KERNEL_DRIVER_DIR=/projects/hnd_swbuild/build_linux/PHOENIX2_REL_6_10_56_85/linux-mfgtest-dongle-usb/2012.4.27.0/

export DRIVER_DIR=$KERNEL_DRIVER_DIR

export TOOL_DIR=$KERNEL_DRIVER_DIR
export wl=$TOOL_DIR/release/bcm/apps/wl
echo $wl

echo "Setup GPIO Signals"
$wl --socket 192.168.1.1 sh "echo 0 > /sys/class/bcmgpio/export"
$wl --socket 192.168.1.1 sh "echo 2 > /sys/class/bcmgpio/export"

$wl --socket 192.168.1.1 sh "echo out > /sys/class/bcmgpio/gpio0/direction"
$wl --socket 192.168.1.1 sh "echo out > /sys/class/bcmgpio/gpio2/direction"

$wl --socket 192.168.1.1 sh "echo 0 > /sys/class/bcmgpio/gpio0/value"
$wl --socket 192.168.1.1 sh "echo 0 > /sys/class/bcmgpio/gpio2/value"

$wl --socket 192.168.1.1 sh "echo 1 > /sys/class/bcmgpio/gpio0/value"

echo "Download firmware to router board"
#$wl --socket 192.168.1.1 hsic_download $DRIVER_DIR/release/bcm/firmware/4334b0-ram/usb-ag-mfgtest-nodis-seqcmds-srfast-sr.bin.trx $KERNEL_DRIVER_DIR/src/shared/nvram/bcm94334OlympicCent_usi_st.txt
#$wl --socket 192.168.1.1 hsic_download $DRIVER_DIR/release/bcm/firmware/4334b1-roml/usb-ag-mfgtest-nodis-seqcmds.bin.trx $KERNEL_DRIVER_DIR/src/shared/nvram/bcm94334OlympicCent_murata_mm.txt

#$wl --socket 192.168.1.1 hsic_download /projects/hnd_software_ext9/work/pgarg/01PMDemo4334/PHOENIX2_BRANCH_6_10/rtecdc_4334b1_srfast_idsup.bin.trx $KERNEL_DRIVER_DIR/src/shared/nvram/bcm94334OlympicCent_murata_mm.txt

#$wl --socket 192.168.1.1 hsic_download /projects/hnd_software_ext9/work/pgarg/01PMDemo4334/PHOENIX2_BRANCH_6_10/rtecdc_4334b1_srfast_idsup.bin.trx bcm94334OlympicCent_murata_mm.txt

$wl --socket 192.168.1.1 hsic_download /projects/hnd_software_ext9/work/pgarg/01PMDemo4334/PHOENIX2_BRANCH_6_10/rtecdc_4334b1_srfast_idsup.bin.trx $KERNEL_DRIVER_DIR/src/shared/nvram/bcm94334OlympicCent_murata_mm.txt

$wl --socket 192.168.1.1 sh rmmod dhd
sleep 1
$wl --socket 192.168.1.1 sh bcmdl -t

echo "download firmware to router"
$wl --socket 192.168.1.1 sh bcmdl /etc/jffs2/dwnldfile.bin

echo "insert kernel module"
$wl --socket 192.168.1.1 sh insmod /lib/modules/dhd.ko

echo "bring up interface"
$wl --socket 192.168.1.1 sh ifconfig eth1 192.168.3.2 up

$wl --socket 192.168.1.1 ver


# turn off BT
#$wl --socket 192.168.1.1 sh dhd gpio 2 0
#$wl --socket 192.168.1.1 up
#$wl --socket 192.168.1.1 MPC 
#sleep 1
#$wl --socket 192.168.1.1 join mforbes-test-2p4
#$wl --socket 192.168.1.1 join Broadcom
#sleep 4
#$wl --socket 192.168.1.1 status

#$wl --socket 192.168.1.1 sh sysctl -w net.core.lpm_state=1
#$wl --socket 192.168.1.1 sh sysctl -w net.core.lpm_hird=15
#$wl --socket 192.168.1.1 sh dhd hsicsleep 1

$wl --socket 192.168.1.1 sh dhd hsicautosleep 1


