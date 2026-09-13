# OrbitMesh Quick Start and Installation

**Applies to:** OrbitMesh R1 router and N1 nodes  
**Document version:** 2.1  
**Published:** 2026-04-10

## Before setup

An OrbitMesh network requires one R1 router and supports up to five N1 nodes. Use the supplied 12 V power adapters. Install the OrbitMesh mobile app and have access to the modem or optical network terminal (ONT); do not share the network password with support.

## Install the R1 router

1. Turn off the modem or ONT for 30 seconds.
2. Connect its Ethernet cable to the blue **WAN** port on the R1. The two white ports are LAN ports.
3. Power on the modem or ONT and wait until it is ready.
4. Power on the R1. Continue in the app when its LED pulses blue.
5. Create the Wi-Fi network in the app. The R1 becomes ready when its LED is solid white.

If the app shows **E11: No internet during setup**, verify the WAN cable and follow the modem-mode checks in the Network Compatibility Guide.

## Add an N1 node

For initial pairing, place the N1 in the same room and within 2 metres of the R1. In the app, choose **Network > Add node**, then power on the N1. Pairing can take up to three minutes. A solid white LED means pairing succeeded.

After pairing, move the N1 to its intended location. Start one or two rooms away from the nearest online OrbitMesh device. Avoid enclosed cabinets, large metal objects, microwave ovens, and placing the node on the floor.

## Wired backhaul

Pair the N1 wirelessly before connecting Ethernet. Connect either white LAN port on the R1 to either Ethernet port on the N1. Do not connect an N1 directly to the modem or ONT. Wired and wireless nodes may coexist on the same network.

## Installation checks

In the app, **Network map** should show every device as online. If a newly moved node flashes amber, return it closer to the nearest online OrbitMesh device. For other light states, use the LED and Error-Code Reference rather than relying on color alone.
