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

export async function listReviews(page = 1, size = 20) {
  const { data } = await api.get('/reviews', { params: { page, size } })
  return data
}

export async function submitReviewAndPoll(diffContent, onProgress) {
  const { review_id } = await submitReview(diffContent)
  let attempts = 0
  while (attempts < 60) {
    const status = await getReviewStatus(review_id)
    if (onProgress) onProgress(status)
    if (status.status === 'completed' || status.status === 'failed') return status
    await new Promise(r => setTimeout(r, 2000))
    attempts++
  }
  throw new Error('Review timed out')
}
