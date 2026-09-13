# OrbitMesh Reset, Recovery, and Data-Loss Guide

**Document version:** 2.2  
**Published:** 2026-05-14

Use the least destructive action that addresses the documented state. A restart, pairing reset, and factory reset are different operations.

## Restart — no configuration loss

Disconnect the unit's power cable, wait 10 seconds, and reconnect it. A restart retains the network name, password, device assignments, and settings. Allow up to three minutes for normal operation to resume.

## N1 pairing reset — removes only that node's pairing

Use for **E24** or when instructed by the pairing procedure. With the N1 powered, hold its reset button for 5–7 seconds and release when the LED pulses blue. This removes the N1 from its current mesh but does not erase the R1 network or other nodes. Remove any pending copy of the N1 from the app before pairing again.

## R1 pairing reset — preserves network configuration

Use for **E17** before considering a factory reset. With the R1 powered, hold its reset button for 5–7 seconds and release when the LED pulses blue. Existing network settings remain stored. Return to the app and retry the documented setup flow.

## Factory reset — erases configuration

A factory reset erases the network name and password, node pairings, guest networks, parental controls, reservations, and all other local configuration. Every node must be paired again. Cloud account history is not erased.

Factory reset is a last resort after the applicable documented path has failed. Immediately before giving the reset action, the assistant must:

1. state what will be erased;
2. check that the customer can recreate the network and reconnect devices; and
3. ask for explicit confirmation to proceed.

Only after confirmation: with the unit powered, hold reset for at least 15 seconds until the LED flashes red, then release. Keep power connected while it recovers. Do not suggest a factory reset for an upstream ISP, DHCP, VLAN, placement, or known firmware problem.

## Recovery and escalation

Never disconnect power during flashing-red firmware recovery. If flashing continues beyond 10 minutes, or **E42** appears, contact support. There is no customer-supported firmware rollback, case opening, internal battery disconnection, or USB recovery procedure.
