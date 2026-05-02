<template>
    <div class="htc-statistics mt-2 px-2">
        <div class="d-flex align-center mb-1">
            <span class="text-caption">Statistics:</span>
            <v-spacer />
            <v-btn icon x-small @click="confirmReset">
                <v-icon small>{{ mdiDelete }}</v-icon>
            </v-btn>
        </div>
        <div class="htc-stats-row d-flex" v-for="(count, tool) in toolChangeCounts" :key="tool">
            <span class="htc-stats-label">T{{ tool }}:</span>
            <span class="htc-stats-value">{{ count }} changes</span>
        </div>
        <div class="htc-stats-row d-flex mt-1">
            <span class="htc-stats-label font-weight-bold">Total:</span>
            <span class="htc-stats-value font-weight-bold">{{ totalChanges }}</span>
        </div>
    </div>
</template>

<script lang="ts">
import { Component, Mixins } from 'vue-property-decorator'
import HtcMixin from '@/components/panels/Htc/HtcMixin'
import { mdiDelete } from '@mdi/js'

@Component
export default class HtcStatistics extends Mixins(HtcMixin) {
    mdiDelete = mdiDelete

    get statistics(): Record<string, unknown> {
        return (this.htc?.statistics as Record<string, unknown>) ?? {}
    }

    get toolChangeCounts(): number[] {
        const counts: number[] = []
        for (let i = 0; i < this.numTools; i++) {
            counts.push((this.statistics[`tool_${i}_changes`] as number) ?? 0)
        }
        return counts
    }

    get totalChanges(): number {
        return this.toolChangeCounts.reduce((sum, c) => sum + c, 0)
    }

    confirmReset() {
        if (confirm('Reset all HTC statistics?')) {
            this.doSend('HTC_RESET_STATS', 'htc_reset_stats')
        }
    }
}
</script>

<style scoped>
.htc-statistics {
    font-size: 0.8125rem;
    border-top: 1px solid rgba(255, 255, 255, 0.05);
    padding-top: 8px;
}

.htc-stats-label {
    min-width: 40px;
    color: rgba(255, 255, 255, 0.6);
}

.htc-stats-value {
    margin-left: 8px;
}
</style>
