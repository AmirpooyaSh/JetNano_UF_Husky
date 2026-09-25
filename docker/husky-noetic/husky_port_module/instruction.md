# Restore the Husky PL2303 Serial Driver on Jetson

This guide is for the following confirmed system:

- Jetson Linux: `R36.5.2`
- Running kernel: `5.15.199-tegra`
- Husky USB adapter: `067b:2303 Prolific PL2303`
- Previous driver: `/lib/modules/5.15.185-tegra/extra/pl2303.ko`

Run these steps directly from a terminal on the Jetson. Do not copy the old `5.15.185-tegra` module into the new kernel directory.

## 1. Confirm the current problem

```bash
uname -r
lsusb | grep '067b:2303'
modinfo pl2303
```

Expected current result:

```text
5.15.199-tegra
067b:2303 Prolific Technology, Inc. PL2303 Serial Port
modinfo: ERROR: Module pl2303 not found
```

## 2. Check available storage

```bash
df -h /home/administrator
```

Do not continue unless several gigabytes are available.

## 3. Install the matching kernel headers and build tools

```bash
sudo apt update
```

```bash
sudo apt install -y \
  build-essential \
  bc \
  flex \
  bison \
  libssl-dev \
  zstd \
  nvidia-l4t-kernel-headers
```

Verify that the current kernel build directory exists:

```bash
ls -ld /lib/modules/$(uname -r)/build
```

The path must refer to the `5.15.199-tegra` headers.

## 4. Check whether the old PL2303 source files are still available

```bash
find /home/administrator /usr/src \
  -type f \
  \( -name 'pl2303.c' -o -name 'pl2303.h' \) \
  2>/dev/null
```

If both `pl2303.c` and `pl2303.h` are found in the same source directory, skip to **Step 7** and copy those two files into the new build directory.

If they are not found, continue to Step 5.

## 5. Download the matching R36.5.2 kernel sources

Create the working directory:

```bash
mkdir -p /home/administrator/pl2303-5.15.199
cd /home/administrator/pl2303-5.15.199
```

Use a limited download rate to avoid overloading the USB Ethernet adapter:

```bash
wget -c \
  https://developer.download.nvidia.com/embedded/L4T/r36_Release_v5.2/sources/public_sources.tbz2
```

If the download is interrupted, run the same command again. The `-c` option resumes the existing partial file.

Verify that the archive exists:

```bash
ls -lh public_sources.tbz2
```

## 6. Extract only the kernel source archive

```bash
tar -xf public_sources.tbz2 \
  Linux_for_Tegra/source/kernel_src.tbz2
```

```bash
tar -xf Linux_for_Tegra/source/kernel_src.tbz2 \
  -C Linux_for_Tegra/source
```

Verify the required source files:

```bash
ls -l \
  Linux_for_Tegra/source/kernel/kernel-jammy-src/drivers/usb/serial/pl2303.c \
  Linux_for_Tegra/source/kernel/kernel-jammy-src/drivers/usb/serial/pl2303.h
```

## 7. Create the standalone module build directory

```bash
mkdir -p /home/administrator/pl2303-5.15.199/module
cd /home/administrator/pl2303-5.15.199/module
```

If the R36.5.2 archive was downloaded, copy the source files with:

```bash
cp ../Linux_for_Tegra/source/kernel/kernel-jammy-src/drivers/usb/serial/pl2303.c .
cp ../Linux_for_Tegra/source/kernel/kernel-jammy-src/drivers/usb/serial/pl2303.h .
```

If Step 4 found existing source files elsewhere, copy those exact `pl2303.c` and `pl2303.h` files into this directory instead.

Verify:

```bash
ls -l pl2303.c pl2303.h
```

## 8. Create the module Makefile

```bash
printf 'obj-m += pl2303.o\n' > Makefile
```

Verify:

```bash
cat Makefile
```

Expected output:

```makefile
obj-m += pl2303.o
```

## 9. Build the module against the running kernel

```bash
make -C /lib/modules/$(uname -r)/build \
  M="$PWD" \
  modules
```

Verify that the module was created:

```bash
ls -lh pl2303.ko
```

Confirm its kernel version before installing it:

```bash
modinfo ./pl2303.ko | grep vermagic
```

The result must contain:

```text
5.15.199-tegra
```

Do not install the module if it reports `5.15.185-tegra` or any other kernel version.

## 10. Install and load the new module

```bash
sudo mkdir -p /lib/modules/$(uname -r)/extra
```

```bash
sudo install -m 644 \
  pl2303.ko \
  /lib/modules/$(uname -r)/extra/pl2303.ko
```

```bash
sudo depmod -a
sudo modprobe pl2303
```

## 11. Verify that the Husky serial port exists

```bash
lsmod | grep pl2303
lsusb -t
ls -l /dev/ttyUSB0
```

The PL2303 entry in `lsusb -t` should now show:

```text
Driver=pl2303
```

The serial device should exist as:

```text
/dev/ttyUSB0
```

If the module is loaded but the device is still missing, disconnect and reconnect the Husky USB connection, then run:

```bash
sudo dmesg -T | tail -n 50
ls -l /dev/ttyUSB*
```

## 12. Load PL2303 automatically after every reboot

```bash
echo pl2303 | sudo tee /etc/modules-load.d/pl2303.conf
```

Verify:

```bash
cat /etc/modules-load.d/pl2303.conf
```

## 13. Test after reboot

```bash
sudo reboot
```

After the Jetson starts, open a local terminal and run:

```bash
uname -r
lsmod | grep pl2303
ls -l /dev/ttyUSB0
```

Expected kernel:

```text
5.15.199-tegra
```

Expected device:

```text
/dev/ttyUSB0
```

## 14. Test the Husky manually

Start the Husky debug container. Inside its ROS 1 terminal, run:

```bash
roslaunch husky_base base.launch
```

In another container terminal, verify:

```bash
rosnode list | grep -E 'husky_node|controller'
rosservice list | grep controller_manager
```

The launch must no longer report:

```text
Unable to open /dev/ttyUSB0
```

## Failure check

If `sudo modprobe pl2303` reports `Invalid module format`, run:

```bash
modinfo ./pl2303.ko | grep vermagic
uname -r
sudo dmesg -T | tail -n 50
```

The module and running kernel versions must match exactly.
