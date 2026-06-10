<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getReviewStatus } from '../api'

const props = defineProps({ id: String })
const router = useRouter()
const review = ref(null)
const loading = ref(true)

onMounted(async () => {
  try {
    review.value = await getReviewStatus(props.id)
  } catch (e) {
    console.log('API unavailable, showing demo')
    review.value = {
      review_id: props.id,
      status: 'completed',
      cost: { tokens_in: 2856, tokens_out: 910, cost_usd: 0.000655 },
      findings: [
        { file: 'src/main/resources/application.yml', line_start: 45, line_end: 48, severity: 'critical', category: 'security',
          title: '硬编码的 API 凭据', confidence: 1.0,
          description: '青蓝 API 的 appid 和 secret 以明文硬编码在 application.yml 中。',
          suggestion: '替换为环境变量：appid: ${QINGLAN_APPID}' },
      ]
    }
  }
  loading.value = false
})

const sevClass = (s) => ({ critical: 'sev-critical', warning: 'sev-warning', suggestion: 'sev-suggestion' }[s] || '')
const badgeClass = (s) => ({ critical: 'bg-red', warning: 'bg-yellow', suggestion: 'bg-blue' }[s] || 'bg-gray')
</script>

<template>
  <div>
    <a @click="router.push('/reviews')" style="cursor:pointer;font-size:13px;color:#3498db;text-decoration:none;margin-bottom:16px;display:inline-block">← 返回列表</a>

    <div v-if="loading" class="empty">加载中...</div>

    <template v-else-if="review">
      <h1 style="margin-bottom:8px">审查详情</h1>
      <div class="card" style="margin-bottom:8px">
        <p style="font-size:13px;color:#888">
          ID: <code>{{ review.review_id }}</code>
          &nbsp;|&nbsp; 状态: <span class="badge" :class="review.status === 'completed' ? 'bg-green' : review.status === 'failed' ? 'bg-red' : 'bg-gray'">{{ review.status }}</span>
        </p>
        <p style="font-size:12px;color:#888;margin-top:4px">
          Tokens: {{ review.cost?.tokens_in || 0 }} in / {{ review.cost?.tokens_out || 0 }} out
          &nbsp;|&nbsp; 成本: ${{ review.cost?.cost_usd?.toFixed(6) || '0.000000' }}
        </p>
      </div>

      <h3 style="margin:16px 0 8px">{{ review.findings?.length || 0 }} 条发现</h3>

      <div v-if="!review.findings?.length" class="empty">无发现，代码质量良好</div>

      <div v-for="(f, i) in review.findings" :key="i" class="finding-card" :class="sevClass(f.severity)">
        <div class="meta">
          <span class="badge" :class="badgeClass(f.severity)">{{ f.severity }}</span>
          <span class="badge bg-gray" style="margin-left:6px">{{ f.category }}</span>
          <span style="margin-left:8px">{{ f.file }}:{{ f.line_start }}-{{ f.line_end }}</span>
          <span v-if="f.confidence" style="margin-left:8px">置信度 {{ (f.confidence * 100).toFixed(0) }}%</span>
        </div>
        <div class="title">{{ i + 1 }}. {{ f.title }}</div>
        <div class="desc">{{ f.description }}</div>
        <div v-if="f.suggestion" class="suggestion">{{ f.suggestion }}</div>
      </div>
    </template>

    <div v-else class="empty">审查未找到</div>
  </div>
</template>
