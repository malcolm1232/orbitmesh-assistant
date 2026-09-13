# OrbitMesh Network Compatibility Guide

**Document version:** 1.8  
**Published:** 2026-03-18

## Upstream equipment

The R1 WAN connection expects an Ethernet handoff and an address assigned by DHCP. An ISP modem/router may remain in router mode, although this creates double NAT and can affect inbound connections, some games, and corporate VPNs. Bridge or passthrough mode is preferred when available but is not required for ordinary browsing.

Some fibre providers require a VLAN ID on the WAN connection. OrbitMesh supports VLAN IDs 1–4094 under **Internet > Advanced > VLAN**. The customer must obtain the correct value from the provider; support must not guess it.

If the R1 shows **E31**, confirm the upstream modem's DHCP service and any provider-required VLAN. Do not factory-reset OrbitMesh merely because an ISP has not assigned an address.

## Wi-Fi compatibility

OrbitMesh broadcasts 2.4 GHz, 5 GHz, and 6 GHz under one network name. WPA2/WPA3 transitional security is the default. WPA3-only mode may prevent older smart-home devices from joining; use transitional mode when such a device requires WPA2.

OrbitMesh does not support WEP, hidden mesh backhaul, or using an N1 as the primary router. An R1 is always required.

## Ethernet backhaul

R1 and N1 Ethernet ports support automatic speed negotiation. Use Cat5e or better cabling. Pair an N1 before wiring it. Connect it to an R1 LAN port or a standard unmanaged switch downstream of the R1. An N1 connected directly to the modem/ONT is outside the OrbitMesh network and will appear offline.

Managed switches must allow ordinary untagged LAN traffic between OrbitMesh units. Loop prevention may disable a port if the same N1 has more than one wired path; use only one Ethernet path per N1.

## Environmental limits

Operate OrbitMesh indoors between 0°C and 40°C with ventilation around each unit. Radio performance degrades near metal enclosures, reinforced concrete, aquariums, and microwave ovens. Overheating, liquid exposure, or visible damage requires disconnection from power and escalation; do not attempt internal repair.
