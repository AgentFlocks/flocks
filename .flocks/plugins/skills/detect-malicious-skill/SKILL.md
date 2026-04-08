name: detect-malicious-skill
category: security
description: >
  对任意 Flocks skill 进行多维度安全审计，覆盖结构完整性、Prompt Injection、
  危险命令、数据外传、凭据泄露、依赖安全、权限最小化、混淆隐藏内容和 LLM 语义分析。
  输出整体风险评级（CRITICAL/HIGH/MEDIUM/LOW/SAFE）及带行号的问题清单。
  用途：在安装来源不明的 skill 前进行安全审查，帮助识别潜在恶意行为。
  使用方式：用户提供 skill 路径或内容后触发，skill 仅报告不阻断。

# ============================================================
# 检测能力覆盖清单
# ============================================================
# (A) SKILL.md 结构完整性与异常 Unicode 字符检测
# (B) Prompt Injection 静态关键词匹配
# (C) 危险命令检测
# (D) 数据外传检测
# (E) 凭据与硬编码敏感信息检测
# (F) 依赖安全检测（Typosquatting、来源、版本锁定）
# (G) 权限最小化检测（tool 与 description 一致性）
# (H) 混淆与隐藏内容检测
# (I) LLM 语义分析（Prompt Injection / 欺骗 / 社会工程）

# ============================================================
# 核心逻辑（所有检测在 Python 代码块中实现）
# ============================================================
implementation: |
  import asyncio
  import base64
  import hashlib
  import json
  import os
  import re
  import unicodedata
  from collections import Counter
  from difflib import SequenceMatcher
  from pathlib import Path
  from typing import Any

  # ---- 威胁签名库 ----

  # (B) Prompt Injection 静态关键词
  INJECT_PATTERNS = [
      (r"\b(忽略|无视|忘记|覆盖|绕过)[\s:：]*(以上|前述|所有|之前|之前|system|指令)", "指令覆盖/忽略指令", True),
      (r"\b(你(现在|从现在)|从此刻).{0,10}(不是|不再是|只是)\s", "角色覆盖伪装", True),
      (r"(DAN|Jailbreak|Do Anything Now|STEVEN)", "Jailbreak 触发词", True),
      (r"(role[\s:=]+['\"]?system['\"]?|system[\s:=]+\{)", "system 角色重声明", True),
      (r"(你是|我扮演|I am|pretend|角色扮演).{0,30}(AI|助手|assistant|没有限制|无约束|no restrictions)", "角色扮演绕过", True),
      (r"(sudo|rm\s+-rf|eval|exec)\s*\(", "危险函数直接调用", True),
      (r"(/etc/passwd|/etc/shadow|\.ssh|\.aws|\.netrc)", "敏感路径试探", False),
      (r"(--no-rcfile|--init-file|~/.bashrc|~/.zshrc)", "Shell 初始化劫持", True),
      (r"(curl|wget).{0,100}(eval|bash|sh)\s*\(", "远程代码执行链", True),
      (r"(whoami|uname|id|cat|ls)\s+(/etc|/|~\.)", "系统侦察指令", False),
  ]

  # (C) 危险命令
  DANGEROUS_CMDS = [
      (r"\brm\s+(-[rf]+\s+)?/", "根目录递归删除", "CRITICAL"),
      (r"\brm\s+-[rf]+\s+(boot|etc|usr|var|opt|home|root)", "系统目录删除", "CRITICAL"),
      (r"\bmkfs\(", "格式化磁盘", "CRITICAL"),
      (r"\bdd\s+.*of=/dev/(sd|vd|nvme)", "直接写盘", "CRITICAL"),
      (r"(sudo|su\s+-|su\s+root)", "提权尝试", "HIGH"),
      (r"\bchmod\s+777\b", "过度授权", "HIGH"),
      (r"\bchmod\s+[0]{3}\b", "移除执行权限", "LOW"),
      (r"\bpkill\s+(-9)?\s+(-1|init|systemd)", "杀核心进程", "CRITICAL"),
      (r"shutdown|halt|reboot|init\s+0|init\s+6", "关机/重启命令", "HIGH"),
      (r"\bcrontab\s+(-r|-e)\b", "篡改定时任务", "HIGH"),
      (r"\bnohup\s+.*&\s*$", "后台隐藏执行", "MEDIUM"),
      (r">\s*/dev/(null|sda|sdb|zero)", "输出重定向到设备", "MEDIUM"),
      (r"eval\s*\(", "动态代码执行", "HIGH"),
      (r"exec\s*\(", "进程替换执行", "HIGH"),
      (r"python.{0,30}(os\.system|subprocess|pty)", "Python 代码注入", "HIGH"),
      (r"subprocess\.(call|run|Popen).{0,50}(shell\s*=\s*True|shlex)", "Shell 注入风险", "HIGH"),
      (r"bash\s+-[ic]\b", "交互式 Bash 劫持", "HIGH"),
      (r"tee\s+.*\|", "管道链注入", "MEDIUM"),
      (r"(\$\(|``|`[^`]*`)", "命令替换注入", "HIGH"),
      (r"\$\{?[A-Z_]{4,}\}?", "环境变量注入", "MEDIUM"),
  ]

  # (D) 数据外传
  EXFIL_PATTERNS = [
      (r"curl\s+.*(http|https)://", "HTTP 外传", "HIGH"),
      (r"wget\s+.*(http|https)://", "HTTP 外传", "HIGH"),
      (r"requests\.(get|post)\(", "Python HTTP 请求", "HIGH"),
      (r"http\.post|http\.get|httpRequest", "HTTP 请求（Node）", "HIGH"),
      (r"fetch\s*\(.{0,30}(http|https)", "Fetch API 外传", "HIGH"),
      (r"smtplib|SMTP\(", "邮件外传通道", "HIGH"),
      (r"sendgrid|mailgun|ses\.send", "第三方邮件服务", "MEDIUM"),
      (r"webhook", "Webhook 通道", "MEDIUM"),
      (r"socket\.connect|dns\.resolve|nslookup", "DNS 通道试探", "MEDIUM"),
      (r"\.open\((.{0,20},.{0,20})?\s*['\"]?(r|w)a?['\"]?\s*\)", "文件操作", "LOW"),
      (r"pathlib\.Path\(.+\)\.read_text\(\)", "读取文件内容", "LOW"),
      (r"glob\(|Path\(.+\)\.glob\(", "文件路径枚举", "MEDIUM"),
  ]

  # (E) 凭据正则
  CREDENTIAL_PATTERNS = [
      (r"sk-[A-Za-z0-9_-]{20,}", "OpenAI API Key", "CRITICAL"),
      (r"(?i)openai[_-]?api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_-]+", "OpenAI API Key（显式）", "CRITICAL"),
      (r"ghp_[A-Za-z0-9]{36,}", "GitHub Personal Access Token", "CRITICAL"),
      (r"(?i)github[_-]?(token|key|pat|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_-]+", "GitHub 凭据（显式）", "CRITICAL"),
      (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID", "CRITICAL"),
      (r"(?i)aws[_-]?(access[_-]?key|secret[_-]?key|token)\s*[:=]\s*['\"]?[^'\"]{10,}", "AWS 凭据（显式）", "CRITICAL"),
      (r"(?i)azure[_-]?(key|token|secret|subscription)\s*[:=]\s*['\"]?[^'\"]{10,}", "Azure 凭据", "CRITICAL"),
      (r"(?i)gcp[_-]?(key|token|secret|credential)\s*[:=]\s*['\"]?[^'\"]{10,}", "GCP 凭据", "CRITICAL"),
      (r"(?i)(slack|discord|telegram|钉钉|飞书)[_-]?(token|key|secret|webhook)\s*[:=]\s*['\"]?[^'\"]{10,}", "IM 平台凭据", "HIGH"),
      (r"-----BEGIN\s+(RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", "私钥文件", "CRITICAL"),
      (r"(?i)password\s*[:=]\s*['\"]?[^'\"]{6,}", "硬编码密码", "HIGH"),
      (r"(?i)(api[_-]?key|apikey|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_-]{16,}", "通用 API Key", "MEDIUM"),
  ]

  # (H) 混淆检测
  OBFUSCATION_PATTERNS = [
      (r"base64\.(b64decode|b64encode|decodebytes)\s*\(", "Base64 编解码操作", "MEDIUM"),
      (r"(import|from)\s+base64", "导入 Base64 模块", "MEDIUM"),
      (r"\\x[0-9a-fA-F]{2}", "十六进制转义序列", "HIGH"),
      (r"\\u[0-9a-fA-F]{4}", "Unicode 转义序列", "HIGH"),
      (r"\\[nrtbfae]", "转义字符混淆", "LOW"),
      (r".{5000,}", "超长单行（5000+字符）", "HIGH"),
      (r"[\u200b\u200c\u200d\u2060\ufeff]", "零宽字符", "HIGH"),
      (r"[\u2800-\u28ff]", "盲文 Unicode 混淆", "HIGH"),
      (r"#\s*(eval|exec|解码|解密|隐藏|hidden|stealth)", "注释中的隐藏指令", "HIGH"),
      (r"(0o[0-7]{3,}|0x[0-9a-fA-F]+)", "八进制/十六进制数字混淆", "MEDIUM"),
      (r"chr?\s*\(\d+\).{0,10}chr?\s*\(\d+\)", "ASCII 数字构建字符串", "HIGH"),
      (r"__import__\s*\(\s*['\"](os|sys|subprocess)", "动态模块导入", "HIGH"),
      (r"getattr\s*\(\s*__import__", "反射式动态调用", "HIGH"),
      (r"(compile|exec|eval)\s*\(\s*[\"']", "动态代码执行字符串", "HIGH"),
      (r"\$[a-zA-Z_]\w*|\\$\{|\\$\w+", "Shell 变量/插值", "MEDIUM"),
      (r"<!--.*?-->", "HTML 注释隐藏", "MEDIUM"),
  ]

  # 著名包名的 Typosquatting 变体
  TYPOSQUATTING_VARIANTS = {
      "requests": ["request", "requesets", "reqeusts", "requestes", "reques.ts"],
      "urllib": ["urlib", "urlib3", "urlllib", "url ib"],
      "flask": ["flsk", "flask2", "flask-", "Flask"],
      "django": ["djang0", "djanga", "djongo", "Django"],
      "numpy": ["numyp", "numpi", "nu mpy", "NumPy"],
      "pandas": ["pandaz", "panas", "panda2", "Pandas"],
      "openssl": ["open-ssl", "openssl2", "opnessl"],
      "pyyaml": ["pyyml", "py-yaml", "pyYAML", "PyYAML"],
      "python-dotenv": ["dotenv", "dot_env", "pythonenv"],
      "flocks": ["fl0cks", "flock", "floks", "fl0ck", "Fl0cks"],
  }

  # (G) 高危 tool 列表
  HIGH_RISK_TOOLS = {
      "bash": ["执行任意命令", "shell 脚本", "系统操作", "命令行执行"],
      "ssh_run_script": ["远程执行命令", "SSH 远程执行"],
      "ssh_host_cmd": ["远程主机命令"],
      "write": ["写入文件", "创建/修改文件"],
      "edit": ["修改文件内容"],
      "delete": ["删除文件", "删除操作"],
      "remove": ["移除文件"],
      "rm": ["删除文件"],
      "unlink": ["删除文件链接"],
      "chmod": ["修改文件权限"],
      "chown": ["修改文件所有者"],
      "pkill": ["终止进程"],
      "kill": ["终止进程"],
      "system": ["执行系统命令"],
      "eval": ["动态代码执行"],
      "exec": ["执行代码"],
      "subprocess": ["子进程执行"],
      "os.system": ["系统命令执行"],
      "shell": ["Shell 执行"],
      "run": ["执行命令或脚本"],
      "download": ["下载文件"],
      "upload": ["上传文件"],
      "file_write": ["写入文件"],
      "code_execution": ["代码执行"],
  }

  def detect_unicode_anomalies(text: str, lines: list[str]) -> list[dict]:
      issues = []
      suspicious_ranges = [
          (0x200B, 0x200F),  # Zero-width chars
          (0x2028, 0x202F),  # Line/para separators
          (0x2060, 0x206F),  # Format chars
          (0xFE00, 0xFE0F),  # Variation selectors
          (0x1F300, 0x1F9FF),  # Emoji (often used in homoglyph attacks)
      ]
      for i, line in enumerate(lines, 1):
          for char in line:
              cat = unicodedata.category(char)
              if cat.startswith("C") and char not in "\n\r\t":
                  issues.append({
                      "line": i,
                      "type": "控制字符",
                      "detail": f"发现控制字符 U+{ord(char):04X} ({unicodedata.name(char, 'UNKNOWN')})",
                      "severity": "MEDIUM",
                  })
              for start, end in suspicious_ranges:
                  if start <= ord(char) <= end:
                      issues.append({
                          "line": i,
                          "type": "异常 Unicode",
                          "detail": f"发现可疑 Unicode 字符 U+{ord(char):04X} (零宽/特殊格式)",
                          "severity": "MEDIUM",
                      })
      return issues

  def check_typosquatting(deps: list[str]) -> list[dict]:
      issues = []
      for dep in deps:
          dep_lower = dep.strip().lower()
          base_match = re.match(r"^([a-z0-9_-]+)", dep_lower)
          if not base_match:
              continue
          name = base_match.group(1)
          if name in TYPOSQUATTING_VARIANTS:
              for typo in TYPOSQUATTING_VARIANTS[name]:
                  if SequenceMatcher(None, dep_lower, typo).ratio() >= 0.8:
                      issues.append({
                          "line": 0,
                          "type": "Typosquatting 嫌疑",
                          "detail": f"依赖 '{dep}' 与正规包 '{name}' 编辑距离极近，可能是 Typosquatting 攻击",
                          "severity": "CRITICAL",
                      })
                      break
      return issues

  PYTHON_KEYWORDS = frozenset({
      "and", "as", "assert", "async", "await", "break", "class", "continue",
      "def", "del", "elif", "else", "except", "finally", "for", "from",
      "global", "if", "import", "in", "is", "lambda", "None", "nonlocal",
      "not", "or", "pass", "raise", "return", "try", "while", "with",
      "yield", "True", "False", "self", "cls",
  })

  KNOWN_THIRD_PARTY = frozenset({
      "os", "sys", "re", "json", "time", "datetime", "pathlib", "typing",
      "collections", "itertools", "functools", "operator", "enum", "uuid",
      "hashlib", "hmac", "secrets", "random", "string", "textwrap", "copy",
      "warnings", "traceback", "gc", "weakref", "dataclasses", "abc",
      "io", "os.path", "socket", "select", "signal", "atexit",
  })

  def check_dependency_security(deps: list[str], text: str) -> list[dict]:
      issues = []
      lines = text.split("\n")

      # 未锁定版本 — 仅检测第三方包，排除 Python 关键字和内置模块
      for i, dep in enumerate(deps, 1):
          base = dep.strip().split(".")[0].lower()
          if base in PYTHON_KEYWORDS or base in KNOWN_THIRD_PARTY:
              continue
          if len(base) < 3:
              continue
          if re.match(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*$", dep.strip()):
              issues.append({
                  "line": 0,
                  "type": "版本未锁定",
                  "detail": f"依赖 '{dep}' 未指定版本范围，建议使用 '>=' 或 '==' 锁定版本",
                  "severity": "LOW",
              })
          if "github" in dep or "git+" in dep:
              issues.append({
                  "line": 0,
                  "type": "Git 依赖来源",
                  "detail": f"依赖 '{dep}' 直接从 Git 安装，来源不可审计",
                  "severity": "HIGH",
              })
          if "http://" in dep or re.match(r".*:\d+/.*", dep):
              issues.append({
                  "line": 0,
                  "type": "非标准包源",
                  "detail": f"依赖 '{dep}' 指向非标准来源，可能引入供应链风险",
                  "severity": "HIGH",
              })

      # Typosquatting
      issues.extend(check_typosquatting(deps))

      # 检查 requirements.txt / setup.py 中的依赖
      for i, line in enumerate(lines, 1):
          dep_match = re.match(r"^\s*([a-zA-Z0-9_-]+)\s*(?:[=<>!~]+.*)?", line.strip())
          if dep_match:
              dep_name = dep_match.group(1)
              if dep_name.lower() in ["requests", "urllib3"]:
                  issues.append({
                      "line": i,
                      "type": "已知漏洞依赖",
                      "detail": f"依赖 '{dep_name}' 存在已知安全漏洞版本，请确保使用最新版或打过补丁的版本",
                      "severity": "MEDIUM",
                  })

      return issues

  def check_tool_permission_consistency(text: str, lines: list[str], skill_name: str) -> list[dict]:
      issues = []
      for i, line in enumerate(lines, 1):
          for tool, danger_tags in HIGH_RISK_TOOLS.items():
              pattern = rf'\b{re.escape(tool)}\b'
              if re.search(pattern, line, re.IGNORECASE):
                  # 检查附近 3 行内是否有对应描述
                  context_start = max(0, i - 3)
                  context_end = min(len(lines), i + 3)
                  context = "\n".join(lines[context_start:context_end])
                  desc_match = re.search(r'description[:：]\s*["\']([^"\']+)', context, re.IGNORECASE)
                  if desc_match:
                      desc = desc_match.group(1).lower()
                      matched_tag = any(tag in desc for tag in danger_tags)
                      if not matched_tag and any(
                          kw in desc for kw in ["安全", "审计", "只读", "查询", "展示", "输出"]
                      ):
                          issues.append({
                              "line": i,
                              "type": "权限声明不一致",
                              "detail": f"Tool '{tool}' 被声明为安全用途，但该工具具备 {', '.join(danger_tags[:2])} 等高危能力",
                              "severity": "MEDIUM",
                          })
                  else:
                      issues.append({
                          "line": i,
                          "type": "高危 Tool 缺少说明",
                          "detail": f"使用高危 Tool '{tool}' 但未在 description 中明确说明用途",
                          "severity": "LOW",
                      })
      return issues

  def decode_base64_inline(s: str) -> str:
      try:
          decoded = base64.b64decode(s).decode("utf-8", errors="ignore")
          if any(c.isprintable() or c in "\n\r\t" for c in decoded):
              return decoded
      except Exception:
          pass
      return ""

  def check_base64_payloads(text: str, lines: list[str]) -> list[dict]:
      issues = []
      for i, line in enumerate(lines, 1):
          # 匹配长 base64 字符串（>40 字符，忽略常见编码内容如 ssh-rsa 等）
          b64_strings = re.findall(r"[A-Za-z0-9+/=]{40,}", line)
          for b64 in b64_strings:
              if re.match(r"^[A-Za-z0-9+/]+=*$", b64) and len(b64) > 40:
                  if not any(
                      b64.startswith(prefix)
                      for prefix in [
                          "ssh-rsa", "ssh-dss", "ssh-ed25519", "ecdsa-sha2",
                          "MII", "LS0t", "-----BEGIN", "eyJ",  # JWT, PEM, JSON
                      ]
                  ):
                      decoded = decode_base64_inline(b64)
                      if decoded:
                          decoded_lower = decoded.lower()
                          if any(
                              re.search(pat, decoded_lower)
                              for pat in [
                                  r"eval\s*\(", r"exec\s*\(", r"os\.system",
                                  r"subprocess", r"curl.*\|", r"bash.*-c",
                                  r"import\s+os", r"import\s+sys",
                              ]
                          ):
                              issues.append({
                                  "line": i,
                                  "type": "Base64 编码 Payload",
                                  "detail": f"Base64 字符串解码后发现可疑代码（首80字符）：{decoded[:80]}",
                                  "severity": "CRITICAL",
                              })
                          elif decoded.strip():
                              issues.append({
                                  "line": i,
                                  "type": "Base64 编码内容",
                                  "detail": f"Base64 字符串解码结果（首80字符）：{decoded[:80]}",
                                  "severity": "LOW",
                              })
      return issues

  def analyze_semantic_risk(text: str) -> list[dict]:
      issues = []
      text_lower = text.lower()

      # 常见欺骗性描述关键词
      deception_keywords = [
          ("帮助你", "无害化伪装", "该 skill 声称无害但未说明实际功能"),
          ("仅用于演示", "演示伪装", "声称演示用途但可能包含真实能力"),
          ("只读操作", "只读伪装", "声称只读但 tool 声明包含写操作"),
          ("安全扫描", "安全伪装", "以安全名义执行非安全操作"),
          ("查询", "查询伪装", "描述为查询但 tool 包含写入或执行能力"),
          ("分析", "分析伪装", "声称分析但 tool 包含数据传输能力"),
          ("自动备份", "备份伪装", "以备份名义进行数据外传"),
          ("优化", "优化伪装", "声称优化但包含数据收集能力"),
      ]

      for keyword, risk_type, desc in deception_keywords:
          if keyword in text_lower:
              # 额外检查 tool 声明中是否有欺骗行为
              tool_section = re.findall(
                  r"(?:bash|write|edit|delete|rm|exec|eval|system|run|subprocess|download|upload|os\.system|shell)"
                  r"\s*[(\s]",
                  text,
                  re.IGNORECASE,
              )
              if tool_section:
                  issues.append({
                      "line": 0,
                      "type": f"语义欺骗 ({risk_type})",
                      "detail": f"description 声称'{keyword}'但实际 tool 包含 {len(tool_section)} 个高危操作，可能存在功能欺骗",
                      "severity": "HIGH",
                  })
                  break

      # 社会工程学诱导
      social_engineering = [
          (r"(请|please).{0,20}(信任|trust|允许|allow|授权|authorize|批准|approve)", "授权诱导", "提示用户信任/授权操作，降低警惕"),
          (r"(免费|free|免费版|for free|无需付费|no cost)", "优惠诱导", "以免费/无限名义绕过用户评估"),
          (r"(免费|无限制|无限次|无需注册)", "优惠诱导", "以免费/无限名义绕过用户评估"),
          (r"(只需|just|只需要).{0,10}步|一键|傻瓜式", "简化诱导", "简化风险描述，诱导快速执行"),
          (r"所有人都能使用|新手友好|easy|simple setup", "普适性伪装", "模糊能力边界，诱导非专业用户"),
          (r"(请先|please provide|simply enter).{0,15}api[_-]?key", "凭据诱导", "诱导用户提供敏感凭据"),
          (r"(复制|粘贴|运行|copy|paste|run).{0,15}(以下|this|the).{0,10}(命令|代码|脚本|command|code|script)", "直接执行诱导", "诱导用户直接执行来源不明的代码"),
          (r"trust me|trust this|don't worry|无风险|no risk|painless", "信任压低诱导", "通过降低焦虑诱导用户放松警惕"),
      ]

      for pattern, risk_type, desc in social_engineering:
          matches = list(re.finditer(pattern, text_lower))
          if matches:
              issues.append({
                  "line": 0,
                  "type": f"社会工程学诱导 ({risk_type})",
                  "detail": f"{desc}。匹配片段：'{matches[0].group()}'",
                  "severity": "MEDIUM",
              })

      return issues

  def run_full_audit(skill_path: str = None, skill_text: str = None) -> dict:
      if skill_path:
          path = Path(skill_path).expanduser()
          if path.exists():
              if path.is_dir():
                  combined = []
                  for f in path.rglob("*"):
                      if f.suffix in (".md", ".yaml", ".yml", ".py", ".json") and not any(
                          part.startswith(".") for part in f.parts
                      ):
                          try:
                              combined.append(f"# ==== {f.relative_to(path)} ====")
                              combined.append(f.read_text(encoding="utf-8", errors="replace"))
                          except Exception:
                              pass
                  text = "\n".join(combined)
              else:
                  text = path.read_text(encoding="utf-8", errors="replace")
          elif skill_text is not None:
              text = skill_text
          else:
              return {"error": f"文件不存在: {skill_path}"}
      elif skill_text is not None:
          text = skill_text
      else:
          return {"error": "请提供 skill_path 或 skill_text 参数"}

      lines = text.split("\n")
      all_issues = []

      # ---- (A) 结构完整性 & Unicode ----
      has_name = bool(re.search(r"^name:\s*\S+", text, re.MULTILINE))
      has_description = bool(re.search(r"^description:\s*>?\s*\S+", text, re.MULTILINE))
      if not has_name:
          all_issues.append({"line": 0, "type": "缺少 name 字段", "detail": "SKILL.md 必须包含 name 字段", "severity": "CRITICAL"})
      if not has_description:
          all_issues.append({"line": 0, "type": "缺少 description 字段", "detail": "SKILL.md 应包含 description 字段以说明用途", "severity": "MEDIUM"})

      all_issues.extend(detect_unicode_anomalies(text, lines))

      # ---- (B) Prompt Injection ----
      for i, line in enumerate(lines, 1):
          for pattern, desc, severity in INJECT_PATTERNS:
              if re.search(pattern, line, re.IGNORECASE):
                  all_issues.append({
                      "line": i,
                      "type": f"Prompt Injection ({desc})",
                      "detail": f"匹配模式 '{pattern}' — {desc}",
                      "severity": severity,
                  })

      # ---- (C) 危险命令 ----
      for i, line in enumerate(lines, 1):
          for pattern, desc, severity in DANGEROUS_CMDS:
              if re.search(pattern, line, re.IGNORECASE):
                  all_issues.append({
                      "line": i,
                      "type": f"危险命令 ({desc})",
                      "detail": f"匹配危险模式 '{pattern}' — {desc}",
                      "severity": severity,
                  })

      # ---- (D) 数据外传 ----
      for i, line in enumerate(lines, 1):
          for pattern, desc, severity in EXFIL_PATTERNS:
              if re.search(pattern, line, re.IGNORECASE):
                  all_issues.append({
                      "line": i,
                      "type": f"数据外传 ({desc})",
                      "detail": f"匹配模式 '{pattern}' — {desc}",
                      "severity": severity,
                  })

      # ---- (E) 凭据 & 敏感信息 ----
      for i, line in enumerate(lines, 1):
          for pattern, desc, severity in CREDENTIAL_PATTERNS:
              if re.search(pattern, line, re.IGNORECASE):
                  all_issues.append({
                      "line": i,
                      "type": f"敏感信息 ({desc})",
                      "detail": f"发现疑似 {desc}，请确认是否为占位符或测试值",
                      "severity": severity,
                  })

          # Base64 解码后二次检测
          b64_strings = re.findall(r"[A-Za-z0-9+/]{32,}", line)
          for b64 in b64_strings:
              if re.match(r"^[A-Za-z0-9+/]+=*$", b64):
                  decoded = decode_base64_inline(b64)
                  if decoded:
                      for pat, desc, sev in CREDENTIAL_PATTERNS + DANGEROUS_CMDS:
                          if re.search(pat, decoded, re.IGNORECASE):
                              all_issues.append({
                                  "line": i,
                                  "type": f"Base64 编码后二次检测 ({desc})",
                                  "detail": f"Base64 内容解码后匹配敏感模式：{desc}",
                                  "severity": sev,
                              })

      # ---- (F) 依赖安全 ----
      deps = []
      deps_matches = re.findall(r"(?:import|from)\s+([a-zA-Z0-9_\.]+)", text)
      deps.extend(deps_matches)
      deps_matches2 = re.findall(r"(?:^\s*|pip install |uv add |requirements|dependencies)[:\s]+([a-zA-Z0-9_\.-]+)", text, re.MULTILINE)
      deps.extend([re.match(r"^([a-z0-9_\.-]+)", d).group(1) for d in deps_matches2 if re.match(r"^[a-z]", d)])
      deps = list(set(deps))
      all_issues.extend(check_dependency_security(deps, text))

      # ---- (G) 权限最小化 ----
      all_issues.extend(check_tool_permission_consistency(text, lines, ""))

      # ---- (H) 混淆与隐藏 ----
      for i, line in enumerate(lines, 1):
          for pattern, desc, severity in OBFUSCATION_PATTERNS:
              if re.search(pattern, line, re.IGNORECASE):
                  all_issues.append({
                      "line": i,
                      "type": f"混淆/隐藏 ({desc})",
                      "detail": f"匹配模式 '{pattern}' — {desc}",
                      "severity": severity,
                  })

      all_issues.extend(check_base64_payloads(text, lines))

      # ---- (I) LLM 语义分析 ----
      all_issues.extend(analyze_semantic_risk(text))

      # 去重（同一行同一类型）
      seen = set()
      unique_issues = []
      for issue in all_issues:
          key = (issue.get("line", 0), issue.get("type", ""))
          if key not in seen:
              seen.add(key)
              unique_issues.append(issue)

      # 风险评级
      severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
      max_sev = min(
          (severity_order.get(i["severity"], 4) for i in unique_issues), default=4
      )
      rating = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "SAFE"][max_sev]

      if not unique_issues:
          rating = "SAFE"

      # 按行号排序
      unique_issues.sort(key=lambda x: (x.get("line", 0), severity_order.get(x["severity"], 4)))

      return {
          "rating": rating,
          "total_issues": len(unique_issues),
          "breakdown": {
              "A_结构与Unicode": sum(1 for i in unique_issues if "Unicode" in i["type"] or "控制字符" in i["type"] or "结构" in i["type"]),
              "B_Prompt_Injection": sum(1 for i in unique_issues if "Prompt Injection" in i["type"]),
              "C_危险命令": sum(1 for i in unique_issues if "危险命令" in i["type"]),
              "D_数据外传": sum(1 for i in unique_issues if "数据外传" in i["type"]),
              "E_凭据泄露": sum(1 for i in unique_issues if "敏感信息" in i["type"] or "Base64.*凭据" in i["type"]),
              "F_依赖安全": sum(1 for i in unique_issues if "Typosquatting" in i["type"] or "版本未锁定" in i["type"] or "Git.*来源" in i["type"] or "非标准包源" in i["type"] or "已知漏洞" in i["type"]),
              "G_权限最小化": sum(1 for i in unique_issues if "权限" in i["type"]),
              "H_混淆隐藏": sum(1 for i in unique_issues if "混淆" in i["type"] or "隐藏" in i["type"] or "Base64" in i["type"]),
              "I_语义分析": sum(1 for i in unique_issues if "语义欺骗" in i["type"] or "社会工程" in i["type"]),
          },
          "issues": unique_issues,
      }

  # ---- 对外入口 ----
  async def detect_malicious_skill(
      ctx: Any = None,
      skill_path: str = None,
      skill_text: str = None,
  ) -> dict:
      """
      对指定 skill 进行多维度安全审计。

      参数：
        skill_path: skill 文件或目录路径（SKILL.md 或包含 SKILL.md 的目录）
        skill_text: skill 内容字符串（与 skill_path 二选一）

      返回：
        整体风险评级 + 问题清单（带行号和风险说明）
      """
      if not skill_path and not skill_text:
          return {"error": "请提供 skill_path 或 skill_text 参数"}

      text = None
      skill_path_str = "直接内容"
      if skill_path:
          p = Path(skill_path).expanduser()
          if not p.exists():
              return {"error": f"文件不存在: {skill_path}"}
          if p.is_dir():
              combined = []
              for f in p.rglob("*"):
                  if f.suffix in (".md", ".yaml", ".yml", ".py", ".json") and not any(
                      part.startswith(".") for part in f.parts
                  ):
                      try:
                          combined.append(f"# ==== {f.relative_to(p)} ====")
                          combined.append(f.read_text(encoding="utf-8", errors="replace"))
                      except Exception:
                          pass
              text = "\n".join(combined)
              skill_path_str = str(p)
          else:
              text = p.read_text(encoding="utf-8", errors="replace")
              skill_path_str = str(p)
      else:
          text = skill_text

      result = run_full_audit(skill_path=skill_path_str, skill_text=text)

      # 格式化输出
      rating = result.get("rating", "UNKNOWN")
      rating_emoji = {
          "CRITICAL": "[CRITICAL]",
          "HIGH": "[HIGH]",
          "MEDIUM": "[MEDIUM]",
          "LOW": "[LOW]",
          "SAFE": "[SAFE]",
      }.get(rating, "[UNKNOWN]")

      lines_output = [
          f"## Skill 安全审计报告 — {skill_path_str}",
          "",
          f"### 整体风险评级：{rating_emoji} {rating}",
          "",
      ]

      breakdown = result.get("breakdown", {})
      if breakdown:
          lines_output.append("### 分类统计")
          lines_output.append("| 类别 | 问题数 |")
          lines_output.append("|------|--------|")
          for cat, cnt in breakdown.items():
              lines_output.append(f"| {cat} | {cnt} |")
          lines_output.append("")

      issues = result.get("issues", [])
      if issues:
          lines_output.append(f"### 发现 {len(issues)} 个问题")
          lines_output.append("")
          lines_output.append("| 级别 | 行号 | 类型 | 说明 |")
          lines_output.append("|------|------|------|------|")
          for issue in issues:
              line_no = issue.get("line", "—")
              lines_output.append(f"| {issue.get('severity','?')} | {line_no} | {issue.get('type','?')} | {issue.get('detail','?')} |")
      else:
          lines_output.append("### 未发现问题")
          lines_output.append("该 skill 通过了所有安全检测维度。")

      lines_output.append("")
      lines_output.append("> **注意**：本工具仅报告风险，不阻断安装。最终决定权在您。")

      output_text = "\n".join(lines_output)

      return {
          "rating": rating,
          "total_issues": result.get("total_issues", 0),
          "breakdown": breakdown,
          "issues": issues,
          "report": output_text,
      }

# ============================================================
# 使用说明（供 Flocks agent 理解如何调用此 skill）
# ============================================================
usage: |
  当用户要求对某个 skill 进行安全审计时，加载本 skill 并调用 detect_malicious_skill 函数。

  **调用示例**：
  - `detect_malicious_skill(skill_path="/path/to/some-skill")`
  - `detect_malicious_skill(skill_text="name: xxx\ndescription: ...")`

  **输出格式**：
  - rating: CRITICAL | HIGH | MEDIUM | LOW | SAFE
  - total_issues: 整数
  - breakdown: 各检测类别问题数量
  - issues: 问题列表（line, type, detail, severity）
  - report: 格式化的中文 Markdown 报告

  **注意**：
  - 本 skill 仅做静态分析，无法检测动态加载或运行时行为
  - 对于复杂混淆，需结合人工研判
  - 建议结合 tool_builder skill 的安全准则共同使用

# ============================================================
# 元信息
# ============================================================
version: "1.0.0"
author: "Flocks SecOps Team"
tags: ["security", "skill-audit", "threat-detection", "prompt-injection", "supply-chain"]
