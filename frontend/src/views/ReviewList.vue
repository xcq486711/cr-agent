<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const reviews = ref([
  { id: 'a1b2c1', repo: 'elderly-care-backend', status: 'completed', findings: 4, duration_ms: 8200, cost_usd: 0.0007, created_at: '2025-06-10 10:30' },
  { id: 'a1b2c2', repo: 'elderly-care-backend', status: 'completed', findings: 2, duration_ms: 12100, cost_usd: 0.0012, created_at: '2025-06-10 09:15' },
  { id: 'a1b2c3', repo: 'cr-agent', status: 'completed', findings: 0, duration_ms: 3400, cost_usd: 0.0003, created_at: '2025-06-09 18:00' },
  { id: 'a1b2c4', repo: 'elderly-care-backend', status: 'completed', findings: 6, duration_ms: 18900, cost_usd: 0.0015, created_at: '2025-06-09 15:20' },
  { id: 'a1b2c5', repo: 'cr-agent', status: 'failed', findings: 0, duration_ms: 0, cost_usd: 0, created_at: '2025-06-09 14:00' },
  { id: 'a1b2c6', repo: 'elderly-care-backend', status: 'completed', findings: 1, duration_ms: 5600, cost_usd: 0.0006, created_at: '2025-06-08 11:45' },
])

const statusClass = (s) => ({ completed: 'bg-green', running: 'bg-blue', failed: 'bg-red', queued: 'bg-gray' }[s] || 'bg-gray')
const goDetail = (id) => router.push(`/reviews/${id}`)
</script>

<template>
  <div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
      <h1>审查记录</h1>
      <span style="font-size:13px;color:#888">{{ reviews.length }} 条记录</span>
    </div>
    <div class="card" style="padding:0">
      <table>
        <thead><tr><th>ID</th><th>仓库</th><th>状态</th><th>发现数</th><th>耗时</th><th>成本</th><th>时间</th><th></th></tr></thead>
        <tbody>
          <tr v-for="r in reviews" :key="r.id" style="cursor:pointer" @click="goDetail(r.id)">
            <td style="font-family:monospace;font-size:12px">{{ r.id }}</td>
            <td>{{ r.repo }}</td>
            <td><span class="badge" :class="statusClass(r.status)">{{ r.status }}</span></td>
            <td>{{ r.findings }}</td>
            <td>{{ r.duration_ms ? (r.duration_ms/1000).toFixed(1) + 's' : '-' }}</td>
            <td>{{ r.cost_usd ? '$' + r.cost_usd.toFixed(4) : '-' }}</td>
            <td style="font-size:12px;color:#888">{{ r.created_at }}</td>
            <td style="color:#3498db;font-size:12px">查看 →</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
