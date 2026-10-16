## Existing TinyUF2 deployment

The instructions below describe the ordinary TinyUF2 configuration only. Do not use them to install or recover an Mboot build: the sdphost commands write a different boot image, and the UF2 drive workflow is disabled in Mboot builds.

The Seeed Arch Mix board is delivered without firmware. The best option to
install MicroPython is installing a UF2 bootstrap loader first, which then can be
used to install and update MicroPython. The bootloader has to be installed
only once.

For initial deployment of the bootloader a few preparation steps are required, which
have to be done once.

1. Get the NXP program sdphost for your operating system, e.g. from
https://github.com/adafruit/tinyuf2/tree/master/ports/mimxrt10xx/sdphost.
You can also get them from the NXP web sites.

2. Get the UF2 boot-loader package https://github.com/adafruit/tinyuf2/releases/tag/0.20.1/tinyuf2-imxrt1050_evkb-0.21.0.zip and extract the files `tinyuf2-imxrt1050_evkb-0.21.0.bin`.
You may as well go for a newer release.

Now you have all files at hand that you will need for updating.

1. Get the firmware file you want to upload  with the .uf2 extension from the MicroPython download page.

2. At the Seeed Arch Mix board, connect the RX pin (J3-19) with 3.3V, and change the DIP switches
3 an 4 at SW1 from 1-0 to 0-1.

3. Push Reset.

4. Run the commands:

```
sudo ./sdphost -u 0x1fc9,0x0130 -- write-file 0x1000 tinyuf2-imxrt1050_evkb-0.21.0.bin
sudo ./sdphost -u 0x1fc9,0x0130 -- jump-address 0x2000
```
Wait until a drive icon appears on the computer (or mount it explicitly). When using the above
mentioned bootloader, it has the label `RT1050BOOT`. Then the UF2 bootloader
is permanently installed.

Using sudo is Linux specific. You may not need it at all, if the access rights are set properly,
and you will not need it for Windows.

5. At the Seeed Arch Mix board, disconnect the RX pin (J3-19) with 3.3V, and change the DIP switches
3 an 4 at SW1 back to 1-0.

6. Once the upload of the bootloader is finished or when it is already installed, push Reset twice.

The bootloader should start and show a drive icon. Do not push too fast. The i.MX RT MCU
have no dedicated Reset Pin and are reset through power cycling, which may be slow.
Copy the .uf2 version of MicroPython to this drive to install or update MicroPython.
If after steps 1-4 the bootloader drive is already shown, you do not have to reset again.

Once the UF2 bootloader is installed, only step 6 is required to deploy MicroPython. If
MicroPython is already installed, the bootloader can as well be invoked by calling
`machine.bootloader()` or switching the USB baud rate at the PC to 1200 baud and back.

If at any time the flash content is corrupted you can always start over from the beginning.

## Mboot build support

Mboot is a separate, opt-in build. Its map reserves flash offsets 0x000000-0x040000 for the bootloader, 0x040000-0x240000 for the primary slot, 0x240000-0x441000 for the offset-swap secondary slot, and 0x441000-0x443000 for the security counter. The application VFS starts at 0x443000. This replaces the ordinary VFS at 0x200000, so back up all needed files before migrating the layout.

Build commands, which do not program the board:

```
make -C ports/mimxrt -f Makefile.mboot BOARD=SEEED_ARCH_MIX MBOOT_PUBKEY=<public-key.pem>
make -C ports/mimxrt BOARD=SEEED_ARCH_MIX MBOOT=1 MBOOT_ROMFS=<romfs-image> MBOOT_PUBKEY=<public-key.pem> MBOOT_SIGN_KEY=<private-key.pem>
make -C ports/mimxrt BOARD=SEEED_ARCH_MIX MBOOT=1 MBOOT_ROMFS=<romfs-image> MBOOT_PUBKEY=<public-key.pem> MBOOT_SIGN_KEY=<private-key.pem> initial
```

The signed application includes a padded 512 KiB ROMFS image; changes to ROMFS require rebuilding and signing the application, not writing ROMFS in place. Do not use the UF2, sdphost or TinyUF2 recovery instructions above for Mboot. Before installation, make a complete flash backup, verify an independent recovery and restore path, and review the binary, map and programmer commands against the flash layout. Check that the signed application fits the configured slot, including the image header, validation metadata and swap trailer. The build commands do not provide a deployment or restore procedure.
