<script setup>
import { ref, onMounted } from 'vue'
import { Chart, registerables } from 'chart.js'
Chart.register(...registerables)

const stats = ref({ total: 42, critical: 17, warning: 89, cost: 0.12 })
const barCanvas = ref(null)
const doughnutCanvas = ref(null)

onMounted(() => {
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
      <div class="stat-card"><div class="value" style="color:#e74c3c">{{ stats.critical }}</div><div class="label">严重发现</div></div>
      <div class="stat-card"><div class="value" style="color:#f39c12">{{ stats.warning }}</div><div class="label">警告发现</div></div>
      <div class="stat-card"><div class="value" style="color:#2ecc71">${{ stats.cost }}</div><div class="label">累计成本</div></div>
    </div>

    <div class="charts">
      <div class="card"><h3>审查趋势（近 7 天）</h3><canvas ref="barCanvas" height="200"></canvas></div>
      <div class="card"><h3>发现分布</h3><canvas ref="doughnutCanvas" height="200"></canvas></div>
    </div>

    <div class="card">
      <h3>最近审查</h3>
      <table>
        <thead><tr><th>ID</th><th>仓库</th><th>状态</th><th>发现数</th><th>耗时</th><th>成本</th></tr></thead>
        <tbody>
          <tr v-for="i in 5" :key="i">
            <td style="font-family:monospace;font-size:12px">{{ 'a1b2c' + i }}</td>
            <td>elderly-care-backend</td>
            <td><span class="badge bg-green">completed</span></td>
            <td>{{ [4,2,0,6,1][i-1] }}</td>
            <td>{{ [8.2,12.1,3.4,18.9,5.6][i-1] }}s</td>
            <td>${{ ['0.0007','0.0012','0.0003','0.0015','0.0006'][i-1] }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
