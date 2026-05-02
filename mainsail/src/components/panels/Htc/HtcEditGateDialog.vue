<template>
    <v-dialog v-model="show" max-width="400" @keydown.esc="show = false">
        <v-card>
            <v-card-title>Edit Gate {{ gateIndex }}</v-card-title>
            <v-card-text>
                <div class="htc-color-row mb-4">
                    <label class="htc-color-label">Color</label>
                    <div class="htc-color-picker-wrap">
                        <span
                            class="htc-color-swatch"
                            :style="{ backgroundColor: colorWithHash }"
                            @click="$refs.colorInput.click()" />
                        <input
                            ref="colorInput"
                            type="color"
                            class="htc-color-input-native"
                            :value="colorWithHash"
                            @input="onColorPick($event.target.value)" />
                        <v-text-field
                            v-model="form.color"
                            placeholder="E53935"
                            dense
                            outlined
                            hide-details
                            class="htc-color-hex-field" />
                    </div>
                </div>
                <v-text-field
                    v-model="form.material"
                    label="Material"
                    placeholder="ABS, PLA, PETG..."
                    dense
                    outlined
                    class="mb-2" />
                <v-text-field
                    v-model.number="form.temperature"
                    label="Temperature"
                    type="number"
                    suffix="°C"
                    dense
                    outlined
                    class="mb-2" />
                <v-text-field
                    v-model="form.name"
                    label="Filament Name"
                    dense
                    outlined
                    class="mb-2" />
                <v-text-field
                    v-model.number="form.spoolId"
                    label="Spool ID (0 = none)"
                    type="number"
                    dense
                    outlined />
            </v-card-text>
            <v-card-actions>
                <v-spacer />
                <v-btn text @click="show = false">Cancel</v-btn>
                <v-btn color="primary" text @click="save">Save</v-btn>
            </v-card-actions>
        </v-card>
    </v-dialog>
</template>

<script lang="ts">
import { Component, Mixins, Prop, Watch } from 'vue-property-decorator'
import HtcMixin from '@/components/panels/Htc/HtcMixin'

@Component
export default class HtcEditGateDialog extends Mixins(HtcMixin) {
    @Prop({ required: true }) readonly value!: boolean
    @Prop({ required: true }) readonly gateIndex!: number

    form = {
        color: '',
        material: '',
        temperature: 0,
        name: '',
        spoolId: 0,
    }

    get show(): boolean {
        return this.value
    }

    set show(val: boolean) {
        this.$emit('input', val)
    }

    get colorWithHash(): string {
        const hex = this.form.color || '555555'
        return hex.startsWith('#') ? hex : `#${hex}`
    }

    @Watch('value')
    onOpen(val: boolean) {
        if (val) {
            this.form.color = this.gateColors[this.gateIndex] ?? ''
            this.form.material = this.gateMaterials[this.gateIndex] ?? ''
            this.form.temperature = this.gateTemperatures[this.gateIndex] ?? 0
            this.form.name = this.gateFilamentNames[this.gateIndex] ?? ''
            this.form.spoolId = this.gateSpoolIds[this.gateIndex] ?? 0
        }
    }

    onColorPick(value: string) {
        this.form.color = value.replace('#', '')
    }

    save() {
        const parts = [`HTC_SET_GATE GATE=${this.gateIndex}`]
        parts.push(`COLOR=${this.form.color.replace('#', '')}`)
        parts.push(`MATERIAL=${this.form.material}`)
        parts.push(`TEMP=${this.form.temperature}`)
        parts.push(`NAME=${this.form.name}`)
        parts.push(`SPOOL_ID=${this.form.spoolId}`)
        this.doSend(parts.join(' '), 'htc_set_gate')
        this.show = false
    }
}
</script>

<style scoped>
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
    transition: border-color 0.2s;
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
