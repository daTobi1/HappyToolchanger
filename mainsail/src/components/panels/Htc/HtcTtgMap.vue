<template>
    <div v-if="ttgMap.length" class="htc-ttg-map mt-2 px-2">
        <div class="d-flex align-center">
            <span class="text-caption mr-2">TTG Map:</span>
            <span v-for="(gate, tool) in ttgMap" :key="tool" class="htc-ttg-item mr-2">
                T{{ tool }}→G{{ gate }}
            </span>
            <v-spacer />
            <v-btn icon x-small @click="$emit('edit-ttg')">
                <v-icon small>{{ mdiPencil }}</v-icon>
            </v-btn>
        </div>
    </div>
</template>

<script lang="ts">
import { Component, Mixins } from 'vue-property-decorator'
import HtcMixin from '@/components/panels/Htc/HtcMixin'
import { mdiPencil } from '@mdi/js'

@Component
export default class HtcTtgMap extends Mixins(HtcMixin) {
    mdiPencil = mdiPencil
}
</script>

<style scoped>
.htc-ttg-map {
    font-size: 0.8125rem;
    border-top: 1px solid rgba(255, 255, 255, 0.05);
    padding-top: 8px;
}

.htc-ttg-item {
    font-family: monospace;
    color: rgba(255, 255, 255, 0.7);
}
</style>
