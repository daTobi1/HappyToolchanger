import Component from 'vue-class-component'
import { Mixins } from 'vue-property-decorator'
import BaseMixin from '@/components/mixins/base'

export const GATE_AVAILABLE = 1
export const GATE_EMPTY = 0

export interface HtcState {
    num_tools: number
    active_tool: number
    is_printing: boolean
    ttg_map: number[]
    gate_status: number[]
    gate_colors: string[]
    gate_materials: string[]
    gate_temperatures: number[]
    gate_spool_ids: number[]
    gate_filament_names: string[]
    endless_spool: {
        enabled: boolean
        groups: number[]
    }
    statistics: Record<string, unknown>
}

export interface HtcSensorState {
    sensor_states: number[]
    num_sensors: number
}

@Component
export default class HtcMixin extends Mixins(BaseMixin) {
    get htc(): HtcState | null {
        return this.$store.state.printer['happy_toolchanger'] ?? null
    }

    get htcSensors(): HtcSensorState | null {
        return this.$store.state.printer['htc_sensor_manager'] ?? null
    }

    get numTools(): number {
        return this.htc?.num_tools ?? 0
    }

    get activeTool(): number {
        return this.htc?.active_tool ?? -1
    }

    get isPrinting(): boolean {
        return this.htc?.is_printing ?? false
    }

    get ttgMap(): number[] {
        return this.htc?.ttg_map ?? []
    }

    get gateStatus(): number[] {
        return this.htc?.gate_status ?? []
    }

    get gateColors(): string[] {
        return this.htc?.gate_colors ?? []
    }

    get gateMaterials(): string[] {
        return this.htc?.gate_materials ?? []
    }

    get gateTemperatures(): number[] {
        return this.htc?.gate_temperatures ?? []
    }

    get gateSpoolIds(): number[] {
        return this.htc?.gate_spool_ids ?? []
    }

    get gateFilamentNames(): string[] {
        return this.htc?.gate_filament_names ?? []
    }

    get endlessSpoolEnabled(): boolean {
        return this.htc?.endless_spool?.enabled ?? false
    }

    get endlessSpoolGroups(): number[] {
        return this.htc?.endless_spool?.groups ?? []
    }

    get hasSensors(): boolean {
        return this.htcSensors !== null && this.htcSensors.num_sensors > 0
    }

    doSend(gcode: string, loading: string | null = null) {
        this.$store.dispatch('server/addEvent', { message: gcode, type: 'command' })
        this.$socket.emit('printer.gcode.script', { script: gcode }, { loading })
    }
}
