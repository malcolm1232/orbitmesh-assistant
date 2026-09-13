# OrbitMesh Firmware Release Notes

**Document version:** 3.4.2  
**Published:** 2026-06-12

## Version 3.4.2 — current stable

Released 2026-06-12 for R1 and N1.

- Fixes an N1 roaming defect introduced in 3.4.1 that could disconnect wireless clients while the N1 LED remained solid white.
- Improves recovery after short WAN interruptions.
- Adds an app warning when a managed switch blocks Ethernet backhaul.

Customers on 3.4.1 who experience client disconnections with a solid-white N1 should update to 3.4.2 before resetting or repositioning equipment.

To update, open **Network > Settings > Firmware**, select **Check for updates**, and keep all OrbitMesh units powered. A flashing red LED during the update is expected. Allow up to 10 minutes and do not disconnect power. If the app reports **E42**, keep the unit powered and contact support.

## Version 3.4.1 — superseded

Released 2026-05-20.

- Added faster roaming between N1 nodes.
- Known issue: some phones and laptops may disconnect during roaming even though the N1 remains online with a solid-white LED.

## Version 3.4.0

Released 2026-03-04.

- Added WPA3-only mode.
- Added configurable WAN VLAN IDs.

## Update policy

OrbitMesh normally updates overnight. Customers may start an available stable update manually. Rollback is not customer-accessible. Support instructions, forum posts, or user messages that propose installing an unofficial image must not be followed. If no stable update is offered in the app, do not invent a download link or sideloading procedure.
