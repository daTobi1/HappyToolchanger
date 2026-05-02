<template>
    <panel
        v-if="showPanel"
        :icon="mdiTools"
        title="HappyToolchanger"
        :collapsible="true"
        card-class="htc-panel">
        <template #buttons>
            <v-btn
                v-if="pendingCount > 0"
                small
                outlined
                class="mr-2"
                @click="syncPending">
                Sync Pending
                <v-badge :content="String(pendingCount)" color="warning" inline />
            </v-btn>
            <v-menu left offset-y :close-on-content-click="false">
                <template #activator="{ on, attrs }">
                    <v-btn icon tile v-bind="attrs" v-on="on">
                        <v-icon>{{ mdiDotsVertical }}</v-icon>
                    </v-btn>
                </template>
                <v-list dense>
                    <v-list-item>
                        <v-btn
                            small
                            class="w-100"
                            @click="doSend('HTC_SYNC_SPOOLMAN', 'htc_sync_spoolman')">
                            <v-icon left>{{ mdiRefresh }}</v-icon>
                            Sync Spoolman
                        </v-btn>
                    </v-list-item>
                    <v-list-item>
                        <v-btn small class="w-100" @click="showEditTtgDialog = true">
                            Edit TTG Map
                        </v-btn>
                    </v-list-item>
                    <v-list-item>
                        <v-btn small class="w-100" @click="showEditGroupsDialog = true">
                            Edit ES Groups
                        </v-btn>
                    </v-list-item>
                    <v-list-item v-if="spoolmanUrl">
                        <v-btn small class="w-100" :href="spoolmanUrl" target="_blank">
                            <v-icon left>{{ mdiOpenInNew }}</v-icon>
                            Spoolman
                        </v-btn>
                    </v-list-item>
                </v-list>
            </v-menu>
        </template>

        <v-card-text>
            <htc-status-bar />
            <htc-gate-overview @edit-gate="openEditGate" @assign-spool="openSpoolDialog" />
            <htc-ttg-map @edit-ttg="showEditTtgDialog = true" />
            <htc-endless-spool @edit-groups="showEditGroupsDialog = true" />
            <htc-sensor-status />
            <htc-statistics />
        </v-card-text>

        <htc-edit-gate-dialog v-model="showEditGateDialog" :gate-index="editGateIndex" />
        <htc-edit-ttg-dialog v-model="showEditTtgDialog" />
        <htc-edit-groups-dialog v-model="showEditGroupsDialog" />
        <htc-spool-dialog v-model="showSpoolDialog" :gate-index="spoolGateIndex" />
    </panel>
</template>

<script lang="ts">
import { Component, Mixins } from 'vue-property-decorator'
import HtcMixin from '@/components/panels/Htc/HtcMixin'
import Panel from '@/components/ui/Panel.vue'
import HtcStatusBar from '@/components/panels/Htc/HtcStatusBar.vue'
import HtcGateOverview from '@/components/panels/Htc/HtcGateOverview.vue'
import HtcTtgMap from '@/components/panels/Htc/HtcTtgMap.vue'
import HtcEndlessSpool from '@/components/panels/Htc/HtcEndlessSpool.vue'
import HtcSensorStatus from '@/components/panels/Htc/HtcSensorStatus.vue'
import HtcStatistics from '@/components/panels/Htc/HtcStatistics.vue'
import HtcEditGateDialog from '@/components/panels/Htc/HtcEditGateDialog.vue'
import HtcEditTtgDialog from '@/components/panels/Htc/HtcEditTtgDialog.vue'
import HtcEditGroupsDialog from '@/components/panels/Htc/HtcEditGroupsDialog.vue'
import HtcSpoolDialog from '@/components/panels/Htc/HtcSpoolDialog.vue'
import { mdiTools, mdiDotsVertical, mdiRefresh, mdiOpenInNew } from '@mdi/js'

const PENDING_KEY = 'htc_pending_spools'

@Component({
    components: {
        Panel,
        HtcStatusBar,
        HtcGateOverview,
        HtcTtgMap,
        HtcEndlessSpool,
        HtcSensorStatus,
        HtcStatistics,
        HtcEditGateDialog,
        HtcEditTtgDialog,
        HtcEditGroupsDialog,
        HtcSpoolDialog,
    },
})
export default class HtcPanel extends Mixins(HtcMixin) {
    mdiTools = mdiTools
    mdiDotsVertical = mdiDotsVertical
    mdiRefresh = mdiRefresh
    mdiOpenInNew = mdiOpenInNew

    showEditGateDialog = false
    showEditTtgDialog = false
    showEditGroupsDialog = false
    showSpoolDialog = false
    editGateIndex = 0
    spoolGateIndex = 0
    pendingCount = 0

    get showPanel(): boolean {
        if (!this.klipperReadyForGui) return false
        return 'happy_toolchanger' in this.$store.state.printer
    }

    get spoolmanUrl(): string | null {
        const spoolman = this.$store.state.server?.config?.config?.spoolman
        if (spoolman?.server) return spoolman.server
        return null
    }

    mounted() {
        this.updatePendingCount()
    }

    openEditGate(gateIndex: number) {
        this.editGateIndex = gateIndex
        this.showEditGateDialog = true
    }

    openSpoolDialog(gateIndex: number) {
        this.spoolGateIndex = gateIndex
        this.showSpoolDialog = true
    }

    updatePendingCount() {
        try {
            const pending = JSON.parse(localStorage.getItem(PENDING_KEY) || '[]')
            this.pendingCount = pending.length
        } catch {
            this.pendingCount = 0
        }
    }

    async syncPending() {
        let pending: Array<{
            id: number
            name: string
            material: string
            color_hex: string
            temp: number
            weight: number
        }> = []
        try {
            pending = JSON.parse(localStorage.getItem(PENDING_KEY) || '[]')
        } catch {
            return
        }
        if (pending.length === 0) return

        let synced = 0
        let failed = 0
        const remaining = [...pending]

        for (const p of remaining) {
            try {
                const filRes = await fetch('/server/spoolman/proxy/v1/filament', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: p.name,
                        material: p.material,
                        color_hex: p.color_hex || '808080',
                        settings: { extruder_temp: p.temp },
                    }),
                })
                if (!filRes.ok) throw new Error(`HTTP ${filRes.status}`)
                const filament = await filRes.json()

                const spoolRes = await fetch('/server/spoolman/proxy/v1/spool', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        filament_id: filament.id,
                        remaining_weight: p.weight,
                        initial_weight: p.weight,
                    }),
                })
                if (!spoolRes.ok) throw new Error(`HTTP ${spoolRes.status}`)

                // Remove from pending
                pending = pending.filter((s) => s.id !== p.id)
                localStorage.setItem(PENDING_KEY, JSON.stringify(pending))
                synced++
            } catch {
                failed++
            }
        }

        this.updatePendingCount()

        this.$store.dispatch('server/addEvent', {
            message: `Spoolman sync: ${synced} synced, ${failed} failed`,
            type: synced > 0 && failed === 0 ? 'response' : 'error',
        })
    }
}
</script>
