# Reactive Cockpit Story Platform 0.12.3

## Altitude exit hardening

Elite removes its surface-position fields after a ship leaves planetary
navigation. The runtime previously retained the last reported low altitude.
An atmosphere layer with a maximum altitude could therefore remain active in
space until a separate `LeaveBody` or `SupercruiseEntry` journal event arrived.

This update clears stale ship surface coordinates when Elite reports that the
latitude/longitude fix has been lost. An altitude-bounded environment now
treats that known loss as a location exit. The existing exception for on-foot
play is preserved because Elite can legitimately omit altitude while the
commander remains inside a surface zone.

The graph's authored altitude value is unchanged. Mission 2's Aiabiko
atmosphere remains limited to `100000.0` metres (100 km / 0.1 Mm).

## Mission 2 final polish

- Updates the Bardin's Vice landing and exploration objectives.
- Names Muni consistently in the post-survey waiting objectives.
- Reduces the waiting-music level.
- Removes the obsolete mission-complete triumph cue.
