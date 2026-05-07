
# 青藤安全平台浏览器自动化

### 浏览器最小操作模板
```bash
agent-browser open "https://<domain>/<path>"
agent-browser wait --load networkidle
agent-browser snapshot -i
```

更多使用方法参考： agent-browser skill

## 重要提醒

- **Session 管理**：详见[零、登录认证](#零登录认证)。任务开始前先确认 `auth-state.json` 存在；CLI 认证失败时先走恢复流程，不要立刻要求用户重新登录。
- **禁止连续失败循环**：同一命令最多重试 2 次；认证恢复流程只走一次，仍失败则提示用户手动重新登录。
  - **以下错误属于需要用户干预的基础设施问题，立即停止所有重试，直接告知用户处理**：
    - `ERR_CERT_AUTHORITY_INVALID`：站点证书不被本机信任，使用 `--ignore-https-errors` 或请求用户处理。
    - `ERR_NAME_NOT_RESOLVED`：域名无法解析，告知用户确认域名或检查 DNS / hosts 配置。