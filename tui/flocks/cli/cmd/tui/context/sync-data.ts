/**
 * 非阻塞 bootstrap 请求失败（如后端 500）时 SDK 返回 data=undefined；
 * 写入 store 前回退到默认值，界面停在 partial 降级，而不是把 undefined 写进去让 home/footer 崩溃。
 */
export function dataOr<T>(response: { data?: T }, fallback: T): T {
  return response.data ?? fallback
}
