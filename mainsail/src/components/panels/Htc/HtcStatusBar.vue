<template>
    <div class="htc-status-bar">
        <div
            v-for="tool in numTools"
            :key="'htc-sb-' + (tool - 1)"
            class="htc-status-dot"
            :class="{
                'htc-status-dot--active': activeTool === tool - 1,
                'htc-status-dot--empty': gateStatus[ttgMap[tool - 1]] !== 1,
            }"
            :style="dotStyle(tool - 1)"
            @click="activateTool(tool - 1)">
            <span class="htc-status-dot__label">T{{ tool - 1 }}</span>
            <span v-if="gateMaterials[ttgMap[tool - 1]]" class="htc-status-dot__material">
                {{ gateMaterials[ttgMap[tool - 1]] }}
            </span>
        </div>
    </div>
</template>

<script lang="ts">
import { Component, Mixins } from 'vue-property-decorator'
import HtcMixin, { GATE_AVAILABLE } from '@/components/panels/Htc/HtcMixin'

@Component
export default class HtcStatusBar extends Mixins(HtcMixin) {
    dotStyle(toolIndex: number) {
        const gate = this.ttgMap[toolIndex] ?? toolIndex
        const hex = this.gateColors[gate] || ''
        const available = this.gateStatus[gate] === GATE_AVAILABLE

        if (hex) {
            const r = parseInt(hex.substring(0, 2), 16)
            const g = parseInt(hex.substring(2, 4), 16)
            const b = parseInt(hex.substring(4, 6), 16)
            return {
                backgroundColor: `rgba(${r}, ${g}, ${b}, 0.2)`,
                borderColor: available ? `#${hex}` : '#f44336',
                color: `#${hex}`,
            }
        }

        return {
            backgroundColor: 'transparent',
            borderColor: available ? '#4caf50' : '#f44336',
        }
    }

    activateTool(toolIndex: number) {
        if (this.activeTool === toolIndex) return
        this.doSend(`T${toolIndex}`, `htc_activate_t${toolIndex}`)
    }
}
</script>

<style scoped>
.htc-status-bar {
    display: flex;
    gap: 8px;
    justify-content: center;
    margin-bottom: 8px;
}

.htc-status-dot {
    width: 48px;
    height: 48px;
    border-radius: 50%;
    border: 2px solid rgba(255, 255, 255, 0.3);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all 0.2s;
    position: relative;
}

.htc-status-dot:hover {
    opacity: 0.8;
    transform: scale(1.05);
}

.htc-status-dot--active {
    box-shadow: 0 0 0 3px currentColor;
}

.htc-status-dot--empty {
    opacity: 0.5;
}

.htc-status-dot__label {
    font-size: 0.75rem;
    font-weight: 600;
    line-height: 1;
}

.htc-status-dot__material {
    font-size: 0.55rem;
    opacity: 0.7;
    line-height: 1;
    margin-top: 2px;
}
</style>
