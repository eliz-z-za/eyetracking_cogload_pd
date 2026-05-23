# GAZEPOINT CONTROL

# USER MANUAL


## Contents

- 1 Introduction
- 2 Technical Specification and Requirements
- 3 Hardware Setup
   - 3.1 Eye Tracker Hardware
   - 3.2 Data and Power Connections
   - 3.3 Eye Tracker Placement
- 4 Software Setup
   - 4.1 Gazepoint Control Main Window
   - 4.2 Gazepoint Control Settings Window
   - 4.3 Gazepoint Control Calibration Window
   - 4.4 Gazepoint Control Calibration Customization
   - 4.5 Gazepoint Control IPD Calibration
   - 4.6 Lab Streaming Layer
- 5 Demo Programs and Source Code
   - 5.1 GPClient Class
   - 5.2 C++ apiclient
   - 5.3 C++ Template
   - 5.4 Matlab
   - 5.5 Other Languages
- 6 Troubleshooting Tips
   - 6.1 Difficulty tracking very young or elderly subjects
   - 6.2 Difficulty tracking users with glasses
   - 6.3 Camera Driver
   - 6.4 Why doesn’t my GP3 appear to be Working?.............................................................................
   - 6.5 Why isn’t the GP3HD running at 150 Hz?
   - 6.6 How should I clean my GP3 eye-tracker?


## 1 Introduction

The Gazepoint Control system is a high-performance biometric data capture system that provides eye
gaze data, pupil diameter, heart rate and galvanic skin response (GSR) as well as self-reporting dial, all at
an extremely affordable price. The hardware and software setup are also very straightforward and easy
to operate. Gazepoint eye trackers are also compatible with the Microsoft Eye Control accessibility
features available in Windows 10.

## 2 Technical Specification and Requirements

```
Eye Tracking GP3 GP3HD
Sampling Rate 60 Hz 150 Hz
Accuracy 0.5 – 1 degree 0.5 – 1 degree
Spatial Resolution (RMS) 0.1 0.
Eye Tracking Mode Binocular Binocular
Operating Distance 50 cm – 80 cm 50 cm – 80 cm
Tracking Range (Head Box) 25 cm x 11 cm 35 cm x 22 cm
Calibration 5 or 9 point 5 or 9 point
Tracking Recovery Time < 50 ms < 20 ms
System Latency < 50 ms (end to end from event to API output)
Data Connection USB 2.0 USB 3.
Dimensions 235 mm x 4 3 mm x 4 7 mm 235 mm x 4 3 mm x 47 mm
Weight 115 g 123 g
Eyewear Compatibility Works with most glasses and contact lenses
Processor Intel i5 (i7 recommended)
Memory 4 GB (8GB recommended)
OS Window 8/10/11 64 bit
```

## 3 Hardware Setup

### 3.1 Eye Tracker Hardware

The Gazepoint eye tracking system ships with the eye tracker (SD or HD), tripod, and 2 USB cables, one
for camera image data and one to power the infrared illumination.

```
Figure 1 - Gazepoint GP3 and Tripod
```
### 3.2 Data and Power Connections

Before connecting the hardware to your computer, first install the Gazepoint software which will install
the required camera drivers (see software setup below). To connect the eye tracker simply plug in the
USB data cable and power cable to both the device and the computer.

Ideally the data cables should be connected directly to the USB port on your computer if sufficient ports
are available. If there are insufficient USB ports, a USB 3 .0 hub can be used provided it is connected to a
USB 3 .0 (or better) port on the computer. The eye tracker power cable can connect to any powered USB
power port and does not need to be connected to the PC (for example a phone charger plug will also
work).

### 3.3 Eye Tracker Placement

Position the eye tracker directly below your computer screen. Avoid using in a room with direct or
indirect sunlight on the face of the user. The eye tracker should be approximately arm’s length distance
away and centered and pointed at your face, the ideal distance is 65 cm to your eyes. If the user is
wearing glasses, it is best to tilt the unit upwards at a greater angle to prevent reflections from
appearing off the glasses lenses.


```
Figure 2 - Gazepoint GP3 Tripod Setup
```
## 4 Software Setup

The software is available to download from the Gazepoint website here: [http://gazept.com/downloads/.](http://gazept.com/downloads/.)
A password is required to download the software and will be sent to you at the time of purchase.

The Gazepoint Control software performs the data collection and provides the data to software clients
such as Gazepoint Analysis. The software also operates the data server which provides data to third-
party programs through the Open Gaze API.

### 4.1 Gazepoint Control Main Window

There are only a few buttons needed to control the Gazepoint hardware:

```
 Calibrate : Start the eye-tracker calibration process
 Gaze pointer : Move mouse cursor wherever you look (block the sensor to get back control)
 Select Screen : For multi-monitor systems, selects the active screen
 Update Rate: Select between 60 Hz and 150 Hz data rate (GP3 is 60 Hz only)
 Biometrics: Toggle on/off biometrics data capture
 Settings: Configuration settings (described below)
```
If a GP3 or GP3HD eye tracker is found, the captured face image will be displayed along with feedback
on the correct head distance. The top indication bar shows **Close** to **Far** with the ideal head depth in the
middle of the bar.


System feedback information is provided through the bottom status bar:

```
 Frame rate : Should be 62 Hz (~16.6ms) or 150 Hz (~6.7ms). If the frame rate is lower you
may need more CPU power for proper operation
 Server : Indicates how many clients are connected and if data is being transmit. Double
click to set the TCP/IP port for API communication (must match Analysis).
 IPD : The inter-pupillary distance the internal system model is using. If real-world
pupil diameter measurements are need see calibration below.
 Information : Indicates the software version number, hardware serial number, USB
connectivity (USB2 or USB3 required for GP3HD) and # of eye trackers.
```
```
Figure 3 - Gazepoint Control Main Interface
```

### 4.2 Gazepoint Control Settings Window

```
 Enable Auto Gain: Enable if the subject has very bright or very dim pupils and the system
has difficulty initially locking. The camera gain will sweep from low to
high until the pupils are found.
 Monocular Tracking: Force tracking on only one eye. Assign visible eye as Left or Right.
```
```
 Enable Auto Switch: If multiple eye trackers are connected, automatically select the eye
tracker on the screen being viewed by the user.
 Next Tracker: If multiple eye trackers are connected, select the next eye tracker.
 Eye Control Assistant: To use Gazepoint eye-trackers with Microsoft Eye Control, run the Eye
Control assistant to connect Gazepoint Control to the Microsoft system.
See the following for details on Eye Control:
```
https://support.microsoft.com/en-us/help/4043921/windows- 10 - get-started-eye-control

```
 Minimize to Tray: When Control is minimized (such as when using Eye Control) the icon
will reside in the tray next to the clock rather than the lower taskbar.
 Cursor Smoothing: The slider increases or decreases smoothing when using gaze (APOG) as
a cursor and Windows Eye Control (defaults to 15 samples smoothing).
 API Port: The TCP/IP communication port used by the API (default 4242).
 API Address: The TCP/IP communication addresses used by the API.
```
```
 Mobile Port:
The TCP/IP communication port used to
calibrate a mobile device using the webpage
(default 4243).
 Mobile Server Address:
The address to type into the mobile device
browser to access the calibration page. If
multiple addresses are available, use the
address from the same network as the
mobile device.
 Mobile Status:
The connection status of the mobile
calibration webpage.
```
```
Figure 4 - Gazepoint Control Settings Interface
```

### 4.3 Gazepoint Control Calibration Window

When the calibration process begins, the screen will go blank and a calibration marker will move
through five positions on the screen. Simply look at the marker at each of the calibration positions. After
calibration a green point-of-gaze estimate will be drawn on the screen to check the calibration accuracy
by observing points on the display. If the calibration is not satisfactory, a second calibration will often
improve the results, as first-time users may not have followed the calibration dots well.

```
 Press ‘C’ to perform the calibration again.
 Press ‘5’ to start using the five-point calibration process. This is the fastest calibration which
provides good accuracy.
 Press ‘9’ to start using the nine-point calibration process. In some circumstances a little more
accuracy may be desired, traded off with a slightly longer calibration.
 Press ‘1’ to start a one-point calibration. This uses a default set of values for calibration and does
a minor correction with the 1 point. This is the fastest calibration, but may be VERY inaccurate
depending on how different the system setup and subject are from the default calibration.
 Press ‘U’ to undo the calibration and reload the last calibration (‘U’ toggles between the current
calibration and the previous calibration).
 Press ‘S’ to enable a short tone to play at each calibration point.
 Press ‘+’ or ‘-‘ to increase or decrease the calibration animation times or ‘D’ to load the default
times. The default values are optimal for most circumstances. For children a slower speed might
improve the calibration performance.
```
```
Figure 5 - Gazepoint Control 5 - Point Calibration Results
```

### 4.4 Gazepoint Control Calibration Customization

It is possible to replace the default calibration animation circles with custom images and sounds. To
replace a calibration point, place the custom PNG image and MP3 audio file in the **Gazepoint** subfolder
in the **Documents** folder on the computer, typically: **C:\Users\???\Documents\Gazepoint** , where ???
is the name of the Windows computer user.

The audio file should be named **CalibPoint1.mp3** and the image file named **CalibPoint1.png** for
point 1. Replace the number with 2, 3, 4, 5, etc for subsequent calibration points. For the best look use a
square image, with the image background color RGB=25,25,25 which will blend with the calibration
window. Sample calibration points can be found in the installation folder under **calibpoints**.

### 4.5 Gazepoint Control IPD Calibration

To increase the accuracy of the pupil diameter measurements, a calibration of the inter-pupil distance
(IPD) is possible by imaging a marker of known size during calibration. Print the marker below and
ensure the width and height are as close to 11 mm as possible. Some printers do not print exactly and so
it is best to measure after printing with a pair of calipers or ruler. Using a small strip of tape affix to the
forehead between the eyes. During calibration press ‘m’ to confirm the marker has been identified
(outlined in red). Redo calibration as normal and the IPD should be calculated.

```
Figure 6 - Gazepoint Control IPD Calibration Marker
```
```
Figure 7 - Gazepoint Control Calibration Showing IPD Marker Tracked
```

### 4.6 Lab Streaming Layer

A lab streaming layer (LSL) app is included in the Gazepoint software installation directory under
Gazepoint/demo/lsl/LSLGazepoint.py. The Gazepoint LSL app is a Python script which streams data from
Gazepoint Control to the LSL recorder.

The Gazepoint LSL app opens two LSL data streams, GazepointEyeTracker and
GazepointBiometrics. The GazepointEyeTracker stream contains point-of-gaze fixation gaze
data determined by the Gazepoint fixation filter. The GazepointBiometrics stream contains dial,
GSR, and heart rate data.

In order to run the Gazepoint LSL app, first ensure PyLSL is installed on your PC. Next, open Gazepoint
Control. You can then run LSLGazepoint.py, and start using LSL to stream eye gaze and biometrics data
from Gazepoint Control.

## 5 Demo Programs and Source Code

A number of example demo programs and source code are provided to get you started quickly using the
Open Gaze API. These files can be found in the Gazepoint software installation folder, typically:

For 32bit OS: _C:\Program Files\Gazepoint\Gazepoint\demo\_

For 64 bit OS: _C:\Program Files (x86)\Gazepoint\Gazepoint\demo\_

If you wish to compile these programs, it is best to copy the demo folder to another location such as the
desktop or _c:\temp\,_ as your operating system will prevent you from creating compile files in the
_“Program Files”_ folder.

### 5.1 GPClient Class

The _GPClient_ class in the _\Demo\Include\_ folder is an MFC based class which simplifies all tasks needed
to use the API. This class is used for all Gazepoint software projects which communication using the API.

### 5.2 C++ apiclient

The apiclient uses the GPClient Class and enumerates the API commands so you can send them one at a
time. This program is great for watching what commands look like as you send them back and forth with
the server.


```
Figure 8 - Gazepoint API Client example program
```
### 5.3 C++ Template

The _template_ project is a simple project which illustrates connecting to the server, sending data, and
printing incoming data to the screen. These are good starting points for developing your own C++
applications.

### 5.4 Matlab

Sample source code showing the API use in a Matlab programming language.

### 5.5 Other Languages

Since the API uses TCP/IP, any language with sockets can communicate with the server. Please contact
us if you would like to see some sample code for a language not listed here.


## 6 Troubleshooting Tips

### 6.1 Difficulty tracking very young or elderly subjects

If the system is having difficulty tracking young subjects eyes (typically due to very bright pupil
responses), enable the Automatic Gain Sweep in the Settings dialog. The gain sweep option cycles the
system gain through the full range of values to try to catch problematic eyes. This option may also be
used for elderly eyes which may exhibit a dim bright pupil response.

### 6.2 Difficulty tracking users with glasses

If the system is having difficulty tracking a subject wearing glasses, try tilting the unit upwards at a
slightly steeper angle, which may also require the user to sit slightly higher. The steeper angle prevents
the infrared reflections off the surface of the glasses from returning to the camera. In the figures below,
the image on the left shows potentially problematic reflections, and the image on the right shows the
result of angling the eye tracker slightly steeper, moving the reflections lower and away from the pupils.

```
Figure 9 – Glasses reflections and mitigation
```
### 6.3 Camera Driver

If the eye-tracker is plugged in before the software is installed, sometimes the camera can get labelled
as an ‘Unknown Device’ in the Windows Device Manager. If you think this is the case, load the Device
Manager and look for the unknown device, right-click and then Uninstall. Make sure the device is
unplugged and then re-install the Gazepoint software package. When the camera driver is installed
successfully, it should be listed as a Point Grey Research device as shown in the figure below.


### 6.4 Why doesn’t my GP3 appear to be Working?.............................................................................

**Dim red lights not visible behind the black plastic on the front of the eye-tracker:**

Check that power is supplied via the second USB cable; ensure that Gazepoint Control is running; kill any
extra Gazepoint Control processes.

**The camera window has a strange split image:**

This is a known bug that occurs infrequently with the Firefly camera on the first start up of the PC or
installation of a new version. Simply restart the Gazepoint Control software to solve this issue.

**The camera shows my eyes properly but the GP3 doesn’t lock onto my eyes:**

A number of artefacts can throw off the GP3’s eye-tracking algorithm:

- Sunlight has a lot of infrared content that can easily wash out the infrared LED sources; try to
    work in environments with halogen or fluorescent lighting. Dark environments work well too.
- Hard contact lenses can shift after a blink which can cause difficulty in tracking
- Soft contact lenses that are too dirty can reduce the specular reflections used to track eye
    movement. Use only clean contact lenses.
- Glasses that are dirty or scratched can cause difficulty in tracking, as well as some shiny metal
    frames. Glasses work best when the angle from the GP3 unit to the face is relatively steep (i.e.
    move your head higher and closer to the monitor and tilt the unit up).
- Some makeup, such as glitter, can lead to spurious reflections. Avoid such makeup.


**The calibration isn’t working:**

Try a few times as some users need to see the process once before understanding how it works. Ask the
user to try to focus on the dot and not to talk while calibrating. Some people anticipate where the dot
will move and this throws off the calibration.

### 6.5 Why isn’t the GP3HD running at 150 Hz?

Ensure that you have connected the eye tracker to a USB 3.0 port. You can verify by looking at the
bottom right of the Control software to see what USB connectivity was detected.

### 6.6 How should I clean my GP3 eye-tracker?

We suggest using compressed air to remove dust from the lens of the device if desired. If there happens
to be finger prints or other smudges on the surface you can use the microfiber cloth bag included with
the eye tracker to gently rub the surface clean. If a stubborn smudge is observed then a small amount of
rubbing alcohol in the cloth and very gentle wiping of the surface can be used.

In general the system is robust to most small dust and smudges on the lens, only a small circle in the
very center is used by the camera lens to see out of and so cleaning is primarily for cosmetic purposes
and is not particularly necessary for proper operation.


