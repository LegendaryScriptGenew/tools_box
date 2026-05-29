# IMS Tool Architecture Notes

## Current Shape

`IMS_Tool_sub.py` is the integrated GUI shell. It owns:

- shared host selector and host management dialog
- tab layout and UI state
- packet capture tab interactions
- IMS NE upgrade tab interactions
- log viewer tab interactions

`IMS_Tool_workers.py` owns background execution logic:

- packet capture workers
- SBCM capture worker
- IMS upgrade worker
- log list/browse/tail/download workers
- NE start/stop service worker

This split keeps long-running SSH/SCP logic out of the QWidget class, so worker changes are less likely to break tab layout or GUI state.

## Extension Guidance

For a new IMS sub-tool, prefer one of these patterns:

1. If it is a standalone tool with its own QWidget, add it as a separate toolbox entry instead of merging it into `IMSTool`.
2. If it must share the IMS host bar and log panel, add it as a new tab in `IMSTool`, but put long-running work in `IMS_Tool_workers.py` or a new worker module.
3. If the tab grows beyond about 200-300 lines, move it into a dedicated controller/widget module, for example `ims_tool_capture_tab.py`.

## Recommended Next Refactor

The next useful split is tab-level modularization:

- `ims_tool_hosts.py`: host loading/saving and host management dialog
- `ims_tool_capture_tab.py`: capture tab UI and state
- `ims_tool_upgrade_tab.py`: upgrade tab UI and state
- `ims_tool_log_tab.py`: log viewer tab UI and state

After that, `IMS_Tool_sub.py` should become a thin container that wires shared host selection, the shared log panel, and tab widgets together.

## GUI Notes

The current UI works, but the interaction model can be improved:

- Disable actions while their worker is running, and show a clear busy state per tab.
- Keep batch operations visible near the table they affect.
- Add per-tab status labels instead of relying only on the shared bottom log.
- Avoid automatic SSH connection attempts on every host selection for slow or unstable networks.
- Prefer grouped controls over very long horizontal rows when adding more filters or options.
