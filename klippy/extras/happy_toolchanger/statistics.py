import time
from datetime import datetime, timezone


class Statistics:
    def __init__(self, num_tools, saved_data=None):
        self.num_tools = num_tools
        if saved_data:
            self._load(saved_data)
        else:
            self.reset()

    def reset(self):
        self.total_swaps = 0
        self.total_errors = 0
        self.endless_spool_events = 0
        self.per_tool = [
            {'swaps_to': 0, 'swaps_from': 0, 'time_active_s': 0}
            for _ in range(self.num_tools)
        ]
        self.last_reset = datetime.now(timezone.utc).isoformat(timespec='seconds')
        self._active_tool = -1
        self._active_since = None

    def _load(self, data):
        self.total_swaps = data.get('total_swaps', 0)
        self.total_errors = data.get('total_errors', 0)
        self.endless_spool_events = data.get('endless_spool_events', 0)
        self.last_reset = data.get('last_reset', '')
        saved_per_tool = data.get('per_tool', [])
        self.per_tool = []
        for i in range(self.num_tools):
            if i < len(saved_per_tool):
                self.per_tool.append(dict(saved_per_tool[i]))
            else:
                self.per_tool.append({'swaps_to': 0, 'swaps_from': 0, 'time_active_s': 0})
        self._active_tool = -1
        self._active_since = None

    def record_swap(self, from_tool, to_tool):
        self.total_swaps += 1
        if 0 <= from_tool < self.num_tools:
            self.per_tool[from_tool]['swaps_from'] += 1
        if 0 <= to_tool < self.num_tools:
            self.per_tool[to_tool]['swaps_to'] += 1

    def record_error(self):
        self.total_errors += 1

    def record_endless_spool_event(self):
        self.endless_spool_events += 1

    def set_active_tool(self, tool, now):
        if self._active_tool >= 0 and self._active_since is not None:
            elapsed = now - self._active_since
            if 0 <= self._active_tool < self.num_tools:
                self.per_tool[self._active_tool]['time_active_s'] += elapsed
        self._active_tool = tool
        self._active_since = now if tool >= 0 else None

    def get_data(self):
        return {
            'total_swaps': self.total_swaps,
            'total_errors': self.total_errors,
            'endless_spool_events': self.endless_spool_events,
            'per_tool': [dict(pt) for pt in self.per_tool],
            'last_reset': self.last_reset,
        }
