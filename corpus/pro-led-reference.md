# OrbitMesh Pro Series LED Reference

**Applies to:** OrbitMesh Pro R5 Pro and N5 Pro only. Do not apply this document to the home R1 and N1 system.
**Document version:** 1.1
**Published:** 2026-02-20

LED color and motion are separate signals. Ask whether a light is solid, flashing, or pulsing.

## R5 Pro gateway LEDs

| Pattern | Meaning | Operator action |
|---|---|---|
| Pulsing blue | Awaiting claim in the Pro Console | Scan the QR claim code |
| Solid white | Online and operating normally | No action |
| Flashing amber | WAN link is absent or no upstream connection is detected | Check the WAN port and modem/ONT |
| Solid amber | Awaiting cloud provisioning | Check the Pro Console for the site status |
| Flashing red | Firmware recovery in progress | Keep power connected for up to 10 minutes |
| No light | No power or PoE injector fault | Check the adapter, PoE budget, and outlet |

## N5 Pro node LEDs

| Pattern | Meaning | Operator action |
|---|---|---|
| Pulsing blue | Claimed and waiting for site assignment | Assign a role in the Pro Console |
| Solid white | Online and mesh link is healthy | No action |
| Flashing amber | Firmware download in progress | Keep the node in place and powered; do not move or unplug it |
| Solid amber | Awaiting cloud provisioning | Check the Pro Console for the site status |
| Flashing red | Firmware recovery in progress | Keep power connected for up to 10 minutes |
| No light | No power or PoE injector fault | Check the adapter, PoE budget, and outlet |

A flashing amber N5 Pro is downloading firmware and must be left powered in place until it completes. Do not interpret it as a placement signal. Never infer warranty coverage from a light state.
