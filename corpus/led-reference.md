# OrbitMesh LED and Error-Code Reference

**Document version:** 2.4  
**Published:** 2026-05-02

LED color and motion are separate signals. Ask whether a light is solid, flashing, or pulsing.

## R1 router LEDs

| Pattern | Meaning | Customer action |
|---|---|---|
| Pulsing blue | Ready for app setup | Continue setup in the app |
| Solid white | Online and operating normally | No action |
| Flashing amber | WAN cable is absent or no upstream link is detected | Check the blue WAN port and modem/ONT |
| Solid amber | Internet authentication or configuration failed | Check the app error and compatibility guide |
| Flashing red | Firmware recovery in progress | Keep power connected for up to 10 minutes |
| Solid red | Unrecoverable startup error | Restart once; escalate if it returns |
| No light | No power, or LEDs disabled in the app | Check app setting, adapter, and outlet |

## N1 node LEDs

| Pattern | Meaning | Customer action |
|---|---|---|
| Pulsing blue | Ready to pair | Add the node in the app |
| Solid white | Online and mesh link is healthy | No action |
| Flashing amber | Mesh signal is too weak to maintain a stable link | Temporarily move closer |
| Solid amber | Node is offline because of software/recovery state | Check app notice and firmware version |
| Flashing red | Firmware recovery in progress | Keep power connected for up to 10 minutes |
| Solid red | Startup or hardware error | Restart once; escalate if it returns |
| No light | No power, or LEDs disabled in the app | Check app setting, adapter, and outlet |

## App errors

| Code | Meaning | First check |
|---|---|---|
| E11 | R1 has no internet during setup | Cable is connected to blue WAN port |
| E17 | R1 contains an existing network configuration | Confirm intended network; use pairing reset first |
| E24 | N1 pairing record conflicts with the app | Remove pending node and use pairing reset |
| E31 | Upstream network did not assign an IP address | Modem DHCP or required VLAN settings |
| E42 | Installed firmware package failed validation | Keep device powered and contact support |

Error codes identify a state, not warranty eligibility. Never infer warranty coverage from a light or error code.
