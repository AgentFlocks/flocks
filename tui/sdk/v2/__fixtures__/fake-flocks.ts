// 测试用假 `flocks serve`：校验 Python CLI 的参数形态，打印 Python 的启动行，再起一个只回 /api/ready 的 HTTP 服务
const args = process.argv.slice(2)
const hostIdx = args.indexOf("--host")
const portIdx = args.indexOf("--port")
if (args[0] !== "serve" || hostIdx < 0 || portIdx < 0 || args.some((a) => a.startsWith("--hostname") || a.startsWith("--log-level"))) {
  console.error(`Usage: flocks serve [OPTIONS]\nTry 'flocks serve --help' for help.\n\nError: No such option: ${args.find((a) => a.startsWith("--")) ?? args.join(" ")}`)
  process.exit(2)
}
const host = args[hostIdx + 1]
const port = Number(args[portIdx + 1])
console.log(`\x1b[36mStarting server on:\x1b[0m http://${host}:${port}`)
console.log(`\x1b[36mAPI docs:\x1b[0m http://${host}:${port}/docs`)
// 模拟 uvicorn 晚一点才绑定端口
await new Promise((r) => setTimeout(r, 400))
Bun.serve({ hostname: host, port, fetch: (req) => (new URL(req.url).pathname === "/api/ready" ? Response.json({ ready: true }) : new Response("nope", { status: 404 })) })
