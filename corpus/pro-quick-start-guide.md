# OrbitMesh Pro Series Quick Start (R5 Pro and N5 Pro)

**Applies to:** OrbitMesh Pro R5 Pro and N5 Pro. Do not apply this document to the home R1 and N1 system.
**Document version:** 1.3
**Published:** 2026-02-20

The Pro Series is a business product line managed from the web-based **OrbitMesh Pro Console**, not the consumer mobile app. A site uses one R5 Pro gateway and supports up to 25 N5 Pro nodes.

## Before setup

Each unit accepts 802.3at Power over Ethernet or the supplied 12 V adapter. Confirm the site has a Pro Console organization and a license seat for every unit. Have the modem or optical network terminal (ONT) reachable during setup.

## Install the R5 Pro gateway

1. Turn off the modem or ONT for 30 seconds.
2. Connect its Ethernet cable to the **WAN** port on the R5 Pro.
3. Power on the modem or ONT and wait until it is ready.
4. Power on the R5 Pro. Sign in to the Pro Console and add the site.
5. Claim the gateway by scanning its QR claim code into the Pro Console.

## Add an N5 Pro node

Provision every node by scanning its QR claim code into the Pro Console under **Site > Devices > Claim**; the Pro Series does not use proximity pairing. After the node is claimed, the console assigns it a site role and pushes its configuration. A pulsing blue LED means the node is claimed and waiting for a site assignment.

An N5 Pro **may connect directly to the modem or ONT and operate as the gateway** when a Pro Console license assigns it the gateway role. This is a Pro Series capability only.

## Wired backhaul

N5 Pro nodes support wired backhaul over PoE switching. Use Cat5e or better cabling and keep one Ethernet path per node.

## Factory reset

Hold the recessed reset pin for **10 seconds until the LED flashes blue**, then release. The node returns to an unclaimed state and must be claimed again in the Pro Console. Site history stored in the console is retained.

## Site checks

In the Pro Console, **Topology** should show every device as online. For any other light state, use the OrbitMesh Pro Series LED Reference rather than relying on color alone.
