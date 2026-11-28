# ChannelForge Alpha 4.6.2

- The Windows in-app updater waits for the updating app to close. Updating TV can leave a running Studio on its current build so it can keep hosting. Update Studio from its own System menu when ready.
- Studio retries its existing VPS route when a startup check fails temporarily. It keeps the same relay key, channel lineup, and directory registration across upgrades.
- Studio reports a planned Windows update as reconnecting while it restarts.
- Includes the Linux relay recovery and desktop volume controls from Alpha 4.6.1.
