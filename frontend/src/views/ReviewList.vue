<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { listReviews } from '../api'

const router = useRouter()
const reviews = ref([])
const loading = ref(true)
const page = ref(1)

async function load() {
  loading.value = true
  try {
    const data = await listReviews(page.value)
    reviews.value = data.reviews || []
  } catch (e) {
    console.log('Using demo data')
    reviews.value = [
      { review_id: 'demo-1', repo_url: 'elderly-care-backend', status: 'completed', findings_count: 4, duration_ms: 8200, cost_usd: 0.0007, created_at: '2025-06-10 10:30' },
      { review_id: 'demo-2', repo_url: 'elderly-care-backend', status: 'completed', findings_count: 2, duration_ms: 12100, cost_usd: 0.0012, created_at: '2025-06-10 09:15' },
    ]
  }
  loading.value = false
}

onMounted(load)

const statusClass = (s) => ({ completed: 'bg-green', running: 'bg-blue', failed: 'bg-red', queued: 'bg-gray' }[s] || 'bg-gray')
const goDetail = (id) => router.push(`/reviews/${id}`)
const prevPage = () => { if (page.value > 1) { page.value--; load() } }
const nextPage = () => { page.value++; load() }
</script>

<template>
  <div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
      <h1>审查记录</h1>
      <span style="font-size:13px;color:#888">{{ reviews.length }} 条</span>
    </div>
    <div v-if="loading" class="empty">加载中...</div>
    <div v-else class="card" style="padding:0">
      <table>
        <thead><tr><th>ID</th><th>仓库</th><th>状态</th><th>发现数</th><th>耗时</th><th>成本</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="r in reviews" :key="r.review_id" style="cursor:pointer" @click="goDetail(r.review_id)">
            <td style="font-family:monospace;font-size:12px">{{ r.review_id.slice(0, 8) }}</td>
            <td>{{ r.repo_url || '-' }}</td>
            <td><span class="badge" :class="statusClass(r.status)">{{ r.status }}</span></td>
            <td>{{ r.findings_count }}</td>
            <td>{{ r.duration_ms ? (r.duration_ms/1000).toFixed(1)+'s' : '-' }}</td>
            <td>{{ r.cost_usd ? '$'+r.cost_usd.toFixed(4) : '-' }}</td>
            <td style="font-size:12px;color:#888">{{ r.created_at?.slice(0, 16) || '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="pagination" v-if="!loading && reviews.length">
      <button @click="prevPage" :disabled="page === 1">上一页</button>
      <button class="active">{{ page }}</button>
      <button @click="nextPage">下一页</button>
    </div>
  </div>
</template>
