import axios from 'axios'

const api = axios.create({ baseURL: '/api/v1' })

export async function submitReview(diffContent, repoUrl = null, prNumber = null) {
  const { data } = await api.post('/reviews', { diff_content: diffContent, repo_url: repoUrl, pr_number: prNumber })
  return data
}

export async function getReviewStatus(reviewId) {
  const { data } = await api.get(`/reviews/${reviewId}`)
  return data
}

export async function getRecentReviews(page = 1, size = 20) {
  const { data } = await api.get('/reviews', { params: { page, size } })
  return data
}

export async function checkHealth() {
  const { data } = await axios.get('/health')
  return data
}
