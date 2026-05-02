<template>
    <v-dialog v-model="show" max-width="350" @keydown.esc="show = false">
        <v-card>
            <v-card-title>Edit Tool-to-Gate Map</v-card-title>
            <v-card-text>
                <div v-for="t in numTools" :key="t - 1" class="d-flex align-center mb-2">
                    <span class="mr-3" style="min-width: 30px">T{{ t - 1 }}:</span>
                    <v-select
                        v-model="form[t - 1]"
                        :items="gateOptions"
                        dense
                        outlined
                        hide-details
                        style="max-width: 120px" />
                </div>
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
export default class HtcEditTtgDialog extends Mixins(HtcMixin) {
    @Prop({ required: true }) readonly value!: boolean

    form: number[] = []

    get show(): boolean {
        return this.value
    }

    set show(val: boolean) {
        this.$emit('input', val)
    }

    get gateOptions(): { text: string; value: number }[] {
        const opts = []
        for (let i = 0; i < this.numTools; i++) {
            opts.push({ text: `Gate ${i}`, value: i })
        }
        return opts
    }

    @Watch('value')
    onOpen(val: boolean) {
        if (val) {
            this.form = [...this.ttgMap]
        }
    }

    save() {
        for (let t = 0; t < this.form.length; t++) {
            if (this.form[t] !== this.ttgMap[t]) {
                this.doSend(`HTC_SET_GATE GATE=${this.form[t]} TOOL=${t}`, null)
            }
        }
        this.show = false
    }
}
</script>
