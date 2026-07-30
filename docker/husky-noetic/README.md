# Install the PL2303 USB-Serial Kernel Module on JetPack 6.2.2

This guide documents how to add support for a **Prolific PL2303 USB-to-Serial adapter** on an NVIDIA Jetson running:

- **JetPack 6.2.2**
- **Jetson Linux / L4T R36.5**
- **Ubuntu 22.04**
- **Kernel:** `5.15.185-tegra`

This was needed because the USB adapter was visible with `lsusb`, but no `/dev/ttyUSB*` device was created.

Example:

```bash
lsusb | grep -i prolific
```

Output:

```text
ID 067b:2303 Prolific Technology, Inc. PL2303 Serial Port
```

But:

```bash
ls -l /dev/ttyUSB*
```

returned:

```text
No such file or directory
```

The kernel configuration confirmed the cause:

```bash
zcat /proc/config.gz | grep -E 'CONFIG_USB_SERIAL=|CONFIG_USB_SERIAL_PL2303'
```

Output:

```text
CONFIG_USB_SERIAL=m
# CONFIG_USB_SERIAL_PL2303 is not set
```

So the generic USB-serial framework exists, but the PL2303 driver is missing.

> This is a Linux kernel driver only. No ROS Noetic or Husky packages need to be installed on the Jetson host.

---

## 1. Confirm the Running Kernel

```bash
uname -r
```

Expected:

```text
5.15.185-tegra
```

The module must be built against the exact running kernel.

---

## 2. Install Build Dependencies and Kernel Headers

```bash
sudo apt update
```

```bash
sudo apt install -y \
    nvidia-l4t-kernel-headers \
    build-essential \
    bc \
    flex \
    bison \
    libssl-dev \
    zstd
```

Verify that the kernel build directory exists:

```bash
ls -l /lib/modules/$(uname -r)/build
```

Also check:

```bash
ls /lib/modules/$(uname -r)/build/Module.symvers
```

---

## 3. Download the Jetson Linux R36.5 Public Sources

Create a working directory:

```bash
mkdir -p ~/pl2303_build
cd ~/pl2303_build
```

Download the NVIDIA public source package:

```bash
wget -O public_sources.tbz2 \
https://developer.nvidia.com/downloads/embedded/l4t/r36_release_v5.0/sources/public_sources.tbz2
```

Extract it:

```bash
tar -xjf public_sources.tbz2
```

The extracted source directory should contain:

```text
Linux_for_Tegra/source/kernel_src.tbz2
```

Move into the source directory:

```bash
cd ~/pl2303_build/Linux_for_Tegra/source
```

Extract the kernel source:

```bash
tar -xjf kernel_src.tbz2
```

Verify the PL2303 source files exist:

```bash
ls kernel/kernel-jammy-src/drivers/usb/serial/pl2303*
```

Expected files include:

```text
pl2303.c
pl2303.h
```

---

## 4. Create a Small External Module Build Directory

```bash
mkdir -p ~/pl2303_build/pl2303_module
cd ~/pl2303_build/pl2303_module
```

Copy the PL2303 source files:

```bash
cp ~/pl2303_build/Linux_for_Tegra/source/kernel/kernel-jammy-src/drivers/usb/serial/pl2303.c .
```

```bash
cp ~/pl2303_build/Linux_for_Tegra/source/kernel/kernel-jammy-src/drivers/usb/serial/pl2303.h .
```

Create a minimal Makefile:

```bash
printf 'obj-m += pl2303.o\n' > Makefile
```

Verify:

```bash
cat Makefile
```

Expected:

```makefile
obj-m += pl2303.o
```

---

## 5. Build the PL2303 Kernel Module

Build against the currently running Jetson kernel:

```bash
make -C /lib/modules/$(uname -r)/build \
    M=$PWD \
    ARCH=arm64 \
    modules
```

After a successful build:

```bash
ls -lh pl2303.ko
```

Verify the module version:

```bash
modinfo ./pl2303.ko | grep -E 'filename|vermagic'
```

The `vermagic` should correspond to the running kernel, for example:

```text
5.15.185-tegra
```

---

## 6. Install the Module

Create a directory for locally added modules:

```bash
sudo mkdir -p /lib/modules/$(uname -r)/extra
```

Copy the module:

```bash
sudo cp pl2303.ko /lib/modules/$(uname -r)/extra/
```

Update the kernel module dependency database:

```bash
sudo depmod -a
```

Verify that Linux can now find the module:

```bash
modinfo pl2303
```

---

## 7. Load the Driver

Load the generic USB-serial framework:

```bash
sudo modprobe usbserial
```

Load PL2303:

```bash
sudo modprobe pl2303
```

Verify:

```bash
lsmod | grep -E 'pl2303|usbserial'
```

---

## 8. Verify the USB Serial Device

With the Prolific adapter connected:

```bash
ls -l /dev/ttyUSB*
```

You should now see something similar to:

```text
/dev/ttyUSB0
```

Check the kernel log:

```bash
sudo dmesg | tail -30
```

Typical messages include:

```text
pl2303 converter detected
pl2303 converter now attached to ttyUSB0
```

You can also verify the adapter itself with:

```bash
lsusb | grep -i prolific
```

---

## 9. Load PL2303 Automatically at Boot

Once the module is confirmed working:

```bash
echo pl2303 | sudo tee /etc/modules-load.d/pl2303.conf
```

Reboot:

```bash
sudo reboot
```

After reboot, verify:

```bash
lsmod | grep pl2303
```

and:

```bash
ls -l /dev/ttyUSB*
```

---

## 10. Using the Device with the Husky Docker Container

Once the host creates:

```text
/dev/ttyUSB0
```

the ROS Noetic Husky container can use it directly.

Example Docker environment setting:

```bash
-e HUSKY_PORT=/dev/ttyUSB0
```

The container should also receive the host device tree:

```bash
-v /dev:/dev
```

Inside the container, the Husky can then be launched with:

```bash
roslaunch husky_base base.launch
```

or explicitly:

```bash
roslaunch husky_base base.launch port:=/dev/ttyUSB0
```

The Jetson host itself does **not** need ROS Noetic, Husky packages, or Clearpath launch files. It only needs the Linux kernel module so that the physical USB-to-serial adapter appears as a device that Docker can access.

---

## Troubleshooting

### `modprobe: FATAL: Module pl2303 not found`

The module has not been installed into the active kernel module tree, or `depmod` has not been run.

Check:

```bash
find /lib/modules/$(uname -r) -name 'pl2303.ko*'
```

Then:

```bash
sudo depmod -a
sudo modprobe pl2303
```

### `lsusb` sees the adapter but `/dev/ttyUSB0` does not exist

Check:

```bash
lsmod | grep -E 'pl2303|usbserial'
```

Then inspect:

```bash
sudo dmesg | tail -50
```

### Kernel updated after installing the module

A kernel module is tied to the kernel version it was built for.

Check:

```bash
uname -r
```

If the Jetson kernel has changed, rebuild `pl2303.ko` against the new:

```text
/lib/modules/$(uname -r)/build
```

directory.
