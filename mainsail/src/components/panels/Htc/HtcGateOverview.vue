<template>
    <div class="htc-gate-overview">
        <htc-gate-row
            v-for="t in numTools"
            :key="t - 1"
            :tool-index="t - 1"
            :gate-index="ttgMap[t - 1] ?? (t - 1)"
            :color="gateColors[ttgMap[t - 1] ?? (t - 1)]"
            :material="gateMaterials[ttgMap[t - 1] ?? (t - 1)]"
            :temperature="gateTemperatures[ttgMap[t - 1] ?? (t - 1)]"
            :name="gateFilamentNames[ttgMap[t - 1] ?? (t - 1)]"
            :status="gateStatus[ttgMap[t - 1] ?? (t - 1)]"
            :is-active="activeTool === (t - 1)"
            @edit="$emit('edit-gate', $event)"
            @assign-spool="$emit('assign-spool', $event)" />
    </div>
</template>

<script lang="ts">
import { Component, Mixins } from 'vue-property-decorator'
import HtcMixin from '@/components/panels/Htc/HtcMixin'
import HtcGateRow from '@/components/panels/Htc/HtcGateRow.vue'

@Component({
    components: { HtcGateRow },
})
export default class HtcGateOverview extends Mixins(HtcMixin) {}
</script>
