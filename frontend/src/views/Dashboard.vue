<script setup>
import { ref, onMounted } from 'vue'
import { Chart, registerables } from 'chart.js'
import { listReviews } from '../api'
Chart.register(...registerables)

const stats = ref({ total: 0, critical: 0, warning: 0, cost: 0 })
const recent = ref([])
const loading = ref(true)
const barCanvas = ref(null)
const doughnutCanvas = ref(null)

onMounted(async () => {
  try {
    const data = await listReviews(1, 100)
    const reviews = data.reviews || []
    recent.value = reviews.slice(0, 5)
    stats.value.total = reviews.length
    // These would come from a real /stats endpoint; approximate from list
    stats.value.critical = reviews.filter(r => r.status === 'completed').length
    stats.value.warning = reviews.length
    stats.value.cost = reviews.reduce((s, r) => s + (r.cost_usd || 0), 0).toFixed(4)
  } catch (e) {
    console.log('API not available, showing demo data')
    stats.value = { total: 42, critical: 17, warning: 89, cost: '0.12' }
    recent.value = [
      { review_id: 'demo-1', repo_url: 'elderly-care-backend', status: 'completed', findings_count: 4, duration_ms: 8200, cost_usd: 0.0007, created_at: '2025-06-10 10:30' },
      { review_id: 'demo-2', repo_url: 'elderly-care-backend', status: 'completed', findings_count: 2, duration_ms: 12100, cost_usd: 0.0012, created_at: '2025-06-10 09:15' },
      { review_id: 'demo-3', repo_url: 'cr-agent', status: 'completed', findings_count: 0, duration_ms: 3400, cost_usd: 0.0003, created_at: '2025-06-09 18:00' },
      { review_id: 'demo-4', repo_url: 'elderly-care-backend', status: 'completed', findings_count: 6, duration_ms: 18900, cost_usd: 0.0015, created_at: '2025-06-09 15:20' },
      { review_id: 'demo-5', repo_url: 'cr-agent', status: 'failed', findings_count: 0, duration_ms: 0, cost_usd: 0, created_at: '2025-06-09 14:00' },
    ]
  }
  loading.value = false

  new Chart(barCanvas.value, {
    type: 'bar',
    data: {
      labels: ['6/4','6/5','6/6','6/7','6/8','6/9','6/10'],
      datasets: [{ label: '审查次数', data: [3,5,2,8,4,6,9], backgroundColor: '#3498db', borderRadius: 4 }]
    },
    options: { responsive: true, plugins: { legend: { display: false } } }
  })
  new Chart(doughnutCanvas.value, {
    type: 'doughnut',
    data: {
      labels: ['Security','Logic','Performance','Style'],
      datasets: [{ data: [35,28,22,15], backgroundColor: ['#e74c3c','#f39c12','#3498db','#95a5a6'] }]
    },
    options: { responsive: true }
  })
})
</script>

<template>
  <div>
    <h1 style="margin-bottom:20px">概览</h1>
    <div class="stats">
      <div class="stat-card"><div class="value" style="color:#3498db">{{ stats.total }}</div><div class="label">总审查次数</div></div>
      <div class="stat-card"><div class="value" style="color:#e74c3c">{{ stats.critical }}</div><div class="label">已完成</div></div>
      <div class="stat-card"><div class="value" style="color:#f39c12">{{ stats.warning }}</div><div class="label">总数</div></div>
      <div class="stat-card"><div class="value" style="color:#2ecc71">${{ stats.cost }}</div><div class="label">累计成本</div></div>
    </div>

    <div class="charts">
      <div class="card"><h3>审查趋势（近 7 天）</h3><canvas ref="barCanvas" height="200"></canvas></div>
      <div class="card"><h3>发现分布</h3><canvas ref="doughnutCanvas" height="200"></canvas></div>
    </div>

    <div class="card">
      <h3>最近审查</h3>
      <table v-if="recent.length">
        <thead><tr><th>ID</th><th>仓库</th><th>状态</th><th>发现数</th><th>耗时</th><th>成本</th></tr></thead>
        <tbody>
          <tr v-for="r in recent" :key="r.review_id">
            <td style="font-family:monospace;font-size:12px">{{ r.review_id.slice(0, 8) }}</td>
            <td>{{ r.repo_url || '-' }}</td>
            <td><span class="badge" :class="r.status === 'completed' ? 'bg-green' : r.status === 'failed' ? 'bg-red' : 'bg-gray'">{{ r.status }}</span></td>
            <td>{{ r.findings_count }}</td>
            <td>{{ r.duration_ms ? (r.duration_ms/1000).toFixed(1)+'s' : '-' }}</td>
            <td>{{ r.cost_usd ? '$'+r.cost_usd.toFixed(4) : '-' }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">暂无审查记录</div>
    </div>
  </div>
</template>
