<template>
    <div class="htc-gate-row d-flex align-center py-1 px-2" :class="{ 'htc-active': isActive }">
        <span class="htc-tool-label mr-2">T{{ toolIndex }}</span>
        <v-icon small class="mr-1">{{ mdiArrowRight }}</v-icon>
        <span class="htc-gate-label mr-3">G{{ gateIndex }}</span>
        <span
            class="htc-color-dot mr-2"
            :style="{ backgroundColor: color || '#555' }" />
        <span v-if="status === 1" class="htc-gate-info">
            <span class="mr-2">{{ material || '—' }}</span>
            <span v-if="temperature" class="mr-2">{{ temperature }}°</span>
            <span class="htc-filament-name">{{ name || '' }}</span>
        </span>
        <span v-else class="htc-gate-empty">empty</span>
        <v-spacer />
        <v-btn icon x-small class="mr-1" @click="$emit('assign-spool', gateIndex)">
            <v-icon small>{{ mdiDatabaseEdit }}</v-icon>
        </v-btn>
        <v-btn icon x-small @click="$emit('edit', gateIndex)">
            <v-icon small>{{ mdiPencil }}</v-icon>
        </v-btn>
    </div>
</template>

<script lang="ts">
import Component from 'vue-class-component'
import Vue from 'vue'
import { Prop } from 'vue-property-decorator'
import { mdiArrowRight, mdiPencil, mdiDatabaseEdit } from '@mdi/js'

@Component
export default class HtcGateRow extends Vue {
    mdiArrowRight = mdiArrowRight
    mdiPencil = mdiPencil
    mdiDatabaseEdit = mdiDatabaseEdit

    @Prop({ required: true }) readonly toolIndex!: number
    @Prop({ required: true }) readonly gateIndex!: number
    @Prop({ default: '' }) readonly color!: string
    @Prop({ default: '' }) readonly material!: string
    @Prop({ default: 0 }) readonly temperature!: number
    @Prop({ default: '' }) readonly name!: string
    @Prop({ default: 1 }) readonly status!: number
    @Prop({ default: false }) readonly isActive!: boolean
}
</script>

<style scoped>
.htc-gate-row {
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    font-size: 0.875rem;
}

.htc-gate-row.htc-active {
    background: rgba(0, 137, 123, 0.1);
}

.htc-tool-label,
.htc-gate-label {
    font-weight: 500;
    min-width: 24px;
}

.htc-color-dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    display: inline-block;
    border: 1px solid rgba(255, 255, 255, 0.2);
}

.htc-gate-empty {
    color: rgba(255, 255, 255, 0.3);
    font-style: italic;
}

.htc-filament-name {
    color: rgba(255, 255, 255, 0.5);
}
</style>
