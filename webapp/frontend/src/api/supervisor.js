// R32 固化 Supervisor：只读观测接口（off 返回 supported=false，前端据此不渲染）
import request from './request'

export const getSupervisor = (id) => request.get(`/projects/${id}/supervisor`)
