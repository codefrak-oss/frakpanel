# Xeneon Edge hardware notes

What we learned getting a Corsair Xeneon Edge to work without iCUE, first on
a Mac (September 2026, macOS 26 on Apple Silicon) and then on the Windows 11
mini PC it lives on now. Nothing here is needed to run frakpanel; it is here
because it took a while to find out.

## Two devices on one cable

- **Video:** plain DisplayPort (USB-C alt mode) or HDMI. The OS sees a normal
  2560x720 external monitor (EDID manufacturer `CRX`). Anything that draws a
  window can drive it.
- **Touch:** a standard USB HID digitizer. WCH controller, VID `0x27c0`, PID
  `0x0859`, manufacturer `wch.cn`, three interfaces: digitizer (usage page
  0x0D), mouse, and a vendor interface (0xFF0A). No Corsair software is needed
  to read it.
- A separate Corsair vendor HID device (VID `0x1b1c`, PID `0x1d0d`) also
  appears. We never touched it.

iCUE is optional per
[Corsair](https://www.corsair.com/us/en/explorer/gamer/monitors/do-i-need-icue-for-the-xeneon-edge/),
and the macOS iCUE build doesn't support the Edge at all. iCUE adds
brightness and color presets and its widget dashboard.

## Plugging in

- If the panel powers up but stays black, the USB-C port probably carries
  data only. A dock's front USB-C port did this: the Edge enumerated on USB
  and never appeared as a display. A Thunderbolt port on the same dock worked.
- macOS first picked 1920x1080 and rotation 180. Set 2560x720 (Option-click
  "Scaled" in Displays to show all modes) and standard rotation.
- Run it at 100% scale so CSS pixels map 1:1 to panel pixels.

## Touch by operating system

**Windows:** the digitizer enumerates as `HID-compliant touch screen` and
Windows' own touch stack handles it. Taps landed correctly with no driver and
no calibration.

**Linux:** not tried by us.
[aabdelghani/corsair-xeneon-edge-linux](https://github.com/aabdelghani/corsair-xeneon-edge-linux)
documents the HID protocol, DDC/CI control and calibration, and is the best
protocol reference we found.

**macOS:** there is no native touchscreen support. macOS delivers the HID
reports and nothing turns them into clicks. Community user-space drivers all
work the same way: open the digitizer through IOHIDManager, seize it so macOS
stops treating it as a mis-mapped mouse, and post synthetic mouse events with
CoreGraphics. Each needs Accessibility and Input Monitoring permission.

| Driver | Notes (as of September 2026) |
|---|---|
| [MorganZ/xeneon-edge-touch-macos](https://github.com/MorganZ/xeneon-edge-touch-macos) | Tested by its author on Apple Silicon and macOS 26. The one we built and ran. |
| [ajvwhite/MacXeneonEdgeTouchDriver](https://github.com/ajvwhite/MacXeneonEdgeTouchDriver) | LaunchAgent install. |
| [ymlaine/TouchscreenDriver](https://github.com/ymlaine/TouchscreenDriver) | Has a calibration tool. Documents the raw ranges: X 0-16383, Y 0-9599. |

It worked, and we stopped using it anyway. What went wrong:

- **Rebuilding the driver lost its permissions.** Ad-hoc code signatures
  change hash on every build, and macOS keys the Accessibility and Input
  Monitoring grants to that hash. The rebuilt app sat "waiting for
  permission", `tccutil reset` plus re-granting did not reliably fix it, and
  macOS fell back to treating the panel as an absolute mouse on the main
  display. A stable signing identity should fix this; we didn't get that far.
- **The pointer leaves the main display for the length of every touch.** The
  driver parks it on the Edge to deliver the click, then warps it back.
- **A tap gives the kiosk window keyboard focus**, so the window you were
  typing in flickers inactive.
- **Tiling window managers react to all of the above.** Ours moved a
  workspace and re-centered the pointer on every tap until it was taught to
  ignore the Edge.
- **Single touch only.** The hardware is 5-point multitouch, but the
  digitizer emits single-touch reports unless the firmware is switched into
  multitouch mode over the vendor interface, which is what iCUE does. That
  command is undocumented. Taps and drags work; pinch and two-finger scroll
  don't.

A full-screen Chrome kiosk on the Mac was fine (all 2560x720 pixels, no
browser chrome). A fresh Chrome profile opens a sign-in page instead of your
URL unless you pass `--no-first-run --no-default-browser-check`.

## Panel settings without iCUE

The Edge has no buttons and no on-screen menu. Settings go over DDC/CI on the
video link, live on the panel, and persist across hosts and reboots.

On a Mac, with [`m1ddc`](https://github.com/waydabber/m1ddc), direct-attached:

| Setting | Read | Write | Notes |
|---|---|---|---|
| Brightness (luminance) | yes | yes | factory 95, max 100 |
| Contrast | yes | yes | factory 50, max 100 |
| Red/green/blue gain | no | untested | `get` returns garbage |
| Color preset (VCP 0x14) | no | no | m1ddc has no command for it; needs raw VCP access |
| Power off/on | untested | untested | the Linux project above reports it works |

```
m1ddc display list                    # find the Edge's index (it shifts on plug and unplug; match on name)
m1ddc display 3 get luminance
m1ddc display 3 set contrast 80
```

DDC from Windows is untried by us (candidates: ControlMyMonitor, Monitorian,
or the Win32 `SetVCPFeature` API). DDC through a dock is the usual failure
case and was not re-tested.

## Sound from a tile

Video and sound in a tile work: the kiosk runs with
`--autoplay-policy=no-user-gesture-required`, and the relay forwards bytes
without parsing HTTP, so `Range` requests and seeking work. The Edge has no
speakers, so the sound comes out of the panel host's own audio output.

## Other projects for this panel

- [Vardek](https://github.com/vardekapp/Vardek), [kira-edge](https://github.com/chadvegas/kira-edge)
  and [edgecontrol](https://github.com/kemalandic/edgecontrol): Mac-native
  dashboards.
- [Clawdeck](https://github.com/Dixie-sketch/Clawdeck): Windows ambient status
  panel built as an iCUE widget.
- [GitHub topic: xeneon-edge](https://github.com/topics/xeneon-edge).
