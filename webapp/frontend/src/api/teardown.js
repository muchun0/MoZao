import request from './request'

export const getTeardownGenres = () => request.get('/teardowns/genres')
export const getTeardowns = (params) => request.get('/teardowns', { params })
export const getTeardownDetail = (id) => request.get(`/teardowns/${id}`)
export const deleteTeardown = (id) => request.delete(`/teardowns/${id}`)
export const analyzeTeardown = (data) => request.post('/teardowns/analyze', data)
