# 新同学上手指南（Windows）

照着做，大约 15 分钟。全程**不需要**任何人把账号、Cookie、API Key 发给你——你自己生成。

---

## 第 0 步：先确认你能不能拿到代码

这个仓库默认是**私有**的。下面三种方式任选，选完再继续第 1 步。

### 方式 A：让作者把你加成协作者（推荐）

作者在仓库 → Settings → Collaborators → Add people → 填你的 GitHub 用户名。
你接受邀请后就能 clone。

### 方式 B：作者把仓库改成公开

作者执行 `gh repo edit <owner>/<repo> --visibility public`。
之后任何人可直接 clone。

### 方式 C：作者直接发你压缩包

作者把仓库打包（可以不带 `.git`），你解压到任意目录，例如 `D:\chaoxing-tool`。
缺点：以后更新要重新要包。

> ⚠️ 不管哪种方式，**作者都不应该把他的 `cookie.txt` 或 `panel/config.json` 发给你**——里面有他的登录态和 API Key，而且对你没用。

---

## 第 1 步：装 Python（3.9 或更高）

1. 打开 https://www.python.org/downloads/
2. 下载 Windows 安装包，运行
3. **安装第一屏一定要勾上 `Add python.exe to PATH`**
4. 装完打开「命令提示符」（Win+R → `cmd` → 回车），验证：

```cmd
python --version
```

看到 `Python 3.9.x` 或更高即可。若提示"不是内部或外部命令"，说明没加 PATH，重装并勾选。

---

## 第 2 步：装浏览器

优先 **Google Chrome**（https://www.google.com/chrome/）。

没有 Chrome 也可以：
- 系统自带 Edge 也行（脚本会自动降级到 Edge）
- 或者执行一次 `python -m playwright install chromium`

---

## 第 3 步：装学习通桌面端并登录（最关键）

1. 到官网下载「学习通」Windows 版并安装
2. **打开并登录你自己的账号**
3. 随便点进一门课，确认能正常看到内容

> 这一步是整套工具的**身份来源**。没有它，工具拿不到你的登录态，什么都做不了。
> 装完不用一直开着，登录过一次、Cookie 在本地就行。

---

## 第 4 步：把代码弄到本地

打开「命令提示符」，进入你想放的目录：

```cmd
cd /d D:\
git clone https://github.com/<作者>/chaoxing-auto-toolkit.git
cd chaoxing-auto-toolkit
```

没有装 Git？用方式 C 的压缩包，然后 `cd /d 你的解压目录`。

---

## 第 5 步：装依赖

```cmd
pip install -r requirements.txt
```

如果下载慢，换国内源：

```cmd
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

---

## 第 6 步：跑自检（重要）

```cmd
python doctor.py
```

它会逐项检查并告诉你哪里不对。**理想输出是全绿：**

```
✅ Python 版本 ≥ 3.9        3.10.6
✅ 依赖包                    requests/pycryptodome/playwright/fastapi/uvicorn 齐全
✅ Chrome 浏览器             C:\Program Files\Google\Chrome\Application\chrome.exe
✅ 学习通桌面端 + 已登录        C:\Users\你\AppData\Roaming\cxstudy
✅ Cookie 解密               19 条 Cookie，uid=xxxxxx
✅ LLM 配置                  已配置 key，模型=deepseek-chat
✅ 面板端口                   8765 可用
✅ 监听地址安全               仅本机可访问（安全）
```

有 ❌ 就按下面对应项处理，处理完重跑：

| 未通过项 | 处理 |
|---|---|
| Python 版本 | 装 3.9+ 并加 PATH |
| 依赖包 | 重跑 `pip install -r requirements.txt` |
| Chrome 浏览器 | 装 Chrome/Edge，或 `python -m playwright install chromium` |
| 学习通桌面端 | 装桌面端并**登录一次** |
| Cookie 解密 | 桌面端处于未登录状态，重新登录 |
| LLM 配置 | 见第 7 步 |
| 面板端口 | 端口被占，改 `panel/config.json` 里的 `port` |
| 监听地址安全 | 见文末「手机访问」 |

---

## 第 7 步：配 LLM（自动作答用）

### 方式 1：面板里填（推荐）

先启动面板（第 9 步），在顶部「LLM 设置」填：

| 字段 | 填什么 |
|---|---|
| API Base URL | `https://api.deepseek.com/v1` |
| 模型 | `deepseek-chat` |
| API Key | 你自己的 Key |

点「保存设置」→ 点「测试连接」，看到"连接成功"即可。

### 方式 2：改配置文件

把 `panel\config.example.json` 复制成 `panel\config.json`，然后编辑：

```json
{
 "port": 8765,
 "host": "127.0.0.1",
 "access_token": "",
 "llm": {
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat",
  "api_key": "sk-你的Key"
 },
 "auto_submit": false
}
```

其他服务商写法：

| 服务 | base_url | model |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 本地 Ollama | `http://127.0.0.1:11434/v1` | `qwen2.5` |

> `panel/config.json` 已被 `.gitignore` 忽略，不会误提交。但**别把它发给别人**。

---

## 第 8 步：第一次同步作业列表

```cmd
python list_works.py
```

正常会打印出你的全部作业，并生成 `worklist.json`。

如果报错：
- 提示找不到 `cookie.txt` → 先跑 `python grab_cookie.py`
- 提示被重定向到登录页 → 打开学习通桌面端确认还是登录状态，再重跑

---

## 第 9 步：启动面板

**双击 `启动面板.bat`**（或命令行 `python panel\app.py`）。

窗口里会打印：

```
面板地址: http://127.0.0.1:8765/
API 文档: http://127.0.0.1:8765/docs
```

浏览器打开 **http://127.0.0.1:8765/**

> 这个窗口别关——关了面板就停了。它由你自己启动，跟别人无关。

---

## 第 10 步：在面板里操作

1. 顶部先填好 LLM 并「测试连接」
2. 表格里勾选要做的作业（默认只显示「未提交」）
3. 点 **「批量自动作答（暂存）」**
   - 首次会先自动「抓题」（开一个 Chrome 窗口跑几秒，别去动它）
   - 然后调 LLM 出答案并保存
4. 日志区会滚动显示进度
5. 做完后点每行的 **「看答案」**，重点扫一眼多选题
6. 确认没问题再点 **「交卷」**（会二次确认，交卷后不可改）

### 关于"交卷"

- 默认流程是**只暂存**，不会自动交卷
- 客观题保存后，学习通可能直接显示「已完成」（自动批阅），这属于正常
- 想改答案就再跑一次「自动作答」，会覆盖

---

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 面板突然打不开 | 黑窗口被关 / 进程结束 | 重新双击 `启动面板.bat`；查进程 `Get-NetTCPConnection -LocalPort 8765 -State Listen` |
| 抓题时弹登录页 | 网页端会话未建立 | 正常，脚本会自动走 SSO；若一直失败，重开桌面端 |
| 提示 `--remote-debugging-port: bad option` | 桌面端有参数校验 | 正常现象，本方案不依赖它，忽略 |
| 自动作答报"模型不存在" | 用了官方 API 却填内部模型名 | 模型改 `deepseek-chat` |
| 多选题偶尔错 | 模型倾向漏选/全选 | 交卷前「看答案」人工复核；实测准确率约 98% |
| 端口 8765 被占 | 别的程序占用 | 改 `config.json` 的 `port` |
| Cookie 解密失败 | 桌面端退出登录了 | 重新登录桌面端 |

---

## 手机访问（可选，同 WiFi）

1. 改 `panel\config.json`：

```json
"host": "0.0.0.0",
"access_token": "设一个只有你知道的口令"
```

2. 重启面板
3. 查电脑 IP：`ipconfig`，找 `IPv4 地址`，如 `192.168.1.8`
4. 手机浏览器打开 `http://192.168.1.8:8765/?token=你的口令`

> ⚠️ 只开 `0.0.0.0` 不设口令，同网段任何人都能操作你的账号、消耗你的 Key。程序会红字警告。

---

## 安全与责任

- `cookie.txt` 等于你的账号钥匙，**不要发给任何人**，不要提交到 Git
- API Key 只存在本机 `panel/config.json`，别外传
- 怀疑泄露：学习通「退出登录」刷新会话；LLM Key 去平台后台吊销重发
- 本工具仅供个人学习辅助，作业代做的后果自负，请遵守学校规定
