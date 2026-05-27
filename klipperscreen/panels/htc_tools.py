import logging

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, Pango, GLib
from ks_includes.screen_panel import ScreenPanel


class Panel(ScreenPanel):
    """HappyToolchanger Tools panel — shows tool-to-spool assignments
    and allows assigning Spoolman spools to each tool's gate."""

    def __init__(self, screen, title):
        title = title or "HTC Tools"
        super().__init__(screen, title)
        self.htc_data = {}
        self.spools_cache = []
        self.tool_buttons = {}
        self.num_tools = 0
        self._spool_select_gate = None

        self._fetch_htc_status()
        self._build_ui()

    def _fetch_htc_status(self):
        result = self._screen.apiclient.send_request(
            "printer/objects/query?happy_toolchanger")
        if result and "status" in result:
            self.htc_data = result["status"].get("happy_toolchanger", {})
            self.num_tools = self.htc_data.get("num_tools", 0)

    def _fetch_spools(self):
        result = self._screen.apiclient.post_request(
            "server/spoolman/proxy", json={
                "request_method": "GET",
                "path": "/v1/spool?allow_archived=false",
            })
        if result and "result" in result:
            self.spools_cache = result["result"]
        else:
            self.spools_cache = []

    def _build_ui(self):
        if self.num_tools == 0:
            label = Gtk.Label(label="HappyToolchanger not available")
            label.set_vexpand(True)
            self.content.add(label)
            return

        scroll = self._gtk.ScrolledWindow()
        self.tools_grid = Gtk.Grid(
            column_homogeneous=True,
            row_homogeneous=True,
            hexpand=True,
            vexpand=True,
        )

        cols = 3 if self.num_tools > 4 else 2
        for i in range(self.num_tools):
            btn = self._create_tool_button(i)
            self.tool_buttons[i] = btn
            col = i % cols
            row = i // cols
            self.tools_grid.attach(btn, col, row, 1, 1)

        scroll.add(self.tools_grid)
        self.content.add(scroll)

    def _create_tool_button(self, tool_idx):
        gate = self._get_gate(tool_idx)
        spool_id = self._get_spool_id(gate)
        material = self._get_material(gate)
        filament_name = self._get_filament_name(gate)
        color_hex = self._get_color(gate)
        active = self.htc_data.get("active_tool", -1) == tool_idx

        # Build label text
        lines = [f"<big><b>T{tool_idx}</b></big>"]
        if active:
            lines[0] = f"<big><b>T{tool_idx} [aktiv]</b></big>"

        if spool_id > 0:
            if filament_name:
                lines.append(f"{filament_name}")
            if material:
                lines.append(f"<small>{material}</small>")
        else:
            lines.append("<small>Keine Spule</small>")

        if gate != tool_idx:
            lines.append(f"<small>Gate {gate}</small>")

        label_text = "\n".join(lines)

        btn = Gtk.Button(hexpand=True, vexpand=True)
        btn.connect("clicked", self._on_tool_click, tool_idx)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                      spacing=4, valign=Gtk.Align.CENTER)

        # Color indicator
        if color_hex and color_hex.strip():
            color_bar = Gtk.DrawingArea()
            color_bar.set_size_request(-1, 8)
            parsed_color = self._parse_color(color_hex)
            color_bar.connect("draw", self._draw_color_bar, parsed_color)
            box.pack_start(color_bar, False, False, 0)

        label = Gtk.Label()
        label.set_markup(label_text)
        label.set_justify(Gtk.Justification.CENTER)
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        box.pack_start(label, True, True, 0)

        btn.add(box)

        # Style active tool
        if active:
            ctx = btn.get_style_context()
            ctx.add_class("button_active")

        return btn

    def _draw_color_bar(self, widget, cr, color):
        width = widget.get_allocated_width()
        cr.set_source_rgb(color[0], color[1], color[2])
        cr.rectangle(0, 0, width, 8)
        cr.fill()

    def _parse_color(self, hex_color):
        hex_color = hex_color.lstrip('#')
        if len(hex_color) == 6:
            r = int(hex_color[0:2], 16) / 255.0
            g = int(hex_color[2:4], 16) / 255.0
            b = int(hex_color[4:6], 16) / 255.0
            return (r, g, b)
        return (0.5, 0.5, 0.5)

    def _get_gate(self, tool_idx):
        ttg = self.htc_data.get("ttg_map", [])
        return ttg[tool_idx] if tool_idx < len(ttg) else tool_idx

    def _get_spool_id(self, gate):
        ids = self.htc_data.get("gate_spool_ids", [])
        return ids[gate] if gate < len(ids) else -1

    def _get_material(self, gate):
        mats = self.htc_data.get("gate_materials", [])
        return mats[gate] if gate < len(mats) else ""

    def _get_filament_name(self, gate):
        names = self.htc_data.get("gate_filament_names", [])
        return names[gate] if gate < len(names) else ""

    def _get_color(self, gate):
        colors = self.htc_data.get("gate_colors", [])
        return colors[gate] if gate < len(colors) else ""

    # --- Tool click: open spool assignment ---

    def _on_tool_click(self, widget, tool_idx):
        gate = self._get_gate(tool_idx)
        self._spool_select_gate = gate
        self._fetch_spools()
        self._show_spool_selector(tool_idx, gate)

    def _show_spool_selector(self, tool_idx, gate):
        current_spool_id = self._get_spool_id(gate)

        scroll = self._gtk.ScrolledWindow()
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)

        # "Clear spool" row
        clear_row = Gtk.ListBoxRow()
        clear_box = Gtk.Box(spacing=10, margin_top=4, margin_bottom=4,
                            margin_start=8, margin_end=8)
        clear_label = Gtk.Label()
        clear_label.set_markup("<b>Keine Spule</b>\nSpulenzuordnung entfernen")
        clear_label.set_halign(Gtk.Align.START)
        clear_label.set_line_wrap(True)
        clear_box.pack_start(clear_label, True, True, 0)
        if current_spool_id <= 0:
            check = Gtk.Image.new_from_icon_name("object-select-symbolic",
                                                  Gtk.IconSize.BUTTON)
            clear_box.pack_end(check, False, False, 0)
        clear_row.add(clear_box)
        listbox.add(clear_row)

        # Spool rows
        for spool in sorted(self.spools_cache, key=lambda s: s.get("id", 0)):
            spool_id = spool.get("id", 0)
            filament = spool.get("filament", {})
            vendor = filament.get("vendor", {})
            vendor_name = vendor.get("name", "") if vendor else ""
            fil_name = filament.get("name", "")
            material = filament.get("material", "")
            color_hex = filament.get("color_hex", "")
            remaining = spool.get("remaining_weight")

            display_name = f"{vendor_name} - {fil_name}" if vendor_name else fil_name

            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(spacing=10, margin_top=4, margin_bottom=4,
                           margin_start=8, margin_end=8)

            # Color dot
            if color_hex:
                color_dot = Gtk.DrawingArea()
                color_dot.set_size_request(24, 24)
                parsed = self._parse_color(color_hex)
                color_dot.connect("draw", self._draw_color_dot, parsed)
                hbox.pack_start(color_dot, False, False, 0)

            # Spool info
            info_label = Gtk.Label()
            remain_str = f" ({remaining:.0f}g)" if remaining is not None else ""
            info_label.set_markup(
                f"<b>#{spool_id}</b> {display_name}\n"
                f"<small>{material}{remain_str}</small>")
            info_label.set_halign(Gtk.Align.START)
            info_label.set_line_wrap(True)
            info_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            hbox.pack_start(info_label, True, True, 0)

            # Active indicator
            if spool_id == current_spool_id:
                check = Gtk.Image.new_from_icon_name(
                    "object-select-symbolic", Gtk.IconSize.BUTTON)
                hbox.pack_end(check, False, False, 0)

            row.add(hbox)
            listbox.add(row)

        listbox.connect("row-activated", self._on_spool_selected, gate, tool_idx)
        scroll.add(listbox)

        # Title bar
        title_box = Gtk.Box(spacing=8)
        back_btn = self._gtk.Button("arrow-left", None, "color1",
                                     self.bts, Gtk.PositionType.LEFT, 1)
        back_btn.connect("clicked", self._on_spool_selector_back)
        back_btn.set_hexpand(False)
        title_label = Gtk.Label()
        title_label.set_markup(f"<big><b>T{tool_idx} — Spule zuordnen</b></big>")
        title_label.set_halign(Gtk.Align.START)
        title_label.set_hexpand(True)
        refresh_btn = self._gtk.Button("refresh", None, "color2",
                                        self.bts, Gtk.PositionType.LEFT, 1)
        refresh_btn.connect("clicked", self._on_spool_refresh,
                            tool_idx, gate)
        title_box.pack_start(back_btn, False, False, 0)
        title_box.pack_start(title_label, True, True, 0)
        title_box.pack_end(refresh_btn, False, False, 0)

        # Replace content
        for child in self.content.get_children():
            self.content.remove(child)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.pack_start(title_box, False, False, 4)
        main.pack_start(scroll, True, True, 0)
        self.content.add(main)
        self.content.show_all()

    def _draw_color_dot(self, widget, cr, color):
        cr.set_source_rgb(color[0], color[1], color[2])
        cr.arc(12, 12, 10, 0, 2 * 3.14159)
        cr.fill()

    def _on_spool_selected(self, listbox, row, gate, tool_idx):
        idx = row.get_index()
        if idx == 0:
            # Clear spool
            self._assign_spool(gate, -1, "", "", "", 0)
        else:
            spool = sorted(self.spools_cache,
                           key=lambda s: s.get("id", 0))[idx - 1]
            spool_id = spool.get("id", 0)
            filament = spool.get("filament", {})
            vendor = filament.get("vendor", {})
            vendor_name = vendor.get("name", "") if vendor else ""
            fil_name = filament.get("name", "")
            material = filament.get("material", "")
            color_hex = filament.get("color_hex", "")
            temp = filament.get("settings_extruder_temp", 0) or 0
            display_name = f"{vendor_name} {fil_name}" if vendor_name else fil_name
            self._assign_spool(gate, spool_id, color_hex,
                               material, display_name, temp)

        # Return to tools view
        self._on_spool_selector_back(None)

    def _assign_spool(self, gate, spool_id, color, material, name, temp):
        cmd = f'HTC_SET_GATE GATE={gate} SPOOL_ID={spool_id}'
        if color:
            cmd += f' COLOR={color}'
        if material:
            cmd += f' MATERIAL="{material}"'
        if name:
            cmd += f' NAME="{name}"'
        if temp:
            cmd += f' TEMP={temp}'
        self._screen._send_action(None, "printer.gcode.script",
                                  {"script": cmd})

    def _on_spool_selector_back(self, widget):
        self._fetch_htc_status()
        for child in self.content.get_children():
            self.content.remove(child)
        self.tool_buttons.clear()
        self._build_ui()
        self.content.show_all()

    def _on_spool_refresh(self, widget, tool_idx, gate):
        self._fetch_spools()
        self._show_spool_selector(tool_idx, gate)

    def process_update(self, action, data):
        if action == "notify_status_update":
            if "happy_toolchanger" in data:
                self.htc_data.update(data["happy_toolchanger"])
                self._refresh_tool_buttons()

    def _refresh_tool_buttons(self):
        if not self.tool_buttons:
            return
        cols = 3 if self.num_tools > 4 else 2
        for child in self.tools_grid.get_children():
            self.tools_grid.remove(child)
        for i in range(self.num_tools):
            btn = self._create_tool_button(i)
            self.tool_buttons[i] = btn
            col = i % cols
            row = i // cols
            self.tools_grid.attach(btn, col, row, 1, 1)
        self.tools_grid.show_all()

    def activate(self):
        self._fetch_htc_status()
        self._refresh_tool_buttons()
        self._screen._ws.klippy.object_subscription(
            {"happy_toolchanger": None})
