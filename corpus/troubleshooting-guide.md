# OrbitMesh Troubleshooting Guide

**Applies to:** OrbitMesh R1 and N1  
**Document version:** 3.0  
**Published:** 2026-05-02

Use the exact LED pattern, device type, connection method, and when the symptom began to choose a procedure. Do not perform several changes at once; check the result after each step.

## N1 node disconnects intermittently

First ask whether the N1 uses wireless or Ethernet backhaul and record its LED pattern during the failure.

### Wireless N1

- **Flashing amber:** Move the node temporarily into the same room as the nearest online OrbitMesh device. Wait two minutes. If it becomes solid white, its previous location had a weak mesh link. Reposition it one or two rooms from the nearest online device.
- **Solid amber:** Check the app for a firmware update or recovery notice. Do not treat this as a placement problem.
- **No light:** Confirm the supplied power adapter is firmly connected and try a known-working wall outlet. If it remains unlit, stop and escalate to support.
- **Solid white while clients disconnect:** Check the firmware version and whether all clients or only one client are affected. Version 3.4.1 has a documented N1 roaming issue; follow the Firmware Release Notes. For one affected client, forget and rejoin the Wi-Fi network on that client before changing OrbitMesh settings.

### Ethernet-connected N1

Confirm the N1 is connected to a white LAN port on the R1 or to a functioning Ethernet switch, not directly to the modem/ONT. Reseat both cable ends. If it stays offline, test with another known-working Ethernet cable. Do not move a wired N1 closer as the first step.

## Initial setup cannot reach the internet

For **E11**, check that the modem/ONT cable reaches the R1 blue WAN port. Restart the modem/ONT, wait until it is ready, and then restart the R1. If E11 remains, consult the Network Compatibility Guide for bridge mode, DHCP, and VLAN requirements.

For **E17: Router already configured**, confirm that the customer intends to add the R1 to a new network. Perform a pairing reset first. A factory reset is a last resort and must follow the Reset, Recovery, and Data-Loss Guide.

## N1 will not pair

Place the node within 2 metres of the R1 and wait three minutes. If it never pulses blue, restart the N1 once. If it pulses blue but the app shows **E24**, remove the pending node from the app and perform the documented pairing reset. Retry pairing before considering a factory reset.

## Slow connection

Establish whether every device is affected, which OrbitMesh unit it uses, and whether the problem occurs over Wi-Fi, Ethernet, or both. Run the app's **Internet test** at the R1 and record the result.

- A normal R1 test with only wireless clients affected indicates a local Wi-Fi or placement issue.
- A slow R1 test over both Wi-Fi and Ethernet indicates an upstream modem/ISP or WAN issue; restart the modem/ONT once, then contact the ISP if its service remains slow.
- If only one client is affected, update or reconnect that client before resetting OrbitMesh equipment.

## Stop conditions

Escalate when a device remains unpowered in a known-working outlet, repeatedly overheats, shows visible damage, smells burnt, or remains in the same failure state after the applicable documented path. Never ask a customer to open a device or power adapter.
