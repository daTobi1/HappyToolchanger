GATE_AVAILABLE = 1
GATE_EMPTY = 0

class EndlessSpool:
    def __init__(self, num_tools, groups=None, enabled=True):
        self.num_tools = num_tools
        self.groups = list(groups) if groups else list(range(num_tools))
        self.enabled = enabled

    def find_next_gate(self, current_gate, gate_status):
        if not self.enabled:
            return -1
        group = self.groups[current_gate]
        for i in range(1, self.num_tools):
            candidate = (current_gate + i) % self.num_tools
            if self.groups[candidate] == group and gate_status[candidate] == GATE_AVAILABLE:
                return candidate
        return -1

    def update_groups(self, new_groups):
        self.groups = list(new_groups)

    def get_status(self):
        return {
            'enabled': self.enabled,
            'groups': list(self.groups),
        }
