<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { t } from '../i18n'

// 032 S7 (D-7): the catch-all of the router. It says the page does not exist and offers the way back; the address
// bar keeps the path the operator typed, so a typo can be seen and corrected.
const route = useRoute()
const router = useRouter()

function goDashboard() {
  void router.push('/dashboard')
}
</script>

<template>
  <el-card class="geoCard">
    <el-empty
      :description="t('notFound.text', { path: route.path })"
      data-testid="not-found"
    >
      <el-button
        type="primary"
        @click="goDashboard"
      >
        {{ t('notFound.toDashboard') }}
      </el-button>
    </el-empty>
  </el-card>
</template>
