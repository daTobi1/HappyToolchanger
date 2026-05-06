<template>
    <v-menu :offset-y="true" :close-on-content-click="false" left>
        <template #activator="{ on, attrs }">
            <v-btn icon tile v-bind="attrs" v-on="on">
                <v-icon>{{ mdiSwapVertical }}</v-icon>
            </v-btn>
        </template>
        <v-list dense>
            <spoolman-tools-dropdown-item v-for="tool in tools" :key="tool" :object-name="tool" />
        </v-list>
    </v-menu>
</template>

<script lang="ts">
import { Component, Mixins, Prop } from 'vue-property-decorator'
import BaseMixin from '@/components/mixins/base'
import { mdiSwapVertical } from '@mdi/js'
import SpoolmanToolsDropdownItem from '@/components/panels/Spoolman/SpoolmanToolsDropdownItem.vue'

@Component({
    components: { SpoolmanToolsDropdownItem },
})
export default class SpoolmanToolsDropdown extends Mixins(BaseMixin) {
    mdiSwapVertical = mdiSwapVertical

    @Prop({ required: false, default: false }) readonly tools!: string[]
}
</script>
