# 新同学上手指南（Windows）

**核心就一句话：下载项目 → 双击 `启动面板.bat` → 等它自己装好并弹出网页。**

首次运行会自动完成：装依赖 → 建桌面快捷方式 → 启动服务 → 打开浏览器。
之后每次只需双击**桌面上的「学习通面板」图标**。

---

## 第 0 步：拿到代码

仓库默认**私有**，三种方式任选：

| 方式 | 说明 |
|---|---|
| A. 作者加你为协作者（推荐） | 作者在 Settings → Collaborators 添加你的 GitHub 用户名，你接受邀请后即可 clone |
| B. 作者改成公开 | 作者执行 `gh repo edit <owner>/<repo> --visibility public` |
| C. 作者发压缩包 | 解压到任意目录，如 `D:\chaoxing-tool`（以后更新要重新要包） |

> ⚠️ 作者不应该把他的 `cookie.txt` / `panel/config.json` 发给你——那是他的登录态和 Key，对你也没用。

---

## 第 1 步：装 Python（3.9 或更高）

1. 打开 https://www.python.org/downloads/
2. 下载 Windows 安装包并运行
3. **第一屏一定要勾 `Add python.exe to PATH`**
4. 验证：Win+R → `cmd` →

```cmd
python --version
```

显示 `Python 3.9.x` 或更高即可。

> 装多个版本没关系，启动脚本会自动挑一个可用且装了依赖的。

---

## 第 2 步：装浏览器 + 学习通桌面端

**浏览器**：Chrome 或 Edge 任一即可（脚本会自动降级）。

**学习通桌面端**（最关键）：

1. 官网下载「学习通」Windows 版并安装
2. **打开并登录你自己的账号**
3. 点进任意一门课，确认能正常显示

> 工具正是从桌面端读出登录态的。没登录过 → 后面抓题一定失败。

---

## 第 3 步：双击 `启动面板.bat`

进入项目文件夹，双击 **`启动面板.bat`**。

它会自动依次做：

```
▶ 检查依赖        ← 缺什么就自动 pip install（失败会换清华源重试）
▶ 检查桌面快捷方式  ← 没有就在桌面创建「学习通面板」
▶ 检查学习通桌面端  ← 没登录会提醒你
▶ 启动面板        ← 并自动打开浏览器
```

首次装依赖大约 1～3 分钟，**保持网络、别关黑窗口**。

看到浏览器自动打开 `http://127.0.0.1:8765/` 就成功了。

> 如果 Windows 弹"是否允许此应用通过防火墙"，点允许（仅本机访问时其实不需要联网权限）。

---

## 第 4 步：填 LLM API Key

面板顶部「LLM 设置」：

| 字段 | 填什么 |
|---|---|
| API Base URL | `https://api.deepseek.com/v1` |
| 模型 | `deepseek-chat` |
| API Key | 你自己的 Key |

点「保存设置」→ 点「测试连接」，出现"连接成功"即可。

其他服务商：

| 服务 | base_url | model |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 本地 Ollama | `http://127.0.0.1:11434/v1` | `qwen2.5` |

Key 只存在本机 `panel/config.json`，已被 `.gitignore` 忽略，不会误提交。

---

## 第 5 步：开始用

1. 表格里勾选要做的作业（默认只显示「未提交」）
2. 点 **「批量自动作答（暂存）」**
   - 首次会先自动抓题，会弹一个 Chrome 窗口跑几秒，别去动它
   - 然后调 LLM 出答案并保存
3. 点每行 **「看答案」**，重点扫一眼多选题
4. 确认无误再点 **「交卷」**（二次确认，交卷后不可改）

> 默认只暂存不交卷。客观题保存后学习通可能直接显示「已完成」，这是自动批阅，属正常。

---

## 之后每次使用

**双击桌面「学习通面板」图标**即可。它会：

- 面板已在运行 → 直接打开网页，不重复起服务
- 没运行 → 起来并打开网页

黑窗口别关，关了就停服务。

---

## 排错

先跑体检：

```cmd
python doctor.py
```

它会逐项检查并给出修复建议。

| 现象 | 原因 | 处理 |
|---|---|---|
| 双击后闪一下就没了 | 没装 Python 3.9+ | 装 Python 并勾 PATH |
| 提示 `No Python with dependencies found` | 依赖没装成功 | 手动执行 `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple` |
| 面板打不开 | 进程被关 / 端口被占 | 重双击图标；或 `Get-NetTCPConnection -LocalPort 8765 -State Listen` 查看 |
| 抓题弹登录页 | 桌面端未登录 | 打开学习通重新登录 |
| 提示 `--remote-debugging-port: bad option` | 桌面端有参数校验 | 正常现象，本方案不依赖，忽略 |
| 自动作答报"模型不存在" | 用了官方 API 却填内部模型名 | 模型改 `deepseek-chat` |
| 多选题偶尔错 | 模型倾向漏选 | 交卷前「看答案」人工复核（实测准确率约 98%） |
| 桌面图标是白底/模糊 | 图标缓存 | 按 F5 刷新桌面；或重启 explorer |

---

## 手机访问（可选，同一 WiFi）

1. 编辑 `panel\config.json`：

```json
"host": "0.0.0.0",
"access_token": "设一个只有你知道的口令"
```

2. 重启面板
3. `ipconfig` 查电脑 IPv4，例如 `192.168.1.8`
4. 手机打开 `http://192.168.1.8:8765/?token=你的口令`

> ⚠️ 只开 `0.0.0.0` 不设口令 = 同网段任何人都能操作你的账号、烧你的 Key。程序会红字警告。

---

## 安全与责任

- `cookie.txt` 等于你的账号钥匙，**不要发给任何人**，不要提交 Git
- API Key 只在本机 `panel/config.json`
- 怀疑泄露：学习通「退出登录」刷新会话；LLM Key 去后台吊销重发
- 本工具仅供个人学习辅助，作业代做后果自负，请遵守学校规定
