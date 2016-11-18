set IP=192.168.1.1
set BLD=\projects\hnd\swbuild\build_linux\


REM TRX='PHOENIX2_REL_6_10_56_189\linux-mfgtest-dongle-usb\2012.8.20.1\release\bcm\firmware\4334b0-ram\usb-ag-mfgtest-nodis-oob-seqcmds-srsusp-srfast-sr.bin.trx'
REM NVR='PHOENIX2_REL_6_10_56_189\linux-mfgtest-dongle-usb\2012.8.20.1\src\shared\nvram\bcm94334CentMurataMSE.txt'

REM set TRX='PHO2203RC1_REL_6_25_27\linux-mfgtest-dongle-usb\2012.3.28.0\release\bcm\firmware\4334b0-ram\usb-ag-mfgtest-nodis-swdiv-oob-seqcmds-srsusp-srfast-sr.bin.trx'
set TRX=PHO2203RC1_REL_6_25_27\linux-mfgtest-dongle-usb\2013.3.28.0\release\bcm\firmware\4334b0-ram\usb-ag-mfgtest-nodis-swdiv-oob-seqcmds-sr.bin.trx
set NVR=PHO2203RC1_REL_6_25_27\linux-mfgtest-dongle-usb\2013.3.28.0\src\shared\nvram\bcm94334CentMurataMSE.txt

REM TRX='PHO2203RC1_REL_6_178_39\linux-mfgtest-dongle-usb\2012.3.28.0\release\bcm\firmware\4334b0-ram\usb-ag-mfgtest-nodis-oob-seqcmds-srsusp-srfast-sr.bin.trx'
REM NVR='PHO2203RC1_REL_6_178_39\linux-mfgtest-dongle-usb\2012.3.28.0\src\shared\nvram\bcm94334CentMurataMSE.txt'

REM TRX='PHOENIX2_REL_6_10_56_185\linux-mfgtest-dongle-usb\2012.8.8.0\release\bcm\firmware\4334b0-ram\usb-ag-mfgtest-nodis-oob-seqcmds-srsusp-srfast-sr.bin.trx'
REM NVR='PHOENIX2_REL_6_10_56_185\linux-mfgtest-dongle-usb\2012.8.8.0\src\shared\nvram\bcm94334CentMurataMSE.txt'

dir %BLD%%TRX%
REM pause

wl --socket %IP% sh rmmod /lib/modules/dhd

wl --socket %IP% hsic_download %BLD%%TRX% %BLD%%NVR%

wl --socket %IP% sh bcmdl /etc/jffs2/dwnldfile.bin
wl --socket %IP% sh insmod /lib/modules/dhd.ko
wl --socket %IP% sh ifconfig eth1 192.168.3.2 up
wl --socket %IP% sh wl up
wl --socket %IP% sh wl ver

REM Z:\projects\hnd\swbuild\build_linux\PHOENIX2_REL_6_10_56_93\linux-mfgtest-dongle-usb\2012.5.16.0\release\bcm\firmware\4334b1-roml\
REM wl --socket 192.168.1.1 hsic_download \projects\hnd\swbuild\build_linux\PHO2203RC1_REL_6_25_27\linux-mfgtest-dongle-usb\2013.3.28.0\release\bcm\firmware\4334b0-ram\usb-ag-mfgtest-nodis-swdiv-oob-seqcmds-sr.bin.trx \projects\hnd\swbuild\build_linux\PHO2203RC1_REL_6_25_27\linux-mfgtest-dongle-usb\2013.3.28.0\src\shared\nvram\bcm94334CentMurataMSE.txt

