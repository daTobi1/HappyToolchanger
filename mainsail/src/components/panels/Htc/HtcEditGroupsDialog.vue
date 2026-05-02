<template>
    <v-dialog v-model="show" max-width="350" @keydown.esc="show = false">
        <v-card>
            <v-card-title>Edit Endless Spool Groups</v-card-title>
            <v-card-text>
                <p class="text-caption mb-3">Gates in the same group are treated as interchangeable for endless spool.</p>
                <div v-for="g in numTools" :key="g - 1" class="d-flex align-center mb-2">
                    <span class="mr-3" style="min-width: 40px">Gate {{ g - 1 }}:</span>
                    <v-select
                        v-model="form[g - 1]"
                        :items="groupOptions"
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
export default class HtcEditGroupsDialog extends Mixins(HtcMixin) {
    @Prop({ required: true }) readonly value!: boolean

    form: number[] = []

    get show(): boolean {
        return this.value
    }

    set show(val: boolean) {
        this.$emit('input', val)
    }

    get groupOptions(): { text: string; value: number }[] {
        const opts = []
        for (let i = 0; i < this.numTools; i++) {
            opts.push({ text: `Group ${i}`, value: i })
        }
        return opts
    }

    @Watch('value')
    onOpen(val: boolean) {
        if (val) {
            this.form = [...this.endlessSpoolGroups]
            while (this.form.length < this.numTools) {
                this.form.push(this.form.length)
            }
        }
    }

    save() {
        for (let g = 0; g < this.form.length; g++) {
            if (this.form[g] !== this.endlessSpoolGroups[g]) {
                this.doSend(`HTC_SET_GATE GATE=${g} GROUP=${this.form[g]}`, null)
            }
        }
        this.show = false
    }
}
</script>
