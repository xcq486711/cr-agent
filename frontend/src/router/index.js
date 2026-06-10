import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from '../views/Dashboard.vue'
import ReviewList from '../views/ReviewList.vue'
import ReviewDetail from '../views/ReviewDetail.vue'

const routes = [
  { path: '/', name: 'Dashboard', component: Dashboard },
  { path: '/reviews', name: 'ReviewList', component: ReviewList },
  { path: '/reviews/:id', name: 'ReviewDetail', component: ReviewDetail, props: true },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
