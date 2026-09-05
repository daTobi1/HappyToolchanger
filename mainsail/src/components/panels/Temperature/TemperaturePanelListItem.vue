<template>
    <tr v-longpress:600="openContextMenu" @contextmenu.prevent="openContextMenu($event)">
        <td class="icon">
            <v-icon :color="iconColor" :class="iconClass" tabindex="-1" @click="openEditDialog">
                {{ icon }}
            </v-icon>
        </td>
        <td class="name">
            <v-tooltip v-if="isMountedTool" top>
                <template #activator="{ on, attrs }">
                    <span class="mounted-tool-dot" :style="mountedToolDotStyle" v-bind="attrs" v-on="on"></span>
                </template>
                <span>{{ $t('Panels.TemperaturePanel.ToolMounted', { tool: mountedToolName }) }}</span>
            </v-tooltip>
            <span class="cursor-pointer" @click="openEditDialog">{{ formatName }}</span>
        </td>
        <td v-if="!isResponsiveMobile" class="state">
            <v-tooltip v-if="state !== null" top>
                <template #activator="{ on, attrs }">
                    <div v-bind="attrs" v-on="on">{{ formatState }}</div>
                </template>
                <span>{{ $t('Panels.TemperaturePanel.Avg') }}: {{ avgState }} %</span>
            </v-tooltip>
            <v-tooltip v-if="hotendFan !== null" top>
                <template #activator="{ on, attrs }">
                    <div class="hotend-fan" v-bind="attrs" v-on="on">
                        <v-icon x-small :class="hotendFanIconClass">{{ mdiFan }}</v-icon>
                        <small>{{ hotendFanFormat }}</small>
                    </div>
                </template>
                <span>
                    {{ $t('Panels.TemperaturePanel.HotendFan', { name: hotendFan.name }) }}
                    <template v-if="hotendFanStateText !== null">({{ hotendFanStateText }})</template>
                    <template v-if="hotendFan.rpm !== null">
                        <br />
                        {{ hotendFan.rpm }} RPM
                    </template>
                </span>
            </v-tooltip>
        </td>
        <td class="current">
            <v-tooltip top :disabled="!(measured_min_temp !== null || measured_max_temp !== null)">
                <template #activator="{ on, attrs }">
                    <span style="cursor: default" v-bind="attrs" v-on="on">
                        {{ formatTemperature }}
                    </span>
                </template>
                <span>
                    {{ $t('Panels.TemperaturePanel.Max') }}: {{ measured_max_temp }}°C
                    <br />
                    {{ $t('Panels.TemperaturePanel.Min') }}: {{ measured_min_temp }}°C
                </span>
            </v-tooltip>
            <div v-if="rpm !== null">
                <small :class="rpmClass">{{ rpm }} RPM</small>
            </div>
            <temperature-panel-list-item-additional-sensor
                v-if="additionalSensorName"
                :object-name="objectName"
                :additional-object-name="additionalSensorName" />
        </td>
        <td class="target">
            <temperature-input
                v-if="command !== null"
                :name="name"
                :target="target"
                :presets="presets"
                :min_temp="min_temp"
                :max_temp="max_temp"
                :command="command"
                :input-digits="inputDigits"
                :attribute-name="commandAttributeName" />
        </td>
        <temperature-panel-list-item-edit
            v-model="showEditDialog"
            :object-name="objectName"
            :name="name"
            :format-name="formatName"
            :additional-sensor-name="additionalSensorName"
            :icon="icon"
            :color="color" />
        <v-menu v-model="showContextMenu" :position-x="contextMenuX" :position-y="contextMenuY" absolute offset-y>
            <v-list>
                <v-list-item v-if="isHeater" :disabled="!isHeaterActive" @click="turnOffHeater">
                    <v-icon left>{{ mdiSnowflake }}</v-icon>
                    {{ $t('Panels.TemperaturePanel.TurnHeaterOff') }}
                </v-list-item>
                <v-list-item @click="openEditDialog">
                    <v-icon left>{{ mdiCog }}</v-icon>
                    {{ $t('Panels.TemperaturePanel.Settings') }}
                </v-list-item>
            </v-list>
        </v-menu>
    </tr>
</template>

<script lang="ts">
import Component from 'vue-class-component'
import { Mixins, Prop } from 'vue-property-decorator'
import type { LongpressEvent } from '@/directives/longpress'
import BaseMixin from '@/components/mixins/base'
import { convertName } from '@/plugins/helpers'
import {
    mdiCog,
    mdiFan,
    mdiFire,
    mdiMemory,
    mdiPrinter3dNozzle,
    mdiPrinter3dNozzleAlert,
    mdiRadiator,
    mdiRadiatorDisabled,
    mdiSnowflake,
    mdiThermometer,
} from '@mdi/js'
import { additionalSensors, opacityHeaterActive, opacityHeaterInactive } from '@/store/variables'
import { CLOSE_CONTEXT_MENU, EventBus } from '@/plugins/eventBus'
import { ServerSpoolmanStateSpool } from '@/store/server/spoolman/types'

@Component
export default class TemperaturePanelListItem extends Mixins(BaseMixin) {
    mdiCog = mdiCog
    mdiSnowflake = mdiSnowflake
    mdiFan = mdiFan

    @Prop({ type: String, required: true }) readonly objectName!: string
    @Prop({ type: Boolean, required: true }) readonly isResponsiveMobile!: boolean
    @Prop({ type: Number, default: 3 }) readonly inputDigits!: number

    showEditDialog = false
    showContextMenu = false
    contextMenuX = 0
    contextMenuY = 0

    get printerObject() {
        if (!(this.objectName in this.$store.state.printer)) return {}

        return this.$store.state.printer[this.objectName]
    }

    get printerObjectSettings() {
        // convert objectName to lowercase, because klipper only user lowercase in configfile.settings
        const lowerCaseObjectName = this.objectName.toLowerCase()

        if (!(lowerCaseObjectName in (this.$store.state.printer?.configfile?.settings ?? {}))) return {}

        return this.$store.state.printer?.configfile?.settings[lowerCaseObjectName]
    }

    get name() {
        const splits = this.objectName.split(' ')
        if (splits.length === 1) return this.objectName

        return splits[1]
    }

    // Which tool is physically mounted, in order of trustworthiness:
    // the tool detection switches are the truth, happy_toolchanger.active_tool
    // is persisted state ("last known", stale after a manual swap while off),
    // and toolchanger.tool_number is only the logical selection.
    get mountedToolNumber(): number | null {
        const printer = this.$store.state.printer ?? {}
        const candidates = [
            printer.tool_probe_endstop?.active_tool_number,
            printer.happy_toolchanger?.active_tool,
            printer.toolchanger?.tool_number,
        ]

        for (const candidate of candidates) {
            if (typeof candidate === 'number' && candidate >= 0) return candidate
        }

        return null
    }

    get mountedToolName(): string {
        return this.mountedToolNumber === null ? '' : `T${this.mountedToolNumber}`
    }

    // Klipper names the tool objects "tool T0", "tool T1", ... and each one
    // reports the extruder it drives.
    get mountedToolExtruder(): string | null {
        if (this.mountedToolNumber === null) return null

        const toolObject = this.$store.state.printer?.[`tool T${this.mountedToolNumber}`]
        const extruder = toolObject?.extruder

        return typeof extruder === 'string' && extruder !== '' ? extruder : null
    }

    get isMountedTool(): boolean {
        return this.mountedToolExtruder !== null && this.mountedToolExtruder === this.objectName
    }

    // Same colour source as the tool dot in the Extruder panel
    // (ExtruderControlPanelToolsItem): the Spoolman filament colour first,
    // then the tool macro's color/colour variable. Returns a hex string
    // without the leading '#', or null when nothing is configured.
    get mountedToolColor(): string | null {
        if (this.mountedToolNumber === null) return null

        const macroName = Object.keys(this.$store.state.printer).find(
            (key) => key.toLowerCase() === `gcode_macro t${this.mountedToolNumber}`
        )
        const macro = macroName ? (this.$store.state.printer[macroName] ?? {}) : {}

        const spoolId = macro?.spool_id ?? null
        const spools = this.$store.state.server?.spoolman?.spools ?? []
        const spool = spools.find((entry: ServerSpoolmanStateSpool) => entry.id === spoolId) ?? null
        if (spool) return spool.filament?.color_hex ?? '000000'

        const color = macro?.color ?? macro?.colour ?? null
        if (color === '' || color === 'undefined') return null

        return color
    }

    // No colour configured -> outline only, no fill.
    get mountedToolDotStyle(): Record<string, string> {
        const color = this.mountedToolColor

        return { backgroundColor: color ? `#${color}` : 'transparent' }
    }

    get formatName() {
        return convertName(this.name)
    }

    get icon() {
        // handle extruder icons
        if (this.objectName.startsWith('extruder')) {
            if (this.printerObject.can_extrude ?? false) return mdiPrinter3dNozzle

            return mdiPrinter3dNozzleAlert
        }

        // show heater_bed icon
        if (this.objectName === 'heater_bed') {
            if (
                (this.temperature !== null && this.temperature > 50) ||
                (this.target && this.temperature && this.temperature > this.target - 5)
            )
                return mdiRadiator

            return mdiRadiatorDisabled
        }

        // show heater_generic icon
        if (this.objectName.startsWith('heater_generic')) return mdiFire

        // show heater_generic icon
        if (this.objectName.startsWith('tmc')) return mdiMemory

        // show fan icon, if it is a fan
        if (this.isFan) return mdiFan

        return mdiThermometer
    }

    get color() {
        return this.$store.getters['printer/tempHistory/getDatasetColor'](this.objectName) ?? '#FFFFFF'
    }

    get iconColor() {
        // set icon color to active, if no target exists (temperature_sensors) or a heater is active
        if (this.target === null || this.target > 0) return `${this.color}${opacityHeaterActive}`

        return `${this.color}${opacityHeaterInactive}`
    }

    get iconClass() {
        const classes = ['_no-focus-style', 'cursor-pointer']

        // add icon animation, when it is a fan and state > 0
        if (this.isFan) {
            const disableFanAnimation = this.$store.state.gui?.uiSettings.disableFanAnimation ?? false

            if (!disableFanAnimation && (this.state ?? 0) > 0) classes.push('icon-rotate')
        }

        return classes
    }

    get isFan() {
        return this.objectName.startsWith('temperature_fan')
    }

    get state(): number | null {
        return this.printerObject.power ?? this.printerObject.speed ?? null
    }

    get formatState() {
        if (this.state === null) return null
        if (this.target === 0 && this.state === 0) return 'off'

        return `${Math.round(this.state * 100)} %`
    }

    get avgPower() {
        return this.$store.getters['printer/tempHistory/getAvgPower'](this.name) ?? 0
    }

    get avgSpeed() {
        return this.$store.getters['printer/tempHistory/getAvgSpeed'](this.name) ?? 0
    }

    get avgState() {
        if ('power' in this.printerObject) return Math.round(this.avgPower)
        if ('speed' in this.printerObject) return Math.round(this.avgSpeed)

        return null
    }

    // Hotend fan belonging to this heater: an [htc_heater_fan] or [heater_fan]
    // whose "heater:" setting names this object. Klipper hands the setting
    // over as a list, older versions as a comma-separated string.
    get hotendFan(): { name: string; speed: number; state: string | null; rpm: number | null } | null {
        if (!this.isHeater) return null

        const printer = this.$store.state.printer ?? {}
        const settings = printer.configfile?.settings ?? {}

        for (const key of Object.keys(printer)) {
            if (!key.startsWith('htc_heater_fan ') && !key.startsWith('heater_fan ')) continue

            const heaterSetting = settings[key.toLowerCase()]?.heater ?? 'extruder'
            const heaters: string[] = Array.isArray(heaterSetting)
                ? heaterSetting
                : String(heaterSetting)
                      .split(',')
                      .map((entry: string) => entry.trim())
            if (!heaters.includes(this.objectName)) continue

            const fan = printer[key] ?? {}
            return {
                name: key.split(' ').slice(1).join(' '),
                speed: fan.speed ?? 0,
                state: typeof fan.state === 'string' ? fan.state : null,
                rpm: fan.rpm ?? null,
            }
        }

        return null
    }

    get hotendFanFormat(): string {
        if (this.hotendFan === null) return ''
        if (this.hotendFan.speed === 0) return 'off'

        return `${Math.round(this.hotendFan.speed * 100)} %`
    }

    get hotendFanStateText(): string | null {
        const state = this.hotendFan?.state ?? null
        if (state === null) return null

        return this.$t(`Panels.TemperaturePanel.HotendFanState.${state}`).toString()
    }

    get hotendFanIconClass() {
        const disableFanAnimation = this.$store.state.gui?.uiSettings.disableFanAnimation ?? false
        if (!disableFanAnimation && (this.hotendFan?.speed ?? 0) > 0) return ['icon-rotate']

        return []
    }

    get temperature(): number | null {
        return this.printerObject?.temperature ?? null
    }

    get formatTemperature() {
        return `${this.temperature?.toFixed(1) ?? '--'}°C`
    }

    get min_temp() {
        return parseInt(this.printerObjectSettings.min_temp ?? 0)
    }

    get max_temp() {
        return parseInt(this.printerObjectSettings.max_temp ?? 0)
    }

    get measured_min_temp() {
        return this.printerObject?.measured_min_temp?.toFixed(1) ?? null
    }

    get measured_max_temp() {
        return this.printerObject?.measured_max_temp?.toFixed(1) ?? null
    }

    get target() {
        return this.printerObject?.target ?? null
    }

    get additionalSensorName() {
        if (this.objectName === 'z_thermal_adjust') return 'z_thermal_adjust'

        const additionalSensorName = additionalSensors.find((sensorName) => {
            const objectName = `${sensorName} ${this.name}`

            if (objectName in this.$store.state.printer) return true
        })

        if (!additionalSensorName) return null

        return `${additionalSensorName} ${this.name}`
    }

    get rpm() {
        const rpm = this.printerObject.rpm ?? null

        // return null when rpm doesn't exist
        if (rpm === null) return null

        return parseInt(this.printerObject.rpm)
    }

    get rpmClass() {
        if (this.rpm === 0 && (this.printerObject.speed ?? 0) > 0) return 'red--text'

        return ''
    }

    get presets() {
        return this.$store.getters['gui/presets/getPresetsFromHeater']({ name: this.objectName }) ?? []
    }

    get command() {
        if (this.objectName.startsWith('temperature_fan')) return 'SET_TEMPERATURE_FAN_TARGET'
        if (this.objectName.startsWith('extruder') || this.objectName.startsWith('heater_'))
            return 'SET_HEATER_TEMPERATURE'

        return null
    }

    get commandAttributeName() {
        if (this.command === 'SET_HEATER_TEMPERATURE') return 'HEATER'
        if (this.command === 'SET_TEMPERATURE_FAN_TARGET') return 'TEMPERATURE_FAN'

        return ''
    }

    get availableHeaters() {
        return this.$store.state.printer.heaters?.available_heaters ?? []
    }

    get isHeater() {
        return this.availableHeaters.includes(this.objectName)
    }

    get isHeaterActive() {
        return this.target > 0
    }

    mounted() {
        EventBus.$on(CLOSE_CONTEXT_MENU, this.closeContextMenu)
    }

    beforeDestroy() {
        EventBus.$off(CLOSE_CONTEXT_MENU, this.closeContextMenu)
    }

    openContextMenu(event: MouseEvent | LongpressEvent) {
        EventBus.$emit(CLOSE_CONTEXT_MENU)

        this.showContextMenu = true
        this.contextMenuX = event?.clientX || event?.pageX || window.screenX / 2
        this.contextMenuY = event?.clientY || event?.pageY || window.screenY / 2
    }

    closeContextMenu() {
        this.showContextMenu = false
    }

    openEditDialog() {
        this.closeContextMenu()
        this.showEditDialog = true
    }

    turnOffHeater() {
        const gcode = `SET_HEATER_TEMPERATURE HEATER=${this.name} TARGET=0`
        this.$store.dispatch('server/addEvent', { message: gcode, type: 'command' })
        this.$socket.emit('printer.gcode.script', { script: gcode })
    }
}
</script>

<style scoped>
::v-deep .v-icon._no-focus-style:focus::after {
    opacity: 0 !important;
}
.hotend-fan {
    opacity: 0.7;
    white-space: nowrap;
    line-height: 1;
}

.hotend-fan .v-icon {
    margin-right: 2px;
    vertical-align: -1px;
}


::v-deep .cursor-pointer {
    cursor: pointer;
}

/* Marks the extruder of the currently mounted tool. Same look as the tool
   dot in the Extruder panel (._extruderColorState), just smaller to fit a
   table row. The fill comes from mountedToolDotStyle; with no colour
   configured the background stays transparent and only the ring remains. */
.mounted-tool-dot {
    display: inline-block;
    box-sizing: border-box;
    width: 11px;
    height: 11px;
    margin-right: 6px;
    border: 1px solid lightgray;
    border-radius: 50%;
    vertical-align: middle;
}
</style>
