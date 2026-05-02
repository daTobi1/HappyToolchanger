<template>
    <div v-if="hasSensors" class="htc-sensors mt-2 px-2">
        <div class="d-flex align-center flex-wrap">
            <span class="text-caption mr-2">Sensors:</span>
            <span
                v-for="(state, idx) in sensorStates"
                :key="idx"
                class="htc-sensor-item mr-2">
                <v-icon x-small :color="state === 1 ? 'success' : 'grey'">
                    {{ state === 1 ? mdiCircle : mdiCircleOutline }}
                </v-icon>
                G{{ idx }}
            </span>
        </div>
    </div>
</template>

<script lang="ts">
import { Component, Mixins } from 'vue-property-decorator'
import HtcMixin from '@/components/panels/Htc/HtcMixin'
import { mdiCircle, mdiCircleOutline } from '@mdi/js'

@Component
export default class HtcSensorStatus extends Mixins(HtcMixin) {
    mdiCircle = mdiCircle
    mdiCircleOutline = mdiCircleOutline

    get sensorStates(): number[] {
        return this.htcSensors?.sensor_states ?? []
    }
}
</script>

<style scoped>
.htc-sensors {
    font-size: 0.8125rem;
    border-top: 1px solid rgba(255, 255, 255, 0.05);
    padding-top: 8px;
}

.htc-sensor-item {
    font-size: 0.75rem;
}
</style>
