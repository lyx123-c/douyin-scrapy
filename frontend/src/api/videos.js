import http from './index'

export function getVideos({
  keyword = '',
  status = 'all',
  page = 1,
  pageSize = 50,
  sortBy = 'library',
  sortOrder = 'asc',
} = {}) {
  return http.get('/api/videos', {
    params: {
      keyword,
      status,
      page,
      page_size: pageSize,
      sort_by: sortBy,
      sort_order: sortOrder,
    },
  })
}

export function getVideoSummary({ signal } = {}) {
  return http.get('/api/videos/summary', { signal })
}
