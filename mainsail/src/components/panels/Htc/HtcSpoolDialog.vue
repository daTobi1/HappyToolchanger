<template>
    <v-dialog v-model="show" max-width="500" @keydown.esc="show = false">
        <v-card>
            <v-card-title class="d-flex align-center">
                <span>Assign Spool to Gate {{ gateIndex }}</span>
                <v-spacer />
                <v-btn icon small @click="show = false">
                    <v-icon>{{ mdiClose }}</v-icon>
                </v-btn>
            </v-card-title>
            <v-card-text>
                <v-text-field
                    v-model="search"
                    placeholder="Search spools..."
                    dense
                    outlined
                    hide-details
                    class="mb-3"
                    :prepend-inner-icon="mdiMagnify" />

                <div class="d-flex mb-3" style="gap: 8px">
                    <v-btn small outlined @click="unassignSpool" class="flex-grow-1">
                        Unassign Spool
                    </v-btn>
                    <v-btn small color="primary" outlined @click="showCreate = true" class="flex-grow-1">
                        <v-icon left small>{{ mdiPlus }}</v-icon>
                        Create New
                    </v-btn>
                </div>

                <!-- Pending Spools -->
                <div v-if="pendingSpools.length > 0" class="mb-3">
                    <div class="htc-section-label mb-1">Pending Spools (not yet in Spoolman)</div>
                    <div
                        v-for="p in pendingSpools"
                        :key="p.id"
                        class="htc-spool-item d-flex align-center pa-2 mb-1">
                        <span class="htc-spool-dot mr-2" :style="{ backgroundColor: '#' + (p.color_hex || '808080') }" />
                        <div class="flex-grow-1">
                            <div class="htc-spool-name">{{ p.name }}</div>
                            <div class="htc-spool-detail">{{ p.material }} &mdash; {{ p.weight }}g &mdash; {{ p.temp }}°C</div>
                        </div>
                        <v-btn x-small icon class="mr-1" @click="removePending(p.id)">
                            <v-icon small>{{ mdiDelete }}</v-icon>
                        </v-btn>
                        <v-btn x-small outlined @click="assignPending(p)">Assign</v-btn>
                    </div>
                </div>

                <!-- Loading -->
                <div v-if="loading" class="text-center pa-4">
                    <v-progress-circular indeterminate size="24" />
                    <span class="ml-2">Loading spools...</span>
                </div>

                <!-- Spool List -->
                <div v-else-if="filteredSpools.length > 0" class="htc-spool-list">
                    <div
                        v-for="spool in filteredSpools"
                        :key="spool.id"
                        class="htc-spool-item d-flex align-center pa-2 mb-1"
                        @click="assignSpool(spool)">
                        <span
                            class="htc-spool-dot mr-2"
                            :style="{ backgroundColor: '#' + safeHex(spool.filament) }" />
                        <div class="flex-grow-1">
                            <div class="htc-spool-name">{{ spoolName(spool) }}</div>
                            <div class="htc-spool-detail">
                                {{ spoolMaterial(spool) }}
                                <span v-if="spool.remaining_weight != null">
                                    &mdash; {{ Math.round(spool.remaining_weight) }}g remaining
                                </span>
                            </div>
                        </div>
                    </div>
                </div>
                <div v-else class="pa-3 text-center" style="color: rgba(255,255,255,0.5)">
                    No spools found.
                </div>
            </v-card-text>
        </v-card>

        <!-- Create Spool Sub-Dialog -->
        <v-dialog v-model="showCreate" max-width="400" @keydown.esc="showCreate = false">
            <v-card>
                <v-card-title>Create New Spool for Gate {{ gateIndex }}</v-card-title>
                <v-card-text>
                    <v-text-field
                        v-model="createForm.name"
                        label="Filament Name"
                        dense
                        outlined
                        class="mb-2" />
                    <v-select
                        v-model="createForm.material"
                        :items="materialOptions"
                        label="Material"
                        dense
                        outlined
                        class="mb-2" />
                    <div class="htc-color-row mb-3">
                        <label class="htc-color-label">Color</label>
                        <div class="htc-color-picker-wrap">
                            <span
                                class="htc-color-swatch"
                                :style="{ backgroundColor: createColorWithHash }"
                                @click="openCreateColorPicker" />
                            <input
                                ref="createColorInput"
                                type="color"
                                class="htc-color-input-native"
                                :value="createColorWithHash"
                                @input="onCreateColorPick" />
                            <v-text-field
                                v-model="createForm.colorHex"
                                placeholder="00897B"
                                dense
                                outlined
                                hide-details
                                class="htc-color-hex-field" />
                        </div>
                    </div>
                    <v-text-field
                        v-model.number="createForm.temp"
                        label="Temperature"
                        type="number"
                        suffix="°C"
                        dense
                        outlined
                        class="mb-2" />
                    <v-text-field
                        v-model.number="createForm.weight"
                        label="Spool Weight"
                        type="number"
                        suffix="g"
                        dense
                        outlined />
                </v-card-text>
                <v-card-actions>
                    <v-spacer />
                    <v-btn text @click="showCreate = false">Cancel</v-btn>
                    <v-btn color="primary" text @click="createSpool" :loading="creating">
                        Create &amp; Assign
                    </v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>
    </v-dialog>
</template>

<script lang="ts">
import { Component, Mixins, Prop, Watch } from 'vue-property-decorator'
import HtcMixin from '@/components/panels/Htc/HtcMixin'
import { mdiClose, mdiMagnify, mdiPlus, mdiDelete } from '@mdi/js'

interface SpoolmanSpool {
    id: number
    remaining_weight: number | null
    filament?: {
        name?: string
        material?: string
        color_hex?: string
        settings?: { extruder_temp?: number }
    }
}

interface PendingSpool {
    id: number
    created_at: string
    name: string
    material: string
    color_hex: string
    temp: number
    weight: number
}

const PENDING_KEY = 'htc_pending_spools'

@Component
export default class HtcSpoolDialog extends Mixins(HtcMixin) {
    mdiClose = mdiClose
    mdiMagnify = mdiMagnify
    mdiPlus = mdiPlus
    mdiDelete = mdiDelete

    @Prop({ required: true }) readonly value!: boolean
    @Prop({ required: true }) readonly gateIndex!: number

    search = ''
    get loading(): boolean {
        return this.$store.state.socket?.loadings?.includes('refreshSpools') ?? false
    }
    showCreate = false
    creating = false
    pendingSpools: PendingSpool[] = []

    materialOptions = ['PLA', 'ABS', 'PETG', 'ASA', 'TPU', 'PA', 'PC', 'PVA', 'HIPS']

    createForm = {
        name: '',
        material: 'PLA',
        colorHex: '00897B',
        temp: 210,
        weight: 1000,
    }

    get show(): boolean {
        return this.value
    }

    set show(val: boolean) {
        this.$emit('input', val)
    }

    get createColorWithHash(): string {
        const hex = this.createForm.colorHex || '555555'
        return hex.startsWith('#') ? hex : `#${hex}`
    }

    get spools(): SpoolmanSpool[] {
        return this.$store.state.server.spoolman.spools ?? []
    }

    get filteredSpools(): SpoolmanSpool[] {
        const q = this.search.toLowerCase().trim()
        if (!q) return this.spools
        return this.spools.filter((s) => {
            const name = (s.filament?.name || '').toLowerCase()
            const mat = (s.filament?.material || '').toLowerCase()
            return name.includes(q) || mat.includes(q)
        })
    }

    openCreateColorPicker() {
        const input = this.$refs.createColorInput as HTMLInputElement
        input?.click()
    }

    onCreateColorPick(event: Event) {
        const input = event.target as HTMLInputElement
        this.createForm.colorHex = input.value.replace('#', '')
    }

    @Watch('value')
    onOpen(val: boolean) {
        if (val) {
            this.search = ''
            this.loadPending()
            this.loadSpools()
            this.createForm = {
                name: '',
                material: 'PLA',
                colorHex: '00897B',
                temp: 210,
                weight: 1000,
            }
        }
    }

    safeHex(filament?: SpoolmanSpool['filament']): string {
        return (filament?.color_hex || '808080').replace(/^#/, '')
    }

    spoolName(spool: SpoolmanSpool): string {
        return spool.filament?.name || `Spool #${spool.id}`
    }

    spoolMaterial(spool: SpoolmanSpool): string {
        return spool.filament?.material || ''
    }

    loadSpools() {
        this.$store.dispatch('server/spoolman/refreshSpools')
    }

    assignSpool(spool: SpoolmanSpool) {
        const color = this.safeHex(spool.filament)
        const material = spool.filament?.material || ''
        const temp = spool.filament?.settings?.extruder_temp || 0
        const name = (spool.filament?.name || '').replace(/ /g, '_')
        this.doSend(
            `HTC_SET_GATE GATE=${this.gateIndex} COLOR=${color} MATERIAL=${material} TEMP=${temp} NAME=${name} SPOOL_ID=${spool.id} STATUS=1`,
            'htc_assign_spool'
        )
        this.show = false
    }

    unassignSpool() {
        this.doSend(
            `HTC_SET_GATE GATE=${this.gateIndex} COLOR= MATERIAL= TEMP=0 NAME= SPOOL_ID=0 STATUS=0`,
            'htc_unassign_spool'
        )
        this.show = false
    }

    // --- Pending Spools (localStorage buffer) ---

    loadPending() {
        try {
            this.pendingSpools = JSON.parse(localStorage.getItem(PENDING_KEY) || '[]')
        } catch {
            this.pendingSpools = []
        }
    }

    savePending() {
        localStorage.setItem(PENDING_KEY, JSON.stringify(this.pendingSpools))
    }

    removePending(id: number) {
        this.pendingSpools = this.pendingSpools.filter((s) => s.id !== id)
        this.savePending()
    }

    assignPending(p: PendingSpool) {
        const name = p.name.replace(/ /g, '_')
        this.doSend(
            `HTC_SET_GATE GATE=${this.gateIndex} COLOR=${p.color_hex} MATERIAL=${p.material} TEMP=${p.temp} NAME=${name} STATUS=1`,
            'htc_assign_pending'
        )
        this.show = false
    }

    async createSpool() {
        if (!this.createForm.name.trim()) return
        this.creating = true

        const { name, material, colorHex, temp, weight } = this.createForm
        const gate = this.gateIndex

        try {
            // Try Spoolman via Moonraker proxy: create filament, then spool
            const filRes = await fetch('/server/spoolman/proxy', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    request_method: 'POST',
                    path: '/v1/filament',
                    body: {
                        name,
                        material,
                        color_hex: colorHex || '808080',
                        settings: { extruder_temp: temp },
                    },
                }),
            })
            if (!filRes.ok) throw new Error(`HTTP ${filRes.status}`)
            const filData = await filRes.json()
            const filament = filData.result ?? filData

            const spoolRes = await fetch('/server/spoolman/proxy', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    request_method: 'POST',
                    path: '/v1/spool',
                    body: {
                        filament_id: filament.id,
                        remaining_weight: weight,
                        initial_weight: weight,
                    },
                }),
            })
            if (!spoolRes.ok) throw new Error(`HTTP ${spoolRes.status}`)
            const spoolData = await spoolRes.json()
            const spool = spoolData.result ?? spoolData

            // Assign to gate with new spool ID
            const safeName = name.replace(/ /g, '_')
            this.doSend(
                `HTC_SET_GATE GATE=${gate} COLOR=${colorHex} MATERIAL=${material} TEMP=${temp} NAME=${safeName} SPOOL_ID=${spool.id} STATUS=1`,
                'htc_create_spool'
            )
            this.showCreate = false
            this.show = false
        } catch {
            // Spoolman offline — save to pending buffer
            this.pendingSpools.push({
                id: Date.now(),
                created_at: new Date().toISOString(),
                name,
                material,
                color_hex: colorHex,
                temp,
                weight,
            })
            this.savePending()

            // Still assign to gate
            const safeName = name.replace(/ /g, '_')
            this.doSend(
                `HTC_SET_GATE GATE=${gate} COLOR=${colorHex} MATERIAL=${material} TEMP=${temp} NAME=${safeName} STATUS=1`,
                'htc_create_spool_pending'
            )
            this.showCreate = false
            this.show = false
        }
        this.creating = false
    }
}
</script>

<style scoped>
.htc-section-label {
    font-size: 0.75rem;
    color: rgba(255, 255, 255, 0.5);
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.htc-spool-list {
    max-height: 300px;
    overflow-y: auto;
}

.htc-spool-item {
    border-radius: 4px;
    cursor: pointer;
    transition: background 0.15s;
}

.htc-spool-item:hover {
    background: rgba(255, 255, 255, 0.05);
}

.htc-spool-dot {
    width: 24px;
    height: 24px;
    min-width: 24px;
    border-radius: 50%;
    border: 1px solid rgba(255, 255, 255, 0.2);
}

.htc-spool-name {
    font-size: 0.875rem;
    font-weight: 500;
}

.htc-spool-detail {
    font-size: 0.75rem;
    color: rgba(255, 255, 255, 0.5);
}

.htc-color-row {
    display: flex;
    flex-direction: column;
}

.htc-color-label {
    font-size: 0.75rem;
    color: rgba(255, 255, 255, 0.7);
    margin-bottom: 4px;
}

.htc-color-picker-wrap {
    display: flex;
    align-items: center;
    gap: 8px;
}

.htc-color-swatch {
    width: 36px;
    height: 36px;
    min-width: 36px;
    border-radius: 50%;
    border: 2px solid rgba(255, 255, 255, 0.3);
    cursor: pointer;
}

.htc-color-swatch:hover {
    border-color: rgba(255, 255, 255, 0.6);
}

.htc-color-input-native {
    width: 0;
    height: 0;
    padding: 0;
    border: none;
    visibility: hidden;
    position: absolute;
}

.htc-color-hex-field {
    max-width: 140px;
}
</style>
