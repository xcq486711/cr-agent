<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({ id: String })
const router = useRouter()

const review = ref({
  review_id: props.id,
  status: 'completed',
  repo_url: 'elderly-care-backend',
  cost: { tokens_in: 2856, tokens_out: 910, cost_usd: 0.000655 },
  findings: [
    { file: 'src/main/resources/application.yml', line_start: 45, line_end: 48, severity: 'critical', category: 'security',
      title: '硬编码的 API 凭据', confidence: 1.0,
      description: '青蓝 API 的 appid 和 secret 以明文硬编码在 application.yml 中。任何能访问源码或构建产物的人都能获取这些凭据，冒充应用访问青蓝 API。',
      suggestion: '替换为环境变量：appid: ${QINGLAN_APPID}\nsecret: ${QINGLAN_SECRET}' },
    { file: 'src/main/java/.../config/WebConfig.java', line_start: 35, line_end: 35, severity: 'warning', category: 'security',
      title: '/radar/callback/** 绕过认证', confidence: 0.85,
      description: '该路径被排除在 JWT 拦截器之外，但未见替代鉴权机制。外部系统可直接调用此回调接口。',
      suggestion: '添加 API Key 或 HMAC 签名验证。' },
    { file: 'src/main/java/.../AlertEventServiceImpl.java', line_start: 237, line_end: 237, severity: 'suggestion', category: 'style',
      title: '魔法数字：随机数范围硬编码', confidence: 0.75,
      description: 'diastolic 的随机数范围 90~104 未定义为常量，出现多次。',
      suggestion: '定义为 DIastolic_MIN/MAX 常量。' },
  ]
})

const sevClass = (s) => ({ critical: 'sev-critical', warning: 'sev-warning', suggestion: 'sev-suggestion', nitpick: '' }[s] || '')
const badgeClass = (s) => ({ critical: 'bg-red', warning: 'bg-yellow', suggestion: 'bg-blue', nitpick: 'bg-gray' }[s] || 'bg-gray')
</script>

<template>
  <div>
    <a @click="router.push('/reviews')" style="cursor:pointer;font-size:13px;color:#3498db;text-decoration:none;margin-bottom:16px;display:inline-block">← 返回列表</a>
    <h1 style="margin-bottom:8px">审查详情</h1>
    <div class="card" style="margin-bottom:8px">
      <p style="font-size:13px;color:#888">ID: <code>{{ review.review_id }}</code> &nbsp;|&nbsp; 仓库: {{ review.repo_url }} &nbsp;|&nbsp; 状态: <span class="badge bg-green">{{ review.status }}</span></p>
      <p style="font-size:12px;color:#888;margin-top:4px">Tokens: {{ review.cost.tokens_in }} in / {{ review.cost.tokens_out }} out &nbsp;|&nbsp; 成本: ${{ review.cost.cost_usd?.toFixed(6) }}</p>
    </div>

    <h3 style="margin:16px 0 8px">{{ review.findings.length }} 条发现</h3>

    <div v-if="!review.findings.length" class="empty">无发现，代码质量良好 ✓</div>

    <div v-for="(f, i) in review.findings" :key="i" class="finding-card" :class="sevClass(f.severity)">
      <div class="meta">
        <span class="badge" :class="badgeClass(f.severity)">{{ f.severity }}</span>
        <span class="badge bg-gray" style="margin-left:6px">{{ f.category }}</span>
        <span style="margin-left:8px">{{ f.file }}:{{ f.line_start }}-{{ f.line_end }}</span>
        <span style="margin-left:8px">置信度 {{ (f.confidence * 100).toFixed(0) }}%</span>
      </div>
      <div class="title">{{ i + 1 }}. {{ f.title }}</div>
      <div class="desc">{{ f.description }}</div>
      <div v-if="f.suggestion" class="suggestion">{{ f.suggestion }}</div>
    </div>
  </div>
</template>
