import request from './request'

// 用户工作台（me 域）接口封装
export const getMe = () => request.get('/me')
export const listMyRecharges = () => request.get('/me/recharges')
export const getReferral = () => request.get('/me/referral')
export const bindReferrer = (invite_code) => request.post('/me/bind-referrer', { invite_code })
export const getRefundable = () => request.get('/me/refundable')
export const submitRefund = (data) => request.post('/me/refund-request', data)
export const listRefundRequests = () => request.get('/me/refund-requests')
