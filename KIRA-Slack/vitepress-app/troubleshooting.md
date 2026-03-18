# Troubleshooting

## macOS says the app cannot be opened

If the current build is not notarized yet, macOS may show a warning on first launch.

Use this path:

1. In Finder, locate `KiraClaw.app`
2. Right-click the app
3. Choose `Open`
4. Confirm again

After that, later launches usually work normally.

## The old KIRA-Slack app still opens

That means you are still launching the legacy app.

Check that you installed and opened:

- `KiraClaw.app`

not the older `KIRA` app.

## I expected KIRA-Slack to auto-update in place

`KIRA-Slack` is now treated as legacy.

Use manual migration instead:

1. Download the latest `KiraClaw`
2. Install it as a separate app
3. Reuse your existing `~/.kira` config if needed

## Slack or Telegram is not responding

Check the desktop app:

- `Channels`
- `Runs`

If the runtime is healthy but no outward reply appears, inspect:

- internal summary
- spoken reply
- tool usage
- silent reason

from the `Runs` screen.

## Where are my local files?

KiraClaw keeps local state under your filesystem base directory, including:

- `skills/`
- `memories/`
- `schedule_data/`
- `logs/`

The desktop app can open the relevant folders directly.
