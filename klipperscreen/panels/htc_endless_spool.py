import logging

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango, GLib
from ks_includes.screen_panel import ScreenPanel


GROUP_COLORS = [
    (0.90, 0.30, 0.30),  # Red
    (0.30, 0.70, 0.90),  # Blue
    (0.40, 0.85, 0.40),  # Green
    (0.95, 0.75, 0.20),  # Yellow
    (0.75, 0.45, 0.90),  # Purple
    (0.95, 0.55, 0.20),  # Orange
]


class Panel(ScreenPanel):
    """HappyToolchanger Endless Spool & TTG Map configuration panel."""

    def __init__(self, screen, title):
        title = title or "Endless Spool"
        super().__init__(screen, title)
        self.htc_data = {}
        self.num_tools = 0

        self._fetch_htc_status()
        self._build_ui()

    def _fetch_htc_status(self):
        result = self._screen.apiclient.send_request(
            "printer/objects/query?happy_toolchanger")
        if result and "status" in result:
            self.htc_data = result["status"].get("happy_toolchanger", {})
            self.num_tools = self.htc_data.get("num_tools", 0)

    def _build_ui(self):
        if self.num_tools == 0:
            label = Gtk.Label(label="HappyToolchanger not available")
            label.set_vexpand(True)
            self.content.add(label)
            return

        scroll = self._gtk.ScrolledWindow()
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                           margin_top=4, margin_start=8, margin_end=8)

        # --- Section 1: Endless Spool Toggle ---
        es_data = self.htc_data.get("endless_spool", {})
        enabled = es_data.get("enabled", False)

        toggle_box = Gtk.Box(spacing=12, margin_top=4, margin_bottom=4)
        toggle_label = Gtk.Label()
        toggle_label.set_markup("<big><b>Endless Spool</b></big>")
        toggle_label.set_halign(Gtk.Align.START)
        toggle_label.set_hexpand(True)

        self.es_switch = Gtk.Switch(halign=Gtk.Align.END,
                                    valign=Gtk.Align.CENTER)
        self.es_switch.set_active(enabled)
        self.es_switch.connect("notify::active", self._on_es_toggle)

        toggle_box.pack_start(toggle_label, True, True, 0)
        toggle_box.pack_end(self.es_switch, False, False, 0)
        main_box.pack_start(toggle_box, False, False, 0)

        # Description
        desc = Gtk.Label()
        desc.set_markup(
            "<small>Wenn eine Spule leer ist, wechselt der Drucker "
            "automatisch zu einem Tool in derselben Gruppe.</small>")
        desc.set_halign(Gtk.Align.START)
        desc.set_line_wrap(True)
        desc.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        main_box.pack_start(desc, False, False, 0)

        # Separator
        main_box.pack_start(Gtk.Separator(), False, False, 4)

        # --- Section 2: Group Assignment ---
        groups_label = Gtk.Label()
        groups_label.set_markup("<big><b>Gruppen</b></big>")
        groups_label.set_halign(Gtk.Align.START)
        main_box.pack_start(groups_label, False, False, 0)

        groups_desc = Gtk.Label()
        groups_desc.set_markup(
            "<small>Tools mit gleicher Gruppennummer teilen sich Filament. "
            "Bei Runout wird auf ein anderes Tool derselben Gruppe gewechselt."
            "</small>")
        groups_desc.set_halign(Gtk.Align.START)
        groups_desc.set_line_wrap(True)
        groups_desc.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        main_box.pack_start(groups_desc, False, False, 0)

        groups = es_data.get("groups", list(range(self.num_tools)))
        ttg_map = self.htc_data.get("ttg_map", list(range(self.num_tools)))
        materials = self.htc_data.get("gate_materials", [""] * self.num_tools)
        filament_names = self.htc_data.get("gate_filament_names",
                                            [""] * self.num_tools)

        self.group_spins = {}
        tools_grid = Gtk.Grid(column_homogeneous=False, row_spacing=6,
                              column_spacing=12, margin_top=8)

        # Header
        for col, text in enumerate(["Tool", "Gate", "Filament", "Gruppe"]):
            lbl = Gtk.Label()
            lbl.set_markup(f"<b>{text}</b>")
            tools_grid.attach(lbl, col, 0, 1, 1)

        for i in range(self.num_tools):
            gate = ttg_map[i] if i < len(ttg_map) else i
            mat = materials[gate] if gate < len(materials) else ""
            fname = filament_names[gate] if gate < len(filament_names) else ""
            group = groups[i] if i < len(groups) else i

            # Tool name
            tool_label = Gtk.Label(label=f"T{i}")
            tool_label.set_halign(Gtk.Align.CENTER)
            tools_grid.attach(tool_label, 0, i + 1, 1, 1)

            # Gate
            gate_label = Gtk.Label(label=str(gate))
            gate_label.set_halign(Gtk.Align.CENTER)
            tools_grid.attach(gate_label, 1, i + 1, 1, 1)

            # Filament info
            fil_text = fname if fname else (mat if mat else "-")
            fil_label = Gtk.Label(label=fil_text)
            fil_label.set_halign(Gtk.Align.START)
            fil_label.set_hexpand(True)
            fil_label.set_ellipsize(Pango.EllipsizeMode.END)
            tools_grid.attach(fil_label, 2, i + 1, 1, 1)

            # Group spinner
            adj = Gtk.Adjustment(value=group, lower=0,
                                 upper=self.num_tools - 1,
                                 step_increment=1)
            spin = Gtk.SpinButton(adjustment=adj, climb_rate=1, digits=0)
            spin.set_halign(Gtk.Align.CENTER)
            spin.set_size_request(80, -1)
            # Color the background based on group
            self._style_spin_by_group(spin, group)
            spin.connect("value-changed", self._on_group_changed, i)
            self.group_spins[i] = spin
            tools_grid.attach(spin, 3, i + 1, 1, 1)

        main_box.pack_start(tools_grid, False, False, 0)

        # --- Section 3: Buttons ---
        main_box.pack_start(Gtk.Separator(), False, False, 4)

        btn_box = Gtk.Box(spacing=8, homogeneous=True)

        apply_btn = self._gtk.Button(None, "Gruppen speichern", "color1",
                                      self.bts, Gtk.PositionType.LEFT, 1)
        apply_btn.connect("clicked", self._on_apply_groups)

        reset_ttg_btn = self._gtk.Button(None, "TTG Reset", "color3",
                                          self.bts, Gtk.PositionType.LEFT, 1)
        reset_ttg_btn.connect("clicked", self._on_reset_ttg)

        status_btn = self._gtk.Button(None, "Status", "color4",
                                       self.bts, Gtk.PositionType.LEFT, 1)
        status_btn.connect("clicked", self._on_show_status)

        btn_box.pack_start(apply_btn, True, True, 0)
        btn_box.pack_start(reset_ttg_btn, True, True, 0)
        btn_box.pack_start(status_btn, True, True, 0)
        main_box.pack_start(btn_box, False, False, 0)

        # --- Section 4: TTG Map Display ---
        main_box.pack_start(Gtk.Separator(), False, False, 4)

        ttg_label = Gtk.Label()
        ttg_label.set_markup("<big><b>Tool-to-Gate Map</b></big>")
        ttg_label.set_halign(Gtk.Align.START)
        main_box.pack_start(ttg_label, False, False, 0)

        ttg_text = "  ".join(
            [f"T{i}\u2192G{ttg_map[i]}" for i in range(self.num_tools)])
        self.ttg_display = Gtk.Label(label=ttg_text)
        self.ttg_display.set_halign(Gtk.Align.START)
        self.ttg_display.set_line_wrap(True)
        main_box.pack_start(self.ttg_display, False, False, 0)

        scroll.add(main_box)
        self.content.add(scroll)

    def _style_spin_by_group(self, spin, group):
        color = GROUP_COLORS[group % len(GROUP_COLORS)]
        r, g, b = int(color[0] * 255), int(color[1] * 255), int(color[2] * 255)
        css = f"spinbutton {{ background-color: rgba({r},{g},{b},0.3); }}"
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        spin.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _on_es_toggle(self, switch, gparam):
        enabled = 1 if switch.get_active() else 0
        self._screen._send_action(
            None, "printer.gcode.script",
            {"script": f"HTC_ENDLESS_SPOOL ENABLE={enabled}"})

    def _on_group_changed(self, spin, tool_idx):
        group = int(spin.get_value())
        self._style_spin_by_group(spin, group)

    def _on_apply_groups(self, widget):
        groups = []
        for i in range(self.num_tools):
            groups.append(str(int(self.group_spins[i].get_value())))
        groups_str = ",".join(groups)
        self._screen._send_action(
            widget, "printer.gcode.script",
            {"script": f"HTC_ENDLESS_SPOOL GROUPS={groups_str}"})
        self._screen.show_popup_message("Gruppen gespeichert", level=1)

    def _on_reset_ttg(self, widget):
        self._screen._confirm_send_action(
            widget,
            "Tool-to-Gate Map auf Standard zurücksetzen?",
            "printer.gcode.script",
            {"script": "HTC_RESET_TTG"})

    def _on_show_status(self, widget):
        self._screen._send_action(
            widget, "printer.gcode.script",
            {"script": "HTC_STATUS"})

    def _rebuild_ui(self):
        for child in self.content.get_children():
            self.content.remove(child)
        self.group_spins.clear()
        self._build_ui()
        self.content.show_all()

    def process_update(self, action, data):
        if action == "notify_status_update":
            if "happy_toolchanger" in data:
                self.htc_data.update(data["happy_toolchanger"])
                # Update TTG display if visible
                if hasattr(self, 'ttg_display') and self.ttg_display:
                    ttg_map = self.htc_data.get("ttg_map",
                                                 list(range(self.num_tools)))
                    ttg_text = "  ".join(
                        [f"T{i}\u2192G{ttg_map[i]}"
                         for i in range(self.num_tools)])
                    self.ttg_display.set_text(ttg_text)

    def activate(self):
        self._fetch_htc_status()
        self._rebuild_ui()
        self._screen._ws.klippy.object_subscription(
            {"happy_toolchanger": None})
