<template>
    <div class="htc-endless-spool mt-2 px-2">
        <div class="d-flex align-center">
            <span class="text-caption mr-2">Endless Spool:</span>
            <v-chip x-small :color="endlessSpoolEnabled ? 'success' : ''" class="mr-2">
                {{ endlessSpoolEnabled ? 'ON' : 'OFF' }}
            </v-chip>
            <span v-if="endlessSpoolEnabled" class="htc-groups-text">
                Groups: [{{ endlessSpoolGroups.join(', ') }}]
            </span>
            <v-spacer />
            <v-btn icon x-small class="mr-1" @click="toggleEndlessSpool">
                <v-icon small>{{ mdiPower }}</v-icon>
            </v-btn>
            <v-btn icon x-small @click="$emit('edit-groups')">
                <v-icon small>{{ mdiPencil }}</v-icon>
            </v-btn>
        </div>
    </div>
</template>

<script lang="ts">
import { Component, Mixins } from 'vue-property-decorator'
import HtcMixin from '@/components/panels/Htc/HtcMixin'
import { mdiPencil, mdiPower } from '@mdi/js'

@Component
export default class HtcEndlessSpool extends Mixins(HtcMixin) {
    mdiPencil = mdiPencil
    mdiPower = mdiPower

    toggleEndlessSpool() {
        const newState = this.endlessSpoolEnabled ? 0 : 1
        this.doSend(`HTC_ENDLESS_SPOOL ENABLE=${newState}`, 'htc_es_toggle')
    }
}
</script>

<style scoped>
.htc-endless-spool {
    font-size: 0.8125rem;
    border-top: 1px solid rgba(255, 255, 255, 0.05);
    padding-top: 8px;
}

.htc-groups-text {
    font-family: monospace;
    color: rgba(255, 255, 255, 0.6);
    font-size: 0.75rem;
}
</style>
